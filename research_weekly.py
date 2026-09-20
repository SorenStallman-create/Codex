#!/usr/bin/env python3
"""Reproducible paper-only walk-forward and two-week strategy experiment."""
from __future__ import annotations
import argparse, csv, datetime as dt, hashlib, html, json, math, random, ssl
import statistics, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from typing import Any
from app import Bar, andean, ema, laguerre_filter, make_signals, mcginley, rsi, sma, strategy_catalog

ROOT = Path(__file__).resolve().parent
OUT_ROOT = ROOT / "research"
DATA_URL = "https://data-api.binance.vision/api/v3/klines"
SYSTEM_CA = Path("/etc/ssl/cert.pem")
SYMBOL, INTERVAL = "BTCUSDT", "1h"
HOUR_MS, CAPITAL = 3_600_000, 10_000.0
ALLOCATION, FEE, SLIPPAGE = 0.10, 0.0015, 0.0005
SEED, WALK_DAYS, FOLDS, TOP = 20_260_916, 180, 4, 25

def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")

def ms(value: dt.datetime) -> int:
    return int(value.timestamp() * 1000)

def fetch_rows(start_ms: int, end_ms: int) -> list[list[Any]]:
    rows, cursor = [], start_ms
    while cursor < end_ms:
        query = urllib.parse.urlencode({"symbol": SYMBOL, "interval": INTERVAL, "startTime": cursor, "endTime": end_ms - 1, "limit": 1000})
        request = urllib.request.Request(DATA_URL + "?" + query, headers={"User-Agent": "TradingLab-Research/1.0"})
        error = None
        for attempt in range(5):
            try:
                context = ssl.create_default_context(cafile=str(SYSTEM_CA) if SYSTEM_CA.exists() else None)
                with urllib.request.urlopen(request, timeout=25, context=context) as response:
                    page = json.load(response)
                error = None
                break
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                error = exc
                time.sleep(min(2 ** attempt, 8))
        if error is not None:
            raise RuntimeError("Public market-data request failed: " + str(error))
        if not isinstance(page, list):
            raise RuntimeError("Unexpected market-data response.")
        if not page:
            break
        rows.extend(page)
        next_cursor = int(page[-1][0]) + HOUR_MS
        if next_cursor <= cursor:
            raise RuntimeError("Market-data pagination did not advance.")
        cursor = next_cursor
        if len(page) < 1000:
            break
        time.sleep(0.12)
    return [r for r in rows if start_ms <= int(r[0]) < end_ms]

def validate_rows(raw: list[list[Any]], start: int, end: int) -> tuple[list[Bar], list[list[Any]], dict[str, Any]]:
    by_time: dict[int, list[Any]] = {}
    duplicates, invalid = [], []
    for row in raw:
        try:
            t = int(row[0])
            op, high, low, close, vol = (float(row[i]) for i in (1, 2, 3, 4, 5))
            if len(row) < 12 or not all(math.isfinite(v) for v in (op, high, low, close, vol)):
                raise ValueError("missing fields or non-finite values")
            if min(op, high, low, close) <= 0 or vol < 0 or low > min(op, close) or high < max(op, close) or low > high:
                raise ValueError("invalid OHLCV relationship")
            if t in by_time:
                duplicates.append(t)
            by_time[t] = row
        except (ValueError, TypeError, IndexError) as exc:
            invalid.append(str(exc))
    expected = list(range(start, end, HOUR_MS))
    missing = [t for t in expected if t not in by_time]
    if missing or duplicates or invalid or len(by_time) != len(expected):
        raise RuntimeError(f"Data validation failed: missing={len(missing)}, duplicates={len(duplicates)}, invalid={len(invalid)}; first gaps={[iso(t) for t in missing[:5]]}")
    ordered = [by_time[t] for t in expected]
    bars = [Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in ordered]
    audit = {
        "expected_candles": len(expected), "received_candles": len(raw), "unique_candles": len(ordered),
        "missing_count": len(missing), "duplicate_count": len(duplicates), "invalid_count": len(invalid),
        "first_open_utc": iso(bars[0].time), "last_open_utc": iso(bars[-1].time),
        "last_close_time_utc": iso(int(ordered[-1][6]) + 1),
    }
    return bars, ordered, audit

def windows_for(now: dt.datetime) -> dict[str, tuple[int, int]]:
    end = now.replace(minute=0, second=0, microsecond=0)
    recent = end - dt.timedelta(days=7)
    offset = random.Random(SEED).randint(84, 104)
    older = recent - dt.timedelta(days=offset)
    return {"recent_7d": (ms(recent), ms(end)), "random_week_~3mo_ago": (ms(older), ms(older + dt.timedelta(days=7)))}

