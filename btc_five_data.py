"""Download and validate BTCUSDT USD-M futures 15-minute candles.

The five-bot study uses Binance Public Data Vision archives rather than an
authenticated endpoint.  The archive is futures data because the study
allows symmetric long/short signals; the evaluator still models one-times
notional and no leverage.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import math
import ssl
import time
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "research" / "btc_five_bots"
DATA = OUT / "data"
SYMBOL = "BTCUSDT"
INTERVAL = "15m"
STEP_MS = 15 * 60 * 1000
START = int(dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
END = int(dt.datetime(2026, 9, 18, tzinfo=dt.timezone.utc).timestamp() * 1000)
CTX = ssl.create_default_context(cafile="/etc/ssl/cert.pem")


def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def months(start: dt.date, end_exclusive: dt.date):
    cur = dt.date(start.year, start.month, 1)
    while cur < end_exclusive:
        yield cur.year, cur.month
        cur = dt.date(cur.year + (cur.month == 12), 1 if cur.month == 12 else cur.month + 1, 1)


def days(start: dt.date, end_exclusive: dt.date):
    cur = start
    while cur < end_exclusive:
        yield cur
        cur += dt.timedelta(days=1)


def fetch(url: str) -> bytes:
    for attempt in range(5):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "TradingLab-BTC-Five/1.0"})
            with urllib.request.urlopen(request, context=CTX, timeout=45) as response:
                return response.read()
        except Exception:
            if attempt == 4:
                raise
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("unreachable")


def parse_zip(blob: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"Expected one CSV, got {names}")
        with archive.open(names[0]) as raw:
            rows = list(csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline="")))
    if rows and rows[0] and rows[0][0] == "open_time":
        rows = rows[1:]
    return rows


def download() -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    rows: dict[int, list[str]] = {}
    source_files: list[dict] = []
    first_month = dt.datetime.fromtimestamp(START / 1000, tz=dt.timezone.utc).date().replace(day=1)
    end_date = dt.datetime.fromtimestamp(END / 1000, tz=dt.timezone.utc).date()
    for year, month in months(first_month, end_date.replace(day=1)):
        name = f"{SYMBOL}-{INTERVAL}-{year:04d}-{month:02d}.zip"
        url = f"https://data.binance.vision/data/futures/um/monthly/klines/{SYMBOL}/{INTERVAL}/{name}"
        try:
            blob = fetch(url)
        except Exception as exc:
            # The current month is not always published as a monthly archive;
            # its daily files are requested below.
            if year == end_date.year and month == end_date.month:
                continue
            raise RuntimeError(f"Could not download {url}: {exc}") from exc
        parsed = parse_zip(blob)
        source_files.append({"kind": "monthly", "name": name, "url": url, "rows": len(parsed), "sha256": hashlib.sha256(blob).hexdigest()})
        for row in parsed:
            if row:
                rows[int(row[0])] = row
        print("monthly", name, len(parsed), flush=True)
    # Complete current-month days up to the fixed cutoff.
    day_start = dt.date(end_date.year, end_date.month, 1)
    for day in days(day_start, end_date):
        name = f"{SYMBOL}-{INTERVAL}-{day.isoformat()}.zip"
        url = f"https://data.binance.vision/data/futures/um/daily/klines/{SYMBOL}/{INTERVAL}/{name}"
        try:
            blob = fetch(url)
        except Exception as exc:
            # A few most recent days may not be published yet.  They are
            # outside the downloaded cutoff only if the archive is absent.
            print("daily unavailable", name, str(exc), flush=True)
            continue
        parsed = parse_zip(blob)
        source_files.append({"kind": "daily", "name": name, "url": url, "rows": len(parsed), "sha256": hashlib.sha256(blob).hexdigest()})
        for row in parsed:
            if row:
                rows[int(row[0])] = row
        print("daily", name, len(parsed), flush=True)
    selected = [rows[t] for t in sorted(rows) if START <= t < END]
    if not selected:
        raise RuntimeError("No rows downloaded")
    path = DATA / f"{SYMBOL}_{INTERVAL}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for row in selected:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    audit = validate(selected)
    audit.update({"source": "https://data.binance.vision/data/futures/um/", "source_files": source_files, "sha256": sha256(path), "path": str(path)})
    (OUT / "data_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps({k: audit[k] for k in ("rows", "first", "end_exclusive", "missing_bars", "shortened_candles", "sha256")}, indent=2), flush=True)
    return audit


def validate(rows: list[list[str]]) -> dict:
    seen: set[int] = set()
    gaps: list[dict] = []
    shortened: list[dict] = []
    previous = START - STEP_MS
    for row in rows:
        if len(row) < 6:
            raise ValueError("Kline row has too few fields")
        t = int(row[0])
        o, h, low, c, v = map(float, row[1:6])
        close_time = int(row[6]) if len(row) > 6 else t + STEP_MS - 1
        if t in seen or t <= previous or t % STEP_MS:
            raise ValueError("Duplicate, reversed, or unaligned kline")
        if not all(math.isfinite(x) for x in (o, h, low, c, v)) or min(o, h, low, c) <= 0 or v < 0:
            raise ValueError("Invalid OHLCV")
        if low > min(o, c) or h < max(o, c):
            raise ValueError("OHLC bounds invalid")
        if t > previous + STEP_MS:
            gaps.append({"from": iso(previous + STEP_MS), "to_exclusive": iso(t), "bars": (t - previous) // STEP_MS - 1})
        if close_time != t + STEP_MS - 1:
            shortened.append({"open": iso(t), "reported_close": iso(close_time + 1)})
        seen.add(t)
        previous = t
    missing = sum(g["bars"] for g in gaps)
    return {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "rows": len(rows),
        "first": iso(int(rows[0][0])),
        "last_open": iso(int(rows[-1][0])),
        "end_exclusive": iso(END),
        "missing_bars": missing,
        "gaps": gaps,
        "shortened_candles": shortened,
        "duplicates": 0,
        "invalid": 0,
    }


def load() -> list[list[float]]:
    path = DATA / f"{SYMBOL}_{INTERVAL}.jsonl"
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


if __name__ == "__main__":
    download()
