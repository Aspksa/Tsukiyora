"""Tsukiyora v0.7 regression tests: local data integrity and user control.

No external network calls, paid API keys, browser installs or test secrets.
"""
from __future__ import annotations
from contextlib import closing
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
from tsukiyora.cognition.cognitive_core import CognitiveCore
from tsukiyora.memory.layered_memory import LayeredMemory
from tsukiyora.runtime.maintenance import Maintenance
from tsukiyora.memory.memory_store import MemoryStore, _fingerprint
from tsukiyora.cognition.reasoning import ReasoningAudit
from tsukiyora.runtime.reliability import Reliability


class V07StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.db = self.folder / "data" / "tsukiyora.sqlite3"
        launcher.init_database(self.db)
        self.store = MemoryStore(self.db)
        self.core = CognitiveCore(self.db)

    def test_layers_are_repairable_and_source_traced(self):
        a = self.store.add_memory("event", "Сегодня закончил проект")
        b = self.store.add_memory("biography", "Работаю инженером")
        c = self.store.add_memory("fact", "В проекте используется SQLite")
        layers = LayeredMemory(self.db)
        first = layers.repair()
        self.assertGreaterEqual(first["examined"], 3)
        self.assertGreaterEqual(first["updated"], 3)
        self.assertEqual(layers.repair()["updated"], 0) 
        self.assertEqual([x["id"] for x in layers.inspect("episodic")["items"]], [a["id"]])
        self.assertEqual([x["id"] for x in layers.inspect("semantic")["items"]], [c["id"]])
        self.assertEqual([x["id"] for x in layers.inspect("autobiographical")["items"]], [b["id"]])
        with closing(self.store.connect()) as db, db:
            lo, hi = sorted((a["id"], b["id"]))
            db.execute(
                "INSERT INTO memory_links(source_id,target_id,relation) VALUES(?,?,'related')",
                (lo,hi),
            )
        self.assertEqual(len(layers.inspect("all")["links"]), 1)
        link = layers.inspect("episodic")["links"][0]
        self.assertNotEqual(link["source_layer"], link["target_layer"])
        self.assertFalse(layers.confidence(b["id"])["truth_verified"])
        previous = layers.inspect("semantic")["items"][0]["revision_id"]
        self.store.correct_memory(c["id"], "В проекте применяется SQLite", "Уточнено")
        changed = layers.inspect("semantic")["items"][0]
        self.assertGreater(changed["revision_id"], previous)
        self.store.delete_memory(c["id"])
        self.assertEqual(layers.inspect("semantic")["items"], [])
        self.assertEqual(layers.inspect("all")["count"], 2)

    def test_short_term_is_local_and_not_promoted(self):
        session = self.store.create_conversation("Начало")
        self.store.append_messages(session["id"], [
            {"role": "user", "content": "Привет", "client_message_id": "msg1"},
            {"role": "assistant", "content": "Привет, хозяин", "client_message_id": "msg2"},
        ])
        layer = LayeredMemory(self.db).inspect("short_term", conversation_id=session["id"])
        self.assertEqual([x["role"] for x in layer["items"]], ["assistant", "user"])
        self.assertEqual(self.store.stats()["memories"], 0)

    def test_verified_backups_rotation_and_manual_restore(self):
        first = self.store.add_memory("fact", "Первоначальная версия")
        reliability = Reliability(self.db, ROOT)
        self.assertFalse(reliability.local_checks()["cloud_live_tested"])
        backup = reliability.ensure_snapshot(force=True)
        self.assertTrue(backup["created"])
        self.assertTrue((self.folder / "data" / "backups" / backup["name"]).exists())
        self.assertFalse(reliability.ensure_snapshot()["created"])
        self.store.add_memory("event", "Временная запись после копии")
        with self.assertRaises(ValueError):
            reliability.restore_offline(backup["name"], confirm=False)
        recovered = reliability.restore_offline(backup["name"], confirm=True)
        self.assertEqual(recovered["restored"], backup["name"])
        self.assertTrue((self.folder / "data" / "backups" / recovered["safety_copy"]).exists())
        self.assertEqual(MemoryStore(self.db).stats()["memories"], 1)
        self.assertEqual(MemoryStore(self.db).find_memories()[0]["id"], first["id"])

    def test_recovery_can_preserve_and_replace_corrupt_current_database(self):
        original = self.store.add_memory("fact", "Из резервной копии")
        recovery = Reliability(self.db, ROOT)
        saved = recovery.ensure_snapshot(force=True)
        # Simulate offline disk corruption only after every database handle is closed.
        self.db.write_bytes(b"damaged local database bytes")
        restored = recovery.restore_offline(saved["name"], confirm=True)
        self.assertTrue(restored["safety_copy"].startswith("damaged-"))
        self.assertTrue((recovery.folder / restored["safety_copy"]).exists())
        self.assertEqual(MemoryStore(self.db).find_memories()[0]["id"], original["id"])

    def test_incompatible_backup_is_rejected_without_touching_database(self):
        self.store.add_memory("fact", "Keep me")
        recovery = Reliability(self.db, ROOT)
        recovery.folder.mkdir(parents=True, exist_ok=True)
        fake = recovery.folder / "memory-corrupt.sqlite3"
        fake.write_bytes(b"not a database")
        with self.assertRaises((sqlite3.DatabaseError, ValueError)):
            recovery.restore_offline(fake.name, confirm=True)
        self.assertEqual(self.store.stats()["memories"], 1)

    def test_source_audit_is_not_truth_verification(self):
        fact = self.store.add_memory("fact", "Лексус требует замену лампы")
        session = self.store.create_conversation()
        answer = f"Источник есть [Память #{fact['id']}] и ложный [Память #99999]"
        assistant = self.store.append_messages(session["id"], [
            {"role": "assistant", "content": answer, "client_message_id": "reply1"}
        ])[0]
        audit = ReasoningAudit(self.db).verify(assistant["id"], answer)
        self.assertEqual(audit["state"], "needs_review")
        self.assertEqual(audit["valid_citations"], [fact["id"]])
        self.assertEqual(audit["missing_citations"], [99999])
        self.assertFalse(audit["truth_verified"])
        with self.assertRaises(ValueError):
            ReasoningAudit(self.db).verify(assistant["id"], "Other answer")

    def test_maintenance_queue_yields_to_chat_and_estimates_tokens(self):
        work = Maintenance(self.db)
        lock = threading.Lock()
        lock.acquire()
        try:
            self.assertEqual(work.tick(self.core, chat_lock=lock)["status"], "deferred")
        finally:
            lock.release()
        result = work.tick(self.core, chat_lock=lock)
        self.assertEqual(result["status"], "ok")
        self.assertIn(result["kind"], ("backup","integrity","layer_index",
                                      "quality","vector_repair","lessons",
                                      "precision_index","academy_cycle",
                                      "persona_continuity","backup_verify",
                                      "academy_curriculum","persona_arc",
                                      "document_index","source_audit",
                                      "atlas_verify","academy_rollback","wal_check"))
        self.assertEqual(work.pending()["usage"]["answered_requests"], 0)
        session = self.store.create_conversation()
        reply = self.store.append_messages(session["id"], [
            {"role": "assistant", "content": "Ответ", "client_message_id": "assistant-1"}
        ])[0]
        work.record_answer(reply["id"], "model/example", 350, 70)
        work.record_answer(reply["id"], "model/example", 350, 70)
        usage = work.pending()["usage"]
        self.assertEqual(usage["answered_requests"], 1)
        self.assertFalse(usage["provider_billed_tokens_known"])
        self.assertEqual(usage["approx_prompt_tokens"], 88)

    def test_large_graph_really_exposes_thousands_and_clustering_metadata(self):
        with closing(self.store.connect()) as db, db:
            for i in range(1100):
                content = "Событие из журнала " + str(i)
                db.execute(
                    "INSERT INTO memory_items(kind,content,normalized_text,fingerprint,"
                    "source_type,verification_state) VALUES(?,?,?,?,?,?)",
                    ("event", content, content.casefold(),
                     _fingerprint("event", content), "manual", "user_reported"),
                )
        full = self.core.graph()
        self.assertEqual(len(full["nodes"]), 1100)
        self.assertEqual(full["total"], 1100)
        self.assertEqual(full["clusters"]["event"], 1100)
        self.assertFalse(full["truncated"])
        cut = self.core.graph(node_limit=120)
        self.assertTrue(cut["truncated"])
        self.assertEqual(len(cut["nodes"]), 120)


