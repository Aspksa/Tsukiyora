"""Cognitive Soul 4.0: evolution, isolation, transfer, budgets and HTTP safety."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import launcher
from cognitive_core import CognitiveCore
from cognitive_evolution import CognitiveEvolution, detect_domain, canonical
from memory_store import MemoryStore


class Guard:
    def status(self):
        return {"limits":{"chat_per_day":200,"per_minute":20},
                "today":{"chat":0}}


class FakeCloud:
    def __init__(self):
        self.guard=Guard()
        self.calls=[]
        self.model="fake-v4-model"

    def status(self):
        return {"configured":True,"verified":True,"model":self.model}

    def complete(self, persona, history, memories, *, extra_guidance=""):
        prompt=history[-1]["content"]
        self.calls.append({"question":prompt,"guidance":extra_guidance,"memories":memories})
        if "Отделяй подтвержденные" in extra_guidance:
            return "Верно "+prompt.split()[-1].rstrip("?")
        return "Неверно"


class Evolution4Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.db)
        self.store=MemoryStore(self.db)
        self.core=CognitiveCore(self.db)
        self.engine=self.core.evolution4

    def add_cases(self, domain="coding", n=30, split="holdout"):
        for i in range(n):
            self.engine.add_case(domain,"Каков ответ для примера "+str(i+100)+"?",
                                 "Верно "+str(i+100),split=split,confirmed=True)

    def test_local_recombination_generations_and_parent_selection(self):
        with self.assertRaises(ValueError):
            self.engine.generate("coding")
        with self.assertRaises(ValueError):
            self.engine.generate("admin",confirmed=True)
        generated=self.engine.generate("coding",count=4,confirmed=True)
        self.assertEqual(len(generated["candidates"]),4)
        self.assertTrue(all(len(x["recipe"]) in (2,3) for x in generated["candidates"]))
        self.assertEqual(generated["cloud_calls"],0)
        self.assertFalse(generated["automatically_enabled"])
        self.assertIsNone(self.engine.guidance("Исправь код на Python")[0])
        later=self.engine.generate("coding",count=3,confirmed=True)
        self.assertEqual(len(later["candidates"]),3)
        self.assertTrue(all(x["generation"]==2 for x in later["candidates"]))
        self.assertNotEqual(
            {tuple(x["recipe"]) for x in later["candidates"]},
            {tuple(x["recipe"]) for x in generated["candidates"]})

    def test_validated_tests_no_leak_no_premature_approval(self):
        candidate=self.engine.generate("coding",confirmed=True)["candidates"][0]["id"]
        cloud=FakeCloud()
        with self.assertRaises(ValueError):
            self.engine.evaluate(candidate,"coding",cloud,{})
        self.assertEqual(cloud.calls,[])
        self.add_cases()
        with self.assertRaises(ValueError):
            self.engine.evaluate(candidate,"coding",cloud,{},
                                 confirmed_send=True,batch_size=7)
        with self.assertRaises(ValueError):
            self.engine.decide(candidate,approve=True,confirmed=True)
        # Five batches of six holdout problems => 30 unique case IDs.
        for batch in range(5):
            if batch in (2,4):
                # Simulate a new calendar day after the daily 24-call cap
                # without changing application logic.
                with closing(self.store.connect()) as db, db:
                    db.execute("DELETE FROM evol4_budget")
            result=self.engine.evaluate(candidate,"coding",cloud,{},
                                        confirmed_send=True)
            self.assertEqual(result["provider_calls"],12)
            self.assertEqual(result["evaluation_domain"],"coding")
            self.assertFalse(result["gold_sent"])
            if batch==0:
                with self.assertRaises(ValueError):
                    self.engine.decide(candidate,approve=True,confirmed=True)
            if batch==1:
                with self.assertRaises(ValueError):
                    self.engine.evaluate(candidate,"coding",cloud,{},
                                         confirmed_send=True)
        self.assertEqual(len(cloud.calls),60)
        self.assertTrue(all(x["memories"]==[] for x in cloud.calls))
        self.assertTrue(all("Верно" not in x["question"] for x in cloud.calls))
        report=self.engine.report()
        measured=next(c for c in report["candidates"] if c["id"]==candidate)
        own=measured["own_results"][0]
        self.assertEqual(own["cases"],30)
        self.assertEqual(own["baseline_correct"],0)
        self.assertEqual(own["candidate_correct"],30)
        self.assertEqual(own["regressions"],0)
        self.assertTrue(measured["eligible"])
        self.assertFalse(report["auto_activation"])
        self.assertIsNone(self.engine.guidance("python код")[0])
        self.assertEqual(
            self.engine.peer_review(candidate)["layers"]["objective_testing"],
            "normalized_exact_match_only")
        with self.assertRaises(ValueError):
            self.engine.decide(candidate,approve=True)
        self.assertEqual(self.engine.decide(candidate,approve=True,confirmed=True)["state"],"activated")
        self.assertEqual(self.engine.guidance("напиши функцию на python")[0],candidate)
        self.assertIsNone(self.engine.guidance("как проходит день?")[0])
        self.assertEqual(self.engine.decide(candidate,approve=False,
                                            confirmed=True)["state"],"rolled_back")
        self.assertIsNone(self.engine.guidance("code python")[0])
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")

    def test_draft_generation_from_negative_ratings(self):
        conversation=self.store.create_conversation()
        messages=self.store.append_messages(conversation["id"],[
            {"role":"assistant","content":"Ошибочный ответ "+str(i)}
            for i in range(4)
        ])
        for m in messages:
            self.core.feedback(m["id"],False,"Ответ неполный")
        drafts=self.engine.report()["candidates"]
        self.assertGreaterEqual(len(drafts),3)
        self.assertTrue(all(x["state"]=="proposed" for x in drafts))
        self.assertEqual(self.engine.observe()["proposed"],0)
        self.assertTrue(self.engine.report()["active"][0]["candidate_id"] is None)
        self.assertTrue(self.engine.signals()["feedback"])
        self.assertFalse(self.engine.signals()["auto_remote_calls"])

    def test_training_curriculum_and_cross_domain_measurement(self):
        candidate=self.engine.generate("coding",confirmed=True)["candidates"][0]["id"]
        self.add_cases("logic",6,"holdout")
        self.add_cases("coding",4,"train")
        course=self.engine.curriculum("coding")
        self.assertEqual(course["stage"],"foundation")
        self.assertEqual(len(course["exercises"]),4)
        result=self.engine.evaluate(candidate,"logic",FakeCloud(),{},
                                    confirmed_send=True)
        self.assertEqual(result["evaluation_domain"],"logic")
        transfer=self.engine.transfer(candidate)
        self.assertTrue(transfer["transfer_verified"])
        self.assertEqual(transfer["cross_domain_results"][0]["domain"],"logic")
        self.assertFalse(next(x for x in self.engine.report()["candidates"]
                              if x["id"]==candidate)["eligible"])
        with self.assertRaises(ValueError):
            self.engine.add_case("logic","Каков ответ для примера 100?",
                                 "Другая версия",confirmed=True)
        self.assertEqual(detect_domain("Проверь договор и условия"),"legal")
        self.assertEqual(detect_domain("Python код не работает"),"coding")
        self.assertEqual(canonical("  Верно, 1! "),canonical("верно 1"))
        self.assertNotEqual(canonical("неверно 1"),canonical("верно 1"))

    def test_failed_run_recovery_and_budget(self):
        self.add_cases("coding",6)
        candidate=self.engine.generate("coding",confirmed=True)["candidates"][0]["id"]
        with closing(self.store.connect()) as db, db:
            cursor=db.execute(
                "INSERT INTO evol4_runs(candidate_id,evaluation_domain,model,status) "
                "VALUES(?,?,?,'running')",(candidate,"coding","fake")
            )
            run_id=cursor.lastrowid
        with self.assertRaises(ValueError):
            self.engine.evaluate(candidate,"coding",FakeCloud(),{},
                                 confirmed_send=True)
        with self.assertRaises(ValueError):
            self.engine.recover(run_id)
        self.assertEqual(self.engine.recover(run_id,confirmed=True)["status"],"failed")
        self.assertFalse(self.engine.peer_review(candidate)["eligible_for_human_approval"])
        plan=self.engine.budget_plan()
        self.assertEqual(plan["today_calls_remaining"],24)
        self.assertTrue(plan["suggestions"])
        self.assertFalse(plan["automatic_paid_calls"])

    def test_degradation_pauses_active_strategy(self):
        # Exercise the actual feedback-link monitoring without fabricating
        # 30 billable comparisons; tests must never spend real Cloud.ru calls.
        candidate=self.engine.generate("legal",confirmed=True)["candidates"][0]["id"]
        with closing(self.store.connect()) as db, db:
            db.execute("UPDATE evol4_candidates SET state='active' WHERE id=?",(candidate,))
            db.execute("UPDATE evol4_active SET candidate_id=? WHERE domain='legal'",(candidate,))
        convo=self.store.create_conversation()
        replies=self.store.append_messages(convo["id"],[
            {"role":"assistant","content":"Мало оснований для вывода "+str(i)}
            for i in range(8)
        ])
        for m in replies:
            self.engine.applied(m["id"],candidate)
            self.core.feedback(m["id"],False,"Недостаточно доказательств")
        self.assertIsNone(self.engine.guidance("Юридический договор")[0])
        item=next(x for x in self.engine.report()["candidates"] if x["id"]==candidate)
        self.assertEqual(item["state"],"paused")
        self.assertTrue(any(x["event"]=="automatic_suspension"
                            for x in self.engine.report()["events"]))
        self.assertFalse(self.engine.peer_review(candidate)["model_weights_changed"])


class Evolution4HttpTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.path)
        self.server=launcher.SoulServer(
            ("127.0.0.1",0),launcher.load_personality(),self.path)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.close)
        self.url="http://127.0.0.1:"+str(self.server.server_address[1])
        self.opener=build_opener(ProxyHandler({}))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=6)

    def get(self,path):
        with self.opener.open(self.url+path,timeout=10) as response:
            return json.load(response)

    def post(self,path,payload,origin=None):
        request=Request(self.url+path,method="POST",
                        data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),
                        headers={"Content-Type":"application/json",
                                 "Origin":origin or self.url})
        with self.opener.open(request,timeout=10) as response:
            return json.load(response)

    def test_api_requires_local_confirmation_and_does_not_call_cloud(self):
        for path in ("/api/soul/evolution4",
                     "/api/soul/evolution4/cases",
                     "/api/soul/evolution4/signals",
                     "/api/soul/evolution4/budget",
                     "/api/soul/evolution4/curriculum?domain=legal"):
            self.assertIsInstance(self.get(path),dict)
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/evolution4/generate",
                      {"domain":"coding","count":4,"confirmed":False})
        self.assertEqual(err.exception.code,400)
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/evolution4/generate",
                      {"domain":"coding","count":4,"confirmed":True},
                      origin="https://attacker.invalid")
        self.assertEqual(err.exception.code,403)
        generated=self.post("/api/soul/evolution4/generate",
                            {"domain":"coding","count":4,"confirmed":True})
        self.assertEqual(len(generated["candidates"]),4)
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/evolution4/evaluate",
                      {"candidate_id":generated["candidates"][0]["id"],
                       "evaluation_domain":"coding","batch_size":6,
                       "confirm_cloud_calls":False})
        self.assertEqual(err.exception.code,400)
        self.assertEqual(self.get("/api/soul/evolution4")["daily_calls_reserved"],0)
        self.assertFalse(self.get("/api/system/selftest")["cloud_live_verified"])

    def test_html_and_javascript_are_wired(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"ui"/"index.html").read_text(encoding="utf-8")
        app=(root/"ui"/"app.ts").read_text(encoding="utf-8")
        for value in ("evolution4-details","evol4-domain","evol4-candidates",
                      "evol4-generate","evol4-add-case","evol4-curriculum",
                      "evol4-test-domain","evol4-overview"):
            self.assertIn('id="'+value+'"',html)
        self.assertIn("refreshEvolution4",app)
        self.assertIn("confirm_cloud_calls:true",app)
        backend=(root/"launcher.py").read_text(encoding="utf-8")
        for route in ("evolution4/evaluate","evolution4/generate",
                      "evolution4/decision","evolution4/budget"):
            self.assertIn(route,backend)


if __name__=="__main__":
    unittest.main()