def diagnostics(bars: list[Bar], spec: Any) -> list[dict[str, Any]]:
    closes = [b.close for b in bars]
    out = [{} for _ in bars]
    if spec.family in {"sma", "ema"}:
        fn = sma if spec.family == "sma" else ema
        fast, slow = fn(closes, int(spec.params["fast"])), fn(closes, int(spec.params["slow"]))
        out = [{"fast_line": fast[i], "slow_line": slow[i], "rule": "fast/slow crossover"} for i in range(len(bars))]
    elif spec.family == "donchian":
        n = int(spec.params["window"])
        ex = max(3, int(n * float(spec.params["exit_ratio"])))
        for i in range(n, len(bars)):
            out[i] = {"prior_upper": max(b.high for b in bars[i-n:i]), "prior_lower": min(b.low for b in bars[i-n:i]),
                      "exit_upper": max(b.high for b in bars[i-ex:i]), "exit_lower": min(b.low for b in bars[i-ex:i]),
                      "rule": "prior-channel breakout with shorter-channel exit"}
    elif spec.family == "rsi":
        values = rsi(closes, int(spec.params["period"]))
        out = [{"rsi": values[i], "lower": spec.params["lower"], "upper": spec.params["upper"],
                "rule": "oversold long / overbought short; neutral exit"} for i in range(len(bars))]
    elif spec.family == "andean_laguerre":
        bull, bear = andean(bars, int(spec.params["length"]))
        baseline = laguerre_filter(closes, float(spec.params["gamma"]))
        out = [{"andean_bull": bull[i], "andean_bear": bear[i], "laguerre_baseline": baseline[i],
                "rule": "bull/bear crossover gated by Laguerre price baseline"} for i in range(len(bars))]
    elif spec.family == "mcginley":
        baseline = mcginley(closes, int(spec.params["period"]))
        out = [{"mcginley_baseline": baseline[i], "rule": "video-described price above/below baseline; approximate"} for i in range(len(bars))]
    else:
        raise ValueError("Unknown model family: " + spec.family)
    return out

def val(x: float | None, digits: int = 8) -> float | str:
    return "" if x is None or not math.isfinite(x) else round(x, digits)

