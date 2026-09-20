import csv
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from paper_execution import (
    DEFAULT_MAX_QUOTE_AGE_MS,
    DEFAULT_MAX_SPREAD_BPS,
    LEDGER_FIELDS,
    allocation_plan,
    append_fill,
    conservative_fill_price,
    measurement_summary,
    quote_gate,
    reconcile_fills,
    slippage_vs_official_bps,
    validate_allocation,
)


class PaperExecutionTests(unittest.TestCase):
    def test_quote_gate_blocks_stale_and_wide_quotes(self):
        now = dt.datetime(2026, 9, 19, tzinfo=dt.timezone.utc)
        stale = quote_gate({"bp": 99.99, "ap": 100.01, "t": "2026-09-18T23:59:00Z"}, now=now)
        self.assertFalse(stale["eligible"])
        self.assertIn("quote_stale", stale["reasons"])
        wide = quote_gate({"bp": 99.0, "ap": 101.0, "t": "2026-09-19T00:00:00Z"}, now=now, max_age_ms=DEFAULT_MAX_QUOTE_AGE_MS + 1_000_000)
        self.assertFalse(wide["eligible"])
        self.assertIn("spread_above_limit", wide["reasons"])

    def test_adverse_side_fill_and_slippage(self):
        now = dt.datetime(2026, 9, 19, tzinfo=dt.timezone.utc)
        gate = quote_gate({"bp": 99.99, "ap": 100.01, "t": now.isoformat()}, now=now)
        self.assertTrue(gate["eligible"])
        self.assertEqual(conservative_fill_price("buy", gate), 100.01)
        self.assertAlmostEqual(slippage_vs_official_bps("buy", 100.01, 100.0), 1.0, places=7)
        self.assertAlmostEqual(slippage_vs_official_bps("sell", 99.99, 100.0), 1.0001000100009788, places=7)

    def test_equal_allocation_never_exceeds_caps(self):
        picks = [{"model": f"m{i}", "symbol": f"S{i}"} for i in range(5)]
        rows = allocation_plan(picks, 10_000.0)
        check = validate_allocation(rows, 10_000.0)
        self.assertTrue(check["ok"])
        self.assertAlmostEqual(check["total_fraction"], 0.50)
        self.assertTrue(all(value <= 0.25 for value in check["model_fractions"].values()))

    def test_ledger_round_trip_and_fixed_decision_waits_for_60_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fills.csv"
            now = dt.datetime(2026, 9, 19, tzinfo=dt.timezone.utc).isoformat()
            for i in range(2):
                date = f"2026-09-{i + 1:02d}"
                base = {
                    "date": date, "symbol": "AAA", "model": "m", "intended_price": 100,
                    "official_close": 100, "official_open": "", "quoted_bid": 99.99,
                    "quoted_ask": 100.02, "spread_bps": 3, "fill_price": 100.02,
                    "slippage_vs_official_bps": 2, "notional": 100, "news_flag": "clear",
                    "quote_age_ms": 10, "partial_fill": "false", "error": "",
                    "trade_id": f"t{i}", "session_date": date, "leg": "entry",
                    "timestamp_utc": now, "fill_source": "test",
                }
                append_fill(path, {**base, "side": "buy"})
                append_fill(path, {**base, "side": "sell", "official_close": "", "official_open": 100, "intended_price": 100, "fill_price": 99.98, "slippage_vs_official_bps": 2, "leg": "exit"})
            result = measurement_summary(path)
            self.assertEqual(result["completed_trades"], 2)
            self.assertEqual(result["completed_sessions"], 2)
            self.assertEqual(result["decision"], "WAITING_FOR_60_COMPLETED_SESSIONS")
            with path.open() as source:
                self.assertEqual(next(csv.reader(source)), list(LEDGER_FIELDS))

    def test_reconciliation_detects_duplicate_leg(self):
        row = {"date": "2026-01-01", "symbol": "AAA", "model": "m", "side": "buy", "trade_id": "x", "error": ""}
        result = reconcile_fills([row, row])
        self.assertFalse(result["ok"])
        self.assertIn("duplicate_legs:x", result["errors"])

    def test_append_refuses_duplicate_trade_leg(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fills.csv"
            row = {"date": "2026-01-01", "symbol": "AAA", "model": "m", "side": "buy", "intended_price": 100, "official_close": 100, "quoted_bid": 99.99, "quoted_ask": 100.01, "fill_price": 100.01, "slippage_vs_official_bps": 1, "notional": 100, "news_flag": "clear", "quote_age_ms": 1, "partial_fill": "false", "error": "", "trade_id": "x", "session_date": "2026-01-01", "leg": "entry"}
            append_fill(path, row)
            with self.assertRaises(RuntimeError):
                append_fill(path, row)


if __name__ == "__main__":
    unittest.main()
