"""Cognitive Soul 5.0: one local queue, persistence, approvals, and privacy."""
from __future__ import annotations

from contextlib import closing
import inspect
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import cloudru
import launcher
from cognitive_core import CognitiveCore, CognitiveWorker
from maintenance import JOBS
from memory_store import MemoryStore


class UnifiedCoreTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.path)
        self.core=CognitiveCore(self.path)
        self.store=MemoryStore(self.path)
        self.unified=self.core.orchestrator

    def run_one(self, kind):
        with closing(self.store.connect()) as db, db:
            db.execute("UPDATE maintenance_queue SET due_at=9999999999,"
                       "state='waiting' WHERE kind<>?",(kind,))
            db.execute("UPDATE maintenance_queue SET due_at=0,state='pending'"
                       " WHERE kind=?",(kind,))
        return self.unified.tick(self.core,max_millis=1500)

    def test_single_queue_and_safe_default_survive_restarts(self):
        plan=self.core.scheduler.pending()
        all_jobs={j["kind"] for j in plan["jobs"]}
        self.assertTrue({"quality","academy_cycle","precision_index","backup",
                         "cog5_links","cog5_skills","cog5_insights",
                         "cog5_hypotheses","cog5_simulator","cog5_budget",
                         "cog5_digest"}<=all_jobs)
        state=self.unified.status(self.core.scheduler)
        self.assertTrue(state["single_queue"])
        self.assertEqual(state["mode"],"safe_local")
        self.assertTrue(state["enabled"])
        self.assertEqual(state["cloud_calls"],0)
        self.assertFalse(state["paid_experiments_in_background"])
        self.assertFalse(state["auto_promotions"])
        self.assertNotIn(".cycle(",inspect.getsource(CognitiveWorker._run))
        self.assertIn("orchestrator.tick",inspect.getsource(CognitiveWorker._run))
        with self.assertRaises(ValueError):
            self.unified.configure(False)
        self.unified.configure(False,confirmed=True)
        self.assertEqual(self.unified.tick(self.core)["status"],"paused")
        launcher.init_database(self.path)
        recovered=CognitiveCore(self.path)
        self.assertFalse(recovered.orchestrator.status()["enabled"])
        recovered.orchestrator.configure(True,confirmed=True)
        self.assertEqual(len(recovered.scheduler.pending()["jobs"]),len(JOBS))

    def test_deduplicated_local_review_no_memory_mutation(self):
        left=self.store.add_memory("fact","Поставка была назначена на понедельник")
        right=self.store.add_memory("fact","Поставка была назначена на вторник")
        with closing(self.store.connect()) as db, db:
            db.execute(
                "INSERT INTO memory_links(source_id,target_id,relation) "
                "VALUES(?,?,'possible_conflict')",(left["id"],right["id"])
            )
        original=[x["content"] for x in self.store.find_memories(limit=30)]
        first=self.unified.run_job("cog5_insights",self.core)
        self.assertGreaterEqual(first["new_reviews"],1)
        second=self.unified.run_job("cog5_insights",self.core)
        self.assertEqual(second["new_reviews"],0)
        events=self.unified.reviews()["items"]
        review=next(x for x in events if x["kind"]=="possible_conflict")
        with self.assertRaises(ValueError):
            self.unified.mark_reviewed(review["id"])
        state=self.unified.mark_reviewed(review["id"],confirmed=True)
        self.assertFalse(state["memory_changed"])
        self.assertEqual(self.unified.reviews()["items"][0]["state"],"reviewed")
        self.assertEqual(
            original,[x["content"] for x in self.store.find_memories(limit=30)]
        )

    def test_safe_learning_simulator_and_daily_digest(self):
        pref=self.store.add_memory("preference","Предпочитаю аккуратные ответы")
        before=self.store.stats()["memories"]
        persona_before=launcher.load_personality()
        with patch("cloudru._request",side_effect=AssertionError("No background Cloud.ru")):
            for name in ("cog5_links","cog5_skills","cog5_hypotheses",
                         "cog5_simulator","cog5_insights","cog5_budget"):
                result=self.unified.run_job(name,self.core)
                self.assertIsInstance(result,dict)
            done=self.unified.digest(self.core)
            again=self.unified.digest(self.core)
            self.assertFalse(done["already_recorded"])
            self.assertTrue(again["already_recorded"])
        self.assertEqual(self.store.stats()["memories"],before)
        self.assertEqual(launcher.load_personality(),persona_before)
        self.assertEqual(self.core.status()["last_cycle"]["status"],"ok")
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM cognitive_v5_digests").fetchone()[0],1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM cognitive_cycles").fetchone()[0],1)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        # Legacy experience clients still have their aggregate timeline.
        self.assertEqual(len(self.core.experience.timeline()),1)
        self.assertIn("sessions",
                      json.loads(self.core.experience.timeline()[0]["summary"]))

    def test_chat_priority_retry_and_bounded_logs(self):
        lock=threading.Lock()
        lock.acquire()
        try:
            self.assertEqual(
                self.unified.tick(self.core,chat_lock=lock)["reason"],
                "chat_active"
            )
        finally:
            lock.release()
        result=self.run_one("cog5_skills")
        self.assertEqual(result["kind"],"cog5_skills")
        self.assertEqual(result["status"],"ok")
        report=self.unified.status(self.core.scheduler)
        self.assertEqual(report["recent_jobs"][0]["kind"],"cog5_skills")
        self.assertIsInstance(report["recent_jobs"][0]["counts"],dict)
        self.assertNotIn("Предпочитаю",json.dumps(report,ensure_ascii=False))
        with patch.object(self.core.orchestrator,"run_job",
                          side_effect=ValueError("A private secret should not leak")):
            bad=self.run_one("cog5_simulator")
        self.assertEqual(bad["status"],"warning")
        self.assertNotIn("private secret",json.dumps(bad))
        with closing(self.store.connect()) as db:
            row=db.execute(
                "SELECT state,failures FROM maintenance_queue "
                "WHERE kind='cog5_simulator'"
            ).fetchone()
        self.assertEqual(row["state"],"waiting")
        self.assertEqual(row["failures"],1)
        retry=self.run_one("cog5_simulator")
        self.assertEqual(retry["status"],"ok")
        self.assertEqual(self.core.cycle()["status"],"ok")
        # Unexpected exceptions do not kill the sole worker or leak details.
        with patch.object(self.core.orchestrator,"run_job",
                          side_effect=TypeError("private content in exception")):
            crashed=self.run_one("cog5_budget")
        self.assertEqual(crashed["status"],"warning")
        self.assertNotIn("private content",json.dumps(crashed))
        with closing(self.store.connect()) as db:
            recovered=db.execute(
                "SELECT state,last_status FROM maintenance_queue "
                "WHERE kind='cog5_budget'"
            ).fetchone()
        self.assertEqual(tuple(recovered),("waiting","warning"))

    def test_user_feedback_still_creates_reversible_v3_v4_drafts(self):
        convo=self.store.create_conversation()
        messages=self.store.append_messages(convo["id"],[
            {"role":"assistant","content":"Неточный ответ "+str(i)}
            for i in range(4)
        ])
        with patch("cloudru._request",side_effect=AssertionError("Forbidden cloud")):
            for row in messages:
                self.core.feedback(row["id"],False,"Не удалось проверить ответ")
            self.unified.run_job("cog5_hypotheses",self.core)
        self.assertTrue(self.core.improvement.report()["proposals"])
        self.assertTrue(self.core.evolution4.report()["candidates"])
        self.assertIsNone(self.core.improvement.report()["active_id"])
        self.assertTrue(all(item["candidate_id"] is None
                            for item in self.core.evolution4.report()["active"]))
        self.assertFalse(self.unified.status()["auto_promotions"])


class UnifiedHttpTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db=Path(temp.name)/"data"/"memory.sqlite3"
        launcher.init_database(db)
        self.server=launcher.SoulServer(
            ("127.0.0.1",0),launcher.load_personality(),db
        )
        self.worker=threading.Thread(target=self.server.serve_forever,
                                     daemon=True)
        self.worker.start()
        self.addCleanup(self.close)
        self.url="http://127.0.0.1:"+str(self.server.server_address[1])
        self.http=build_opener(ProxyHandler({}))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=6)

    def get(self,path):
        with self.http.open(self.url+path,timeout=10) as response:
            return json.load(response)

    def post(self,path,value,origin=None):
        payload=json.dumps(value,ensure_ascii=False).encode("utf-8")
        req=Request(self.url+path,method="POST",data=payload,
                    headers={"Content-Type":"application/json",
                             "Origin":origin or self.url})
        with self.http.open(req,timeout=10) as response:
            return json.load(response)

    def test_api_consent_origin_pause_and_resume(self):
        self.assertTrue(self.get("/api/soul/unified")["enabled"])
        self.assertIsInstance(self.get("/api/soul/unified/reviews")["items"],list)
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/soul/unified/control",
                      {"enabled":False,"confirmed":False})
        self.assertEqual(denied.exception.code,400)
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/soul/unified/control",
                      {"enabled":False,"confirmed":True},
                      origin="https://attacker.invalid")
        self.assertEqual(denied.exception.code,403)
        self.assertFalse(self.post("/api/soul/unified/control",
                       {"enabled":False,"confirmed":True})["enabled"])
        self.assertEqual(self.post("/api/soul/unified/tick",
                        {"confirmed":True})["status"],"paused")
        self.assertTrue(self.post("/api/soul/unified/control",
                       {"enabled":True,"confirmed":True})["enabled"])
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/soul/unified/tick",{"confirmed":False})
        self.assertEqual(denied.exception.code,400)
        with patch("cloudru._request",side_effect=AssertionError("No remote calls")):
            result=self.post("/api/soul/unified/tick",{"confirmed":True})
        self.assertIn(result["status"],("ok","idle","warning","deferred"))
        self.assertEqual(self.get("/api/soul/unified")["cloud_calls"],0)

    def test_v5_ui_still_contains_legacy_controls(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"ui"/"index.html").read_text(encoding="utf-8")
        app=(root/"ui"/"app.ts").read_text(encoding="utf-8")
        for name in ("cognitive-unified","unified-state","unified-toggle",
                     "unified-tick","unified-refresh","unified-overview",
                     "unified-review-list","unified-activity","cognitive-advanced",
                     "cognitive-tools","cognitive-next","self-improvement",
                     "evolution4-details"):
            self.assertIn('id="'+name+'"',html)
        self.assertIn("refreshUnifiedCognitive",app)
        for route in ("/api/soul/unified/control",
                      "/api/soul/unified/review",
                      "/api/soul/unified/tick"):
            self.assertIn(route,(root/"launcher.py").read_text(encoding="utf-8"))
        self.assertFalse(self.get("/api/system/selftest")["cloud_live_verified"])


if __name__=="__main__":
    unittest.main()
