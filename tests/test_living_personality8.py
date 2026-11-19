"""Cognitive Soul 8.0: grounded character, local autonomy and consent tests."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import hashlib
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
from tsukiyora.runtime.maintenance import JOBS


class LivingPersonalityTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db=Path(temp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.db)
        self.core=CognitiveCore(self.db)
        self.soul=self.core.living8
        self.store=MemoryStore(self.db)

    def add_ratings(self,note="Мне понравилось, очень понятно объяснил",count=3):
        conversation=self.store.create_conversation()
        messages=self.store.append_messages(conversation["id"],[
            {"role":"assistant","content":"Safe response "+str(i)}
            for i in range(count)
        ])
        with closing(self.store.connect()) as conn,conn:
            for message in messages:
                conn.execute(
                    "INSERT INTO cognitive_feedback(message_id,helpful,note)"
                    " VALUES(?,1,?)",(message["id"],note)
                )
        return [x["id"] for x in messages]

    def test_default_level_three_is_local_and_sensitive_scopes_denied(self):
        state=self.soul.settings()
        self.assertEqual(state["autonomy_level"],3)
        self.assertTrue(state["local_permissions"]["proactive_chat"])
        self.assertTrue(state["local_permissions"]["local_practice"])
        self.assertFalse(state["local_permissions"]["indexed_documents"])
        for dangerous in ("paid_cloud","external_actions","rewrite_persona",
                          "activate_skills","modify_user_memory"):
            self.assertFalse(state["local_permissions"][dangerous])
            with self.assertRaises(ValueError):
                self.soul.permission(dangerous,True,confirmed=True)
        for invalid in (-1,4,2.3,True,"3"):
            with self.assertRaises(ValueError):
                self.soul.control(invalid,confirmed=True)
        with self.assertRaises(ValueError):
            self.soul.control(2)
        with self.assertRaises(ValueError):
            self.soul.permission("indexed_documents",True)
        self.assertFalse(self.soul.settings()["new_paid_cloud_calls"])

    def test_goal_cycle_reuses_existing_sources_and_checkpoint_resume(self):
        sid=self.core.forge.propose("coding",confirmed=True)["id"]
        self.core.forge.shadow()
        with closing(self.store.connect()) as conn,conn:
            conn.execute(
                "INSERT INTO academy_curriculum(skill,objective,state,samples,review_required)"
                " VALUES('accuracy','Improve measured accuracy','regressing',10,1)"
            )
            conn.execute(
                "INSERT INTO cognitive_next_questions(fingerprint,question,source_note)"
                " VALUES(?,?,?)",
                (hashlib.sha256(b"gap").hexdigest(),"Why does this happen?",
                 "Test-only local question")
            )
        detected=self.soul.discover(self.core)
        self.assertGreaterEqual(detected["new_goals"],3)
        self.assertEqual(self.soul.discover(self.core)["new_goals"],0)
        initial=self.soul.agenda()["items"]
        self.assertTrue(any(x["category"]=="skill" for x in initial))
        self.assertTrue(any(x["category"]=="academy" for x in initial))
        self.assertTrue(any(x["category"]=="curiosity" for x in initial))
        for stage in range(1,4):
            processed=self.soul.work(self.core)
            self.assertEqual(processed["checkpoint"],stage)
            self.assertEqual(processed["processed"],1)
        proposals=self.soul.proposals()["items"]
        self.assertTrue(proposals)
        self.assertEqual(proposals[0]["kind"],"goal_review")
        self.assertFalse(self.soul.proposals()["automatic_approval"])
        with closing(self.store.connect()) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM soul8_checkpoints"
            ).fetchone()[0],3)
        launcher.init_database(self.db)
        recovered=CognitiveCore(self.db).living8
        self.assertEqual(recovered.agenda()["items"][-1]["checkpoint_count"],3)
        self.assertEqual(recovered.settings()["autonomy_level"],3)
        self.assertEqual(len(recovered.proposals()["items"]),1)

    def test_proposal_decision_requires_confirm_and_never_activates_skill(self):
        self.soul.discover(self.core)
        for _ in range(3):
            self.soul.work(self.core)
        pending=self.soul.proposals()["items"][0]
        with self.assertRaises(ValueError):
            self.soul.decide(pending["id"],True)
        with self.assertRaises(ValueError):
            self.soul.decide(pending["id"],1,confirmed=True)
        result=self.soul.decide(pending["id"],True,confirmed=True)
        self.assertTrue(result["persona_core_unchanged"])
        self.assertFalse(result["new_skill_activated"])
        self.assertEqual(self.soul.proposals()["items"],[])
        with self.assertRaises(ValueError):
            self.soul.decide(pending["id"],True,confirmed=True)
        self.assertEqual(self.soul.agenda()["items"][0]["state"],"reviewed")

    def test_personality_style_requires_three_real_positive_notes(self):
        original_persona=launcher.load_personality()
        self.add_ratings(count=2)
        self.assertEqual(self.soul.reflect(self.core)["proposed_styles"],0)
        self.add_ratings(count=1)
        proposed=self.soul.reflect(self.core)
        self.assertGreaterEqual(proposed["proposed_styles"],1)
        rows=[x for x in self.soul.proposals()["items"] if x["kind"]=="style"]
        self.assertTrue(rows)
        trait=next(x for x in rows if x["target"]=="clarity")
        self.assertEqual(self.soul.approved_style(),"")
        self.soul.decide(trait["id"],True,confirmed=True)
        self.assertIn("ясно",self.soul.approved_style())
        self.assertEqual(launcher.load_personality(),original_persona)
        # Positive evidence withdrawn: style is not silently kept in prompt.
        with closing(self.store.connect()) as conn,conn:
            conn.execute("UPDATE cognitive_feedback SET helpful=0 "
                         "WHERE message_id=(SELECT MAX(message_id) FROM cognitive_feedback)")
        self.assertNotIn("ясно",self.soul.approved_style())
        self.assertEqual(launcher.load_personality(),original_persona)

    def test_levels_disable_local_work_and_chat_proposals(self):
        self.soul.discover(self.core)
        self.soul.control(0,confirmed=True)
        self.assertTrue(self.soul.discover(self.core)["paused"])
        self.assertEqual(self.soul.work(self.core)["processed"],0)
        self.assertEqual(self.soul.proposals()["items"],[])
        self.soul.control(1,confirmed=True)
        self.assertEqual(self.soul.work(self.core)["processed"],0)
        self.soul.control(2,confirmed=True)
        self.soul.permission("local_practice",False,confirmed=True)
        self.assertEqual(self.soul.work(self.core)["processed"],0)
        self.soul.permission("local_practice",True,confirmed=True)
        self.assertEqual(self.soul.work(self.core)["processed"],1)
        self.soul.control(3,confirmed=True)
        self.soul.permission("proactive_chat",False,confirmed=True)
        for _ in range(2):
            self.soul.work(self.core)
        self.assertEqual(self.soul.proposals()["items"],[])
        self.soul.permission("proactive_chat",True,confirmed=True)
        self.assertFalse(self.soul.settings()["local_permissions"]["indexed_documents"])

    def test_document_metadata_requires_permission_and_never_reads_body(self):
        with closing(self.store.connect()) as conn,conn:
            conn.execute(
                "INSERT INTO source_documents(name,sha256,bytes) VALUES(?,?,?)",
                ("Approved-local-example.txt","0"*64,24)
            )
        self.soul.discover(self.core)
        self.assertFalse(any(x["category"]=="documents"
                             for x in self.soul.agenda()["items"]))
        self.soul.permission("indexed_documents",True,confirmed=True)
        result=self.soul.discover(self.core)
        self.assertGreaterEqual(result["new_goals"],1)
        self.assertTrue(any(x["category"]=="documents"
                            for x in self.soul.agenda()["items"]))
        with closing(self.store.connect()) as conn:
            original=conn.execute(
                "SELECT sha256 FROM source_documents WHERE name=?",
                ("Approved-local-example.txt",)
            ).fetchone()["sha256"]
        self.assertEqual(original,"0"*64)

    def test_journal_is_idempotent_and_no_new_cloud_calls(self):
        before=self.core.forge.auto_status()
        source_before=launcher.load_personality()
        memory=self.store.add_memory("fact","Trusted original data kept intact")
        with patch("tsukiyora.cloud.cloudru._request",
                   side_effect=AssertionError("No Cloud for Cognitive Soul 8")):
            for kind in ("cog8_discover","cog8_practice","cog8_reflect",
                         "cog8_health","cog8_daily"):
                self.assertIsInstance(self.soul.run_job(kind,self.core),dict)
        one=self.soul.daily(self.core)
        self.assertTrue(one["already_recorded"])
        self.assertEqual(self.soul.settings()["new_paid_cloud_calls"],0)
        self.assertEqual(self.core.forge.auto_status(),before)
        self.assertEqual(launcher.load_personality(),source_before)
        self.assertEqual(self.store.find_memories(limit=1)[0]["id"],memory["id"])
        self.assertEqual(self.soul.journal()["simulation_not_subjective_life"],True)
        self.assertTrue({"cog8_discover","cog8_practice","cog8_reflect",
                         "cog8_health","cog8_daily"} <= set(JOBS))
        with closing(self.store.connect()) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],"ok")


class LivingPersonalityHttpTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path=Path(temp.name)/"data"/"memory.sqlite3"
        launcher.init_database(path)
        self.server=launcher.SoulServer(
            ("127.0.0.1",0),launcher.load_personality(),path)
        worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(lambda:(self.server.shutdown(),
                                self.server.server_close(),worker.join(timeout=6)))
        self.base="http://127.0.0.1:"+str(self.server.server_address[1])
        self.http=build_opener(ProxyHandler({}))

    def get(self,route):
        with self.http.open(self.base+route,timeout=15) as res:
            return json.load(res)

    def post(self,route,data,origin=None):
        r=Request(
            self.base+route,data=json.dumps(data,ensure_ascii=False).encode(),
            method="POST",headers={"Content-Type":"application/json",
                                   "Origin":origin or self.base})
        with self.http.open(r,timeout=15) as res:
            return json.load(res)

    def test_http_level_and_permission_guards_and_intentions(self):
        for path in ("/api/soul/living8","/api/soul/living8/agenda",
                     "/api/soul/living8/proposals","/api/soul/living8/journal",
                     "/api/soul/living8/diary"):
            self.assertIsInstance(self.get(path),dict)
        self.assertEqual(self.get("/api/soul/living8")["autonomy_level"],3)
        with self.assertRaises(HTTPError) as error:
            self.post("/api/soul/living8/control",{"level":0})
        self.assertEqual(error.exception.code,400)
        with self.assertRaises(HTTPError) as error:
            self.post("/api/soul/living8/control",
                      {"level":0,"confirmed":True},
                      origin="https://untrusted.example")
        self.assertEqual(error.exception.code,403)
        control=self.post("/api/soul/living8/control",
                          {"level":0,"confirmed":True})
        self.assertEqual(control["autonomy_level"],0)
        with self.assertRaises(HTTPError) as error:
            self.post("/api/soul/living8/permission",
                      {"scope":"paid_cloud","enabled":True,"confirmed":True})
        self.assertEqual(error.exception.code,400)
        self.assertFalse(self.get("/api/soul/living8")["local_permissions"]["paid_cloud"])
        on=self.post("/api/soul/living8/control",
                     {"level":3,"confirmed":True})
        self.assertEqual(on["autonomy_level"],3)
        allowed=self.post("/api/soul/living8/permission",
                          {"scope":"indexed_documents","enabled":True,
                           "confirmed":True})
        self.assertTrue(allowed["local_permissions"]["indexed_documents"])

    def test_ui_and_legacy_preserved(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"ui"/"index.html").read_text(encoding="utf-8")
        ts=(root/"ui"/"app.ts").read_text(encoding="utf-8")
        for name in ("living8","living8-level","living8-status","living8-goals",
                     "living8-chat-proposal","living8-chat-approve","living8-chat-reject",
                     "living8-proactive","living8-practice","living8-documents",
                     "cognitive-v7","skill-forge","forge-chat-proposal","cognitive-unified"):
            self.assertIn('id="'+name+'"',html)
        for phrase in ("refreshLiving8Chat","refreshLiving8",
                       "decideLiving8","refreshForgeChatUI"):
            self.assertIn(phrase,ts)


if __name__=="__main__":
    unittest.main()
