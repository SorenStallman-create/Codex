#!/usr/bin/env python3
"""Read-only SIP minute history importer; standard library only.

Calendar CSV must list actual exchange sessions as date,open,close (NY local
HH:MM), including early closes, for the entire requested range. Obtain it from
an authoritative exchange/broker calendar. Weekdays are NOT assumed sessions.
No broker account or order endpoints are used.
"""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
UTC = timezone.utc
HOST = 'https://data.alpaca.markets/v2/stocks/bars'
SYMBOLS = ('SPY', 'QQQ')


def parse_time(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamps must include timezone, e.g. 2025-01-01T00:00:00Z')
    return result.astimezone(UTC)


def stamp(value):
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, separators=(',', ':')))
    temporary.replace(path)


def calendar_minutes(path, start, end):
    expected = set()
    sessions = []
    seen = set()
    with path.open(newline='') as handle:
        reader = csv.DictReader(handle)
        if not {'date', 'open', 'close'}.issubset(reader.fieldnames or []):
            raise ValueError('Calendar needs date,open,close columns')
        for row in reader:
            day = row['date']
            if day in seen:
                raise ValueError('Duplicate calendar session: ' + day)
            seen.add(day)
            opened = datetime.fromisoformat(day + 'T' + row['open']).replace(tzinfo=NY).astimezone(UTC)
            closed = datetime.fromisoformat(day + 'T' + row['close']).replace(tzinfo=NY).astimezone(UTC)
            if closed <= opened or opened.second or closed.second:
                raise ValueError('Invalid calendar session: ' + day)
            if opened < end and closed > start:
                sessions.append({'date': day, 'open': stamp(opened), 'close': stamp(closed)})
                point = opened
                while point < closed:
                    if start <= point < end:
                        expected.add(stamp(point))
                    point += timedelta(minutes=1)
    if not sessions:
        raise ValueError('Calendar contains no sessions intersecting requested range')
    return expected, sorted(sessions, key=lambda x: x['date'])


def next_token(payload, seen):
    token = payload.get('next_page_token')
    if token is not None and not isinstance(token, str):
        raise ValueError('Invalid pagination token')
    if token and token in seen:
        raise ValueError('Repeated pagination token; refusing incomplete download')
    if token:
        seen.add(token)
    return token or None


def normalize_bar(symbol, bar):
    point = parse_time(bar['t'])
    if point.second or point.microsecond:
        raise ValueError('Non-minute bar timestamp')
    values = [float(bar[key]) for key in ('o', 'h', 'l', 'c', 'v')]
    o, h, l, c, v = values
    if not all(math.isfinite(x) for x in values) or min(o, h, l, c) <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h:
        raise ValueError('Invalid OHLCV bar')
    return {'symbol': symbol, 'time': stamp(point), 'open': o, 'high': h,
            'low': l, 'close': c, 'volume': v}


def audit_bars(payloads, expected, start, end):
    bars = {}
    duplicates = 0
    outside_session = 0
    for payload in payloads:
        mapping = payload.get('bars')
        if not isinstance(mapping, dict):
            raise ValueError('Response missing bars mapping')
        for symbol, source in mapping.items():
            if symbol not in SYMBOLS or not isinstance(source, list):
                raise ValueError('Unexpected symbol or bars shape')
            for raw in source:
                row = normalize_bar(symbol, raw)
                point = parse_time(row['time'])
                # API end is inclusive; importer contract is exclusive.
                if not start <= point < end:
                    continue
                if row['time'] not in expected:
                    outside_session += 1
                    continue
                key = symbol, row['time']
                if key in bars:
                    if bars[key] != row:
                        raise ValueError('Conflicting duplicate bar')
                    duplicates += 1
                bars[key] = row
    missing = {symbol: sorted(expected - {t for s, t in bars if s == symbol}) for symbol in SYMBOLS}
    return [bars[k] for k in sorted(bars)], {'missing_minutes': missing,
        'identical_duplicates': duplicates, 'excluded_non_session_bars': outside_session,
        'expected_per_symbol': len(expected), 'complete': not any(missing.values())}


