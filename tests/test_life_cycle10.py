"""Safety/behavior regression tests for Tsukiyora 10 Unified Cognitive Life Cycle.

No live provider, new dependency, new worker, persona edits or paid experiments.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import build_opener, ProxyHandler

import launcher
from tsukiyora.cognition.cognitive_core import CognitiveCore
from tsukiyora.memory.memory_store import MemoryStore


class UnifiedLifeCycle10Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "data" / "memory.sqlite3"
        launcher.init_database(self.path)
        self.core = CognitiveCore(self.path)
        self.store = MemoryStore(self.path)

    def test_one_existing_queue_and_no_parallel_or_paid_worker(self):
        jobs = self.core.scheduler.pending()["jobs"]
        self.assertEqual(len([j for j in jobs if j["kind"] == "cog10_audit"]), 1)
        self.assertEqual(self.core.life10.status(self.core)["additional_paid_requests"], 0)
        self.assertTrue(self.core.life10.status(self.core)["systems"]["coordination"]["one_worker"])
        self.assertFalse((self.path.parent / "life10.sqlite3").exists())
        self.assertEqual(self.core.living8.settings()["autonomy_level"], 3)

    def test_intent_is_a_cautious_local_hint_not_a_claim_of_emotion(self):
        engine = self.core.life10
        self.assertEqual(engine.intent("Реализуй улучшение GitHub")["kind"], "task")
        self.assertEqual(engine.intent("Объясни, почему это работает")["kind"], "learning")
        self.assertEqual(engine.intent("Мне очень грустно")["kind"], "support")
        self.assertEqual(engine.intent("Доброе утро")["kind"], "conversation")
        for query in ("", "x" * 8001, None):
            with self.assertRaises(ValueError):
                engine.intent(query)

    def test_shared_bounded_prompt_never_mixes_ids_or_duplicates_recent(self):
        a = self.store.create_conversation("Старый проект")
        b = self.store.create_conversation("Новый проект")
        old = self.store.append_messages(a["id"], [{
            "role": "user",
            "content": "Мы решили хранить архитектуру Tsukiyora SQLite память на SSD."
        }])[0]
        for i in range(24):
            self.store.append_messages(a["id"], [{
                "role": "assistant",
                "content": "Обычный ответ " + str(i)
            }])
        current = self.store.append_messages(b["id"], [{
            "role": "user",
            "content": "Помнишь архитектуру Tsukiyora SQLite память?"
        }])[0]
        recent = self.store.list_messages(b["id"], limit=18)
        prompt = self.core.life10.chat_context(
            self.core, current["content"], b["id"], recent, max_chars=1550)
        self.assertIn("Сообщение #" + str(old["id"]), prompt)
        self.assertIn("диалог " + a["id"], prompt)
        self.assertNotIn("Сообщение #" + str(current["id"]), prompt)
        self.assertIn("непроверенные цитаты", prompt)
        self.assertLessEqual(len(prompt), 1550)
        self.assertIn("естественный разговор", prompt)
        self.assertEqual(prompt, self.core.life10.chat_context(
            self.core, current["content"], b["id"], recent, max_chars=1550))
        with self.assertRaises(ValueError):
            self.core.life10.chat_context(self.core, "SQLite", b["id"], recent,
                                          max_chars=99999)
        with self.assertRaises(ValueError):
            self.core.life10.chat_context(self.core, "SQLite", b["id"],
                                          [{"id": "fake"}])

    def test_audit_is_day_idempotent_and_redacts_messages(self):
        conversation = self.store.create_conversation("Локальные отзывы")
        secret = "DO_NOT_SAVE_THIS_PERSONAL_TEXT_XYZ_5678"
        answers = self.store.append_messages(conversation["id"], [
            {"role": "assistant", "content": secret + str(i)} for i in range(3)
        ])
        with closing(self.store.connect()) as db, db:
            for answer in answers:
                db.execute("INSERT INTO cognitive_feedback(message_id,helpful,note) "
                           "VALUES(?,0,'Отрицательный отзыв')", (answer["id"],))
        result = self.core.life10.audit_once(self.core)
        self.assertFalse(result["already_recorded"])
        self.assertTrue(result["review_recommended"])
        self.assertEqual(result["signals"]["recent_negative"], 3)
        again = self.core.life10.audit_once(self.core)
        self.assertTrue(again["already_recorded"])
        self.assertEqual(result["signals"], again["signals"])
        with closing(self.store.connect()) as db:
            rows = db.execute(
                "SELECT event,source_type,source_ref,counters_json "
                "FROM soul8_journal WHERE event='life10_audit'"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_type"], "day")
        self.assertEqual(json.loads(rows[0]["counters_json"])["recent_negative"], 3)
        self.assertNotIn(secret, str(dict(rows[0])))
        self.assertFalse(result["writes_to_user_memory"])
        self.assertFalse(result["personality_modified"])

    def test_level_zero_pauses_audit_without_new_journal(self):
        self.core.living8.control(0, confirmed=True)
        result = self.core.life10.audit_once(self.core)
        self.assertTrue(result["paused"])
        with closing(self.store.connect()) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM soul8_journal WHERE event='life10_audit'"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_scheduler_runs_only_one_due_audit_and_survives_restart(self):
        with closing(self.store.connect()) as db, db:
            db.execute(
                "UPDATE maintenance_queue SET state='waiting',due_at=4102444800 "
                "WHERE kind<>'cog10_audit'"
            )
            db.execute(
                "UPDATE maintenance_queue SET state='pending',due_at=0 "
                "WHERE kind='cog10_audit'"
            )
        result = self.core.scheduler.tick(self.core)
        self.assertEqual(result["kind"], "cog10_audit")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["details"]["already_recorded"])
        launcher.init_database(self.path)
        again = self.core.life10.audit_once(self.core)
        self.assertTrue(again["already_recorded"])
        with closing(self.store.connect()) as db:
            n = db.execute(
                "SELECT COUNT(*) FROM soul8_journal WHERE event='life10_audit'"
            ).fetchone()[0]
        self.assertEqual(n, 1)

    def test_dashboard_actions_are_sourced_and_existing_sections_only(self):
        with closing(self.store.connect()) as db, db:
            db.execute(
                "INSERT INTO forge_skills(domain,recipe,fingerprint,origin,state) "
                "VALUES('logic','[]','life10-test-unique-skill','manual','shadow')"
            )
        data = self.core.life10.status(self.core)
        self.assertEqual(data["systems"]["learning"]["skill_candidates"], 1)
        self.assertEqual(data["actions"][0]["section"], "cognitive-v7")
        self.assertEqual(data["version"], "10.0")
        self.assertTrue(data["manual_approval_for_persona_memory_and_skills"])
        self.assertEqual(data["additional_paid_requests"], 0)
        self.assertIsNone(data["last_local_audit"])
        self.assertFalse(data["systems"]["personality"]["real_emotions"])

    def test_original_persona_file_is_unmodified_and_approval_gate_remains(self):
        original = (launcher.ROOT / "soul" / "personality.json").read_bytes()
        conversation = self.store.create_conversation("Учёба")
        self.store.append_messages(conversation["id"], [{
            "role": "user", "content": "Привет"
        }])
        self.core.life10.audit_once(self.core)
        self.core.life10.status(self.core)
        self.assertEqual(
            original, (launcher.ROOT / "soul" / "personality.json").read_bytes())
        self.assertEqual(
            self.core.living8.settings()["local_permissions"]["activate_skills"], False)


class UnifiedLifeCycleHttpTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "data" / "memory.sqlite3"
        launcher.init_database(path)
        self.server = launcher.SoulServer(
            ("127.0.0.1", 0), launcher.load_personality(), path)
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(),
                                worker.join(timeout=6)))
        self.base = "http://127.0.0.1:" + str(self.server.server_address[1])
        self.http = build_opener(ProxyHandler({}))

    def test_read_only_api_and_ui_wiring(self):
        with self.http.open(self.base + "/api/soul/life10", timeout=15) as response:
            data = json.load(response)
        self.assertEqual(data["version"], "10.0")
        self.assertTrue(data["systems"]["coordination"]["one_queue"])
        self.assertEqual(data["additional_paid_requests"], 0)
        self.assertFalse(data["systems"]["personality"]["real_emotions"])
        root = launcher.ROOT
        html = (root / "ui" / "index.html").read_text(encoding="utf-8")
        ts = (root / "ui" / "app.ts").read_text(encoding="utf-8")
        sync = (root / "launcher.py").read_text(encoding="utf-8")
        stream = (root / "tsukiyora" / "cloud" / "stream_runtime.py").read_text(
            encoding="utf-8")
        for name in ("life10", "life10-refresh", "life10-state", "life10-systems",
                     "life10-actions", "life10-evidence"):
            self.assertIn('id="' + name + '"', html)
        self.assertIn("/api/soul/life10", ts)
        self.assertIn("life10.chat_context", stream)
        self.assertIn("life10.chat_context", sync)
        self.assertIn('kind == "cog10_audit"', (
            root / "tsukiyora" / "runtime" / "maintenance.py"
        ).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
