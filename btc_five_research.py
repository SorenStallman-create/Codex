"""Fixed five-bot BTC intraday study.

All signals are generated from completed 15-minute candles and executed at the
next candle open.  The evaluator is deliberately small and auditable: one BTC
contract, one-times notional cap, no leverage, no parameter sweep, daily
flattening, explicit fees/slippage, and a frozen chronological holdout.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from btc_five_data import DATA, END, OUT, SYMBOL, load, sha256


UTC = dt.timezone.utc
NY = ZoneInfo("America/New_York")
BAR_MS = 15 * 60 * 1000
CAPITAL = 10_000.0
FEE = 0.0005
SLIP = 0.0002
RISK_PER_TRADE = 0.005
NOTIONAL_CAP = 0.50
FUNDING_PER_8H = 0.0001


@dataclass(frozen=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class BotSpec:
    id: str
    name: str
    thesis: str
    source: str
    stop_atr: float
    max_hold: int
    max_entries_per_day: int = 1


SPECS = (
    BotSpec(
        "volume_impulse",
        "Volume Impulse",
        "After an unusually high-volume first two UTC hours, follow a large opening move for the rest of the day.",
        "Shen et al. Bitcoin intraday time-series momentum",
        2.0,
        88,
    ),
    BotSpec(
        "lag1_reversal",
        "Lag-1 Reversal",
        "Fade a sufficiently large prior 15-minute move when the bar is liquid enough; hold one bar.",
        "Kitron and Wengrowicz short-horizon crypto reversal study",
        1.0,
        1,
    ),
    BotSpec(
        "vwap_reversion",
        "Daily VWAP Reversion",
        "Fade large, volatility-normalized dislocations from the UTC-day VWAP and exit near fair value.",
        "Published intraday mean-reversion family; fixed rule for this study",
        1.5,
        8,
    ),
    BotSpec(
        "ny_open_breakout",
        "New York Open Breakout",
        "Trade the first confirmed break of the 09:30 New York 30-minute range, then flatten before the close.",
        "Opening-range breakout research lead, adapted to BTC's 24/7 tape",
        1.5,
        20,
    ),
    BotSpec(
        "ema_atr_trend",
        "EMA/ATR Trend",
        "Trade aligned fast/slow EMA direction after a volatility-sized impulse and exit on trend failure.",
        "Time-series momentum and volatility-scaling family",
        2.0,
        64,
    ),
)


def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def day(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


def ny_parts(ms: int) -> tuple[str, int]:
    x = dt.datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone(NY)
    return x.date().isoformat(), x.hour * 60 + x.minute


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for i, value in enumerate(values):
        running += value
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if not values:
        return out
    alpha = 2.0 / (period + 1.0)
    value = values[0]
    for i, current in enumerate(values):
        value = current if i == 0 else value + alpha * (current - value)
        if i >= period - 1:
            out[i] = value
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, period + 1)]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, period + 1)]
    gain = sum(gains) / period
    loss = sum(losses) / period
    out[period] = 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = (gain * (period - 1) + max(change, 0.0)) / period
        loss = (loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)
    return out


def atr(bars: list[Bar], period: int = 14) -> list[float | None]:
    trs: list[float] = []
    for i, bar in enumerate(bars):
        previous = bars[i - 1].close if i else bar.close
        trs.append(max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous)))
    out: list[float | None] = [None] * len(bars)
    if len(bars) < period:
        return out
    value = sum(trs[:period]) / period
    out[period - 1] = value
    for i in range(period, len(bars)):
        value = (value * (period - 1) + trs[i]) / period
        out[i] = value
    return out


def rolling_std(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    square = 0.0
    for i, value in enumerate(values):
        total += value
        square += value * value
        if i >= period:
            old = values[i - period]
            total -= old
            square -= old * old
        if i >= period - 1:
            mean = total / period
            out[i] = math.sqrt(max(0.0, square / period - mean * mean))
    return out


def daily_vwap(bars: list[Bar]) -> list[float]:
    out: list[float] = []
    current_day = ""
    pv = 0.0
    volume = 0.0
    for bar in bars:
        d = day(bar.time)
        if d != current_day:
            current_day = d
            pv = volume = 0.0
        typical = (bar.high + bar.low + bar.close) / 3.0
        pv += typical * bar.volume
        volume += bar.volume
        out.append(pv / volume if volume else bar.close)
    return out


def prior_day_first2h(bars: list[Bar]) -> dict[str, tuple[float, float]]:
    stats: dict[str, tuple[float, float]] = {}
    current = ""
    opened = 0.0
    close = 0.0
    volume = 0.0
    count = 0
    for bar in bars:
        d = day(bar.time)
        if d != current:
            if current and count >= 8:
                stats[current] = (close / opened - 1.0, volume)
            current = d
            opened = bar.open
            close = bar.close
            volume = bar.volume
            count = 1
        elif count < 8:
            close = bar.close
            volume += bar.volume
            count += 1
    if current and count >= 8:
        stats[current] = (close / opened - 1.0, volume)
    return stats


def build_features(bars: list[Bar]) -> dict[str, list]:
    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    a = atr(bars)
    fast = ema(closes, 64)
    slow = ema(closes, 256)
    rs = rsi(closes)
    vwap = daily_vwap(bars)
    vol_mean = sma(volumes, 96)
    residual = [closes[i] - vwap[i] for i in range(len(bars))]
    residual_sd = rolling_std(residual, 32)
    day_index: list[int] = []
    current = ""
    n = -1
    for b in bars:
        d = day(b.time)
        if d != current:
            current = d
            n = 0
        else:
            n += 1
        day_index.append(n)
    first2 = prior_day_first2h(bars)
    # Historical first-two-hour volume median is calculated causally below.
    impulse_volume_ratio: list[float | None] = [None] * len(bars)
    impulse_return: list[float | None] = [None] * len(bars)
    by_day: dict[str, tuple[float, float]] = {}
    for d, pair in first2.items():
        by_day[d] = pair
    day_values: list[tuple[str, float, float]] = []
    for d in sorted(by_day):
        day_values.append((d, by_day[d][0], by_day[d][1]))
    prior_volumes: list[float] = []
    day_first: dict[str, tuple[float, float]] = {}
    for d, ret, vol in day_values:
        day_first[d] = (ret, vol)
    seen_days: list[str] = []
    for i, b in enumerate(bars):
        d = day(b.time)
        if day_index[i] == 7 and d in day_first:
            prior = [day_first[x][1] for x in seen_days[-20:]]
            if len(prior) >= 10:
                median = statistics.median(prior)
                impulse_volume_ratio[i] = day_first[d][1] / median if median else None
                impulse_return[i] = day_first[d][0]
        if d not in seen_days:
            seen_days.append(d)
    return {
        "atr": a,
        "ema_fast": fast,
        "ema_slow": slow,
        "rsi": rs,
        "vwap": vwap,
        "volume_mean": vol_mean,
        "residual": residual,
        "residual_sd": residual_sd,
        "day_index": day_index,
        "impulse_volume_ratio": impulse_volume_ratio,
        "impulse_return": impulse_return,
    }


def build_signals(bars: list[Bar], f: dict[str, list], bot: BotSpec) -> list[int]:
    signals = [0] * len(bars)
    if bot.id == "volume_impulse":
        for i, b in enumerate(bars):
            ratio = f["impulse_volume_ratio"][i]
            move = f["impulse_return"][i]
            if ratio is not None and move is not None and ratio >= 1.20 and abs(move) >= 0.0025:
                signals[i] = 1 if move > 0 else -1
            elif f["day_index"][i] >= 8:
                signals[i] = signals[i - 1]
    elif bot.id == "lag1_reversal":
        for i in range(1, len(bars)):
            ret = bars[i].close / bars[i - 1].close - 1.0
            liquid = f["volume_mean"][i] is not None and bars[i].volume >= f["volume_mean"][i] * 0.8
            if liquid and ret <= -0.0015:
                signals[i] = 1
            elif liquid and ret >= 0.0015:
                signals[i] = -1
    elif bot.id == "vwap_reversion":
        for i, b in enumerate(bars):
            sd = f["residual_sd"][i]
            atr_value = f["atr"][i]
            r = f["rsi"][i]
            if sd is None or atr_value is None or r is None or f["day_index"][i] < 8:
                continue
            distance = b.close - f["vwap"][i]
            scale = max(sd, atr_value * 0.75)
            if distance <= -1.25 * scale and r <= 38:
                signals[i] = 1
            elif distance >= 1.25 * scale and r >= 62:
                signals[i] = -1
            elif abs(distance) <= 0.25 * scale:
                signals[i] = 0
            else:
                signals[i] = signals[i - 1]
    elif bot.id == "ny_open_breakout":
        ranges: dict[str, tuple[float, float]] = {}
        triggered: set[str] = set()
        for i, b in enumerate(bars):
            d, minute = ny_parts(b.time)
            if 570 <= minute < 600:  # 09:30 through 09:45 UTC-local bars
                old = ranges.get(d)
                ranges[d] = (b.high if old is None else max(old[0], b.high), b.low if old is None else min(old[1], b.low))
            if 600 <= minute < 720 and d in ranges and d not in triggered:
                high, low = ranges[d]
                volume_ok = f["volume_mean"][i] is not None and b.volume >= f["volume_mean"][i]
                if volume_ok and b.close > high:
                    signals[i] = 1
                    triggered.add(d)
                elif volume_ok and b.close < low:
                    signals[i] = -1
                    triggered.add(d)
            elif 720 <= minute < 945 and d in triggered:
                signals[i] = signals[i - 1]
            elif minute >= 945 or minute < 570:
                signals[i] = 0
    elif bot.id == "ema_atr_trend":
        for i, b in enumerate(bars):
            fast = f["ema_fast"][i]
            slow = f["ema_slow"][i]
            atr_value = f["atr"][i]
            if fast is None or slow is None or atr_value is None:
                continue
            impulse = b.close / bars[i - 4].close - 1.0 if i >= 4 else 0.0
            if fast > slow and impulse >= 0.002 and b.close > fast:
                signals[i] = 1
            elif fast < slow and impulse <= -0.002 and b.close < fast:
                signals[i] = -1
            elif (signals[i - 1] > 0 and b.close < fast) or (signals[i - 1] < 0 and b.close > fast):
                signals[i] = 0
            else:
                signals[i] = signals[i - 1]
    else:
        raise ValueError(bot.id)
    return signals


def fill_price(market: float, direction: int, entry: bool) -> float:
    if entry:
        return market * (1.0 + SLIP if direction > 0 else 1.0 - SLIP)
    return market * (1.0 - SLIP if direction > 0 else 1.0 + SLIP)


def equity(balance: float, direction: int, quantity: float, entry_fill: float | None, market: float) -> float:
    if not direction or not quantity or entry_fill is None:
        return balance
    return balance + direction * quantity * (market - entry_fill)


def simulate(
    bars: list[Bar],
    features: dict[str, list],
    signals: list[int],
    bot: BotSpec,
    start_ms: int,
    end_ms: int,
    collect: bool = False,
    fee: float = FEE,
    slip: float = SLIP,
    funding_per_8h: float = FUNDING_PER_8H,
) -> dict:
    balance = CAPITAL
    direction = 0
    quantity = 0.0
    entry_fill: float | None = None
    entry_market: float | None = None
    entry_time: int | None = None
    entry_fee: float | None = None
    stop: float | None = None
    held = 0
    current_day = ""
    day_start_balance = CAPITAL
    funding_bucket: int | None = None
    trades: list[dict] = []
    fills: list[dict] = []
    curve: list[tuple[int, float]] = []
    daily: list[dict] = []
    actions: list[dict] = []
    peak = CAPITAL
    max_dd = 0.0
    fees_total = 0.0
    funding_total = 0.0
    exposure_bars = 0
    entries_today = 0
    last_bar: Bar | None = None

    def close_position(market: float, t: int, reason: str) -> None:
        nonlocal balance, direction, quantity, entry_fill, entry_market, entry_time, entry_fee, stop, held, funding_bucket, fees_total
        if not direction or entry_fill is None or entry_time is None or entry_fee is None:
            return
        out_fill = market * (1.0 - slip if direction > 0 else 1.0 + slip)
        fee_out = quantity * abs(out_fill) * fee
        fees_total += fee_out
        price_pnl = direction * quantity * (out_fill - entry_fill)
        net_pnl = price_pnl - entry_fee - fee_out
        balance += price_pnl - fee_out
        trade = {
            "bot": bot.id,
            "entry_time": iso(entry_time),
            "exit_time": iso(t),
            "direction": "LONG" if direction > 0 else "SHORT",
            "quantity": quantity,
            "entry_market": entry_market,
            "entry_fill": entry_fill,
            "exit_market": market,
            "exit_fill": out_fill,
            "entry_fee": entry_fee,
            "exit_fee": fee_out,
            "net_pnl": net_pnl,
            "return_on_notional_pct": 100.0 * net_pnl / max(abs(quantity * entry_fill), 1e-12),
            "bars_held": held,
            "reason": reason,
        }
        trades.append(trade)
        fills.append({"time": iso(t), "side": "SELL" if direction > 0 else "BUY", "market": market, "fill": out_fill, "quantity": quantity, "fee": fee_out, "reason": reason})
        direction = 0
        quantity = 0.0
        entry_fill = entry_market = entry_time = entry_fee = stop = None
        held = 0
        funding_bucket = None

    def open_position(market: float, t: int, new_direction: int, atr_value: float | None) -> None:
        nonlocal balance, direction, quantity, entry_fill, entry_market, entry_time, entry_fee, stop, held, funding_bucket, fees_total, entries_today
        if entries_today >= bot.max_entries_per_day or not new_direction or atr_value is None or not math.isfinite(atr_value) or atr_value <= 0:
            return
        eq = balance
        stop_distance = bot.stop_atr * atr_value
        risk_qty = eq * RISK_PER_TRADE / stop_distance
        cap_qty = eq * NOTIONAL_CAP / market
        q = min(risk_qty, cap_qty)
        if q <= 0:
            return
        in_fill = market * (1.0 + slip if new_direction > 0 else 1.0 - slip)
        fee_in = q * in_fill * fee
        balance -= fee_in
        fees_total += fee_in
        direction = new_direction
        quantity = q
        entry_fill = in_fill
        entry_market = market
        entry_time = t
        entry_fee = fee_in
        entries_today += 1
        held = 0
        funding_bucket = t // (8 * 60 * 60 * 1000)
        stop = market - stop_distance if new_direction > 0 else market + stop_distance
        fills.append({"time": iso(t), "side": "BUY" if new_direction > 0 else "SELL", "market": market, "fill": in_fill, "quantity": q, "fee": fee_in, "reason": "signal_entry"})

    def record(t: int, b: Bar, action: str) -> None:
        nonlocal peak, max_dd, exposure_bars
        eq = equity(balance, direction, quantity, entry_fill, b.close)
        peak = max(peak, eq)
        max_dd = max(max_dd, 1.0 - eq / peak if peak else 0.0)
        curve.append((t + BAR_MS, eq))
        if direction:
            exposure_bars += 1
        if collect:
            actions.append({"time": iso(t), "close": b.close, "equity": eq, "direction": direction, "quantity": quantity, "action": action})

    for i, b in enumerate(bars):
        if b.time < start_ms:
            continue
        if b.time >= end_ms:
            break
        last_bar = b
        d = day(b.time)
        action = "hold_cash"
        opened_today = current_day == d
        if current_day == "":
            current_day = d
            day_start_balance = balance
            entries_today = 0
        elif d != current_day:
            if direction:
                close_position(b.open, b.time, "utc_day_flatten")
                action = "utc_day_flatten"
            daily.append({"date": current_day, "ending_equity": balance, "return_pct": 100.0 * (balance / day_start_balance - 1.0)})
            current_day = d
            day_start_balance = balance
            entries_today = 0
            opened_today = False
        can_open = True
        if direction and funding_bucket is not None:
            bucket = b.time // (8 * 60 * 60 * 1000)
            crossed = bucket - funding_bucket
            if crossed > 0:
                cost = quantity * b.open * funding_per_8h * crossed
                balance -= cost
                funding_total += cost
                funding_bucket = bucket
        if direction and i > 0:
            a = features["atr"][i - 1]
            previous_close = bars[i - 1].close
            if a is not None and stop is not None:
                if direction > 0:
                    stop = max(stop, previous_close - bot.stop_atr * a)
                else:
                    stop = min(stop, previous_close + bot.stop_atr * a)
        if direction and stop is not None:
            hit = b.open <= stop if direction > 0 else b.open >= stop
            intrabar = b.low <= stop if direction > 0 else b.high >= stop
            if hit:
                close_position(b.open, b.time, "gap_stop")
                action = "gap_stop"
                can_open = False
            elif intrabar:
                close_position(stop, b.time, "intrabar_stop")
                action = "intrabar_stop"
                can_open = False
        if direction:
            held += 1
            if held >= bot.max_hold:
                close_position(b.open, b.time, "max_hold")
                action = "max_hold"
                can_open = False
        # The signal from the previous completed bar may not cross a UTC day.
        target = signals[i - 1] if i > 0 and day(bars[i - 1].time) == d else 0
        if direction and can_open and target != direction:
            close_position(b.open, b.time, "signal_exit")
            action = "signal_exit"
        if not direction and can_open and target and opened_today:
            open_position(b.open, b.time, target, features["atr"][i - 1] if i > 0 else None)
            if direction:
                action = "signal_entry"
        record(b.time, b, action)
    if last_bar is None:
        raise ValueError("No execution candles")
    if direction:
        close_position(last_bar.close, last_bar.time + BAR_MS, "period_end")
    if current_day:
        daily.append({"date": current_day, "ending_equity": balance, "return_pct": 100.0 * (balance / day_start_balance - 1.0)})
    final_curve = [v for _, v in curve] + [balance]
    if not curve:
        final_curve = [CAPITAL, balance]
    pnls = [float(t["net_pnl"]) for t in trades]
    gains = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    daily_returns = [float(x["return_pct"]) / 100.0 for x in daily]
    mean_daily = statistics.fmean(daily_returns) if daily_returns else 0.0
    daily_std = statistics.pstdev(daily_returns) if len(daily_returns) > 1 else 0.0
    sharpe = mean_daily / daily_std * math.sqrt(365.0) if daily_std else 0.0
    return {
        "bot": bot.id,
        "name": bot.name,
        "start": iso(start_ms),
        "end_exclusive": iso(end_ms),
        "net_pnl": balance - CAPITAL,
        "return_pct": 100.0 * (balance / CAPITAL - 1.0),
        "ending_equity": balance,
        "max_drawdown_pct": max_dd * 100.0,
        "trades": len(trades),
        "wins": sum(p > 0 for p in pnls),
        "win_rate_pct": 100.0 * sum(p > 0 for p in pnls) / len(pnls) if pnls else 0.0,
        "profit_factor": gains / losses if losses else (999.0 if gains else 0.0),
        "mean_daily_return_pct": mean_daily * 100.0,
        "sharpe_daily": sharpe,
        "fees": fees_total,
        "funding": funding_total,
        "exposure_pct": 100.0 * exposure_bars / max(len(curve), 1),
        "daily": daily,
        "trades_rows": trades,
        "fills_rows": fills,
        "actions_rows": actions,
        "curve": [{"time": iso(t), "equity": v} for t, v in curve] + ([{"time": iso(last_bar.time + BAR_MS), "equity": balance}] if last_bar else []),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_bars() -> list[Bar]:
    rows = load()
    return [Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


def stamp(text: str) -> int:
    return int(dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def quality(result: dict) -> float:
    pf_bonus = min(float(result["profit_factor"]), 3.0) if result["profit_factor"] else 0.0
    trade_penalty = 0.25 if result["trades"] < 30 else 0.0
    return float(result["mean_daily_return_pct"]) - 0.15 * float(result["max_drawdown_pct"]) + 0.05 * math.log1p(pf_bonus) - trade_penalty


def bootstrap(daily: list[dict], seed: int = 20260918, draws: int = 2000) -> dict:
    returns = [float(x["return_pct"]) / 100.0 for x in daily]
    if not returns:
        return {"lower_95_pct": 0.0, "upper_95_pct": 0.0, "positive_fraction": 0.0, "days": 0}
    rng = __import__("random").Random(seed)
    samples: list[float] = []
    n = len(returns)
    for _ in range(draws):
        chosen: list[float] = []
        while len(chosen) < n:
            start = rng.randrange(n)
            chosen.extend(returns[(start + j) % n] for j in range(min(7, n)))
        total = math.prod(1.0 + x for x in chosen[:n]) - 1.0
        samples.append(total * 100.0)
    samples.sort()
    return {
        "method": "Circular seven-day blocks; 2,000 resamples; selection uncertainty excluded",
        "lower_95_pct": samples[int(0.025 * len(samples))],
        "upper_95_pct": samples[int(0.975 * len(samples)) - 1],
        "positive_fraction": sum(x > 0 for x in samples) / len(samples),
        "days": n,
    }


def run_study() -> dict:
    audit_path = OUT / "data_audit.json"
    if not audit_path.exists():
        raise RuntimeError("Run btc_five_data.py first")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    data_path = DATA / "BTCUSDT_15m.jsonl"
    if sha256(data_path) != audit.get("sha256"):
        raise ValueError("BTC data hash changed after validation")
    bars = load_bars()
    features = build_features(bars)
    signals = {bot.id: build_signals(bars, features, bot) for bot in SPECS}
    periods = {
        "development": (stamp("2021-07-01T00:00:00Z"), stamp("2023-01-01T00:00:00Z")),
        "validation": (stamp("2023-01-01T00:00:00Z"), stamp("2024-01-01T00:00:00Z")),
        "holdout": (stamp("2024-01-01T00:00:00Z"), stamp("2025-01-01T00:00:00Z")),
        "regression": (stamp("2025-01-01T00:00:00Z"), END),
    }
    summary_rows: list[dict] = []
    daily_rows: list[dict] = []
    equity_rows: list[dict] = []
    trade_rows: list[dict] = []
    fill_rows: list[dict] = []
    action_rows: list[dict] = []
    results: dict[str, dict] = {}
    for period_name, (start, end) in periods.items():
        results[period_name] = {}
        for bot in SPECS:
            result = simulate(bars, features, signals[bot.id], bot, start, end, collect=period_name == "holdout")
            clean = {k: result[k] for k in ("bot", "name", "start", "end_exclusive", "net_pnl", "return_pct", "ending_equity", "max_drawdown_pct", "trades", "wins", "win_rate_pct", "profit_factor", "mean_daily_return_pct", "sharpe_daily", "fees", "funding", "exposure_pct")}
            clean["quality"] = quality(result)
            results[period_name][bot.id] = clean
            summary_rows.append({"period": period_name, **clean})
            daily_rows.extend({"period": period_name, "bot": bot.id, **row} for row in result["daily"])
            equity_rows.extend({"period": period_name, "bot": bot.id, **row} for row in result["curve"])
            if period_name == "holdout":
                trade_rows.extend({"period": period_name, **row} for row in result["trades_rows"])
                fill_rows.extend({"period": period_name, **row} for row in result["fills_rows"])
                action_rows.extend({"period": period_name, **row} for row in result["actions_rows"])
            print(period_name, bot.id, round(result["return_pct"], 3), "pct", result["trades"], "trades", flush=True)
    validation_rank = sorted((dict(results["validation"][bot.id]) for bot in SPECS), key=lambda x: x["quality"], reverse=True)
    champion = validation_rank[0]["bot"]
    stress_rows: list[dict] = []
    gates: list[dict] = []
    for bot in SPECS:
        base = simulate(bars, features, signals[bot.id], bot, *periods["holdout"])
        zero = simulate(bars, features, signals[bot.id], bot, *periods["holdout"], fee=0.0, slip=0.0, funding_per_8h=0.0)
        double = simulate(bars, features, signals[bot.id], bot, *periods["holdout"], fee=FEE * 2, slip=SLIP * 2, funding_per_8h=FUNDING_PER_8H * 2)
        delayed_signals = [0] + signals[bot.id][:-1]
        delayed = simulate(bars, features, delayed_signals, bot, *periods["holdout"])
        for scenario, result in (("base", base), ("zero_cost", zero), ("double_cost", double), ("one_bar_delay", delayed)):
            stress_rows.append({"bot": bot.id, "scenario": scenario, "return_pct": result["return_pct"], "net_pnl": result["net_pnl"], "max_drawdown_pct": result["max_drawdown_pct"], "trades": result["trades"], "fees": result["fees"], "funding": result["funding"]})
        boot = bootstrap(base["daily"])
        checks = {
            "positive_holdout": base["return_pct"] > 0,
            "at_least_30_trades": base["trades"] >= 30,
            "drawdown_at_most_15pct": base["max_drawdown_pct"] <= 15,
            "positive_double_cost": double["return_pct"] > 0,
            "positive_one_bar_delay": delayed["return_pct"] > 0,
            "bootstrap_lower_above_zero": boot["lower_95_pct"] > 0,
        }
        gates.append({"bot": bot.id, "passes": all(checks.values()), "checks": checks, "bootstrap": boot})
    frozen = {
        "created_utc": dt.datetime.now(UTC).isoformat(),
        "data_sha256": audit["sha256"],
        "source": audit["source"],
        "symbol": SYMBOL,
        "interval": "15m",
        "capital": CAPITAL,
        "fee_per_side": FEE,
        "slippage_per_side": SLIP,
        "funding_stress_per_8h": FUNDING_PER_8H,
        "risk_per_trade": RISK_PER_TRADE,
        "notional_cap": NOTIONAL_CAP,
        "bots": [asdict(bot) for bot in SPECS],
        "periods": {name: {"start": iso(start), "end_exclusive": iso(end)} for name, (start, end) in periods.items()},
        "validation_ranking": validation_rank,
        "champion_before_holdout": champion,
        "selection_note": "Five rules were fixed before the holdout; champion is selected from validation only.",
    }
    (OUT / "frozen_config.json").write_text(json.dumps(frozen, indent=2), encoding="utf-8")
    report = {
        "created_utc": dt.datetime.now(UTC).isoformat(),
        "data_audit": audit,
        "frozen": frozen,
        "summary": summary_rows,
        "stress": stress_rows,
        "gates": gates,
        "results_by_period": results,
        "model_catalog": [asdict(bot) for bot in SPECS],
        "no_live_orders": True,
        "no_kimi": True,
        "notes": [
            "The data are Binance USD-M futures candles; funding history and order-book fills are not reconstructed.",
            "All positions are flattened at the next observed UTC-day boundary; notional is capped at 50% with no leverage.",
            "The recent regression period is descriptive and was not an untouched prospective paper test.",
        ],
    }
    (OUT / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_csv(OUT / "summary.csv", summary_rows)
    write_csv(OUT / "daily_results.csv", daily_rows)
    write_csv(OUT / "equity.csv", equity_rows)
    write_csv(OUT / "stress.csv", stress_rows)
    write_csv(OUT / "trade_log.csv", trade_rows)
    write_csv(OUT / "fills.csv", fill_rows)
    write_csv(OUT / "holdout_actions.csv", action_rows)
    write_csv(OUT / "model_catalog.csv", [asdict(bot) for bot in SPECS])
    manifest = {p.name: sha256(p) for p in sorted(OUT.iterdir()) if p.is_file() and p.suffix in {".csv", ".json", ".md"} and p.name != "manifest.json"}
    (OUT / "manifest.json").write_text(json.dumps({"created_utc": dt.datetime.now(UTC).isoformat(), "files": manifest}, indent=2), encoding="utf-8")
    print("CHAMPION", champion, "GATES", json.dumps(gates), flush=True)
    return report


if __name__ == "__main__":
    run_study()