def fetch(params, key, secret):
    context = ssl.create_default_context()
    cert = Path('/etc/ssl/cert.pem')
    if cert.exists():
        context = ssl.create_default_context(cafile=str(cert))
    request = Request(HOST + '?' + urlencode(params), headers={
        'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret,
        'User-Agent': 'TradingLabReadOnlyHistory/1.0'})
    for attempt in range(5):
        try:
            with urlopen(request, timeout=60, context=context) as response:
                return json.load(response)
        except HTTPError as error:
            # Do not print response bodies, requests or credentials.
            if error.code in (401, 403):
                raise RuntimeError('Data authentication/entitlement denied; no fallback feed used') from None
            if error.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise RuntimeError('Market-data HTTP status ' + str(error.code)) from None
        except (URLError, TimeoutError):
            if attempt == 4:
                raise RuntimeError('Market-data network failure after bounded retries') from None
        time.sleep(min(2 ** attempt, 16))
    raise RuntimeError('Download failed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--start', required=True, help='Inclusive timezone-aware timestamp')
    parser.add_argument('--end', required=True, help='Exclusive timezone-aware timestamp, at least 15 minutes old')
    parser.add_argument('--calendar', type=Path, required=True, help='Complete session CSV: date,open,close in New York local time')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'research/licensed_data')
    args = parser.parse_args(argv)
    start, end = parse_time(args.start), parse_time(args.end)
    if start >= end or end > datetime.now(UTC) - timedelta(minutes=15):
        raise ValueError('Require start < end and end at least 15 minutes in the past')
    if start.second or start.microsecond or end.second or end.microsecond:
        raise ValueError('Start and end must be minute aligned')
    expected, sessions = calendar_minutes(args.calendar, start, end)
    params = dict(symbols=','.join(SYMBOLS), timeframe='1Min', feed='sip', adjustment='raw',
                  sort='asc', start=stamp(start), end=stamp(end), limit=10000)
    specification = {'endpoint': HOST, 'parameters': params,
                     'calendar_sha256': hashlib.sha256(args.calendar.read_bytes()).hexdigest()}
    identity = hashlib.sha256(json.dumps(specification, sort_keys=True).encode()).hexdigest()[:20]
    destination = args.output / identity
    destination.mkdir(parents=True, exist_ok=True)
    atomic_json(destination / 'request.json', specification)
    payloads, manifest, seen = [], [], set()
    token = None
    page = 0
    while True:
        cache = destination / ('page_%06d.json' % page)
        query = dict(params)
        if token:
            query['page_token'] = token
        if cache.exists():
            saved = json.loads(cache.read_text())
            if saved['query'] != query:
                raise ValueError('Cache query mismatch')
            payload = saved['response']
        else:
            key, secret = os.environ.get('ALPACA_API_KEY'), os.environ.get('ALPACA_SECRET_KEY')
            if not key or not secret:
                raise RuntimeError('Set ALPACA_API_KEY and ALPACA_SECRET_KEY locally; do not paste credentials into chat')
            payload = fetch(query, key, secret)
            # Validate pagination before persisting a bad response.
            if not isinstance(payload.get('bars'), dict):
                raise ValueError('Invalid API response')
            atomic_json(cache, {'query': query, 'fetched_at': stamp(datetime.now(UTC)), 'response': payload})
            time.sleep(.35)  # Below Basic 200 requests/minute, one worker.
        payloads.append(payload)
        manifest.append({'file': cache.name, 'sha256': hashlib.sha256(cache.read_bytes()).hexdigest()})
        token = next_token(payload, seen)
        atomic_json(destination / 'checkpoint.json', {'pages': page + 1, 'download_complete': token is None})
        if token is None:
            break
        page += 1
    rows, audit = audit_bars(payloads, expected, start, end)
    with (destination / 'bars.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['symbol', 'time', 'open', 'high', 'low', 'close', 'volume'])
        writer.writeheader()
        writer.writerows(rows)
    audit.update({'sessions': sessions, 'rows': len(rows), 'feed': 'sip',
                  'calendar_note': 'Coverage depends on supplied complete authoritative calendar; no calendar completeness inferred.',
                  'missing_policy': 'No synthesized bars. Investigate missing intervals before backtesting.'})
    atomic_json(destination / 'audit.json', audit)
    atomic_json(destination / 'manifest.json', {'request': specification, 'raw_pages': manifest,
        'bars_sha256': hashlib.sha256((destination / 'bars.csv').read_bytes()).hexdigest()})
    print(json.dumps({'output': str(destination), 'rows': len(rows), 'complete': audit['complete']}))
    return 0 if audit['complete'] else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        # Messages from our validators never include credential material.
        print('Import failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
