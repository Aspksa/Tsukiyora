"""Tsukiyora 12.0 selected 19 features: local thought-notebook regression tests.

No LLM chain-of-thought access, subjective consciousness or new paid calls.
Tests use temporary SQLite; protected persona and real user data untouched.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

import launcher
from tsukiyora.cognition.cognitive_core import CognitiveCore
from tsukiyora.memory.memory_store import MemoryStore


class InnerWorld12Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "data" / "memory.sqlite3"
        launcher.init_database(self.path)
        self.core = CognitiveCore(self.path)
        self.store = MemoryStore(self.path)

    def fixtures(self):
        """Populate genuine previously supported records, not made-up goals."""
        private = "PRIVATE_TEXT_MUST_NOT_LEAK_IN_NOTES_2026"
        old = self.store.add_memory("event",
            "Мы согласовали архив данных и SQLite " + private)
        convo = self.store.create_conversation("Реальные локальные данные")
        user = self.store.append_messages(convo["id"], [{
            "role": "user",
            "content": "Запомни, что предпочитаю сохранять память в SQLite "
                       + private
        }])[0]
        assistant = self.store.append_messages(convo["id"], [{
            "role": "assistant", "content": "Неудачный пример " + private
        }])[0]
        self.core.growth.propose(user["id"], user["content"])
        with closing(self.store.connect()) as db, db:
            db.execute(
                "INSERT INTO cognitive_feedback(message_id,helpful,note) "
                "VALUES(?,0,'неверный ответ')", (assistant["id"],)
            )
            skill = db.execute(
                "INSERT INTO forge_skills(domain,recipe,fingerprint,origin,state)"
                " VALUES('logic','[]','inner12-shadow-unique','manual','shadow')"
            ).lastrowid
            question = db.execute(
                "INSERT INTO cognitive_next_questions(fingerprint,question,source_note)"
                " VALUES('inner12-question-unique','Как проверить очереди?','test')"
            ).lastrowid
            goal = db.execute(
                "INSERT INTO soul8_goals(fingerprint,category,source_ref,state,progress)"
                " VALUES('inner12-goal','daily','source-test','active',1)"
            ).lastrowid
            db.execute(
                "INSERT INTO cognitive_v5_reviews(issue_key,kind,memory_id)"
                " VALUES('inner12-conflict-key','possible_conflict',?)",
                (old["id"],)
            )
            db.execute(
                "INSERT INTO soul8_proposals(fingerprint,kind,target,source_id)"
                " VALUES('inner12-style-unique','style','clarity',0)"
            )
        return {"memory": old["id"], "feedback": assistant["id"],
                "skill": skill, "question": question, "goal": goal,
                "private": private}

    def test_selected_19_features_and_omitted_4(self):
        state = self.core.inner12.status(self.core)
        self.assertEqual(state["version"], "12.0")
        self.assertEqual(state["selected_features"],
            [1,2,3,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20])
        self.assertTrue(state["omitted_feature_4"])
        self.assertFalse(state["real_consciousness_proven"])
        self.assertFalse(state["automatic_memory_writes"])
        self.assertFalse(state["automatic_skill_activation"])
        self.assertEqual(state["runtime"]["new_paid_cloud_calls"], 0)
        self.assertTrue(state["runtime"]["chat_has_priority"])
        self.assertTrue(state["protected_persona_unchanged"])

    def test_source_linked_notes_and_no_raw_text_duplication(self):
        data = self.fixtures()
        with patch("tsukiyora.cloud.cloudru._request",
                   side_effect=AssertionError("No additional cloud")):
            run = self.core.inner12.cycle(self.core)
            self.assertLessEqual(run["new_notes"], 8)
            self.assertGreaterEqual(run["new_notes"], 5)
            self.assertEqual(run["cloud_calls"], 0)
        packet = self.core.inner12.notes(limit=20)
        self.assertTrue(packet["items"])
        kinds = {x["kind"] for x in packet["items"]}
        self.assertIn("reflect_feedback", kinds)
        self.assertIn("check_memory", kinds)
        self.assertIn("test_strategy", kinds)
        self.assertIn("study_question", kinds)
        self.assertIn("continue_goal", kinds)
        self.assertIn("continuity", kinds)
        for note in packet["items"]:
            self.assertGreater(note["source_id"], 0)
            self.assertTrue(note["source_current"])
            self.assertTrue(note["requires_user_review"])
            self.assertTrue(note["not_model_private_reasoning"])
        with closing(self.store.connect()) as db:
            sqlite_rows = [dict(r) for r in db.execute("SELECT * FROM soul12_notes")]
        self.assertNotIn(data["private"], str(sqlite_rows))
        self.assertNotIn(data["private"], json.dumps(packet))
        self.assertEqual(packet["cloud_calls"], 0)

    def test_dismiss_does_not_delete_source_and_never_recreates(self):
        data = self.fixtures()
        self.core.inner12.cycle(self.core)
        note = next(x for x in self.core.inner12.notes()["items"]
                    if x["kind"] == "reflect_feedback")
        with self.assertRaises(ValueError):
            self.core.inner12.dismiss(note["id"])
        result = self.core.inner12.dismiss(note["id"], confirmed=True)
        self.assertTrue(result["dismissed"])
        self.assertFalse(result["source_modified"])
        self.assertFalse(result["skill_approved"])
        self.core.inner12.cycle(self.core)
        self.assertNotIn(note["id"],
            [x["id"] for x in self.core.inner12.notes()["items"]])
        with closing(self.store.connect()) as db:
            original = db.execute(
                "SELECT helpful FROM cognitive_feedback WHERE message_id=?",
                (data["feedback"],)
            ).fetchone()
        self.assertEqual(original["helpful"], 0)

    def test_old_or_completed_evidence_is_not_presented_as_active_thought(self):
        data = self.fixtures()
        self.core.inner12.cycle(self.core)
        target = next(x for x in self.core.inner12.notes()["items"]
                      if x["kind"] == "study_question")
        with closing(self.store.connect()) as db, db:
            db.execute(
                "UPDATE cognitive_next_questions SET state='dismissed' WHERE id=?",
                (data["question"],)
            )
        self.assertNotIn(target["id"],
            [x["id"] for x in self.core.inner12.notes()["items"]])
        evidence = self.core.inner12.inspect(self.core, target["id"])
        self.assertFalse(evidence["source_current"])
        self.assertFalse(evidence["checks"]["source_exists_and_still_requires_attention"])

    def test_local_shadow_strategy_inspection_is_not_independent_llm_test(self):
        self.fixtures()
        self.core.inner12.cycle(self.core)
        note = next(x for x in self.core.inner12.notes()["items"]
                    if x["kind"] == "test_strategy")
        result = self.core.inner12.inspect(self.core, note["id"])
        self.assertEqual(result["checks"]["control_cases"], 0)
        self.assertFalse(result["checks"]["reliable_llm_test"])
        self.assertTrue(result["not_a_real_model_simulation"])
        self.assertFalse(result["new_strategy_activated"])

    def test_daily_idempotence_and_restart_with_same_sqlite_queue(self):
        self.fixtures()
        with closing(self.store.connect()) as db, db:
            db.execute(
                "UPDATE maintenance_queue SET state='waiting',due_at=4102444800 "
                "WHERE kind<>'cog12_reflect'"
            )
            db.execute(
                "UPDATE maintenance_queue SET state='pending',due_at=0 "
                "WHERE kind='cog12_reflect'"
            )
        job = self.core.scheduler.tick(self.core)
        self.assertEqual(job["status"], "ok")
        self.assertEqual(job["kind"], "cog12_reflect")
        self.assertGreater(job["details"]["new_notes"], 0)
        repeated = self.core.inner12.cycle(self.core)
        self.assertEqual(repeated["new_notes"], 0)
        self.assertTrue(repeated["daily_already_recorded"])
        launcher.init_database(self.path)
        after = self.core.inner12.cycle(self.core)
        self.assertEqual(after["new_notes"], 0)
        with closing(self.store.connect()) as db:
            journal = db.execute(
                "SELECT COUNT(*) FROM soul8_journal "
                "WHERE event='inner12_daily'"
            ).fetchone()[0]
            count = db.execute(
                "SELECT COUNT(*) FROM maintenance_queue "
                "WHERE kind='cog12_reflect'"
            ).fetchone()[0]
        self.assertEqual(journal, 1)
        self.assertEqual(count, 1)

    def test_autonomy_zero_and_local_practice_false_pause(self):
        self.fixtures()
        self.core.living8.control(0, confirmed=True)
        paused = self.core.inner12.cycle(self.core)
        self.assertTrue(paused["paused"])
        self.assertEqual(paused["new_notes"], 0)
        self.core.living8.control(3, confirmed=True)
        self.core.living8.permission("local_practice",False,confirmed=True)
        paused = self.core.inner12.cycle(self.core)
        self.assertTrue(paused["paused"])
        with closing(self.store.connect()) as db:
            rows = db.execute("SELECT COUNT(*) FROM soul12_notes").fetchone()[0]
        self.assertEqual(rows, 0)

    def test_chat_hint_only_relevant_and_allowed(self):
        self.fixtures()
        self.core.inner12.cycle(self.core)
        self.assertEqual(self.core.inner12.chat_hint(
            self.core,"Как дела?"),"")
        hint=self.core.inner12.chat_hint(self.core,"Давай улучшим память")
        self.assertIn("Локальная заметка",hint)
        self.assertIn("НЕ",hint)
        self.core.living8.permission("proactive_chat",False,confirmed=True)
        self.assertEqual(self.core.inner12.chat_hint(
            self.core,"Память"),"")
        self.assertEqual(self.core.inner12.chat_hint(
            self.core,"Продолжи проект"),"")
        self.assertTrue(self.core.dialogue9.guidance())

    def test_diary_only_aggregates_and_identity_file_stays_unchanged(self):
        before = (launcher.ROOT/"soul"/"personality.json").read_bytes()
        data = self.fixtures()
        self.core.inner12.cycle(self.core)
        state = self.core.inner12.status(self.core)
        journal=state["last_diary_entry"]
        self.assertIsNotNone(journal)
        self.assertEqual(journal["utc_day"], journal["utc_day"][:10])
        self.assertNotIn(data["private"], json.dumps(journal))
        self.assertTrue(all(type(value) is int for value in journal["counters"].values()))
        self.assertEqual(before,
            (launcher.ROOT/"soul"/"personality.json").read_bytes())

    def test_invalid_inputs_and_no_second_queue(self):
        self.fixtures()
        self.core.inner12.cycle(self.core)
        for bad in (0, -1, "1", True, None):
            with self.assertRaises(ValueError):
                self.core.inner12.inspect(self.core,bad)
            with self.assertRaises(ValueError):
                self.core.inner12.dismiss(bad,confirmed=True)
        for limit in (0, True, 9999):
            with self.assertRaises(ValueError):
                self.core.inner12.notes(limit=limit)
        self.assertEqual(
            sum(j["kind"]=="cog12_reflect" for j in
                self.core.scheduler.pending()["jobs"]), 1)


class InnerWorld12HttpTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path=Path(tmp.name)/"data"/"memory.sqlite3"
        launcher.init_database(path)
        self.server=launcher.SoulServer(
            ("127.0.0.1",0),launcher.load_personality(),path)
        self.server.cognition.next  # Existing cognitive engine still present
        self.server.cognition.inner12.cycle(self.server.cognition)
        self.thread=threading.Thread(
            target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(lambda:(self.server.shutdown(),
                         self.server.server_close(),self.thread.join(timeout=6)))
        self.base="http://127.0.0.1:"+str(self.server.server_address[1])
        self.http=build_opener(ProxyHandler({}))

    def get(self,path):
        with self.http.open(self.base+path,timeout=12) as res:
            return json.load(res)

    def post(self,path,obj,origin=None):
        req=Request(
            self.base+path,data=json.dumps(obj).encode(),method="POST",
            headers={"Content-Type":"application/json",
                     "Origin":origin or self.base})
        with self.http.open(req,timeout=12) as res:
            return json.load(res)

    def test_local_status_and_notes_api_without_unapproved_mutations(self):
        status=self.get("/api/soul/inner12")
        self.assertEqual(status["version"],"12.0")
        self.assertFalse(status["real_consciousness_proven"])
        notes=self.get("/api/soul/inner12/notes")
        self.assertIsInstance(notes["items"],list)
        with self.assertRaises(HTTPError) as error:
            self.post("/api/soul/inner12/dismiss",{"note_id":1})
        self.assertEqual(error.exception.code,400)
        with self.assertRaises(HTTPError) as error:
            self.post("/api/soul/inner12/inspect",{"note_id":1},
                      origin="https://attacker.example")
        self.assertEqual(error.exception.code,403)

    def test_ui_source_controls_and_bat_preserved(self):
        root=launcher.ROOT
        html=(root/"ui"/"index.html").read_text(encoding="utf-8")
        app=(root/"ui"/"app.ts").read_text(encoding="utf-8")
        for marker in ("inner12","inner12-refresh","inner12-status",
                       "inner12-notes","inner12-result"):
            self.assertIn('id="'+marker+'"',html)
        self.assertIn("/api/soul/inner12/notes",app)
        self.assertIn("/api/soul/inner12/dismiss",app)
        self.assertIn("inner12.chat_hint",(
            root/"tsukiyora"/"cognition"/"life_cycle10.py"
        ).read_text(encoding="utf-8"))
        self.assertIn("cog12_reflect",(
            root/"tsukiyora"/"runtime"/"maintenance.py"
        ).read_text(encoding="utf-8"))
        self.assertTrue((root/"Tsukiyora.bat").exists())


if __name__=="__main__":
    unittest.main()
