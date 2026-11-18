"""Long-term soul and live streaming tests. Never contact Cloud.ru or spend tokens."""
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cloudru
import launcher
from memory_store import MemoryStore
from soul_growth import SoulGrowth
from stream_runtime import StreamCancellation, stop_stream


def stream_stub(self, prompt, history, cancellation):
    for token in ["Привет, ", "хозяин!"]:
        if cancellation.is_set():
            return
        yield token


class SoulGrowthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "soul.sqlite3"
        launcher.init_database(self.db)
        self.store = MemoryStore(self.db)
        self.soul = SoulGrowth(self.db)

    def test_review_provenance_and_dedup(self):
        convo = self.store.create_conversation()
        message = self.store.append_messages(convo["id"], [
            {"role": "user", "content": "Я люблю чёрный чай и я работаю в гараже"}
        ])[0]
        suggestions = self.soul.propose(message["id"], message["content"])
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(self.soul.propose(message["id"], message["content"])[0]["id"],
                         suggestions[0]["id"])
        self.assertEqual(self.store.stats()["memories"], 0)
        self.assertEqual(len(self.soul.suggestions()), 1)
        reviewed = self.soul.review(suggestions[0]["id"], True)
        self.assertEqual(reviewed["state"], "approved")
        self.assertEqual(self.store.stats()["memories"], 1)
        item = self.store.find_memories()[0]
        self.assertEqual(item["source_type"], "message")
        self.assertEqual(item["source_ref"], str(message["id"]))
        self.assertEqual(self.soul.suggestions(), [])
        self.assertIn("Подтверждена", self.soul.journal_entries()[0]["detail"])
        with self.assertRaises(ValueError):
            self.soul.review(suggestions[0]["id"], True)

    def test_reject_and_no_pretend_facts(self):
        convo = self.store.create_conversation()
        msg = self.store.append_messages(convo["id"], [
            {"role": "user", "content": "А сколько стоит поезд?"}
        ])[0]
        self.assertFalse(self.soul.propose(msg["id"], msg["content"]))
        msg2 = self.store.append_messages(convo["id"], [
            {"role": "user", "content": "Я предпочитаю гулять после работы."}
        ])[0]
        candidates = self.soul.propose(msg2["id"], msg2["content"])
        self.soul.review(candidates[0]["id"], False)
        self.assertEqual(self.store.stats()["memories"], 0)

    def test_links_conflicts_and_retrieval(self):
        a = self.store.add_memory("preference", "Я люблю крепкий зелёный чай")
        b = self.store.add_memory("preference", "Я не люблю крепкий зелёный чай")
        links = self.soul.build_links(b["id"])
        self.assertTrue(any(x["relation"] == "possible_conflict" for x in links))
        self.assertEqual(len(self.soul.links(a["id"])), 1)
        results = self.soul.retrieve("зелёный чай")
        self.assertTrue(any(x["source_type"] == "manual" for x in results))
        self.store.delete_memory(a["id"])
        self.assertFalse(self.soul.links(a["id"]))

    def test_style_from_approved_preferences(self):
        self.store.add_memory("preference", "Говори короче и нежнее")
        self.store.add_memory("fact", "Цукиёра должна соблюдать честность")
        self.assertIn("Говори короче", self.soul.style())
        self.assertNotIn("должна соблюдать", self.soul.style())
        self.soul.journal("conversation_completed", "Первая беседа", "message", "88")
        self.soul.journal("conversation_completed", "Первая беседа", "message", "88")
        self.assertEqual(len(self.soul.journal_entries()), 1)


class CloudSSEProtocolTests(unittest.TestCase):
    def setUp(self):
        self.cloud = cloudru.CloudSession()
        self.cloud.set_key("FAKE_FOR_TEST")
        self.cloud._model = "fake/ok"
        self.cloud._models = ["fake/ok"]
        self.cloud._verified = True

    def response(self, include_done=True):
        chunks = [
            b'data: {"choices":[{"delta":{"content":"Привет"}}]}\\n\\n'.replace(b"\\n", b"\n"),
            b'data: {"choices":[{"delta":{"content":"! привет"}}]}\\n\\n'.replace(b"\\n", b"\n"),
        ]
        if include_done:
            chunks.append(b"data: [DONE]\n\n")
        class FakeResponse:
            headers = {"Content-Type": "text/event-stream; charset=utf-8"}
            closed = False
            def __init__(self, frames):
                self.data = iter(frames)
            def readline(self, limit):
                if self.closed:
                    return b""
                return next(self.data, b"")
            def close(self):
                self.closed = True
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
        return FakeResponse(chunks)

    def test_sse_frames_are_joined_and_stream_closed(self):
        response = self.response()
        class FakeOpener:
            def open(self, request, timeout):
                self.request = request
                return response
        opener = FakeOpener()
        with patch.object(cloudru, "build_opener", return_value=opener):
            tokens = list(self.cloud.stream("Характер Цукиёры", [
                {"role": "user", "content": "Привет"}], StreamCancellation()))
        self.assertEqual(tokens, ["Привет", "! привет"])
        self.assertTrue(response.closed)
        self.assertEqual(opener.request.full_url,
                         "https://foundation-models.api.cloud.ru/v1/chat/completions")
        self.assertIn(b'"stream": true', opener.request.data)

    def test_missing_done_does_not_commit_partial_reply(self):
        response = self.response(include_done=False)
        class FakeOpener:
            def open(self, request, timeout):
                return response
        with patch.object(cloudru, "build_opener", return_value=FakeOpener()):
            with self.assertRaises(cloudru.CloudError):
                list(self.cloud.stream("Характер", [{"role": "user", "content": "Привет"}],
                                       StreamCancellation()))


class StreamingApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "memory.sqlite3"
        launcher.init_database(self.db)
        self.server = launcher.SoulServer(("127.0.0.1", 0), launcher.load_personality(), self.db)
        self.server.cloud.set_key("TEST_NOT_REAL")
        self.server.cloud._verified = True
        self.server.cloud._models = ["fake/ok"]
        self.server.cloud._model = "fake/ok"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = build_opener(ProxyHandler({}))

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, route: str, obj: dict):
        req = Request(self.url + route,
                      data=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                      method="POST",
                      headers={"Content-Type": "application/json", "Origin": self.url})
        return self.opener.open(req, timeout=10)

    def test_stream_persists_only_finished_answer_and_suggestions(self):
        with patch.object(cloudru.CloudSession, "stream", stream_stub):
            with self.request("/api/chat/stream", {
                "content": "Я люблю крепкий чай", "client_message_id": "stream-1",
            }) as response:
                self.assertIn("text/event-stream", response.headers["Content-Type"])
                events = response.read().decode("utf-8")
        self.assertIn("event: token", events)
        self.assertIn("event: done", events)
        self.assertIn("Привет", events)
        history = MemoryStore(self.db).list_conversations()
        self.assertEqual(len(history), 1)
        msgs = MemoryStore(self.db).list_messages(history[0]["id"])
        self.assertEqual([x["role"] for x in msgs], ["user", "assistant"])
        self.assertEqual(len(SoulGrowth(self.db).suggestions()), 1)
        self.assertEqual(MemoryStore(self.db).stats()["memories"], 0)
        with patch.object(cloudru.CloudSession, "stream", side_effect=AssertionError("billed twice")):
            with self.request("/api/chat/stream", {
                "content": "Я люблю крепкий чай", "client_message_id": "stream-1",
                "conversation_id": history[0]["id"],
            }) as response:
                events = response.read().decode("utf-8")
                self.assertIn('"cached": true', events)

    def test_cancel_does_not_store_partial_reply(self):
        def canceled(self, prompt, history, cancellation):
            yield "Начало"
            cancellation.set()
            yield " ДАЛЬШЕ НЕ ДОЛЖНО ПОПАСТЬ"
        with patch.object(cloudru.CloudSession, "stream", canceled):
            with self.request("/api/chat/stream", {
                "content": "Сегодня я начал новое дело", "client_message_id": "stop-1",
            }) as response:
                output = response.read().decode("utf-8")
        self.assertIn("event: stopped", output)
        self.assertNotIn("event: done", output)
        self.assertNotIn("ДАЛЬШЕ", output)
        self.assertEqual(MemoryStore(self.db).stats()["messages"], 1)

    def test_live_diagnostic_errors_are_explicit(self):
        with patch.object(cloudru.CloudSession, "diagnostic", side_effect=cloudru.CloudError("Cloud.ru недоступен", 502)):
            with self.assertRaises(HTTPError) as caught:
                self.request("/api/cloud/diagnose", {}).read()
            self.assertEqual(caught.exception.code, 502)

    def test_semantic_retrieval_api_uses_model_ids(self):
        store = MemoryStore(self.db)
        x = store.add_memory("fact", "Наш гараж обслуживает автопарк")
        store.add_memory("fact", "Гараж проводит ремонт автомобилей")
        with patch.object(cloudru.CloudSession, "rerank", return_value=[x["id"]]) as rank:
            with self.request("/api/soul/search", {"query": "гараж", "semantic": True}) as response:
                result = json.load(response)
        self.assertTrue(result["semantic"])
        self.assertEqual(result["items"][0]["id"], x["id"])

    def test_manual_review_endpoint(self):
        convo = MemoryStore(self.db).create_conversation()
        m = MemoryStore(self.db).append_messages(convo["id"], [
            {"role": "user", "content": "Я люблю слушать музыку"}
        ])[0]
        suggestions = SoulGrowth(self.db).propose(m["id"], m["content"])
        with self.request("/api/soul/review", {"id": suggestions[0]["id"], "approve": True}) as response:
            self.assertEqual(json.load(response)["state"], "approved")
        self.assertEqual(MemoryStore(self.db).stats()["memories"], 1)


if __name__ == "__main__":
    unittest.main()
