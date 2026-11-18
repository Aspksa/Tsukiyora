"""Standard-library smoke tests for Tsukiyora runtime."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import launcher


class RuntimeTests(unittest.TestCase):
    def test_personality(self):
        profile = launcher.load_personality()
        self.assertEqual(profile["identity"]["name"], "Tsukiyora")
        self.assertEqual(profile["identity"]["tails"], 8)
        self.assertTrue(profile["protected_values"])

    def test_sqlite_initialization(self):
        with tempfile.TemporaryDirectory() as folder:
            db_file = Path(folder) / "memory.sqlite3"
            launcher.init_database(db_file)
            with sqlite3.connect(db_file) as db:
                rows = db.execute("SELECT COUNT(*) FROM runtime_events").fetchone()
            self.assertEqual(rows[0], 1)

    def test_http_api_and_interface(self):
        server = launcher.SoulServer(("127.0.0.1", 0), launcher.load_personality())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            opener = build_opener(ProxyHandler({}))
            base = "http://127.0.0.1:%d" % server.server_address[1]
            for path in ["/api/health", "/api/status", "/api/personality"]:
                with opener.open(base + path, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn("application/json", response.headers["Content-Type"])
                    data = json.load(response)
                    self.assertIsInstance(data, dict)
            with opener.open(base + "/", timeout=3) as response:
                html = response.read().decode("utf-8")
                self.assertIn("SOUL CORE", html)
            with opener.open(base + "/ui/app.ts", timeout=3) as response:
                self.assertIn("text/javascript", response.headers["Content-Type"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