def simulate(bars: list[Bar], signals: list[int], diag: list[dict[str, Any]], start: int, end: int,
             *, fee: float = FEE, slip: float = SLIPPAGE, allocation: float = ALLOCATION,
             model: str = "", window: str = "", collect: bool = False) -> dict[str, Any]:
    """Spot-only cash account; a completed-candle signal fills at the next open."""
    if not (0 <= start < end <= len(bars)):
        raise ValueError("Invalid simulation range")
    cash, qty, entry = CAPITAL, 0.0, None
    curve, decisions, trades = [CAPITAL], [], []
    invested_hours = 0
    for i in range(start, end):
        bar, source = bars[i], i - 1
        raw = signals[source] if source >= 0 else 0
        long_target = raw > 0
        action, fill, notional, fee_paid, slip_paid = "hold_cash", "", 0.0, 0.0, 0.0
        was_long = qty > 0
        equity_open = cash + qty * bar.open
        if not was_long and long_target:
            notional = min(cash / (1 + fee), max(0.0, equity_open * allocation))
            if notional > 0:
                fill = bar.open * (1 + slip)
                fee_paid, slip_paid = notional * fee, notional - (notional / fill) * bar.open
                qty = notional / fill
                cash -= notional + fee_paid
                action = "open_long"
                entry = {"model": model, "window": window,
                         "entry_signal_close_utc": iso(bars[source].time + HOUR_MS) if source >= 0 else "",
                         "entry_time_utc": iso(bar.time), "entry_market_open": bar.open, "entry_fill": fill,
                         "entry_gross_notional": notional, "entry_cash_debit": notional + fee_paid,
                         "entry_signal": raw, "allocation_at_entry": allocation}
        elif was_long and not long_target:
            fill = bar.open * (1 - slip)
            notional = qty * fill
            fee_paid, slip_paid = notional * fee, qty * bar.open * slip
            proceeds = notional - fee_paid
            cash += proceeds
            action = "close_long_short_or_flat_signal"
            if entry:
                basis = float(entry["entry_cash_debit"])
                trades.append({**entry, "exit_signal_close_utc": iso(bars[source].time + HOUR_MS) if source >= 0 else "",
                               "exit_time_utc": iso(bar.time), "exit_market_open": bar.open, "exit_fill": fill,
                               "exit_signal": raw, "exit_gross_proceeds": notional, "exit_commission": fee_paid,
                               "net_cash_pnl": proceeds - basis, "net_trade_return_pct": (proceeds / basis - 1) * 100,
                               "holding_hours": (bar.time - int(dt.datetime.fromisoformat(entry["entry_time_utc"].replace("Z", "+00:00")).timestamp() * 1000)) / HOUR_MS,
                               "exit_reason": "strategy target changed"})
            qty, entry = 0.0, None
        elif long_target:
            action = "hold_long"
        elif raw < 0:
            action = "stay_cash_short_signal_no_spot_shorting"
        if qty > 0:
            invested_hours += 1
        equity_close = cash + qty * bar.close
        curve.append(equity_close)
        if collect:
            signal_bar = bars[source] if source >= 0 else bar
            decisions.append({
                "model": model, "window": window, "signal_bar_close_utc": iso(signal_bar.time + HOUR_MS),
                "execution_open_utc": iso(bar.time), "bar_open_utc": iso(bar.time),
                "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close, "base_volume": bar.volume,
                "raw_signal": raw, "executable_target": "long" if long_target else "cash", "order_action": action,
                "fill_price": val(fill) if isinstance(fill, float) else "", "gross_fill_notional": val(notional, 4),
                "estimated_slippage_cost": val(slip_paid, 4), "commission": val(fee_paid, 4),
                "cash_after_fill": val(cash, 4), "spot_quantity_after_fill": val(qty, 10),
                "equity_marked_at_close": val(equity_close, 4),
                "signal_indicator_values": json.dumps(diag[source] if source >= 0 else {}, separators=(",", ":"), allow_nan=False),
                "signal_on_this_bar_for_next_open": signals[i],
            })
    last = bars[end - 1]
    if qty > 0:
        fill = last.close * (1 - slip)
        proceeds = qty * fill
        fee_paid, slip_paid = proceeds * fee, qty * last.close * slip
        cash += proceeds - fee_paid
        if entry:
            basis = float(entry["entry_cash_debit"])
            trades.append({**entry, "exit_signal_close_utc": iso(last.time + HOUR_MS),
                           "exit_time_utc": iso(last.time + HOUR_MS), "exit_market_open": last.close,
                           "exit_fill": fill, "exit_signal": signals[end - 1], "exit_gross_proceeds": proceeds,
                           "exit_commission": fee_paid, "exit_slippage_cost": slip_paid,
                           "net_cash_pnl": proceeds - fee_paid - basis,
                           "net_trade_return_pct": ((proceeds - fee_paid) / basis - 1) * 100,
                           "holding_hours": (last.time + HOUR_MS - int(dt.datetime.fromisoformat(entry["entry_time_utc"].replace("Z", "+00:00")).timestamp() * 1000)) / HOUR_MS,
                           "exit_reason": "forced liquidation at test-window close"})
        if collect:
            decisions.append({"model": model, "window": window, "signal_bar_close_utc": iso(last.time + HOUR_MS),
                              "execution_open_utc": "", "bar_open_utc": iso(last.time), "open": "", "high": "",
                              "low": "", "close": last.close, "base_volume": "", "raw_signal": signals[end-1],
                              "executable_target": "cash at window end", "order_action": "forced_liquidation_at_close",
                              "fill_price": val(fill), "gross_fill_notional": val(proceeds, 4),
                              "estimated_slippage_cost": val(slip_paid, 4), "commission": val(fee_paid, 4),
                              "cash_after_fill": val(cash, 4), "spot_quantity_after_fill": 0,
                              "equity_marked_at_close": val(cash, 4),
                              "signal_indicator_values": json.dumps(diag[end-1], separators=(",", ":"), allow_nan=False),
                              "signal_on_this_bar_for_next_open": ""})
        qty = 0.0
    peak, maxdd = curve[0], 0.0
    curve.append(cash)
    for equity in curve:
        peak = max(peak, equity)
        if peak > 0:
            maxdd = max(maxdd, (peak - equity) / peak)
    pnls = [float(t["net_cash_pnl"]) for t in trades]
    win_count = sum(p > 0 for p in pnls)
    loss_count = sum(p < 0 for p in pnls)
    loss_amount = -sum(p for p in pnls if p < 0)
    pf = sum(p for p in pnls if p > 0) / loss_amount if loss_amount else None
    return {"model": model, "window": window, "window_start_utc": iso(bars[start].time),
            "window_end_exclusive_utc": iso(bars[end-1].time + HOUR_MS), "starting_capital": CAPITAL,
            "allocation_fraction": allocation, "commission_per_side": fee, "slippage_per_side": slip,
            "net_return_pct": (cash / CAPITAL - 1) * 100, "ending_equity": cash, "net_pnl": cash - CAPITAL,
            "max_drawdown_pct": maxdd * 100, "closed_trades": len(trades), "wins": win_count,
            "losses": loss_count, "win_rate_pct": win_count / len(pnls) * 100 if pnls else None,
            "profit_factor": pf, "profit_factor_status": "finite" if loss_amount else "no_losses" if win_count else "no_trades_or_flat", "hours_with_spot_position": invested_hours,
            "market_exposure_pct": invested_hours / (end-start) * 100, "hourly_bars": end-start,
            "decision_rows": decisions, "trades_rows": trades, "equity_values": curve}

