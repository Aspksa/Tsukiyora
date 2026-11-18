"""Standard-library tests: python -m unittest discover -s tests -v"""
import json
from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from launcher import ProfileError, handler_for, load_profile, render_page, safe_profile, validate_profile
from http.server import ThreadingHTTPServer


class TestPersonality(unittest.TestCase):
    def test_real_profile_loads(self):
        profile = load_profile()
        self.assertEqual(profile["identity"]["name"], "Tsukiyora")
        self.assertEqual(safe_profile(profile)["address"], "хозяин")

    def test_missing_values_fail_closed(self):
        with self.assertRaises(ProfileError):
            validate_profile({"schema_version": "0.1.0", "identity": {}})

    def test_html_escapes_untrusted_values(self):
        profile = load_profile()
        profile["identity"]["name_ru"] = '<img src=x onerror="alert(1)">'
        page = render_page(profile)
        self.assertNotIn('<img src=x', page)
        self.assertIn('&lt;img', page)

    def test_http_api(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(load_profile()))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(url + "/api/health", timeout=5) as response:
                data = json.load(response)
                self.assertTrue(data["profile_loaded"])
                self.assertFalse(data["chat_available"])
            with urlopen(url + "/api/personality", timeout=5) as response:
                self.assertEqual(json.load(response)["name_ru"], "Цукиёра")
            with urlopen(url + "/", timeout=5) as response:
                self.assertIn("SOUL CORE", response.read().decode("utf-8"))
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url + "/api/chat", data=b"{}", method="POST"), timeout=5)
            self.assertEqual(error.exception.code, 501)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
