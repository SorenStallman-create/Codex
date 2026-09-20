import tempfile
import unittest
from pathlib import Path
import alpaca_history_import as imp


class ImportTests(unittest.TestCase):
    def test_timezone_required(self):
        with self.assertRaises(ValueError):
            imp.parse_time('2025-01-02')

    def test_early_close_and_dst(self):
        with tempfile.TemporaryDirectory() as folder:
            calendar = Path(folder) / 'calendar.csv'
            calendar.write_text('date,open,close\n2025-07-03,09:30,13:00\n')
            expected, _ = imp.calendar_minutes(calendar, imp.parse_time('2025-07-03T00:00:00Z'), imp.parse_time('2025-07-04T00:00:00Z'))
            self.assertEqual(len(expected), 210)
            self.assertIn('2025-07-03T13:30:00Z', expected)
            self.assertNotIn('2025-07-03T17:00:00Z', expected)

    def test_pagination_not_page_length(self):
        seen = set()
        self.assertEqual(imp.next_token({'bars': {}, 'next_page_token': 'next'}, seen), 'next')
        with self.assertRaises(ValueError):
            imp.next_token({'next_page_token': 'next'}, seen)
        self.assertIsNone(imp.next_token({'next_page_token': None}, seen))

    def test_gap_and_conflicting_duplicates(self):
        t = '2025-07-03T13:30:00Z'
        bar = {'t': t, 'o': 1, 'h': 2, 'l': 1, 'c': 2, 'v': 10}
        start, end = imp.parse_time(t), imp.parse_time('2025-07-03T13:31:00Z')
        rows, audit = imp.audit_bars([{'bars': {'SPY': [bar]}}], {t}, start, end)
        self.assertEqual(len(rows), 1)
        self.assertEqual(audit['missing_minutes']['QQQ'], [t])
        self.assertFalse(audit['complete'])
        with self.assertRaises(ValueError):
            imp.audit_bars([{'bars': {'SPY': [bar, dict(bar, c=1.5)]}}], {t}, start, end)

    def test_cache_resume_offline_and_exclusive_end(self):
        from unittest.mock import patch
        import json
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            calendar = directory / 'calendar.csv'
            calendar.write_text('date,open,close\n2025-07-03,09:30,09:31\n')
            t = '2025-07-03T13:30:00Z'
            bar = {'t': t, 'o': 1, 'h': 2, 'l': 1, 'c': 2, 'v': 10}
            args = ['--start', t, '--end', '2025-07-03T13:31:00Z', '--calendar', str(calendar), '--output', str(directory / 'out')]
            with patch.dict(imp.os.environ, {'ALPACA_API_KEY': 'TEST_NOT_REAL', 'ALPACA_SECRET_KEY': 'TEST_NOT_REAL'}), patch.object(imp, 'fetch', return_value={'bars': {'SPY': [bar], 'QQQ': [bar]}, 'next_page_token': None}) as fetch, patch.object(imp.time, 'sleep'):
                self.assertEqual(imp.main(args), 0)
                self.assertEqual(fetch.call_count, 1)
            with patch.object(imp, 'fetch', side_effect=AssertionError('Cache should avoid network')):
                self.assertEqual(imp.main(args), 0)
            for file in (directory / 'out').rglob('*.json'):
                self.assertNotIn('TEST_NOT_REAL', file.read_text())


if __name__ == '__main__':
    unittest.main()
