"""Real Windows BAT launcher smoke test (no extra Python dependencies)."""
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "runtime.json"


@unittest.skipUnless(os.name == "nt", "Windows BAT test")
class WindowsBatchTests(unittest.TestCase):
    def test_bat_starts_server_and_handles_duplicate(self):
        env = os.environ.copy()
        env["TSUKIYORA_NO_BROWSER"] = "1"
        command = ["cmd.exe", "/d", "/c", str(ROOT / "Tsukiyora.bat")]
        process = subprocess.Popen(
            command, cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        try:
            opener = build_opener(ProxyHandler({}))
            healthy = False
            url = ""
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    self.fail(f"BAT exited too early: {process.returncode}")
                try:
                    state = json.loads(STATE.read_text(encoding="utf-8"))
                    port = state.get("port")
                    if type(port) is int and 1 <= port <= 65535:
                        url = f"http://127.0.0.1:{port}"
                        with opener.open(url + "/api/health", timeout=2) as response:
                            healthy = json.load(response).get("ok") is True
                    if healthy:
                        break
                except (OSError, ValueError, TypeError):
                    pass
                time.sleep(0.25)
            self.assertTrue(healthy, "BAT did not start a healthy local server")
            with opener.open(url + "/api/personality", timeout=3) as response:
                self.assertEqual(json.load(response)["identity"]["name"], "Tsukiyora")

            duplicate = subprocess.run(
                command, cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=15,
            )
            self.assertEqual(duplicate.returncode, 0, duplicate.stdout)
            self.assertIn("already running", duplicate.stdout)
            self.assertIsNone(process.poll(), "Duplicate stopped the original server")
        finally:
            if process.poll() is None:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True, check=False, timeout=10,
                )
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