class V07HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "data" / "tsukiyora.sqlite3"
        launcher.init_database(self.db)
        self.server = launcher.SoulServer(("127.0.0.1", 0), launcher.load_personality(), self.db)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.opener = build_opener(ProxyHandler({}))

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self, path):
        with self.opener.open(self.url + path, timeout=5) as response:
            return json.load(response)

    def test_local_diagnostics_layers_and_queue_are_safe(self):
        local = self.get("/api/system/diagnostics")
        self.assertFalse(local["cloud_live_tested"])
        self.assertIn("sqlite", local["checks"])
        self.assertIn("python", local["checks"])
        q = self.get("/api/system/workload")
        self.assertGreaterEqual(len(q["jobs"]), 6)
        layers = self.get("/api/soul/layers?layer=all")
        self.assertEqual(layers["items"], [])
        self.assertEqual(self.get("/api/soul/audits")["items"], [])
        self.assertEqual(self.get("/api/system/backups")["items"], [])

    def test_html_keeps_single_page_and_expected_interactions(self):
        html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "ui" / "app.ts").read_text(encoding="utf-8")
        for item in ("system-diagnostics", "run-system-check", "memory-layers",
                     "layer-selector", "quality-results", "stop-message",
                     "cloud-diagnose", "soul-canvas"):
            self.assertIn('id="' + item + '"', html)
        for api in ("/api/system/diagnostics", "/api/soul/layers",
                    "/api/system/backup", "/api/cloud/checkup"):
            self.assertIn(api, script)
        self.assertNotIn("localStorage.setItem", script)


if __name__ == "__main__":
    unittest.main()
