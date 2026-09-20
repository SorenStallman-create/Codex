"""Securely configure and test an Alpaca paper-only connection.

Keys are stored outside the project at ``~/.config/trading_lab/alpaca.env``
with mode 0600.  This script never prints key values and never writes them to
the research bundle.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import ssl
import urllib.parse
import urllib.request
from pathlib import Path


CONFIG_PATH = Path.home() / ".config" / "trading_lab" / "alpaca.env"
PAPER_BASE = "https://paper-api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"
SSL_CONTEXT = ssl.create_default_context()


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def read_config(path: Path = CONFIG_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_env(path.read_text(encoding="utf-8"))


def write_config(values: dict[str, str], path: Path = CONFIG_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Alpaca paper-only credentials; keep this file private.\n"
        f"APCA_API_KEY_ID={values['APCA_API_KEY_ID']}\n"
        f"APCA_API_SECRET_KEY={values['APCA_API_SECRET_KEY']}\n"
        "APCA_API_BASE_URL=https://paper-api.alpaca.markets\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    return path


def _headers(values: dict[str, str]) -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": values["APCA_API_KEY_ID"],
        "APCA-API-SECRET-KEY": values["APCA_API_SECRET_KEY"],
        "User-Agent": "trading-lab-paper-setup/1.0",
    }


def request(base: str, path: str, values: dict[str, str], params: dict[str, str] | None = None) -> dict:
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    req = urllib.request.Request(f"{base}{path}{query}", headers=_headers(values))
    with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as response:
        return json.loads(response.read().decode("utf-8"))


def check(path: Path = CONFIG_PATH) -> dict:
    values = read_config(path)
    if not values.get("APCA_API_KEY_ID") or not values.get("APCA_API_SECRET_KEY") or any("REPLACE_WITH" in values.get(key, "") for key in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")):
        raise RuntimeError(f"No complete Alpaca key file at {path}. Run: python3 alpaca_setup.py set")
    account = request(PAPER_BASE, "/v2/account", values)
    quote = request(DATA_BASE, "/v2/stocks/quotes/latest", values, {"symbols": "SPY", "feed": "iex"})
    return {
        "paper_connection": True,
        "account_status": account.get("status"),
        "account_id_suffix": str(account.get("id", ""))[-4:] if account.get("id") else None,
        "buying_power_present": bool(account.get("buying_power")),
        "spy_quote_received": bool((quote.get("quotes") or {}).get("SPY")),
        "config_path": str(path),
        "config_mode": oct(path.stat().st_mode & 0o777),
        "paper_endpoint": PAPER_BASE,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure and verify Alpaca paper-only access without exposing keys")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the private key-file location with a template")
    sub.add_parser("set", help="prompt for paper keys without echoing the secret")
    sub.add_parser("check", help="verify paper account access and an IEX SPY quote")
    args = parser.parse_args()
    if args.command == "init":
        if CONFIG_PATH.exists():
            print(json.dumps({"exists": True, "path": str(CONFIG_PATH), "mode": oct(CONFIG_PATH.stat().st_mode & 0o777)}))
            return
        write_config({"APCA_API_KEY_ID": "REPLACE_WITH_PAPER_KEY", "APCA_API_SECRET_KEY": "REPLACE_WITH_PAPER_SECRET"})
        print(json.dumps({"created": True, "path": str(CONFIG_PATH), "mode": "0o600", "next": "python3 alpaca_setup.py set"}, indent=2))
        return
    if args.command == "set":
        key = getpass.getpass("Alpaca paper API key ID (input hidden): ").strip()
        secret = getpass.getpass("Alpaca paper secret key (input hidden): ").strip()
        if not key or not secret or "REPLACE_WITH" in key or "REPLACE_WITH" in secret:
            raise SystemExit("Both values are required; nothing was written.")
        write_config({"APCA_API_KEY_ID": key, "APCA_API_SECRET_KEY": secret})
        print(json.dumps({"saved": True, "path": str(CONFIG_PATH), "mode": "0o600", "next": "python3 alpaca_setup.py check"}, indent=2))
        return
    try:
        print(json.dumps(check(), indent=2))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
