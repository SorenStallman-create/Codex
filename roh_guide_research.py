"""Paper-only test of the article's testable statistical-arbitrage category.

This is intentionally a separate study from the frozen intraday_v2 results.
It uses only the already downloaded, public five-minute SPY/QQQ data.  The
strategy is a causal, dollar-scaled pair trade.  It does not submit orders,
use Kimi, call an LLM, or claim to reproduce the author's architecture.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import statistics
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from intraday_v2_data import OUT as V2_OUT, digest, validate, bounds, STEP, MINUTE

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "research" / "roh_guide_v1"
CAPITAL = 10_000.0
SYMBOLS = ("SPY", "QQQ")
NY = dt.timezone(dt.timedelta(hours=-5))


@dataclass(frozen=True)
class PairConfig:
    name: str
    z_entry: float = 2.0
    z_exit: float = 0.5
    z_stop: float = 4.0
    lookback_bars: int = 780       # ten regular sessions of five-minute bars
    fee: float = 0.00005
    slip: float = 0.00010
    gross_cap: float = 0.95
    max_hold_bars: int = 18        # 90 minutes; prevents an unbounded intraday hold

    @property
    def id(self):
        return f"{self.name}-z{self.z_entry:g}"


def read_data():
    audit = json.loads((V2_OUT / "data_audit.json").read_text())
    result = {}
    for symbol in SYMBOLS:
        path = V2_OUT / "data" / f"{symbol}.json"
        if digest(path) != audit["audits"][symbol]["data_sha256"]:
            raise RuntimeError(f"Input hash changed: {path}")
        result[symbol] = json.loads(path.read_text())
    common = sorted(set(result[SYMBOLS[0]]) & set(result[SYMBOLS[1]]))
    for day in common:
        if len(result["SPY"][day]) != len(result["QQQ"][day]):
            raise ValueError(f"Pair session length mismatch: {day}")
        validate(day, result["SPY"][day])
        validate(day, result["QQQ"][day])
    return result, common


def ols_beta(x, y):
    """OLS beta of x on y, with a stable positive fallback."""
    if len(x) != len(y) or len(x) < 2:
        return 1.0
    mx, my = statistics.mean(x), statistics.mean(y)
    den = sum((v - my) ** 2 for v in y)
    return max(0.1, min(5.0, sum((a - mx) * (b - my) for a, b in zip(x, y)) / den if den else 1.0))


def causal_signals(data, days, config):
    """Return per-day z-scores using only bars strictly before each signal."""
    flat = {s: [] for s in SYMBOLS}
    for day in days:
        flat["SPY"].extend(data["SPY"][day])
        flat["QQQ"].extend(data["QQQ"][day])
    if len(flat["SPY"]) != len(flat["QQQ"]):
        raise ValueError("Pair history lengths differ")
    by_day = {d: {} for d in days}
    index = {d: i * len(data["SPY"][d]) for i, d in enumerate(days)}
    # The first ten sessions have no signal.  At a given bar, all estimation
    # rows end at i-1; today's close can never affect its own z-score.
    for day_i, day in enumerate(days):
        start = index[day]
        for j, pair in enumerate(zip(data["SPY"][day], data["QQQ"][day])):
            i = start + j
            if i < config.lookback_bars:
                continue
            spy, qqq = pair
            history_spy = flat["SPY"][i - config.lookback_bars : i]
            history_qqq = flat["QQQ"][i - config.lookback_bars : i]
            # Fit and standardize on the same causal history.  The current
            # close is used once, only as the signal being evaluated.
            log_spy = [math.log(b[4]) for b in history_spy]
            log_qqq = [math.log(b[4]) for b in history_qqq]
            beta = ols_beta(log_spy, log_qqq)
            spread = [a - beta * b for a, b in zip(log_spy, log_qqq)]
            mean = statistics.mean(spread)
            sd = statistics.pstdev(spread)
            if sd <= 1e-12:
                continue
            current = math.log(spy[4]) - beta * math.log(qqq[4])
            z = (current - mean) / sd
            by_day[day][spy[0]] = {
                "closed_ms": spy[0] + STEP,
                "z": z,
                "beta": beta,
                "spread_mean": mean,
                "spread_sd": sd,
            }
    return by_day


def _fill(price, side, slip):
    return price * (1 + slip if side == "buy" else 1 - slip)


def run_day(data, day, signals, config, collect=True):
    spy, qqq = data["SPY"][day], data["QQQ"][day]
    validate(day, spy)
    validate(day, qqq)
    if len(spy) != len(qqq) or any(a[0] != b[0] for a, b in zip(spy, qqq)):
        raise ValueError(f"Pair bars are not aligned: {day}")
    cash = CAPITAL
    position = None
    peak = CAPITAL
    maxdd = 0.0
    fees = slippage = turnover = 0.0
    trades, fills, decisions, curve = [], [], [], [CAPITAL]

    def exit_position(i, reason):
        nonlocal cash, position, fees, slippage, turnover
        if position is None:
            return
        a = spy[i]
        b = qqq[i]
        direction = position["direction"]
        # direction +1 = long SPY/short QQQ; -1 is the inverse.
        legs = [("SPY", position["spy_qty"], "sell" if direction > 0 else "buy", a[1]),
                ("QQQ", position["qqq_qty"], "buy" if direction > 0 else "sell", b[1])]
        proceeds = 0.0
        leg_rows = []
        for symbol, qty, side, market in legs:
            fill = _fill(market, side, config.slip)
            fee = abs(qty * fill) * config.fee
            value = qty * fill if side == "sell" else -qty * fill
            proceeds += value - fee
            fees += fee
            slippage += abs(qty * (fill - market))
            turnover += abs(qty * market)
            leg_rows.append({"symbol": symbol, "side": side, "qty": qty, "fill": fill, "fee": fee})
        cash += proceeds
        pnl = cash - position["cash_before"]
        trades.append({**position, "exit_ms": a[0], "exit_reason": reason, "net_pnl": pnl,
                       "legs_exit": json.dumps(leg_rows, separators=(",", ":"))})
        fills.extend({"time_ms": a[0], "reason": reason, **row} for row in leg_rows)
        position = None

    for i, (a, b) in enumerate(zip(spy, qqq)):
        t, so, sh, sl, sc, sv = a
        qo, qh, ql, qc, qv = b[1:]
        sig = signals.get(t)
        action = "flat"
        z = sig["z"] if sig else None
        if position is not None:
            mark = position["direction"] * (sc * position["spy_qty"]
                                              - qc * position["qqq_qty"])
            # z can be unavailable during the first bars, but an open trade has
            # a stored spread direction and is always flattened by the day end.
            elapsed = i - position["entry_i"]
            if (z is not None and ((position["direction"] > 0 and z >= -config.z_exit)
                                   or (position["direction"] < 0 and z <= config.z_exit))):
                exit_position(i, "mean_reversion_exit")
                action = "exit_mean"
            elif z is not None and abs(z) >= config.z_stop:
                exit_position(i, "spread_stop")
                action = "exit_spread_stop"
            elif elapsed >= config.max_hold_bars:
                exit_position(i, "time_limit")
                action = "exit_time_limit"
        if position is None and sig is not None and i < len(spy) - 1:
            direction = 1 if z <= -config.z_entry else -1 if z >= config.z_entry else 0
            if direction:
                # The next bar open is the first executable observation after
                # the signal close.  Size gross exposure, then use whole shares.
                next_spy, next_qqq = spy[i + 1][1], qqq[i + 1][1]
                beta = sig["beta"]
                spy_notional = CAPITAL * config.gross_cap / (1 + beta)
                qqq_notional = spy_notional * beta
                spy_qty = math.floor(spy_notional / next_spy)
                qqq_qty = math.floor(qqq_notional / next_qqq)
                if spy_qty and qqq_qty:
                    # For direction +1 buy SPY/sell QQQ; direction -1 reverse.
                    spy_side = "buy" if direction > 0 else "sell"
                    qqq_side = "sell" if direction > 0 else "buy"
                    sf = _fill(next_spy, spy_side, config.slip)
                    qf = _fill(next_qqq, qqq_side, config.slip)
                    entry_fees = (abs(spy_qty * sf) + abs(qqq_qty * qf)) * config.fee
                    entry_slip = abs(spy_qty * (sf - next_spy)) + abs(qqq_qty * (qf - next_qqq))
                    cash_before = cash
                    cash -= spy_qty * sf if spy_side == "buy" else -spy_qty * sf
                    cash -= qqq_qty * qf if qqq_side == "buy" else -qqq_qty * qf
                    cash -= entry_fees
                    fees += entry_fees
                    slippage += entry_slip
                    turnover += abs(spy_qty * next_spy) + abs(qqq_qty * next_qqq)
                    position = {"entry_ms": next_spy and spy[i + 1][0], "entry_i": i + 1,
                                "direction": direction, "z_entry": z, "beta": beta,
                                "spy_qty": spy_qty, "qqq_qty": qqq_qty,
                                "spy_entry": sf, "qqq_entry": qf,
                                "cash_before": cash_before,
                                "entry_fee": entry_fees, "entry_slippage": entry_slip}
                    fills.extend([{"time_ms": spy[i + 1][0], "symbol": "SPY", "side": spy_side,
                                   "qty": spy_qty, "fill": sf, "fee": entry_fees * abs(spy_qty * sf) /
                                   (abs(spy_qty * sf) + abs(qqq_qty * qf))},
                                  {"time_ms": spy[i + 1][0], "symbol": "QQQ", "side": qqq_side,
                                   "qty": qqq_qty, "fill": qf, "fee": entry_fees * abs(qqq_qty * qf) /
                                   (abs(spy_qty * sf) + abs(qqq_qty * qf))}])
                    action = "enter_long_spread" if direction > 0 else "enter_short_spread"
        if i == len(spy) - 1 and position is not None:
            exit_position(i, "scheduled_close")
            action = "day_end"
        if position is None:
            equity = cash
        else:
            direction = position["direction"]
            equity = cash + direction * (sc * position["spy_qty"]
                                         - qc * position["qqq_qty"])
        peak = max(peak, equity)
        maxdd = max(maxdd, 1 - equity / peak)
        curve.append(equity)
        if collect:
            decisions.append({"time_ms": t, "z": z, "action": action, "equity": equity,
                              "position": position["direction"] if position else 0})
    if position is not None:
        raise AssertionError("Pair position leaked overnight")
    return {"day": day, "model": config.id, "net_pnl": cash - CAPITAL,
            "return_pct": (cash / CAPITAL - 1) * 100, "trades": len(trades),
            "fees": fees, "slippage": slippage, "turnover": turnover,
            "drawdown_pct": maxdd * 100, "curve": curve, "trades_rows": trades,
            "fills": fills, "decisions": decisions}


def summary(rows):
    values = [r["net_pnl"] for r in rows]
    return {"days": len(rows), "mean_pnl": statistics.mean(values),
            "median_pnl": statistics.median(values), "total_pnl": sum(values),
            "best_pnl": max(values), "worst_pnl": min(values),
            "positive_days": sum(x > 1e-9 for x in values),
            "flat_days": sum(abs(x) <= 1e-9 for x in values),
            "negative_days": sum(x < -1e-9 for x in values),
            "trades": sum(r["trades"] for r in rows),
            "fees": sum(r["fees"] for r in rows), "slippage": sum(r["slippage"] for r in rows),
            "max_drawdown_pct": max(r["drawdown_pct"] for r in rows)}


def write_csv(path, rows):
    if not rows:
        return
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(stage):
    OUT.mkdir(parents=True, exist_ok=True)
    data, days = read_data()
    # These cutoffs are frozen before reading any results.  The whole sample
    # is exploratory because the source dates were visible in v1/v2 work.
    warmup = 10
    usable = days[warmup:]
    dev, val, evaluation = usable[:14], usable[14:21], usable[21:]
    configs = [PairConfig("canonical", z_entry=z) for z in (1.5, 2.0, 2.5)]
    signals = {c.id: causal_signals(data, days, c) for c in configs}
    if stage == "freeze":
        ranking = []
        for c in configs:
            rows = [run_day(data, d, signals[c.id][d], c, False) for d in dev]
            ranking.append({"config": asdict(c), "development": summary(rows)})
        # The canonical 2.0σ rule remains the named lead; selection records
        # alternatives but does not let a tiny sample promote a variant.
        frozen = {"created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "data_hashes": {s: digest(V2_OUT / "data" / f"{s}.json") for s in SYMBOLS},
                  "source_dates": days, "warmup_sessions": warmup,
                  "splits": {"development": dev, "validation": val, "evaluation": evaluation},
                  "configs": [asdict(c) for c in configs], "lead": configs[1].id,
                  "development_ranking": ranking,
                  "status": "EXPLORATORY; dates previously visible; no untouched OOS"}
        (OUT / "frozen.json").write_text(json.dumps(frozen, indent=2))
        print(json.dumps(frozen, indent=2))
        return
    frozen = json.loads((OUT / "frozen.json").read_text())
    rows, trades, fills, decisions = [], [], [], []
    for c in configs:
        for phase, phase_days in (("development", dev), ("validation", val), ("evaluation", evaluation)):
            for d in phase_days:
                result = run_day(data, d, signals[c.id][d], c)
                small = {k: v for k, v in result.items() if k not in ("curve", "trades_rows", "fills", "decisions")}
                rows.append({"phase": phase, **small})
                trades.extend({"phase": phase, "model": c.id, "day": d, **t} for t in result["trades_rows"])
                fills.extend({"phase": phase, "model": c.id, "day": d, **f} for f in result["fills"])
                decisions.extend({"phase": phase, "model": c.id, "day": d, **x} for x in result["decisions"])
    report = {"created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "freeze": frozen,
              "summary": {c.id: {phase: summary([r for r in rows if r["model"] == c.id and r["phase"] == phase])
                                  for phase in ("development", "validation", "evaluation")} for c in configs},
              "daily": rows,
              "research_note": "Market-neutral pair trades require short availability, borrow costs, margin and quote-aware fills; none are modeled here.",
              "qualified": False,
              "gates": {"minimum_evaluation_sessions": len(evaluation) >= 120,
                        "minimum_trades": sum(r["trades"] for r in rows if r["phase"] == "evaluation") >= 100,
                        "forward_paper": False,
                        "real_orders": False}}
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
    write_csv(OUT / "daily.csv", rows)
    write_csv(OUT / "trades.csv", trades)
    write_csv(OUT / "fills.csv", fills)
    write_csv(OUT / "decisions.csv", decisions)
    for c in configs:
        print(c.id, json.dumps(report["summary"][c.id]["evaluation"], sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("freeze", "evaluate"))
    run(parser.parse_args().stage)
