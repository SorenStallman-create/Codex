"""Alpaca REST adapter for the overnight equity paper workflow.

No network call is made on import. The adapter reads credentials only when a
function is called, from environment variables or the private setup file at
``~/.config/trading_lab/alpaca.env``. Paper-order submission is opt-in through
the CLI flag in the example at the bottom; the research runner never calls it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import ssl
import urllib.parse
import urllib.request


DATA_BASE = "https://data.alpaca.markets"
PAPER_BASE = "https://paper-api.alpaca.markets"
SSL_CONTEXT = ssl._create_unverified_context()
PRIVATE_CONFIG = Path.home() / ".config" / "trading_lab" / "alpaca.env"


def _private_file_credentials() -> tuple[str, str]:
    """Read the optional 0600 setup file without printing its contents."""
    if not PRIVATE_CONFIG.exists():
        return "", ""
    values: dict[str, str] = {}
    for line in PRIVATE_CONFIG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values.get("APCA_API_KEY_ID", ""), values.get("APCA_API_SECRET_KEY", "")


def credentials() -> tuple[str, str]:
    key = os.getenv("APCA_API_KEY_ID") or os.getenv("APCA-API-KEY-ID") or os.getenv("ALPACA_API_KEY") or ""
    secret = os.getenv("APCA_API_SECRET_KEY") or os.getenv("APCA-API-SECRET-KEY") or os.getenv("ALPACA_SECRET_KEY") or ""
    if not key or not secret:
        file_key, file_secret = _private_file_credentials()
        key = key or file_key
        secret = secret or file_secret
    if not key or not secret or "REPLACE_WITH" in key or "REPLACE_WITH" in secret:
        raise RuntimeError("Set APCA-API-KEY-ID/APCA-API-SECRET-KEY (or ALPACA_API_KEY/ALPACA_SECRET_KEY) first")
    return key, secret


def request_json(base: str, path: str, params: dict | None = None, method: str = "GET", body: dict | None = None) -> dict:
    key, secret = credentials()
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{base}{path}{query}",
        data=payload,
        method=method,
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "Content-Type": "application/json", "User-Agent": "overnight-equities-research/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
        return json.loads(response.read().decode("utf-8"))


def historical_bars(symbols: list[str], start: str, end: str, feed: str = "iex") -> dict:
    """Fetch split/dividend-adjusted daily bars with Alpaca pagination."""
    result: dict = {}
    token = None
    while True:
        params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start, "end": end, "adjustment": "all", "feed": feed, "limit": "10000", "sort": "asc"}
        if token:
            params["page_token"] = token
        page = request_json(DATA_BASE, "/v2/stocks/bars", params=params)
        for symbol, rows in (page.get("bars") or {}).items():
            result.setdefault(symbol, []).extend(rows)
        token = page.get("next_page_token")
        if not token:
            break
    return result


def historical_news(symbols: list[str], start: str, end: str, include_content: bool = False) -> list[dict]:
    """Fetch Alpaca's historical news feed (currently Benzinga-provided)."""
    articles: list[dict] = []
    token = None
    while True:
        params = {"symbols": ",".join(symbols), "start": start, "end": end, "sort": "asc", "limit": "50", "include_content": str(include_content).lower(), "exclude_contentless": "false"}
        if token:
            params["page_token"] = token
        page = request_json(DATA_BASE, "/v1beta1/news", params=params)
        articles.extend(page.get("news") or [])
        token = page.get("next_page_token")
        if not token:
            break
    return articles


def latest_quotes(symbols: list[str], feed: str = "iex") -> dict:
    """Fetch the latest quote for each symbol from Alpaca's stock-data API.

    The response is returned unchanged so the execution layer can preserve the
    broker timestamp and bid/ask values in the ledger.  No order endpoint is
    touched by this function.
    """
    if not symbols:
        return {}
    payload = request_json(DATA_BASE, "/v2/stocks/quotes/latest", params={"symbols": ",".join(sorted(set(symbols))), "feed": feed})
    return payload.get("quotes") or {}


def submit_paper_order(symbol: str, notional: float, side: str = "buy", client_order_id: str | None = None, dry_run: bool = True) -> dict:
    """Submit a DAY market order to Alpaca paper only when dry_run=False."""
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    body = {"symbol": symbol, "notional": f"{notional:.2f}", "side": side, "type": "market", "time_in_force": "day", "extended_hours": False}
    if client_order_id:
        body["client_order_id"] = client_order_id
    if dry_run:
        return {"dry_run": True, "endpoint": f"{PAPER_BASE}/v2/orders", "body": body}
    return request_json(PAPER_BASE, "/v2/orders", method="POST", body=body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Alpaca credentials or preview a paper order")
    parser.add_argument("--preview-order", metavar="SYMBOL")
    parser.add_argument("--notional", type=float, default=1000.0)
    parser.add_argument("--execute-paper", action="store_true", help="actually submit to Alpaca paper; omitted means preview only")
    args = parser.parse_args()
    if args.preview_order:
        print(json.dumps(submit_paper_order(args.preview_order, args.notional, dry_run=not args.execute_paper), indent=2))
    else:
        key, _ = credentials()
        print(json.dumps({"credentials_present": bool(key), "paper_endpoint": f"{PAPER_BASE}/v2/orders", "news_endpoint": f"{DATA_BASE}/v1beta1/news"}, indent=2))


if __name__ == "__main__":
    main()
