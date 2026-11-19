"""Cognitive Soul 6.0 — 15 capabilities, provenance, safe automation and consent."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

import launcher
from memory_store import MemoryStore
from cognitive_core import CognitiveCore
from maintenance import JOBS
from skill_forge import SkillForge, BASE, STEPS, DOMAINS


class FakeCloud:
    def __init__(self,answer=None):
        self.calls=[]
        self.answer=answer or json.dumps({"steps":list(BASE["coding"])})

    def status(self):
        return {"configured":True,"verified":True,"model":"fake-cloudru"}

    def complete(self,persona,history,memories,*,extra_guidance=""):
        self.calls.append({"history":history,"memories":memories,
                           "extra_guidance":extra_guidance})
        return self.answer


class ForgeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.path)
        self.core=CognitiveCore(self.path)
        self.forge=self.core.forge
        self.store=MemoryStore(self.path)

    def score_answers(self,helpful,notes,subject="python код и тесты"):
        convo=self.store.create_conversation()
        rows=self.store.append_messages(convo["id"],[
            {"role":"assistant","content":subject+" №"+str(i)}
            for i in range(len(helpful))
        ])
        for item,success,note in zip(rows,helpful,notes):
            self.core.feedback(item["id"],success,note)
        return rows

    def test_negative_and_positive_evidence_creates_inactive_skills(self):
        self.assertFalse(self.forge.status()["auto_approval"])
        result=self.forge.discover()
        self.assertEqual(result["new_drafts"],0)
        self.score_answers([True,True],["Полезно объяснил причину ошибки"]*2)
        scanned=self.forge.discover()
        self.assertGreaterEqual(scanned["new_drafts"],1)
        items=self.forge.skills()["items"]
        coding=next(x for x in items if x["domain"]=="coding")
        self.assertEqual(coding["origin"],"positive_feedback")
        self.assertEqual(coding["metrics"]["positive_sources"],2)
        self.assertEqual(coding["state"],"draft")
        self.assertEqual(self.forge.guidance("поправь Python код"),(None,""))
        self.assertEqual(self.forge.discover()["new_drafts"],0)
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM forge_evidence WHERE skill_id=?",
                (coding["id"],)).fetchone()[0],2)

    def test_shadow_activate_live_selection_and_revoke(self):
        created=self.forge.propose("legal",confirmed=True)
        skill_id=created["id"]
        with self.assertRaises(ValueError):
            self.forge.decide(skill_id,approve=True,confirmed=True)
        self.assertEqual(self.forge.shadow()["tested"],1)
        item=next(x for x in self.forge.skills()["items"] if x["id"]==skill_id)
        self.assertEqual(item["metrics"]["scenario_count"],4)
        self.assertEqual(item["metrics"]["structural_passes"],4)
        self.assertEqual(self.forge.guidance("Проверь договор"),(None,""))
        with self.assertRaises(ValueError):
            self.forge.decide(skill_id,approve=True)
        self.forge.decide(skill_id,approve=True,confirmed=True)
        selected,guidance=self.forge.guidance("Юридический договор")
        self.assertEqual(selected,skill_id)
        self.assertIn("источ",guidance.casefold()) if False else self.assertIn("условия",guidance)
        self.assertNotIn("api_key",guidance)
        self.forge.decide(skill_id,approve=False,confirmed=True)
        self.assertEqual(self.forge.guidance("Юридический договор"),(None,""))
        self.assertEqual(self.forge.skills()["items"][0]["state"],"paused")

    def test_safe_composition_and_transfer_need_local_checks(self):
        original=self.forge.propose("coding",confirmed=True)["id"]
        with self.assertRaises(ValueError):
            self.forge.compose(original,original,confirmed=True)
        self.forge.shadow()
        variant=self.forge.variant(original,confirmed=True)
        self.assertTrue(variant["created"])
        self.assertEqual(variant["version"],2)
        with self.assertRaises(ValueError):
            self.forge.compose(original,variant["id"],confirmed=True)
        self.forge.shadow()
        combined=self.forge.compose(original,variant["id"],confirmed=True)
        self.assertIn(combined["state"],("draft","shadow"))
        auto=self.forge.auto_compose()
        self.assertIn("created",auto)
        transferred=self.forge.transfer(original,"rag",confirmed=True)
        self.assertFalse(transferred["transfer_verified"])
        self.assertEqual(transferred["domain"],"rag")
        self.assertEqual(self.forge.guidance("найди RAG память"),(None,""))
        self.assertFalse(self.forge.status()["real_world_performance_verified"])
        for x in self.forge.skills()["items"]:
            self.assertTrue(all(step in STEPS for step in x["steps"]))

    def test_curriculum_competition_archiving_and_audit(self):
        skill=self.forge.propose("documents",confirmed=True)
        foundation=self.forge.curriculum("documents")
        self.assertEqual(foundation["stage"],"foundation")
        self.assertEqual(self.forge.shadow()["tested"],1)
        scores=self.forge.compete("documents")
        self.assertTrue(scores["rank_is_heuristic_not_quality_proof"])
        self.assertEqual(scores["ranked"][0]["id"],skill["id"])
        with self.assertRaises(ValueError):
            self.forge.archive(skill["id"])
        self.forge.archive(skill["id"],confirmed=True)
        self.assertEqual(self.forge.skills()["items"][0]["state"],"archived")
        self.assertTrue(self.forge.history()["original_sources_unchanged"])
        self.assertFalse(self.forge.audit()["automatic_delete"])

    def test_feedback_can_create_revisions_without_auto_activation(self):
        sid=self.forge.propose("coding",confirmed=True)["id"]
        self.forge.shadow()
        self.forge.decide(sid,approve=True,confirmed=True)
        responses=self.score_answers(
            [False]*8,["Не устраивает точность ответа"]*8)
        # Explicitly attach subsequent ratings to the tested skill.
        # Ratings cannot activate an unapproved skill.
        for item in responses:
            self.forge.applied(item["id"],sid)
            self.forge.feedback(item["id"],False)
        stats=self.forge.status()
        self.assertEqual(stats["by_state"].get("approved",0),0)
        self.assertIsNone(self.forge.guidance("ошибка Python код")[0])
        self.assertTrue(any(x["kind"]=="degraded"
                            for x in self.forge.history()["items"]))
        self.assertTrue(any(x["origin"]=="variant" and x["state"]=="draft"
                            for x in self.forge.skills()["items"]))

    def test_revoked_positive_feedback_pauses_skill(self):
        answers=self.score_answers([True,True],["Отлично проверил код и тесты"]*2)
        self.forge.discover()
        skill=next(x for x in self.forge.skills()["items"]
                   if x["origin"]=="positive_feedback")
        self.forge.shadow()
        self.forge.decide(skill["id"],approve=True,confirmed=True)
        # A user may change one original positive assessment.
        self.core.feedback(answers[0]["id"],False,"Теперь вижу ошибку")
        self.assertEqual(self.forge.audit()["revoked_skills_paused"],1)
        self.assertEqual(self.forge.guidance("Python код"),(None,""))
        self.assertTrue(any(x["kind"]=="source_revoked"
                            for x in self.forge.history()["items"]))

    def test_bg_queue_cannot_send_cloud_or_change_persona(self):
        persona=launcher.load_personality()
        memory=self.store.add_memory("fact","Проверочная запись не меняется")
        with patch("cloudru._request",side_effect=AssertionError("Never in background")):
            for kind in ("cog6_discover","cog6_shadow","cog6_audit",
                         "cog6_curriculum"):
                result=self.core.orchestrator.run_job(kind,self.core)
                self.assertIsInstance(result,dict)
        self.assertEqual(launcher.load_personality(),persona)
        self.assertEqual(
            self.store.find_memories(limit=1)[0]["id"],memory["id"]
        )
        self.assertTrue({"cog6_discover","cog6_shadow",
                         "cog6_audit","cog6_curriculum"}<=set(JOBS))
        self.assertEqual(self.forge.budget_status()["state"],"unconfigured")
        launcher.init_database(self.path)
        self.assertEqual(self.forge.budget_status()["state"],"unconfigured")
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")

    def test_budget_separate_agreement_and_paid_call_is_bounded(self):
        sid=self.forge.propose("coding",confirmed=True)["id"]
        cloud=FakeCloud(json.dumps({"steps":[
            "scope","inspect_code","gather_evidence",
            "test_boundaries","draft_answer","verify_answer"]}))
        with self.assertRaises(ValueError):
            self.forge.cloud_draft(sid,cloud,{},confirmed_send=True)
        self.assertEqual(cloud.calls,[])
        with self.assertRaises(ValueError):
            self.forge.request_budget(3)
        proposal=self.forge.request_budget(1,confirmed=True)
        self.assertEqual(proposal["state"],"pending")
        with self.assertRaises(ValueError):
            self.forge.cloud_draft(sid,cloud,{},confirmed_send=True)
        self.assertEqual(cloud.calls,[])
        with self.assertRaises(ValueError):
            self.forge.decide_budget(True,confirmed=True)
        with self.assertRaises(ValueError):
            self.forge.decide_budget(True,confirmed=True,ack_unpriced=False)
        self.forge.decide_budget(True,confirmed=True,ack_unpriced=True)
        with self.assertRaises(ValueError):
            self.forge.cloud_draft(sid,cloud,{})
        self.assertEqual(cloud.calls,[])
        result=self.forge.cloud_draft(sid,cloud,{},confirmed_send=True)
        self.assertTrue(result["needs_shadow_test"])
        self.assertFalse(result["auto_approved"])
        self.assertEqual(len(cloud.calls),1)
        self.assertEqual(cloud.calls[0]["memories"],[])
        self.assertEqual(cloud.calls[0]["extra_guidance"],"")
        self.assertNotIn("Проверочная запись",json.dumps(cloud.calls[0],ensure_ascii=False))
        with self.assertRaises(ValueError):
            self.forge.cloud_draft(sid,cloud,{},confirmed_send=True)
        self.assertEqual(len(cloud.calls),1)
        self.assertEqual(self.forge.budget_status()["today_reserved"],1)
        self.forge.decide_budget(False,confirmed=True)
        self.assertEqual(self.forge.budget_status()["state"],"revoked")

    def test_untrusted_cloud_output_is_not_an_executable_skill(self):
        sid=self.forge.propose("coding",confirmed=True)["id"]
        self.forge.request_budget(2,confirmed=True)
        self.forge.decide_budget(True,confirmed=True,ack_unpriced=True)
        cloud=FakeCloud('{"steps":["scope","rm -rf /","draft_answer"]}')
        rejected=self.forge.cloud_draft(sid,cloud,{},confirmed_send=True)
        self.assertEqual(rejected["state"],"rejected_cloud_output")
        self.assertFalse(rejected["candidate_created"])
        self.assertTrue(self.forge.status()["by_state"].get("draft",0)>=1)
        cloud.answer='{"steps":["scope","inspect_code","verify_answer"],"shell":"yes"}'
        rejected=self.forge.cloud_draft(sid,cloud,{},confirmed_send=True)
        self.assertFalse(rejected["candidate_created"])
        self.assertEqual(self.forge.budget_status()["today_reserved"],2)
        self.assertFalse(self.forge.budget_status()["currency_budget_verified"])


class ForgeHttpTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path=Path(temp.name)/"data"/"memory.sqlite3"
        launcher.init_database(path)
        self.server=launcher.SoulServer(
            ("127.0.0.1",0),launcher.load_personality(),path)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.close)
        self.base="http://127.0.0.1:"+str(self.server.server_address[1])
        self.http=build_opener(ProxyHandler({}))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=6)

    def get(self,route):
        with self.http.open(self.base+route,timeout=15) as response:
            return json.load(response)

    def post(self,route,payload,origin=None):
        request=Request(self.base+route,method="POST",
                        data=json.dumps(payload,ensure_ascii=False).encode(),
                        headers={"Content-Type":"application/json",
                                 "Origin":origin or self.base})
        with self.http.open(request,timeout=15) as response:
            return json.load(response)

    def test_local_api_origin_consent_and_cloud_gate(self):
        for path in ("/api/soul/forge","/api/soul/forge/skills",
                     "/api/soul/forge/history","/api/soul/forge/budget",
                     "/api/soul/forge/compete?domain=rag",
                     "/api/soul/forge/curriculum?domain=legal"):
            self.assertIsInstance(self.get(path),dict)
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/forge/propose",
                      {"domain":"logic","confirmed":False})
        self.assertEqual(err.exception.code,400)
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/forge/propose",
                      {"domain":"logic","confirmed":True},
                      origin="https://malicious.invalid")
        self.assertEqual(err.exception.code,403)
        skill=self.post("/api/soul/forge/propose",
                        {"domain":"logic","confirmed":True})
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/forge/cloud/draft",
                      {"skill_id":skill["id"],"confirm_cloud_calls":True})
        self.assertEqual(err.exception.code,400)
        self.assertEqual(self.get("/api/soul/forge/budget")["today_reserved"],0)
        self.assertFalse(self.get("/api/system/selftest")["cloud_live_verified"])

    def test_ui_includes_skill_forge_and_old_panels(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"ui"/"index.html").read_text(encoding="utf-8")
        ts=(root/"ui"/"app.ts").read_text(encoding="utf-8")
        for name in ("skill-forge","forge-domain","forge-state",
                     "forge-candidates","forge-budget","forge-budget-calls",
                     "forge-budget-approve","forge-propose","forge-shadow",
                     "forge-history","cognitive-unified","cognitive-advanced"):
            self.assertIn('id="'+name+'"',html)
        self.assertIn("refreshSkillForge",ts)
        self.assertIn("confirm_cloud_calls:true",ts)
        self.assertIn("ack_unpriced:true",ts)
        api=(root/"launcher.py").read_text(encoding="utf-8")
        for route in ("forge/cloud/draft","forge/budget/decide",
                      "forge/compose","forge/transfer","forge/decide"):
            self.assertIn(route,api)


if __name__=="__main__":
    unittest.main()
