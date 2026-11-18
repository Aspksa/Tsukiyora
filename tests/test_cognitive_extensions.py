"""Focused integration and safety tests for the three cognitive extensions."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import launcher
from memory_store import MemoryStore
from cognitive_core import CognitiveCore


class CognitiveExtensionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memory.sqlite3"
        launcher.init_database(self.path)
        self.store = MemoryStore(self.path)
        self.core = CognitiveCore(self.path)

    def test_retrospective_uses_only_real_revisions_and_links(self):
        memory = self.store.add_memory(
            "fact", "Проект Рубин первоначально согласован на сентябрь")
        self.store.correct_memory(
            memory["id"], "Проект Рубин перенесён на октябрь",
            reason="Пользователь исправил срок")
        result = self.core.reflection.inspect("Проект Рубин", limit=4)
        self.assertEqual(result["cloud_requests"], 0)
        self.assertFalse(result["truth_verified"])
        self.assertTrue(result["no_causal_claims_without_evidence"])
        record = next(x for x in result["items"] if x["id"] == memory["id"])
        self.assertEqual(record["content"], "Проект Рубин перенесён на октябрь")
        self.assertTrue(any(x["operation"] == "correct" for x in record["revisions"]))
        self.assertTrue(any(x["reason"] == "Пользователь исправил срок"
                            for x in record["revisions"]))
        with self.assertRaises(ValueError):
            self.core.reflection.inspect("test", limit=100)

    def test_skills_must_be_positive_confirmed_and_can_be_revoked(self):
        convo = self.store.create_conversation()
        msg = self.store.append_messages(convo["id"], [
            {"role": "assistant", "content": "Полезный проверяемый ответ"}
        ])[0]
        note = "Указывай различия между гипотезами и подтверждёнными фактами."
        rated = self.core.feedback(msg["id"], True, note)
        self.assertEqual(rated["skill_candidate"]["state"], "pending")
        candidate = rated["skill_candidate"]["candidate_id"]
        self.assertNotIn(note, self.core.skills.guidance())
        with self.assertRaises(ValueError):
            self.core.skills.decide(candidate, approve=True)
        self.core.skills.decide(candidate, approve=True, confirmed=True)
        self.assertIn(note, self.core.skills.guidance())
        self.core.feedback(msg["id"], False, "Изменил оценку")
        self.assertNotIn(note, self.core.skills.guidance())
        self.assertEqual(self.core.skills.list()["items"][0]["state"], "disabled")
        with self.assertRaises(ValueError):
            self.core.skills.decide(candidate, approve=True, confirmed=True)

    def test_evolution_is_manual_ab_measured_and_reversible(self):
        evo = self.core.evolution
        with self.assertRaises(ValueError):
            evo.propose([])
        trial = evo.propose("causal_steps")["trial_id"]
        self.assertEqual(evo.propose("causal_steps")["trial_id"], trial)
        with self.assertRaises(ValueError):
            evo.start(trial)
        evo.start(trial, confirmed=True)
        with self.assertRaises(ValueError):
            evo.decide(trial, activate=True, confirmed=True)
        counts = {"baseline": 0, "candidate": 0}
        for i in range(120):
            if min(counts.values()) >= 8:
                break
            convo = self.store.create_conversation()
            advice = evo.guidance_for_conversation(convo["id"])
            with closing(self.store.connect()) as db:
                arm = db.execute(
                    "SELECT arm FROM evolution_assignments WHERE conversation_id=?",
                    (convo["id"],)
                ).fetchone()["arm"]
            self.assertEqual(bool(advice), arm == "candidate")
            if counts[arm] >= 8:
                continue
            msg = self.store.append_messages(convo["id"], [
                {"role":"assistant","content":"Экспериментальный ответ " + str(i)}
            ])[0]
            helpful = arm == "candidate"
            with closing(self.store.connect()) as db, db:
                db.execute("INSERT INTO cognitive_feedback(message_id,helpful) VALUES(?,?)",
                           (msg["id"],int(helpful)))
            self.assertTrue(evo.record(msg["id"], helpful)["measured"])
            counts[arm] += 1
        self.assertEqual(counts, {"baseline":8,"candidate":8})
        tested = next(t for t in evo.report()["trials"] if t["id"] == trial)
        self.assertEqual(tested["gain"], 1.0)
        self.assertTrue(tested["ready_for_review"])
        with self.assertRaises(ValueError):
            evo.decide(trial, activate=True)
        self.assertEqual(evo.decide(trial, activate=True, confirmed=True)["state"], "active")
        new_conversation = self.store.create_conversation()
        self.assertIn("причин", evo.guidance_for_conversation(new_conversation["id"]))
        self.assertFalse(evo.record(msg["id"], helpful)["measured"])
        evo.decide(trial, activate=False, confirmed=True)
        self.assertEqual(evo.guidance_for_conversation(new_conversation["id"]), "")


if __name__ == "__main__":
    unittest.main()
