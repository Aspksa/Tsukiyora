"""Regression tests for user-selected dialogue/growth improvements 2, 3, 4, 5.

Entirely local: all evaluations reuse already saved SQLite evidence.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import launcher
from tsukiyora.cognition.cognitive_core import CognitiveCore
from tsukiyora.memory.memory_store import MemoryStore


class QualitySelectedTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "data" / "memory.sqlite3"
        launcher.init_database(self.path)
        self.core = CognitiveCore(self.path)
        self.store = MemoryStore(self.path)

    def test_bounded_topical_cross_chat_recall_with_true_ids(self):
        first = self.store.create_conversation("Первый разговор")
        second = self.store.create_conversation("Второй разговор")
        specific = self.store.append_messages(first["id"], [{
            "role": "user",
            "content": "Мы решили хранить память Tsukiyora только в SQLite на SSD."
        }])[0]
        for i in range(25):
            self.store.append_messages(first["id"], [{
                "role": "user", "content": "Другая тема номер " + str(i)
            }])
        related = self.store.append_messages(second["id"], [{
            "role": "user",
            "content": "Мне важно резервное копирование памяти SQLite."
        }])[0]
        current = self.store.append_messages(second["id"], [{
            "role": "user", "content": "Расскажи про память SQLite."
        }])[0]
        recent = self.store.list_messages(second["id"], limit=18)
        with patch("tsukiyora.cloud.cloudru._request",
                   side_effect=AssertionError("Unexpected cloud call")):
            result = self.core.topic_recall.relevant(
                "Наша память SQLite резервное копирование", second["id"], recent)
            prompt = self.core.topic_recall.prompt(
                "Наша память SQLite резервное копирование", second["id"], recent)
        ids = {x["message_id"] for x in result["items"]}
        self.assertIn(specific["id"], ids)
        self.assertNotIn(current["id"], ids)
        self.assertNotIn(related["id"], ids)  # Recent chat context already covers it
        self.assertIn("Сообщение #" + str(specific["id"]), prompt)
        self.assertIn(first["id"], prompt)
        self.assertIn("непроверенные данные", prompt)
        self.assertTrue(result["cross_dialogue_is_explicit"])
        self.assertEqual(result["cloud_calls"], 0)
        self.assertLessEqual(len(result["items"]), 4)

    def test_topic_recall_requires_real_scope_and_specific_topic(self):
        first = self.store.create_conversation("Проверка")
        self.assertEqual(
            self.core.topic_recall.relevant("Привет", first["id"], [])["items"], [])
        with self.assertRaises(ValueError):
            self.core.topic_recall.relevant("SQLite", "unknown", [])
        with self.assertRaises(ValueError):
            self.core.topic_recall.relevant("SQLite", first["id"], [], max_chars=9999)

    def test_quality_reports_real_observations_separate_from_attested_tests(self):
        baseline = self.core.growth_quality.status(self.core)
        self.assertEqual(baseline["ratings"]["total"], 0)
        self.assertEqual(baseline["extra_cloud_calls"], 0)
        dialogue = self.store.create_conversation("Проверка полезности")
        answer = self.store.append_messages(dialogue["id"], [{
            "role": "assistant", "content": "Сохранённый ответ с реальным ID"
        }])[0]
        with closing(self.store.connect()) as db, db:
            cur = db.execute(
                "INSERT INTO forge_skills(domain,recipe,fingerprint,origin,state) "
                "VALUES('logic','[]','test-unique-quality-fp','manual','shadow')"
            )
            skill_id = cur.lastrowid
            db.execute(
                "INSERT INTO cognitive_feedback(message_id,helpful,note) "
                "VALUES(?,1,'Проверенный ответ')", (answer["id"],)
            )
            db.execute(
                "INSERT INTO forge_applied(message_id,skill_id,helpful) "
                "VALUES(?,?,1)", (answer["id"],skill_id)
            )
        report = self.core.growth_quality.status(self.core)
        self.assertEqual(report["ratings"]["positive"], 1)
        skill = next(x for x in report["skills"] if x["skill_id"] == skill_id)
        self.assertEqual(skill["real_answer_ratings"], 1)
        self.assertEqual(skill["real_answer_positive"], 1)
        self.assertEqual(skill["attested_cases"], 0)
        self.assertFalse(skill["v7_evidence_ready"])
        self.assertFalse(skill["causal_improvement_proven"])
        self.assertEqual(skill["observed_signal"], "insufficient")
        self.assertEqual(report["changes_or_approvals_performed"], 0)

    def test_initiative_uses_conversation_topic_and_respects_v8_permissions(self):
        self.core.living8.discover(self.core)
        self.core.living8.work(self.core)
        convo = self.store.create_conversation("Проверка контекста")
        self.store.append_messages(convo["id"], [{
            "role": "user", "content": "Давай поговорим про поездку на море."
        }])
        self.assertIsNone(self.core.dialogue9.initiative(
            self.core, convo["id"])["item"])
        self.store.append_messages(convo["id"], [{
            "role": "user", "content": "Какой прогресс развития и локальных целей?"
        }])
        response = self.core.dialogue9.initiative(self.core, convo["id"])
        self.assertIsNotNone(response["item"])
        self.assertEqual(response["item"]["category"], "daily")
        self.assertEqual(response["item"]["relevance"], "recent_chat_topic")
        self.core.living8.permission("proactive_chat", False, confirmed=True)
        self.assertIsNone(self.core.dialogue9.initiative(
            self.core, convo["id"])["item"])

    def test_api_route_and_ui_markers_present(self):
        html = (launcher.ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        js = (launcher.ROOT / "ui" / "app.ts").read_text(encoding="utf-8")
        py = (launcher.ROOT / "launcher.py").read_text(encoding="utf-8")
        self.assertIn('id="growth-quality"', html)
        self.assertIn('id="growth-quality-skills"', html)
        self.assertIn("/api/soul/growth-quality", py)
        self.assertIn("/api/soul/growth-quality", js)
        self.assertIn("topic_recall.prompt", py)
        self.assertIn("conversation_id:activeSession", js)


if __name__ == "__main__":
    unittest.main()
