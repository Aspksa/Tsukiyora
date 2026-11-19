"""Cognitive Soul 7.0: evidence gating, migration, unified registry, local CI."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import ProxyHandler, Request, build_opener
from urllib.error import HTTPError

import launcher
from tsukiyora.memory.memory_store import MemoryStore
from tsukiyora.cognition.cognitive_core import CognitiveCore
from tsukiyora.runtime.maintenance import JOBS


class Supervisor7Tests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.path)
        self.core=CognitiveCore(self.path)
        self.engine=self.core.supervisor7
        self.forge=self.core.forge
        self.store=MemoryStore(self.path)

    def skill(self,domain="coding"):
        proposed=self.forge.propose(domain,confirmed=True)
        self.forge.shadow(limit=8)
        return proposed["id"]

    def paired(self,skill_id,number,*,baseline="incorrect",candidate="correct",
               model="same-model",confirmed=True,attested=True):
        return self.engine.add_case(
            skill_id,"holdout question "+str(skill_id)+"-"+str(number),
            "correct",baseline,candidate,model,
            confirmed=confirmed,attested=attested
        )

    def test_old_skill_grandfathered_but_new_cannot_bypass_legacy_endpoint(self):
        sid=self.skill()
        self.assertTrue(self.engine.verdict(sid)["new_policy"])
        self.assertEqual(self.engine.verdict(sid)["cases"],0)
        with self.assertRaises(ValueError):
            self.forge.decide(sid,approve=True,confirmed=True)
        with self.assertRaises(ValueError):
            self.engine._approve(self.forge,sid,confirmed=True)
        self.assertIsNone(self.forge.guidance("Python код")[0])
        ready=self.forge.ready_proposals()["items"][0]
        self.assertEqual(ready["id"],sid)
        self.assertFalse(ready["can_approve"])
        self.assertFalse(ready["evidence_ready"])
        for n in range(7):
            state=self.paired(sid,n)
            self.assertFalse(state["evidence_ready"])
        with self.assertRaises(ValueError):
            self.forge.decide(sid,approve=True,confirmed=True)
        eighth=self.paired(sid,7)
        self.assertTrue(eighth["evidence_ready"])
        self.assertEqual(eighth["net_gain"],8)
        self.assertTrue(self.forge.ready_proposals()["items"][0]["can_approve"])
        result=self.engine._approve(self.forge,sid,confirmed=True)
        self.assertTrue(result["manually_approved"])
        self.assertEqual(self.forge.guidance("python код")[0],sid)
        self.assertEqual(self.forge.ready_proposals()["items"],[])
        self.forge.decide(sid,approve=False,confirmed=True)
        self.assertIsNone(self.forge.guidance("Python код")[0])

    def test_attestation_validation_duplicates_and_model_parity(self):
        sid=self.skill("legal")
        with self.assertRaises(ValueError):
            self.paired(sid,1,attested=False)
        with self.assertRaises(ValueError):
            self.paired(sid,1,confirmed=False)
        with self.assertRaises(ValueError):
            self.engine.add_case(sid,"question","truth","truth","truth",
                                 "model",confirmed=True,attested=True)
        first=self.paired(sid,1)
        self.assertEqual(first["cases"],1)
        with self.assertRaises(ValueError):
            self.paired(sid,1)
        with self.assertRaises(ValueError):
            self.paired(sid,2,model="different-model")
        with closing(self.store.connect()) as db:
            row=db.execute(
                "SELECT question_hash,model_label,baseline_correct,candidate_correct"
                " FROM soul_v7_cases WHERE skill_id=?",(sid,)
            ).fetchone()
        self.assertEqual(row["model_label"],"same-model")
        self.assertEqual(row["candidate_correct"],1)
        self.assertEqual(row["baseline_correct"],0)
        self.assertNotIn("holdout question",row["question_hash"])

    def test_regressions_block_even_with_many_gains(self):
        sid=self.skill("rag")
        # 7 successes but one regression means no activation.
        for n in range(7):
            self.paired(sid,n)
        final=self.paired(sid,7,baseline="correct",candidate="incorrect")
        self.assertEqual(final["regressions"],1)
        self.assertFalse(final["evidence_ready"])
        with self.assertRaises(ValueError):
            self.engine._approve(self.forge,sid,confirmed=True)

    def test_single_registry_reuses_sources_and_retains_persona(self):
        before=launcher.load_personality()
        memory=self.store.add_memory("fact","The source is never rewritten.")
        sid=self.skill()
        report=self.engine.registry()
        self.assertEqual(report["sources"],4)
        self.assertTrue(report["source_tables_unchanged"])
        self.assertTrue(any(x["key"]=="forge6:"+str(sid)
                            for x in report["items"]))
        self.assertTrue(all(x["source"] in
                            ("forge6","experience1","evolution4","academy")
                            for x in report["items"]))
        with patch("tsukiyora.cloud.cloudru._request",
                   side_effect=AssertionError("Network call forbidden")):
            for kind in ("cog7_review","cog7_health","cog7_daily"):
                self.assertIsInstance(
                    self.engine.run_job(kind,self.core),dict)
        self.assertEqual(launcher.load_personality(),before)
        self.assertEqual(self.store.find_memories(limit=1)[0]["id"],memory["id"])
        status=self.engine.status(self.core)
        self.assertEqual(status["cloud_training_calls"],0)
        self.assertTrue(status["protected_persona_unchanged"])
        self.assertTrue(status["health"]["last_checks"])
        self.assertTrue(self.engine.health(self.core)["healthy"])
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")

    def test_reboot_does_not_reset_cutover_or_duplicate_reports(self):
        sid=self.skill()
        with closing(self.store.connect()) as db:
            first=db.execute(
                "SELECT first_new_forge_id FROM soul_v7_policy WHERE id=1"
            ).fetchone()[0]
        result=self.engine.daily(self.core)
        self.assertFalse(result["already_recorded"])
        self.assertTrue(self.engine.daily(self.core)["already_recorded"])
        launcher.init_database(self.path)
        recovered=CognitiveCore(self.path)
        with closing(self.store.connect()) as db:
            second=db.execute(
                "SELECT first_new_forge_id FROM soul_v7_policy WHERE id=1"
            ).fetchone()[0]
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM soul_v7_reports").fetchone()[0],1)
        self.assertEqual(first,second)
        self.assertTrue(recovered.supervisor7.verdict(sid)["new_policy"])
        self.assertTrue({"cog7_review","cog7_health","cog7_daily"}<=set(JOBS))
        self.assertEqual(len(recovered.scheduler.pending()["jobs"]),len(JOBS))

    def test_offline_check_never_modifies_cloud_permissions(self):
        state=self.forge.auto_status()
        sid=self.skill()
        self.paired(sid,0)
        for kind in ("cog7_review","cog7_health","cog7_daily"):
            self.engine.run_job(kind,self.core)
        self.assertEqual(self.forge.auto_status(),state)
        self.assertEqual(self.forge.budget_status()["today_reserved"],0)
        self.assertEqual(self.engine.plan(self.core)["cloud_requests"],0)


class Supervisor7HttpTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db=Path(tmp.name)/"data"/"memory.sqlite3"
        launcher.init_database(db)
        self.server=launcher.SoulServer(
            ("127.0.0.1",0),launcher.load_personality(),db
        )
        worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(lambda:(self.server.shutdown(),
                                self.server.server_close(),worker.join(timeout=6)))
        self.url="http://127.0.0.1:"+str(self.server.server_address[1])
        self.http=build_opener(ProxyHandler({}))

    def get(self,route):
        with self.http.open(self.url+route,timeout=15) as res:
            return json.load(res)

    def post(self,route,data,origin=None):
        request=Request(
            self.url+route,
            data=json.dumps(data,ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type":"application/json",
                     "Origin":origin or self.url}
        )
        with self.http.open(request,timeout=15) as res:
            return json.load(res)

    def test_api_requires_origin_attestation_and_verified_cases(self):
        for endpoint in ("/api/soul/cognitive7",
                         "/api/soul/cognitive7/registry",
                         "/api/soul/cognitive7/health",
                         "/api/soul/cognitive7/plan"):
            self.assertIsInstance(self.get(endpoint),dict)
        created=self.post("/api/soul/forge/propose",
                          {"domain":"coding","confirmed":True})
        sid=created["id"]
        self.post("/api/soul/forge/shadow",{"limit":1,"confirmed":True})
        no={"skill_id":sid,"question":"q1","expected":"yes",
            "baseline_answer":"no","candidate_answer":"yes",
            "model":"same-model","confirmed":True,"attested":True}
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/soul/cognitive7/case",no,
                      origin="https://attacker.invalid")
        self.assertEqual(denied.exception.code,403)
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/soul/cognitive7/approve",
                      {"skill_id":sid,"confirmed":True})
        self.assertEqual(denied.exception.code,400)
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/soul/cognitive7/case",
                      {**no,"attested":False})
        self.assertEqual(denied.exception.code,400)
        for n in range(8):
            evidence=self.post("/api/soul/cognitive7/case",
                               {**no,"question":"q"+str(n)})
        self.assertTrue(evidence["evidence_ready"])
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/soul/cognitive7/approve",
                      {"skill_id":sid,"confirmed":False})
        self.assertEqual(denied.exception.code,400)
        result=self.post("/api/soul/cognitive7/approve",
                         {"skill_id":sid,"confirmed":True})
        self.assertTrue(result["manually_approved"])
        self.assertIsNone(self.get("/api/soul/forge/ready")["items"][0]
                          if self.get("/api/soul/forge/ready")["items"] else None)
        self.assertEqual(self.get("/api/soul/forge/budget")["today_reserved"],0)

    def test_ui_preserves_existing_legacy_controls(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"ui"/"index.html").read_text(encoding="utf-8")
        app=(root/"ui"/"app.ts").read_text(encoding="utf-8")
        for el in ("cognitive-v7","v7-add-case","v7-approve","v7-state",
                   "v7-test-panel","v7-registry","v7-plan",
                   "skill-forge","forge-chat-proposal","cognitive-unified",
                   "cognitive-advanced","forge-auto-toggle"):
            self.assertIn('id="'+el+'"',html)
        for fn in ("refreshCognitive7","refreshForgeChatUI","toggleForgeAuto"):
            self.assertIn(fn,app)
        self.assertIn("if(!p.can_approve)",app)


if __name__=="__main__":
    unittest.main()
