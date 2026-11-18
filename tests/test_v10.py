"""Tsukiyora v1.0: privacy, quota, retrieval, recovery and scale regressions.

All cloud interactions are mocked or forbidden. Tests never claim live Cloud.ru
or real Windows browser verification.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request,build_opener,ProxyHandler
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cloudru
import launcher
from academy_experiments import AcademyExperiments
from brain_atlas import BrainAtlas,REGION_SPAN
from cognitive_core import CognitiveCore
from memory_store import MemoryStore,_fingerprint
from rag_fusion import RagFusion
from reliability import Reliability
from resource_guard import ResourceGuard,LimitReached


class V10DataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.db)
        self.mem=MemoryStore(self.db)
        self.core=CognitiveCore(self.db)
        self.guard=ResourceGuard(self.db)
        self.guard.initialize()

    def test_quotas_are_atomic_under_parallel_requests(self):
        self.guard.configure(chat_per_day=50,embeddings_per_day=40,per_minute=7)
        def attempt(_):
            try:return self.guard.reserve("chat")
            except LimitReached:return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            answers=list(pool.map(attempt,range(30)))
        self.assertEqual(sum(x is not None for x in answers),7)
        self.assertEqual(self.guard.status()["minute_used"],7)
        with self.assertRaises(LimitReached):
            self.guard.reserve("embeddings")
        for bad in (0,-1,1001,True,"4"):
            with self.assertRaises(ValueError):
                self.guard.configure(chat_per_day=bad,embeddings_per_day=10,per_minute=5)

    def test_redacted_events_never_store_secrets_or_dialogues(self):
        self.guard.event("cloud_failure","provider")
        with self.assertRaises(ValueError):
            self.guard.event("API key xyz","raw secret")
        status=json.dumps(self.guard.status(),ensure_ascii=False)
        self.assertNotIn("API key",status)
        self.assertNotIn("текст диалога",status)
        self.assertEqual(self.guard.status()["recent_events"][0]["reason"],"provider")

    def test_cloud_diagnostic_is_limited_before_paid_mock_call(self):
        cloud=cloudru.CloudSession()
        cloud.guard=self.guard
        self.guard.configure(chat_per_day=1,embeddings_per_day=2,per_minute=2)
        cloud.set_key("not-real-test-key")
        cloud._verified=True
        cloud._models=["chat/test"]
        cloud._model="chat/test"
        result={"choices":[{"message":{"content":"проверка"}}]}
        with patch.object(cloudru,"_request",return_value=result) as call:
            self.assertEqual(cloud.diagnostic()["answer"],"проверка")
            with self.assertRaises(cloudru.CloudError) as blocked:
                cloud.diagnostic()
            self.assertEqual(blocked.exception.status,429)
            self.assertEqual(call.call_count,1)
        self.assertEqual(self.guard.status()["today"]["chat"],1)

    def test_local_rag4_cached_vectors_never_leak_to_cloud(self):
        important=self.mem.add_memory("biography","Работаю над юридическими договорами")
        other=self.mem.add_memory("fact","Я люблю китайский чай")
        model="BAAI/bge-m3"
        vec=self.core.vectors
        vec._remember(model,"юридическая помощь",[1.,0.,0.],None)
        vec._remember(model,important["content"],[1.,0.,0.],important["id"])
        vec._remember(model,other["content"],[0.,1.,0.],other["id"])
        with patch.object(cloudru,"_request",side_effect=AssertionError("RAG called cloud")):
            result=self.core.fusion.search("юридическая помощь",model=model,
                                            use_cached_vectors=True)
        self.assertEqual(result["cloud_requests"],0)
        self.assertEqual(result["hidden_embedding_requests"],0)
        self.assertEqual(result["vector_cache"],"used")
        self.assertEqual(result["items"][0]["id"],important["id"])
        self.assertIn("vector",result["items"][0]["channels"])
        self.assertFalse(result["items"][0]["factual_truth_verified"])
        missed=self.core.fusion.search("другой запрос",model=model,use_cached_vectors=True)
        self.assertEqual(missed["vector_cache"],"cache_miss")
        self.assertEqual(missed["cloud_requests"],0)

    def test_academy_experiments_stable_per_chat_and_ratings_only(self):
        convo=self.mem.create_conversation("Опыт")
        self.assertEqual(self.core.experiments.assign(convo["id"]),
                         self.core.experiments.assign(convo["id"]))
        reply=self.mem.append_messages(convo["id"],[
            {"role":"assistant","content":"Практический ответ",
             "client_message_id":"exp-rating"}])[0]
        self.assertTrue(self.core.experiments.record(reply["id"],True)["measured"])
        self.assertEqual(self.core.experiments.report()["assigned_conversations"],1)
        self.assertFalse(self.core.experiments.report()["comparison_ready"])
        self.assertFalse(self.core.experiments.report()["model_training"])
        self.core.experiments.record(reply["id"],False)
        report=self.core.experiments.report()
        self.assertEqual(sum(x["samples"] for x in report["strategies"].values()),1)
        self.assertEqual(sum(x["successes"] for x in report["strategies"].values()),0)
        self.assertEqual(self.core.experiments.guidance(convo["id"],enabled=False),"")
        self.assertEqual(self.mem.stats()["memories"],0)

    def test_million_id_navigation_not_million_loaded(self):
        memory_id=1_000_003
        kind="event"
        content="Реальное воспоминание с большим идентификатором"
        with closing(self.mem.connect()) as db, db:
            db.execute(
                "INSERT INTO memory_items(id,kind,content,normalized_text,fingerprint,"
                "source_type,verification_state) VALUES(?,?,?,?,?,?,?)",
                (memory_id,kind,content,content.casefold(),
                 _fingerprint(kind,content),"manual","user_reported"))
        atlas=BrainAtlas(self.db)
        location=atlas.locate(memory_id)
        self.assertEqual(location["region"],(memory_id-1)//REGION_SPAN)
        self.assertEqual(location["bucket"],(memory_id-1)//256)
        region=atlas.regions(location["sector"])
        self.assertEqual(region["regions"][0]["region"],location["region"])
        nodes=atlas.nodes(location["sector"],location["bucket"])
        self.assertEqual(nodes["nodes"][0]["id"],memory_id)
        self.assertLess(len(json.dumps(nodes)),20000)
        with self.assertRaises(ValueError):atlas.locate(0)
        with self.assertRaises(ValueError):atlas.locate(2)

    def test_concurrent_backup_requests_create_one_snapshot(self):
        self.mem.add_memory("fact","Нужно сохранить историю")
        recovery=Reliability(self.db,ROOT)
        def save(_):return recovery.ensure_snapshot(keep=6,force=False)
        with ThreadPoolExecutor(max_workers=5) as pool:
            reports=list(pool.map(save,range(5)))
        self.assertEqual(sum(x["created"] for x in reports),1)
        self.assertEqual(len(recovery.backups()),1)
        sample=recovery.backups()[0]["filename"]
        rehearsal=recovery.dry_run_restore(sample)
        self.assertTrue(rehearsal["restorable"])
        self.assertFalse(rehearsal["live_database_modified"])
        self.assertEqual(MemoryStore(self.db).stats()["memories"],1)

    def test_persona_remains_protected_and_recorded_in_sources(self):
        old=launcher.load_personality()
        personal=self.mem.add_memory("preference","Отвечай мне структурно и без воды")
        self.core.continuity.consolidate()
        state=self.core.continuity.state()
        self.assertEqual(state["stable_habits"][0]["id"],personal["id"])
        self.assertFalse(state["feelings_claimed"])
        self.assertFalse(state["protected_character_mutable"])
        self.assertEqual(old,launcher.load_personality())


class V10HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.db)
        self.server=launcher.SoulServer(("127.0.0.1",0),launcher.load_personality(),self.db)
        self.runner=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.runner.start()
        self.addCleanup(self.close)
        self.url="http://127.0.0.1:"+str(self.server.server_address[1])
        self.open=build_opener(ProxyHandler({}))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.runner.join(timeout=6)

    def get(self,path):
        with self.open.open(self.url+path,timeout=10) as r:
            return json.load(r)

    def post(self,path,body,origin=None):
        request=Request(self.url+path,method="POST",
            data=json.dumps(body,ensure_ascii=False).encode(),
            headers={"Origin":origin or self.url,"Content-Type":"application/json"})
        with self.open.open(request,timeout=10) as r:
            return json.load(r)

    def test_local_budget_rag4_and_atlas_routes(self):
        self.assertEqual(self.get("/api/system/cloud-budget")["limits"]["per_minute"],10)
        changed=self.post("/api/system/cloud-budget",
            {"chat_per_day":3,"embeddings_per_day":8,"per_minute":2})
        self.assertEqual(changed["limits"]["chat_per_day"],3)
        self.assertFalse(self.get("/api/soul/academy/experiments")["model_training"])
        self.assertEqual(self.post("/api/soul/rag4",{"query":"тест"})["cloud_requests"],0)
        memory=MemoryStore(self.db).add_memory("fact","Важная проверка")
        result=self.get("/api/soul/atlas?level=locate&id="+str(memory["id"]))
        self.assertEqual(result["memory_id"],memory["id"])
        self.assertTrue(result["is_real_record"])
        with self.assertRaises(HTTPError) as err:
            self.post("/api/system/cloud-budget",
              {"chat_per_day":1,"embeddings_per_day":1,"per_minute":1},
              origin="https://attacker.example")
        self.assertEqual(err.exception.code,403)

    def test_all_critical_controls_present_without_fake_browser_e2e(self):
        html=(ROOT/"ui"/"index.html").read_text(encoding="utf8")
        app=(ROOT/"ui"/"app.ts").read_text(encoding="utf8")
        atlas=(ROOT/"ui"/"atlas_view.ts").read_text(encoding="utf8")
        for control in ("budget-save","budget-chat","brain-find-btn","brain-find-id",
                        "academy-experiments","send-message","stop-message",
                        "cloud-diagnose","academy-enabled","run-system-check"):
            self.assertIn('id="'+control+'"',html)
        for endpoint in ("/api/system/cloud-budget","/api/soul/atlas?",
                         "/api/soul/academy/experiments","/api/soul/rag4"):
            self.assertIn(endpoint,app+atlas)
        self.assertIn("async goto(",atlas)
        self.assertNotIn("localStorage.setItem",app)

if __name__=="__main__":
    unittest.main()
