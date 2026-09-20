import tempfile
import unittest
from pathlib import Path

import alpaca_setup


class AlpacaSetupTests(unittest.TestCase):
    def test_parse_env_ignores_comments_and_quotes(self):
        values = alpaca_setup.parse_env("# comment\nA=one\nB='two'\n")
        self.assertEqual(values, {"A": "one", "B": "two"})

    def test_write_config_is_private_and_paper_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "alpaca.env"
            alpaca_setup.write_config({"APCA_API_KEY_ID": "key", "APCA_API_SECRET_KEY": "secret"}, path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            text = path.read_text()
            self.assertIn("paper-api.alpaca.markets", text)
            self.assertEqual(alpaca_setup.read_config(path)["APCA_API_KEY_ID"], "key")


if __name__ == "__main__":
    unittest.main()
