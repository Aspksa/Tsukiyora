"""Cloud.ru adapter and local chat API tests: no live key or paid network calls."""
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tsukiyora.cloud.cloudru as cloudru
import launcher
from tsukiyora.memory.memory_store import MemoryStore


def fake_cloud(method, resource, token, payload=None):
    if resource == "models":
        return {"data": [{"id": "test/model-1"}, {"id": "test/model-2"}]}
    if resource == "chat/completions":
        assert payload["model"] in ("test/model-1", "test/model-2")
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][-1]["role"] == "user"
        return {"choices": [{"message": {"content": "Я тебя услышала, хозяин."}}]}
    raise AssertionError("Unexpected remote endpoint")


class CloudAdapterTests(unittest.TestCase):
    def test_secret_is_never_persisted_and_prompt_uses_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Path(folder) / "preferences.json"
            session = cloudru.CloudSession(settings)
            status = session.set_key("secret-123")
            self.assertNotIn("secret-123", json.dumps(status))
            with patch.object(cloudru, "_request", side_effect=fake_cloud):
                names = session.models()
                self.assertEqual(names, ["test/model-1", "test/model-2"])
                session.select("test/model-1")
                profile = launcher.load_personality()
                answer = session.complete(
                    profile, [{"role": "user", "content": "Привет"}],
                    [{"kind": "preference", "content": "Любит китайский чай"}],
                )
                self.assertEqual(answer, "Я тебя услышала, хозяин.")
            self.assertNotIn("secret-123", settings.read_text(encoding="utf-8"))
            self.assertIn("model-1", settings.read_text(encoding="utf-8"))
            self.assertIn("Любит китайский чай",
                          cloudru.persona_prompt(profile, [{"kind": "preference", "content": "Любит китайский чай"}]))
            other = cloudru.CloudSession(settings)
            self.assertEqual(other.status()["model"], "test/model-1")
            self.assertFalse(other.status()["configured"])

    def test_large_history_is_bounded(self):
        session = cloudru.CloudSession()
        session.set_key("example")
        with patch.object(cloudru, "_request", side_effect=fake_cloud):
            session.models()
        session.select("test/model-1")
        history = [
            {"role": "assistant" if i % 2 else "user", "content": "x" * 8000}
            for i in range(50)
        ]
        history.append({"role": "user", "content": "Расскажи о себе"})
        with patch.object(cloudru, "_request", side_effect=fake_cloud) as remote:
            session.complete(launcher.load_personality(), history, [])
        payload = remote.call_args.args[3]
        self.assertLessEqual(sum(len(m["content"]) for m in payload["messages"][1:]), 24000)
        self.assertEqual(payload["messages"][-1]["content"], "Расскажи о себе")

    def test_validate_models_and_keys(self):
        session = cloudru.CloudSession()
        with self.assertRaises(ValueError):
            session.set_key("bad\nkey")
        with self.assertRaises(cloudru.CloudError):
            session.models()
        session.set_key("test-token")
        with patch.object(cloudru, "_request", side_effect=fake_cloud):
            session.models()
        with self.assertRaises(ValueError):
            session.select("not-in-catalog")
        session.select("test/model-1")
        self.assertTrue(session.status()["verified"])
        session.set_key("")
        self.assertFalse(session.status()["verified"])

    def test_cloud_request_uses_pinned_https_host(self):
        class FakeTransport:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return io.BytesIO(b'{"data":[]}')
        fake = FakeTransport()
        with patch.object(cloudru, "build_opener", return_value=fake):
            data = cloudru._request("GET", "models", "abc-key")
        self.assertEqual(data, {"data": []})
        self.assertEqual(fake.request.full_url, "https://foundation-models.api.cloud.ru/v1/models")
        self.assertEqual(fake.request.get_header("Authorization"), "Bearer abc-key")
        self.assertLessEqual(fake.timeout, 60)
        with self.assertRaises(ValueError):
            cloudru._request("GET", "http://malicious.example", "abc-key")


class CloudApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memory.sqlite3"
        launcher.init_database(self.path)
        MemoryStore(self.path).add_memory("preference", "Мой любимый цвет — золотой")
        self.server = launcher.SoulServer(("127.0.0.1", 0), launcher.load_personality(), self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = build_opener(ProxyHandler({}))

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, method="GET", payload=None, origin=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if method != "GET":
            headers["Origin"] = origin or self.url
        req = Request(self.url + path, method=method, data=body, headers=headers)
        with self.opener.open(req, timeout=5) as response:
            return response.status, json.load(response)

    def test_chat_persists_history_and_does_not_double_bill_on_retry(self):
        saved_status = self.request("/api/cloud/status")[1]
        self.assertFalse(saved_status["configured"])
        with patch.object(cloudru, "_request", side_effect=fake_cloud) as remote:
            self.request("/api/cloud/key", "POST", {"api_key": "super-secret"})
            status, catalog = self.request("/api/cloud/models")
            self.assertEqual(status, 200)
            self.assertEqual(catalog["models"], ["test/model-1", "test/model-2"])
            self.request("/api/cloud/model", "POST", {"model": "test/model-1"})
            status, info = self.request("/api/status")
            self.assertEqual(info["chat"], "available")
            status, result = self.request("/api/chat", "POST", {
                "content": "Привет, ты меня помнишь?", "client_message_id": "u-1",
            })
            self.assertEqual(status, 200)
            self.assertEqual(result["answer"], "Я тебя услышала, хозяин.")
            convo = result["conversation_id"]
            self.assertFalse(result["cached"])
            status, cached = self.request("/api/chat", "POST", {
                "conversation_id": convo, "content": "Привет, ты меня помнишь?",
                "client_message_id": "u-1",
            })
            self.assertTrue(cached["cached"])
            with self.assertRaises(HTTPError) as repeated_error:
                self.request("/api/chat", "POST", {
                    "conversation_id": convo, "content": "Злоумышленный другой текст",
                    "client_message_id": "u-1",
                })
            self.assertEqual(repeated_error.exception.code, 400)
            self.assertEqual(remote.call_count, 2, "1 models call + 1 paid chat call")
            _, history = self.request("/api/memory/messages?conversation_id=" + convo)
            self.assertEqual([x["role"] for x in history["messages"]], ["user", "assistant"])
            self.assertEqual(MemoryStore(self.path).stats()["messages"], 2)
            _, public_status = self.request("/api/cloud/status")
            self.assertNotIn("super-secret", json.dumps(public_status))
            self.assertNotIn("super-secret", (self.path.parent / "cloudru_preferences.json").read_text(encoding="utf-8"))
            payload = remote.call_args_list[-1].args[3]
            self.assertIn("Мой любимый цвет — золотой", payload["messages"][0]["content"])
            self.assertIn("Альбедо", json.dumps(launcher.load_personality(), ensure_ascii=False))
        with closing(sqlite3.connect(self.path)) as db:
            # API keys must never be saved in the local SQLite memory.
            for table, column in (("messages", "content"), ("memory_items", "content")):
                for row in db.execute(f"SELECT {column} FROM {table}"):
                    self.assertNotIn("super-secret", row[0])

    def test_provider_error_does_not_invent_assistant_reply(self):
        self.request("/api/cloud/key", "POST", {"api_key": "bad"})
        with patch.object(cloudru, "_request", side_effect=fake_cloud):
            self.request("/api/cloud/models")
        self.request("/api/cloud/model", "POST", {"model": "test/model-1"})
        with patch.object(cloudru, "_request", side_effect=cloudru.CloudError("Ключ отклонён.", 401)):
            with self.assertRaises(HTTPError) as ctx:
                self.request("/api/chat", "POST", {"content": "Один вопрос"})
            self.assertEqual(ctx.exception.code, 401)
        self.assertEqual(MemoryStore(self.path).stats()["messages"], 1)

    def test_no_cross_origin_writes_or_unconfigured_chat(self):
        with self.assertRaises(HTTPError) as ctx:
            self.request("/api/chat", "POST", {"content": "secret"},
                         origin="https://attacker.example")
        self.assertEqual(ctx.exception.code, 403)
        with self.assertRaises(HTTPError) as ctx:
            self.request("/api/chat", "POST", {"content": "hello"})
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(MemoryStore(self.path).stats()["messages"], 0)


if __name__ == "__main__":
    unittest.main()