def buy_hold(bars: list[Bar], start: int, end: int, fee: float = FEE, slip: float = SLIPPAGE,
             allocation: float = ALLOCATION) -> float:
    first, last = bars[start], bars[end-1]
    budget = min(CAPITAL / (1 + fee), CAPITAL * allocation)
    qty = budget / (first.open * (1 + slip))
    cash = CAPITAL - budget * (1 + fee) + qty * last.close * (1 - slip) * (1 - fee)
    return (cash / CAPITAL - 1) * 100

def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def make_report(path: Path, asof: str, selected: list[dict[str, Any]],
                windows: list[dict[str, Any]], results: list[dict[str, Any]], candidate_count: int) -> None:
    html_text = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paper trading test</title><style>
:root{--ink:#182230;--muted:#5c6b7a;--line:#d9e1e8;--paper:#fff;--wash:#f3f6f8;--blue:#245f8f;--amber:#fff3d6}
*{box-sizing:border-box}body{margin:0;background:var(--wash);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1200px;margin:0 auto;padding:28px 20px 60px}h1{font-size:30px;line-height:1.2;margin:0 0 8px}h2{font-size:20px;margin:28px 0 10px}
p{margin:8px 0;color:var(--muted)}.card{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:18px;margin:14px 0}
.warn{background:var(--amber);border-color:#edcf89}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.metric{padding:12px;background:var(--wash);border-radius:10px}.metric b{display:block;font-size:19px}.muted,small{color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:8px;border-bottom:1px solid var(--line);vertical-align:top}th{position:sticky;top:0;background:#f8fafb}
.scroll{overflow:auto;max-height:520px}a{color:var(--blue)}label{margin-right:12px}select,input{padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:white}
ul{padding-left:20px}.tag{display:inline-block;padding:3px 8px;border-radius:20px;background:#e8f0f6;color:var(--blue);font-size:12px}
footer{margin-top:34px;color:var(--muted);font-size:12px}
</style></head><body><main>
<h1>Paper-trading strategy test</h1>
<p>BTCUSDT spot · 1-hour candles · fixed 10% cash-funded position · no leverage · report as of @@ASOF@@ UTC</p>
<div class="card warn"><b>Interpretation:</b> These are historical simulations, not live execution and not a promise of future returns. One-week windows are far too short to establish an edge. The complete configured video scripts are not reproduced here. Only the video-described McGinley rule is included as a clearly labeled proxy. This run neither accesses nor trades your capital.</div>
<div class="card"><div class="grid">
<div class="metric"><small>Candidate models screened</small><b>@@COUNT@@</b></div>
<div class="metric"><small>Models frozen before holdouts</small><b>25</b></div>
<div class="metric"><small>Walk-forward selection</small><b>4 × 45-day folds</b></div>
<div class="metric"><small>Bars per holdout</small><b>168 complete hourly bars</b></div>
</div><p>Universe frozen using a 180-day walk-forward screen ending before the older holdout. The selected 25 are then tested unchanged on both one-week windows.</p></div>
<h2>Test windows and control settings</h2><div id="windowcards" class="grid"></div>
<div class="card"><p><b>Common controls:</b> Binance BTCUSDT spot OHLCV, 1h; $10,000 account; 10% allocation at entry; one long position at a time; short signals mean cash (spot only); closed-candle signals execute at next candle open; forced liquidation at window end. Base friction is 0.15% commission + 0.05% slippage per side. No leverage, funding, borrow, stops, or intrabar fills. This deliberately does <b>not</b> reproduce the video’s 10× leverage with 10% of portfolio as margin: our maximum entry exposure is 10% of account value, unleveraged.</p><p><b>Variable isolation:</b> zero-friction and 2×-friction cases isolate transaction costs; 5%, 10%, and 20% cases isolate position size. Same signal, dates, market and other assumptions are held constant. Buy-and-hold has the same 10% allocation.</p></div>
<h2>Top 25 selected on pre-holdout walk-forward data</h2><div class="card scroll"><table><thead><tr><th>Rank</th><th>Model</th><th>Family</th><th>Robust score</th><th>Profitable folds</th></tr></thead><tbody>@@SELECTED@@</tbody></table></div>
<div class="card"><p><span class="tag">Research leader</span> Rank 1 is the first model I would continue to paper-test—not a recommendation to fund or a claim that it will make money. It was selected on older walk-forward folds, not on either displayed week.</p><p id="leader"></p></div>
<h2>Holdout results</h2><div class="card"><label>Window <select id="window"></select></label> <label>Search model <input id="search" placeholder="e.g. EMA 10/50"></label><div class="scroll"><table id="results"><thead><tr><th>Rank</th><th>Model</th><th>Return</th><th>Max DD</th><th>Closed trades</th><th>Win rate</th><th>PF</th><th>Exposure</th><th>10%-alloc B&H</th></tr></thead><tbody></tbody></table></div><p class="muted">Zero closed trades is not evidence of a profitable or reliable strategy. Returns include modeled fills and costs.</p></div>
<h2>What the videos do—and do not—let us reproduce</h2><div class="card"><ul>
<li>The showcased bots use deterministic indicator rules. “AI” appears to describe platform/bot features or analysis, not an LLM independently deciding each candle. A one-for-one test needs exact script version, inputs, symbol/timeframe, sizing/leverage, and trade export.</li>
<li><b>Screened, but not in the holdout:</b> three standard-formula McGinley “above baseline long / below baseline short” variants were added as proxies. They ranked 65–67 of 67 candidates in pre-holdout walk-forward scoring, so none passed the frozen top-25 cutoff and no McGinley holdout trades are reported. The Andean/Laguerre variants ranked 44–62 and also did not pass the cutoff.</li>
<li><b>Not claimed as exact replications:</b> Trendhoo/T3 (indicator parameters and full entry/exit code missing); Bitify + El Patreon (configured DIY conditions not stated); Super FVMA + Zero Lag + FVMA + Supertrend (periods, full settings, and order logic missing); Dead Zone WAE + T3 (some conditions described, full parameters/exits missing); unnamed 4-hour model.</li>
<li>Other candidates are the local lab’s SMA/EMA crossovers, Donchian breakouts, RSI mean reversion, and Andean + Laguerre variants. They are controlled baselines, not claims of exact identity with every video strategy.</li>
</ul></div>
<h2>Data quality and limits</h2><div class="card"><p>Fetched from Binance’s public market-data endpoint. The run rejects missing, duplicate, invalid, or non-hourly candles instead of silently filling gaps. Signals use only candles available at that time; fills are delayed to the next open. The full candle history, decision ledger, trades, equity path, all candidate scores, and cost/size sensitivities are downloadable below.</p><p>Selection among many variants still introduces multiple-comparison risk, even with later holdouts. Two 168-hour windows and potentially few trades cannot establish reliability or drawdown behavior. Spread variation, market impact, outages, taxes, and order queue effects are not captured by fixed-slippage OHLCV simulation.</p><p>References: <a href="https://developers.binance.com/en/docs/products/spot/rest-api">Binance Spot REST API documentation</a> and <a href="https://www.tradingview.com/support/solutions/43000786181-broker-emulator/">TradingView broker emulator notes</a> on commission/slippage modeling.</p></div>
<h2>Download the full evidence</h2><div class="card"><ul>
<li><a href="market_data.csv">All raw BTCUSDT hourly candles used</a></li><li><a href="decision_log.csv">Every model decision and simulated execution on both test weeks</a></li>
<li><a href="trade_log.csv">Every closed simulated trade</a></li><li><a href="equity_curve.csv">Hourly marked-to-market equity</a></li>
<li><a href="holdout_summary.csv">Base-friction holdout summary</a></li><li><a href="cost_sensitivity.csv">Zero, baseline, and 2× transaction-cost tests</a></li>
<li><a href="allocation_sensitivity.csv">5%, 10%, and 20% allocation tests</a></li><li><a href="walk_forward_folds.csv">Every model × training-fold score and metric</a></li>
<li><a href="selection_scores.csv">Full pre-holdout ranking for all candidates</a></li><li><a href="model_catalog.csv">Rules, parameters, and replication-confidence labels</a></li>
<li><a href="manifest.json">Window definitions, data validation, assumptions, and SHA-256 checksums</a></li></ul></div>
<footer>Educational historical paper simulation; not financial advice. No API keys, orders, or leverage were involved.</footer>
</main><script>
const results=@@RESULTS@@, selected=@@SELECTEDJSON@@, windows=@@WINDOWS@@;
document.getElementById('leader').textContent=selected.length?'Selected: #1 '+selected[0].name+' ('+selected[0].id+'), robust training score '+selected[0].robust_score.toFixed(4)+'. Keep it in forward paper mode; this is not a live-capital recommendation.':'';
const sel=document.getElementById('window');
windows.forEach(function(w){let o=document.createElement('option');o.value=w.id;o.textContent=w.name+' ('+w.start+' — '+w.end_exclusive+' UTC)';sel.appendChild(o);});
document.getElementById('windowcards').innerHTML=windows.map(function(w){return '<div class="metric"><small>'+w.name+'</small><b>'+w.start+'</b><small>to '+w.end_exclusive+' UTC · '+w.bars+' bars'+(w.random_seed?' · seed '+w.random_seed:'')+'</small></div>';}).join('');
function draw(){let q=document.getElementById('search').value.toLowerCase(),win=sel.value;let rows=results.filter(function(r){return r.window===win&&r.name.toLowerCase().includes(q);});document.querySelector('#results tbody').innerHTML=rows.map(function(r){return '<tr><td>'+r.selection_rank+'</td><td>'+r.name+'</td><td>'+r.net_return_pct.toFixed(3)+'%</td><td>'+r.max_drawdown_pct.toFixed(3)+'%</td><td>'+r.closed_trades+'</td><td>'+(r.win_rate_pct===null?'—':r.win_rate_pct.toFixed(1)+'%')+'</td><td>'+(r.profit_factor===null?'—':r.profit_factor.toFixed(2))+'</td><td>'+r.market_exposure_pct.toFixed(1)+'%</td><td>'+r.buy_hold_return_pct.toFixed(3)+'%</td></tr>';}).join('');}
sel.addEventListener('change',draw);document.getElementById('search').addEventListener('input',draw);draw();
</script></body></html>''';
    html_text = html_text.replace("@@ASOF@@", html.escape(asof))
    html_text = html_text.replace("@@COUNT@@", str(candidate_count))
    html_text = html_text.replace("@@SELECTED@@", "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{:.4f}</td><td>{}/{}</td></tr>".format(
            m["selection_rank"], html.escape(m["name"]), html.escape(m["family"]),
            m["robust_score"], m["walk_forward_profitable_folds"], FOLDS)
        for m in selected))
    html_text = html_text.replace("@@RESULTS@@", json.dumps(results, allow_nan=False, separators=(",", ":")))
    html_text = html_text.replace("@@SELECTEDJSON@@", json.dumps(selected, allow_nan=False, separators=(",", ":")))
    html_text = html_text.replace("@@WINDOWS@@", json.dumps(windows, allow_nan=False, separators=(",", ":")))
    path.write_text(html_text, encoding="utf-8")

def run(out_dir: Path | None = None, now: dt.datetime | None = None) -> Path:
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Timestamp must be timezone-aware")
    now = now.astimezone(dt.timezone.utc)
    windows = windows_for(now)
    hist_start = windows["random_week_~3mo_ago"][0]
    recent_end = windows["recent_7d"][1]
    train_start = hist_start - WALK_DAYS * 24 * HOUR_MS
    # Warm up long lookback indicators before the first walk-forward fold.
    fetch_start = train_start - 30 * 24 * HOUR_MS
    bars, raw_rows, audit = validate_rows(fetch_rows(fetch_start, recent_end), fetch_start, recent_end)
    index = {b.time: i for i, b in enumerate(bars)}
    specs = sorted(strategy_catalog(), key=lambda s: s.id)
    spec_map = {s.id: s for s in specs}
    if len(spec_map) != len(specs):
        raise RuntimeError("Duplicate model identifier in catalog")
    signals = {s.id: make_signals(bars, s) for s in specs}
    diags = {s.id: diagnostics(bars, s) for s in specs}

    fold_hours = WALK_DAYS * 24 // FOLDS
    fold_rows, ranks = [], []
    for spec in specs:
        per_fold = []
        for fold in range(FOLDS):
            start_ms = train_start + fold * fold_hours * HOUR_MS
            end_ms = start_ms + fold_hours * HOUR_MS
            result = simulate(bars, signals[spec.id], diags[spec.id], index[start_ms], index[end_ms],
                              model=spec.id, window="walk_forward_" + str(fold + 1))
            pf = result["profit_factor"]
            scoring_pf = 4.0 if result["profit_factor_status"] == "no_losses" else float(pf or 0)
            score = result["net_return_pct"] / 100 - 1.25 * result["max_drawdown_pct"] / 100
            score += 0.02 * math.log1p(min(scoring_pf, 4.0))
            if result["closed_trades"] < 2:
                score -= 0.025
            row = {"model_id": spec.id, "model_name": spec.name, "family": spec.family,
                   "fold": fold+1, "start_utc": iso(start_ms), "end_exclusive_utc": iso(end_ms),
                   "net_return_pct": round(result["net_return_pct"], 6),
                   "max_drawdown_pct": round(result["max_drawdown_pct"], 6),
                   "closed_trades": result["closed_trades"],
                   "win_rate_pct": "" if result["win_rate_pct"] is None else round(result["win_rate_pct"], 6),
                   "profit_factor": "" if pf is None else round(float(pf), 6),
                   "robust_fold_score": round(score, 8)}
            per_fold.append(row)
            fold_rows.append(row)
        scores = [float(r["robust_fold_score"]) for r in per_fold]
        median, dispersion = statistics.median(scores), statistics.pstdev(scores)
        ranks.append({"id": spec.id, "name": spec.name, "family": spec.family,
                      "params_json": json.dumps(spec.params, sort_keys=True),
                      "median_fold_score": median, "fold_score_dispersion": dispersion,
                      "robust_score": median - 0.35 * dispersion,
                      "walk_forward_profitable_folds": sum(float(r["net_return_pct"]) > 0 for r in per_fold),
                      "walk_forward_total_trades": sum(int(r["closed_trades"]) for r in per_fold),
                      "replication_note": "Standard McGinley rule proxy; video timeframe/settings not fully stated." if spec.family == "mcginley" else "Local lab indicator-rule baseline; not asserted to match a private video script."})
    ranks.sort(key=lambda r: (r["robust_score"], r["walk_forward_profitable_folds"], r["walk_forward_total_trades"]), reverse=True)
    for rank, row in enumerate(ranks, 1):
        row["selection_rank"] = rank
    selected = ranks[:TOP]

    win_rows, results, cost_rows, size_rows = [], [], [], []
    decisions, trades, equity_rows = [], [], []
    for win_id, (wstart, wend) in windows.items():
        i0, i1 = index[wstart], index.get(wend, len(bars))
        window_info = {"id": win_id,
                       "name": "Latest 7 complete days" if win_id == "recent_7d" else "Seeded random week about 3 months earlier",
                       "start": iso(wstart), "end_exclusive": iso(wend), "bars": i1-i0,
                       "random_seed": SEED if win_id != "recent_7d" else None}
        win_rows.append(window_info)
        for selected_model in selected:
            spec = spec_map[selected_model["id"]]
            base = simulate(bars, signals[spec.id], diags[spec.id], i0, i1,
                            model=spec.id, window=win_id, collect=True)
            result = {k: v for k, v in base.items() if k not in {"decision_rows", "trades_rows", "equity_values"}}
            result.update({"selection_rank": selected_model["selection_rank"], "name": spec.name,
                           "family": spec.family, "params_json": json.dumps(spec.params, sort_keys=True),
                           "buy_hold_return_pct": buy_hold(bars, i0, i1),
                           "walk_forward_robust_score": round(float(selected_model["robust_score"]), 8)})
            results.append(result)
            decisions.extend(base["decision_rows"])
            trades.extend(base["trades_rows"])
            for n, value in enumerate(base["equity_values"]):
                equity_rows.append({"model": spec.id, "window": win_id, "point_index": n,
                                    "time_utc": iso(wstart + n * HOUR_MS) if n < base["hourly_bars"] else iso(wend),
                                    "equity": round(value, 8)})
            for scenario, cfee, cslip in (("zero", 0.0, 0.0), ("base", FEE, SLIPPAGE), ("2x_base", FEE*2, SLIPPAGE*2)):
                test = simulate(bars, signals[spec.id], diags[spec.id], i0, i1, fee=cfee, slip=cslip,
                                allocation=ALLOCATION, model=spec.id, window=win_id)
                cost_rows.append({"selection_rank": selected_model["selection_rank"], "model": spec.id,
                                  "name": spec.name, "window": win_id, "scenario": scenario,
                                  "commission_per_side": cfee, "slippage_per_side": cslip,
                                  "allocation_fraction": ALLOCATION, "net_return_pct": round(test["net_return_pct"], 6),
                                  "max_drawdown_pct": round(test["max_drawdown_pct"], 6),
                                  "closed_trades": test["closed_trades"],
                                  "profit_factor": "" if test["profit_factor"] is None else round(float(test["profit_factor"]), 6)})
            for size in (0.05, 0.10, 0.20):
                test = simulate(bars, signals[spec.id], diags[spec.id], i0, i1, fee=FEE, slip=SLIPPAGE,
                                allocation=size, model=spec.id, window=win_id)
                size_rows.append({"selection_rank": selected_model["selection_rank"], "model": spec.id,
                                  "name": spec.name, "window": win_id, "allocation_fraction": size,
                                  "commission_per_side": FEE, "slippage_per_side": SLIPPAGE,
                                  "net_return_pct": round(test["net_return_pct"], 6),
                                  "max_drawdown_pct": round(test["max_drawdown_pct"], 6),
                                  "closed_trades": test["closed_trades"],
                                  "profit_factor": "" if test["profit_factor"] is None else round(float(test["profit_factor"]), 6)})

    recent_end_dt = dt.datetime.fromtimestamp(recent_end/1000, tz=dt.timezone.utc)
    output = out_dir or OUT_ROOT / ("weekly_" + recent_end_dt.strftime("%Y%m%dT%H00Z"))
    output.mkdir(parents=True, exist_ok=True)
    candle_rows = []
    for r in raw_rows:
        candle_rows.append({"open_time_utc": iso(int(r[0])), "open_time_ms": int(r[0]),
                            "open": r[1], "high": r[2], "low": r[3], "close": r[4], "base_volume": r[5],
                            "close_time_utc": iso(int(r[6])+1), "close_time_ms": int(r[6]),
                            "quote_volume": r[7], "trade_count": r[8],
                            "taker_buy_base_volume": r[9], "taker_buy_quote_volume": r[10]})
    write_csv(output / "market_data.csv", candle_rows)
    write_csv(output / "walk_forward_folds.csv", fold_rows)
    write_csv(output / "selection_scores.csv", ranks)
    write_csv(output / "holdout_summary.csv", results)
    write_csv(output / "cost_sensitivity.csv", cost_rows)
    write_csv(output / "allocation_sensitivity.csv", size_rows)
    write_csv(output / "decision_log.csv", decisions)
    write_csv(output / "trade_log.csv", trades)
    write_csv(output / "equity_curve.csv", equity_rows)
    catalog_rows = []
    for rank in ranks:
        spec = spec_map[rank["id"]]
        description = ("Close above standard McGinley Dynamic = long; below = short signal, mapped to cash in spot. Approximation; exact video timeframe/settings differ."
                      if spec.family == "mcginley" else
                      "Fast/slow crossover; negative/short state maps to cash." if spec.family in {"sma", "ema"} else
                      "Prior-window Donchian breakout/exit; short state maps to cash." if spec.family == "donchian" else
                      "RSI oversold/overbought entries with neutral exit; short state maps to cash." if spec.family == "rsi" else
                      "Andean bull/bear crossover gated by Laguerre baseline; short state maps to cash.")
        catalog_rows.append({"selection_rank": rank["selection_rank"], "id": spec.id, "name": spec.name,
                             "family": spec.family, "parameters_json": json.dumps(spec.params, sort_keys=True),
                             "signal_summary": description, "replication_note": rank["replication_note"]})
    write_csv(output / "model_catalog.csv", catalog_rows)

    manifest = {"run_created_at_utc": iso(ms(now)), "as_of_utc": iso(recent_end),
                "data_source": DATA_URL, "instrument": "Binance Spot BTCUSDT", "timeframe": INTERVAL,
                "raw_data_range_start_utc": audit["first_open_utc"],
                "raw_data_range_end_exclusive_utc": iso(recent_end), "window_definitions": win_rows,
                "data_validation": audit,
                "selection": {"candidate_count": len(specs), "selected_model_count": TOP,
                              "training_start_utc": iso(train_start), "training_end_exclusive_utc": iso(hist_start),
                              "folds": FOLDS, "fold_days": WALK_DAYS//FOLDS,
                              "score_formula": "median of [net return - 1.25*max drawdown + 0.02*ln(1+min(PF,4)) - 0.025 when fewer than 2 trades], minus 0.35*population standard deviation",
                              "selected_ids": [m["id"] for m in selected]},
                "simulation": {"starting_capital_usd": CAPITAL, "allocation_fraction": ALLOCATION,
                               "commission_per_side": FEE, "slippage_per_side": SLIPPAGE,
                               "execution": "Closed-hour signal at next hourly open; adverse fixed slippage; position <=10% of account at entry; cash-funded spot; shorts map to cash; force-liquidate at test close.",
                               "not_modeled": ["leverage", "funding", "borrow", "variable market impact", "outages", "tax", "intrabar stop/limit fills"],
                               "candidate_note": "64 local indicator-rule variants plus 3 standard-formula McGinley variants. Other video strategies excluded from exact-replica claim because full parameters/rules are incomplete."}}
    checksums = {}
    for file in sorted(output.iterdir()):
        if file.is_file():
            checksums[file.name] = {"bytes": file.stat().st_size, "sha256": sha256(file)}
    manifest["artifacts"] = checksums
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    make_report(output / "index.html", iso(recent_end), selected, win_rows, results, len(specs))
    manifest["artifacts"]["index.html"] = {"bytes": (output / "index.html").stat().st_size,
                                           "sha256": sha256(output / "index.html")}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return output

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    folder = run(args.output)
    print(folder)
    print(folder / "index.html")

if __name__ == "__main__":
    main()
