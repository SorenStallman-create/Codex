#!/usr/bin/env python3
"""Paper-only trading strategy lab using only the Python standard library."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import mimetypes
import re
import socketserver
import ssl
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
STATE_ROOT = ROOT / "state"
BINANCE_DATA_URL = "https://data-api.binance.vision/api/v3/klines"
INTERVALS = {"15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"}
SYMBOL_RE = re.compile(r"^[A-Z0-9]{5,20}$")
COMMISSION = 0.0015
STARTING_EQUITY = 10_000.0
STATE_LOCK = threading.Lock()


@dataclass(frozen=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class StrategySpec:
    id: str
    family: str
    name: str
    params: dict[str, float | int]


def fetch_klines(symbol: str, interval: str, limit: int) -> list[Bar]:
    symbol = symbol.upper().strip()
    if not SYMBOL_RE.fullmatch(symbol):
        raise ValueError("Symbol must be 5–20 uppercase letters or numbers.")
    if interval not in INTERVALS:
        raise ValueError("Unsupported interval.")
    if not 200 <= limit <= 1000:
        raise ValueError("Limit must be between 200 and 1000 bars.")
    query = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": limit})
    request = urllib.request.Request(
        f"{BINANCE_DATA_URL}?{query}",
        headers={"User-Agent": "TradingLab/1.0"},
    )
    try:
        system_ca = Path("/etc/ssl/cert.pem")
        tls_context = ssl.create_default_context(cafile=str(system_ca) if system_ca.exists() else None)
        with urllib.request.urlopen(request, timeout=15, context=tls_context) as response:
            raw = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Binance returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("Could not reach Binance market data.") from exc
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("Binance returned no candles for that market.")
    return [
        Bar(
            time=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        for row in raw
    ]


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
    if not values:
        return []
    out: list[float | None] = [None] * len(values)
    alpha = 2.0 / (period + 1.0)
    current = values[0]
    for i, value in enumerate(values):
        current = value if i == 0 else current + alpha * (value - current)
        if i >= period - 1:
            out[i] = current
    return out


def rsi(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def laguerre_filter(values: list[float], gamma: float) -> list[float]:
    if not values:
        return []
    l0 = l1 = l2 = l3 = values[0]
    out: list[float] = []
    for price in values:
        p0, p1, p2 = l0, l1, l2
        l0 = (1 - gamma) * price + gamma * l0
        l1 = -gamma * l0 + p0 + gamma * l1
        l2 = -gamma * l1 + p1 + gamma * l2
        l3 = -gamma * l2 + p2 + gamma * l3
        out.append((l0 + 2 * l1 + 2 * l2 + l3) / 6.0)
    return out


def andean(bars: list[Bar], length: int) -> tuple[list[float], list[float]]:
    """Andean bull and bear components using exponential price envelopes."""
    alpha = 2.0 / (length + 1.0)
    up1 = dn1 = bars[0].close
    up2 = dn2 = bars[0].close**2
    bulls: list[float] = []
    bears: list[float] = []
    for bar in bars:
        c, o = bar.close, bar.open
        up1 = max(c, o, up1 - alpha * (up1 - c))
        up2 = max(c * c, o * o, up2 - alpha * (up2 - c * c))
        dn1 = min(c, o, dn1 + alpha * (c - dn1))
        dn2 = min(c * c, o * o, dn2 + alpha * (c * c - dn2))
        bulls.append(math.sqrt(max(0.0, dn2 - dn1 * dn1)))
        bears.append(math.sqrt(max(0.0, up2 - up1 * up1)))
    return bulls, bears


def mcginley(values: list[float], period: int) -> list[float]:
    """McGinley Dynamic baseline using the standard adaptive denominator."""
    if not values:
        return []
    out = [values[0]]
    for value in values[1:]:
        previous = out[-1]
        ratio = value / previous if previous else 1.0
        denominator = max(period * ratio**4, 1e-12)
        out.append(previous + (value - previous) / denominator)
    return out


def crossed_above(a: list[float | None], b: list[float | None], i: int) -> bool:
    return (
        i > 0
        and a[i] is not None
        and b[i] is not None
        and a[i - 1] is not None
        and b[i - 1] is not None
        and a[i - 1] <= b[i - 1]
        and a[i] > b[i]
    )


def make_signals(bars: list[Bar], spec: StrategySpec) -> list[int]:
    closes = [bar.close for bar in bars]
    signals = [0] * len(bars)
    position = 0

    if spec.family in {"sma", "ema"}:
        average = sma if spec.family == "sma" else ema
        fast = average(closes, int(spec.params["fast"]))
        slow = average(closes, int(spec.params["slow"]))
        for i in range(1, len(bars)):
            if crossed_above(fast, slow, i):
                position = 1
            elif crossed_above(slow, fast, i):
                position = -1
            signals[i] = position

    elif spec.family == "donchian":
        window = int(spec.params["window"])
        exit_window = max(3, int(window * float(spec.params["exit_ratio"])))
        for i in range(window, len(bars)):
            upper = max(bar.high for bar in bars[i - window : i])
            lower = min(bar.low for bar in bars[i - window : i])
            exit_high = max(bar.high for bar in bars[i - exit_window : i])
            exit_low = min(bar.low for bar in bars[i - exit_window : i])
            if bars[i].close > upper:
                position = 1
            elif bars[i].close < lower:
                position = -1
            elif position > 0 and bars[i].close < exit_low:
                position = 0
            elif position < 0 and bars[i].close > exit_high:
                position = 0
            signals[i] = position

    elif spec.family == "rsi":
        values = rsi(closes, int(spec.params["period"]))
        lower, upper = float(spec.params["lower"]), float(spec.params["upper"])
        for i, value in enumerate(values):
            if value is None:
                continue
            if value < lower:
                position = 1
            elif value > upper:
                position = -1
            elif position > 0 and value >= 52:
                position = 0
            elif position < 0 and value <= 48:
                position = 0
            signals[i] = position

    elif spec.family == "andean_laguerre":
        bull, bear = andean(bars, int(spec.params["length"]))
        baseline = laguerre_filter(closes, float(spec.params["gamma"]))
        for i in range(1, len(bars)):
            bull_cross = bull[i - 1] <= bear[i - 1] and bull[i] > bear[i]
            bear_cross = bear[i - 1] <= bull[i - 1] and bear[i] > bull[i]
            if bull_cross and closes[i] > baseline[i]:
                position = 1
            elif bear_cross and closes[i] < baseline[i]:
                position = -1
            elif position > 0 and closes[i] < baseline[i]:
                position = 0
            elif position < 0 and closes[i] > baseline[i]:
                position = 0
            signals[i] = position
    elif spec.family == "mcginley":
        # The video describes price-above/below-baseline direction, but not
        # the exact chart settings. This is the standard formula with a
        # transparent period parameter; reports label it an approximation.
        baseline = mcginley(closes, int(spec.params["period"]))
        for i, close in enumerate(closes):
            position = 1 if close > baseline[i] else -1 if close < baseline[i] else 0
            signals[i] = position
    else:
        raise ValueError(f"Unknown strategy family: {spec.family}")
    return signals


def max_drawdown(equity: list[float]) -> float:
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        worst = max(worst, (peak - value) / peak if peak else 0.0)
    return worst


def backtest(
    bars: list[Bar],
    signals: list[int],
    start: int = 1,
    end: int | None = None,
    commission: float = COMMISSION,
) -> dict[str, Any]:
    end = len(bars) if end is None else min(end, len(bars))
    start = max(1, start)
    equity = STARTING_EQUITY
    curve = [equity]
    dates = [bars[start - 1].time]
    position = 0
    entry_price: float | None = None
    trades: list[float] = []
    daily_returns: list[float] = []
    for i in range(start, end):
        previous_close = bars[i - 1].close
        open_price = bars[i].open
        close_price = bars[i].close
        before = equity
        if position:
            equity *= 1.0 + position * (open_price / previous_close - 1.0)
        target = signals[i - 1]  # completed-candle signal, next-open execution
        if target != position:
            equity *= 1.0 - commission * abs(target - position)
            if position and entry_price:
                trades.append(position * (open_price / entry_price - 1.0) - commission * 2)
            entry_price = open_price if target else None
            position = target
        if position:
            equity *= 1.0 + position * (close_price / open_price - 1.0)
        curve.append(equity)
        dates.append(bars[i].time)
        daily_returns.append(equity / before - 1.0)
    if position and entry_price:
        trades.append(position * (bars[end - 1].close / entry_price - 1.0) - commission)
    gains = sum(value for value in trades if value > 0)
    losses = -sum(value for value in trades if value < 0)
    profit_factor = gains / losses if losses > 0 else (9.99 if gains > 0 else 0.0)
    downside = [value for value in daily_returns if value < 0]
    volatility = statistics.pstdev(daily_returns) if len(daily_returns) > 1 else 0.0
    sharpe = (statistics.fmean(daily_returns) / volatility * math.sqrt(365)) if volatility else 0.0
    downside_dev = math.sqrt(statistics.fmean([value * value for value in downside])) if downside else 0.0
    sortino = (statistics.fmean(daily_returns) / downside_dev * math.sqrt(365)) if downside_dev else 0.0
    total_return = equity / STARTING_EQUITY - 1.0
    drawdown = max_drawdown(curve)
    buy_hold = bars[end - 1].close / bars[start].open - 1.0
    win_rate = sum(value > 0 for value in trades) / len(trades) if trades else 0.0
    return {
        "return": total_return,
        "max_drawdown": drawdown,
        "win_rate": win_rate,
        "profit_factor": min(profit_factor, 9.99),
        "trades": len(trades),
        "sharpe": sharpe,
        "sortino": sortino,
        "buy_hold": buy_hold,
        "equity": [round(value, 2) for value in curve],
        "dates": dates,
        "ending_equity": equity,
    }


def strategy_catalog() -> list[StrategySpec]:
    specs: list[StrategySpec] = []
    for family in ("sma", "ema"):
        for fast in (5, 10, 20, 30, 50):
            for slow in (50, 100, 200):
                if fast < slow:
                    specs.append(StrategySpec(f"{family}-{fast}-{slow}", family, f"{family.upper()} {fast}/{slow}", {"fast": fast, "slow": slow}))
    for window in (10, 20, 30, 40, 55, 75, 100):
        for ratio in (0.25, 0.5, 0.75):
            specs.append(StrategySpec(f"donchian-{window}-{ratio}", "donchian", f"Donchian {window} · exit {ratio:g}", {"window": window, "exit_ratio": ratio}))
    for period in (7, 14, 21):
        for lower, upper in ((25, 75), (30, 70)):
            specs.append(StrategySpec(f"rsi-{period}-{lower}", "rsi", f"RSI {period} · {lower}/{upper}", {"period": period, "lower": lower, "upper": upper}))
    for length in (20, 35, 50):
        for gamma in (0.2, 0.5, 0.8):
            specs.append(StrategySpec(f"andean-{length}-{gamma}", "andean_laguerre", f"Andean {length} + Laguerre {gamma:g}", {"length": length, "gamma": gamma}))
    for period in (10, 14, 20):
        specs.append(StrategySpec(f"mcginley-{period}", "mcginley", f"McGinley Dynamic {period} · video-rule proxy", {"period": period}))
    return specs


def score_result(oos: dict[str, Any]) -> float:
    # Strong penalties for fragile, tiny-sample, and high-drawdown results.
    sample_factor = min(1.0, oos["trades"] / 12.0)
    pf = min(oos["profit_factor"], 3.0)
    quality = oos["return"] - 1.75 * oos["max_drawdown"] + 0.08 * math.log1p(pf) + 0.01 * oos["sortino"]
    return quality - 0.25 * (1.0 - sample_factor)


def run_batch(bars: list[Bar]) -> dict[str, Any]:
    split = int(len(bars) * 0.70)
    results: list[dict[str, Any]] = []
    for spec in strategy_catalog():
        signals = make_signals(bars, spec)
        insample = backtest(bars, signals, start=1, end=split)
        oos = backtest(bars, signals, start=split, end=len(bars))
        full = backtest(bars, signals)
        results.append({
            "id": spec.id,
            "family": spec.family,
            "name": spec.name,
            "params": spec.params,
            "score": score_result(oos),
            "insample": {key: value for key, value in insample.items() if key not in {"equity", "dates"}},
            "oos": {key: value for key, value in oos.items() if key not in {"equity", "dates"}},
            "full": full,
        })
    results.sort(key=lambda row: row["score"], reverse=True)
    for rank, row in enumerate(results, 1):
        row["rank"] = rank
    return {"split_index": split, "results": results}


def iso_time(milliseconds: int) -> str:
    return dt.datetime.fromtimestamp(milliseconds / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def serialize_bars(bars: list[Bar]) -> list[dict[str, Any]]:
    return [{**asdict(bar), "iso": iso_time(bar.time)} for bar in bars]


def selected_spec(strategy_id: str) -> StrategySpec:
    for spec in strategy_catalog():
        if spec.id == strategy_id:
            return spec
    raise ValueError("Unknown strategy id.")


def paper_step(symbol: str, interval: str, strategy_id: str) -> dict[str, Any]:
    bars = fetch_klines(symbol, interval, 500)
    spec = selected_spec(strategy_id)
    signals = make_signals(bars, spec)
    target = signals[-1]
    price = bars[-1].close
    timestamp = iso_time(bars[-1].time)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    state_path = STATE_ROOT / "paper_state.json"
    ledger_path = STATE_ROOT / "ledger.jsonl"
    learnings_path = STATE_ROOT / "learnings.md"
    with STATE_LOCK:
        state = {"position": 0, "entry_price": None, "strategy_id": strategy_id}
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        previous = int(state.get("position", 0))
        events: list[dict[str, Any]] = []
        if previous != target:
            if previous and state.get("entry_price"):
                entry_price = float(state["entry_price"])
                pnl_pct = previous * (price / entry_price - 1.0) - COMMISSION * 2
                close_event = {
                    "time": timestamp,
                    "kind": "CLOSE",
                    "symbol": symbol,
                    "interval": interval,
                    "strategy": spec.name,
                    "side": "LONG" if previous > 0 else "SHORT",
                    "price": price,
                    "entry_price": entry_price,
                    "pnl_pct": pnl_pct,
                    "reason": "Strategy target changed on a completed candle.",
                }
                events.append(close_event)
                with learnings_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"\n## {timestamp} — {spec.name}\n\n")
                    handle.write(f"Paper {close_event['side'].lower()} closed at {price:,.2f}; result {pnl_pct:+.2%}. ")
                    handle.write("Observation only: review this outcome as part of a larger sample; do not create a permanent rule from one trade.\n")
            if target:
                events.append({
                    "time": timestamp,
                    "kind": "OPEN",
                    "symbol": symbol,
                    "interval": interval,
                    "strategy": spec.name,
                    "side": "LONG" if target > 0 else "SHORT",
                    "price": price,
                    "reason": "Latest completed candle produced a strategy signal.",
                })
            state = {"position": target, "entry_price": price if target else None, "strategy_id": strategy_id, "updated_at": timestamp}
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            with ledger_path.open("a", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event, separators=(",", ":")) + "\n")
        return {"changed": bool(events), "events": events, "state": state, "price": price, "candle_time": timestamp}


def read_ledger(limit: int = 50) -> dict[str, Any]:
    ledger_path = STATE_ROOT / "ledger.jsonl"
    state_path = STATE_ROOT / "paper_state.json"
    events: list[dict[str, Any]] = []
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8").splitlines()[-limit:]:
            if line.strip():
                events.append(json.loads(line))
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"position": 0, "entry_price": None}
    return {"events": list(reversed(events)), "state": state}


class Handler(BaseHTTPRequestHandler):
    server_version = "TradingLab/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def json_response(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def error_response(self, exc: Exception, status: int = 400) -> None:
        self.json_response({"error": str(exc)}, status)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/api/run":
                symbol = query.get("symbol", ["BTCUSDT"])[0].upper()
                interval = query.get("interval", ["1d"])[0]
                limit = int(query.get("limit", ["1000"])[0])
                bars = fetch_klines(symbol, interval, limit)
                batch = run_batch(bars)
                self.json_response({
                    "symbol": symbol,
                    "interval": interval,
                    "commission": COMMISSION,
                    "generated_at": iso_time(int(time.time() * 1000)),
                    "bars": serialize_bars(bars),
                    **batch,
                })
            elif parsed.path == "/api/ledger":
                self.json_response(read_ledger())
            elif parsed.path in {"/", "/index.html"}:
                intraday = ROOT / "research" / "intraday_v1" / "index.html"
                focused = ROOT / "research" / "focused_bot" / "index.html"
                replay = ROOT / "research" / "repaired_replay" / "index.html"
                self.serve_file(intraday if intraday.exists() else focused if focused.exists() else replay if replay.exists() else WEB_ROOT / "index.html", "text/html; charset=utf-8")
            elif parsed.path == "/lab":
                self.serve_file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
            elif parsed.path.startswith("/research/"):
                target = (ROOT / urllib.parse.unquote(parsed.path).lstrip("/")).resolve()
                if not target.is_relative_to((ROOT / "research").resolve()):
                    self.send_error(403)
                else:
                    if target.is_dir():
                        target = target / "index.html"
                    self.serve_file(target, mimetypes.guess_type(str(target))[0] or "application/octet-stream")
            else:
                self.send_error(404)
        except (ValueError, RuntimeError) as exc:
            self.error_response(exc)
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self.error_response(RuntimeError("The local analysis failed. Check the server output for details."), 500)
            print(f"Unexpected error: {exc!r}")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if parsed.path == "/api/intraday/stop":
                (ROOT / "research" / "intraday_v1" / "STOP_LIVE").write_text("Stop requested from dashboard\n")
                self.json_response({"status": "stop_requested"})
            elif parsed.path == "/api/paper/step":
                self.json_response(paper_step(
                    str(payload.get("symbol", "BTCUSDT")).upper(),
                    str(payload.get("interval", "1d")),
                    str(payload["strategy_id"]),
                ))
            else:
                self.send_error(404)
        except (ValueError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
            self.error_response(exc)

    def serve_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FastThreadingHTTPServer(ThreadingHTTPServer):
    """Avoid a reverse-DNS lookup that can stall local-only startup on macOS."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local paper-only Trading Lab.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = FastThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Trading Lab: http://{args.host}:{args.port}")
    print("Paper-only. No exchange credentials or live-order routes are present.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
