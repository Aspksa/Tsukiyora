"""Memory behaviour, privacy and real HTTP API tests (no third-party packages)."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import launcher
from tsukiyora.memory.memory_store import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private.sqlite3"
        launcher.init_database(self.path)
        self.store = MemoryStore(self.path)

    def test_manual_memory_search_dedup_revisions_and_soft_delete(self):
        saved = self.store.add_memory("preference", "Люблю горячий чай")
        self.assertEqual(saved["source_type"], "manual")
        self.assertEqual(saved["verification_state"], "user_reported")
        self.assertEqual(self.store.add_memory("preference", "  люблю   горячий чай  ")["id"], saved["id"])
        self.assertEqual(self.store.stats()["memories"], 1)
        self.assertEqual(self.store.find_memories("ЧАЙ")[0]["id"], saved["id"])
        self.assertFalse(self.store.find_memories("%"))
        self.assertFalse(self.store.find_memories("_"))
        updated = self.store.correct_memory(saved["id"], "Люблю зелёный чай", "Уточнено")
        self.assertEqual(updated["content"], "Люблю зелёный чай")
        self.assertFalse(self.store.find_memories("горячий"))
        self.assertEqual(self.store.find_memories("зелёный")[0]["id"], saved["id"])
        self.store.delete_memory(saved["id"])
        self.assertEqual(self.store.stats()["memories"], 0)
        with closing(sqlite3.connect(self.path)) as db:
            revisions = db.execute("SELECT operation FROM memory_revisions ORDER BY id").fetchall()
            self.assertEqual([r[0] for r in revisions], ["create", "correct", "delete"])
        self.assertEqual(self.store.add_memory("preference", "Люблю зелёный чай")["status"], "active")

    def test_conversations_persist_and_idempotency(self):
        item = self.store.create_conversation("Первый разговор")
        messages = [
            {"role": "user", "content": "Привет", "client_message_id": "user-1"},
            {"role": "assistant", "content": "Здравствуйте", "client_message_id": "reply-1"},
        ]
        self.assertEqual(len(self.store.append_messages(item["id"], messages)), 2)
        self.store.append_messages(item["id"], messages)
        self.assertEqual(self.store.stats()["messages"], 2)
        with self.assertRaisesRegex(ValueError, "different content"):
            self.store.append_messages(item["id"], [
                {"role": "user", "content": "Изменённый текст", "client_message_id": "user-1"}])
        with self.assertRaises(ValueError):
            self.store.append_messages(item["id"], [
                {"role": "user", "content": "Третья запись", "client_message_id": "user-3"},
                {"role": "alien", "content": "Некорректная роль"}])
        self.assertEqual(self.store.stats()["messages"], 2)
        self.assertEqual([m["content"] for m in MemoryStore(self.path).list_messages(item["id"])],
                         ["Привет", "Здравствуйте"])
        self.assertEqual(len(self.store.list_conversations()), 1)

    def test_backup_consistent_with_wal(self):
        self.store.add_memory("biography", "Первая важная запись")
        self.store.append_messages(self.store.create_conversation()["id"], [
            {"role": "user", "content": "Не терять историю!"}])
        saved = self.store.backup()
        self.assertTrue(saved.is_file())
        with closing(sqlite3.connect(saved)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

    def test_input_validation(self):
        for text in ["", " ", "x" * 8001]:
            with self.assertRaises(ValueError):
                self.store.add_memory("fact", text)
        with self.assertRaises(ValueError):
            self.store.add_memory("invalid", "value")
        with self.assertRaises(ValueError):
            self.store.correct_memory(12345, "Нет")
        with self.assertRaises(ValueError):
            self.store.list_messages("missing", limit=0)


class MemoryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "local.sqlite3"
        launcher.init_database(self.path)
        self.server = launcher.SoulServer(("127.0.0.1", 0), launcher.load_personality(), self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.opener = build_opener(ProxyHandler({}))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, method="GET", body=None, headers=None):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = Request(
            self.url + path, data=payload, method=method,
            headers=headers or ({"Content-Type": "application/json", "Origin": self.url} if method != "GET" else {}),
        )
        with self.opener.open(request, timeout=5) as response:
            return response.status, json.load(response)

    def test_memory_api_full_cycle(self):
        status, data = self.request("/api/memory", "POST", {"kind": "event", "content": "Появилась новая запись"})
        self.assertEqual(status, 201)
        memory_id = data["item"]["id"]
        _, found = self.request("/api/memory?" + urlencode({"query": "новая"}))
        self.assertEqual(found["items"][0]["id"], memory_id)
        _, changed = self.request(f"/api/memory/{memory_id}", "PUT", {"content": "Исправленная запись"})
        self.assertEqual(changed["item"]["content"], "Исправленная запись")
        _, conversation = self.request("/api/memory/conversations", "POST", {"title": "Встреча"})
        conversation_id = conversation["conversation"]["id"]
        _, messages = self.request("/api/memory/messages", "POST", {
            "conversation_id": conversation_id,
            "messages": [{"role": "user", "content": "Привет!"}],
        })
        self.assertEqual(len(messages["messages"]), 1)
        _, history = self.request("/api/memory/messages?" + urlencode({"conversation_id": conversation_id}))
        self.assertEqual(history["messages"][0]["content"], "Привет!")
        _, backup = self.request("/api/memory/backup", "POST", {})
        self.assertTrue((self.path.parent / "backups" / backup["filename"]).exists())
        _, result = self.request(f"/api/memory/{memory_id}", "DELETE")
        self.assertTrue(result["deleted"])
        _, status = self.request("/api/memory/status")
        self.assertEqual(status["memories"], 0)
        self.assertEqual(status["messages"], 1)

    def test_reject_other_origins_and_content_types(self):
        wrong = Request(
            self.url + "/api/memory", method="POST",
            data=b'{"kind":"fact","content":"unsafe"}',
            headers={"Content-Type": "application/json", "Origin": "https://foreign.example"},
        )
        with self.assertRaises(HTTPError) as ctx:
            self.opener.open(wrong, timeout=5)
        self.assertEqual(ctx.exception.code, 403)

        wrong_type = Request(
            self.url + "/api/memory", method="POST",
            data=b'{"kind":"fact","content":"unsafe"}',
            headers={"Content-Type": "text/plain"},
        )
        with self.assertRaises(HTTPError) as ctx:
            self.opener.open(wrong_type, timeout=5)
        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(MemoryStore(self.path).stats()["memories"], 0)


if __name__ == "__main__":
    unittest.main()
