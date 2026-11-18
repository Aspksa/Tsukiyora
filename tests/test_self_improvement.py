"""Self-improvement safeguards: no paid calls, data leaks or silent activation."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import launcher
from cognitive_core import CognitiveCore
from memory_store import MemoryStore


class FakeGuard:
    def __init__(self, daily=100, minute=10):
        self.daily=daily
        self.minute=minute

    def status(self):
        return {"limits":{"chat_per_day":self.daily,"per_minute":self.minute},
                "today":{"chat":0}}


class FakeCloud:
    def __init__(self):
        self.guard=FakeGuard()
        self.calls=[]
        self.current_model="fake-model"

    def status(self):
        return {"verified":True,"configured":True,"model":self.current_model}

    def complete(self, persona, history, memories, *, extra_guidance=""):
        prompt=history[-1]["content"]
        self.calls.append({"question":prompt,"guidance":extra_guidance,
                           "memories":memories})
        if "цепочку доводов" in extra_guidance:
            return "Верно " + prompt.split()[-1].rstrip("?")
        return "Неверно"


class ImprovementTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db=Path(temp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.db)
        self.store=MemoryStore(self.db)
        self.core=CognitiveCore(self.db)
        self.engine=self.core.improvement

    def cases(self):
        for i in range(1,5):
            self.engine.add_case("Точный ответ на вопрос "+str(i)+"?",
                                 "Верно "+str(i),split="holdout",confirmed=True)

    def test_explicit_consent_and_protected_gold_answers(self):
        with self.assertRaises(ValueError):
            self.engine.propose("causal_steps")
        with self.assertRaises(ValueError):
            self.engine.propose([])
        proposal=self.engine.propose("causal_steps",confirmed=True)
        self.assertEqual(proposal["state"],"proposed")
        self.assertEqual(self.engine.guidance(),(None,""))
        cloud=FakeCloud()
        with self.assertRaises(ValueError):
            self.engine.evaluate(proposal["id"],cloud,{})
        with self.assertRaises(ValueError):
            self.engine.evaluate(proposal["id"],cloud,{},
                                 confirmed_send=True)
        self.assertEqual(cloud.calls,[])
        self.cases()
        with self.assertRaises(ValueError):
            self.engine.evaluate(proposal["id"],cloud,{},
                                 confirmed_send=True,max_pairs=6)
        cloud.guard=FakeGuard(daily=100,minute=4)
        with self.assertRaises(ValueError):
            self.engine.evaluate(proposal["id"],cloud,{},
                                 confirmed_send=True)
        self.assertEqual(cloud.calls,[])
        cloud.guard=FakeGuard()
        result=self.engine.evaluate(proposal["id"],cloud,{},
                                    confirmed_send=True)
        self.assertEqual(len(cloud.calls),8)
        self.assertTrue(all(x["memories"]==[] for x in cloud.calls))
        self.assertTrue(all("Верно" not in x["question"] for x in cloud.calls))
        self.assertEqual(result["run"]["status"],"completed")
        self.assertEqual(result["run"]["cases"],4)
        self.assertEqual(result["run"]["candidate_correct"],4)
        self.assertEqual(result["run"]["baseline_correct"],0)
        self.assertTrue(result["run"]["ready_for_review"])
        self.assertFalse(result["gold_answers_sent_to_cloud"])
        self.assertEqual(self.engine.guidance(),(None,""))
        with self.assertRaises(ValueError):
            self.engine.decide(proposal["id"],approve=True)
        applied=self.engine.decide(proposal["id"],approve=True,confirmed=True)
        self.assertEqual(applied["active_id"],proposal["id"])
        self.assertIn("цепочку доводов",self.engine.guidance()[1])
        reverted=self.engine.decide(proposal["id"],approve=False,confirmed=True)
        self.assertIsNone(reverted["active_id"])
        self.assertEqual(self.engine.guidance(),(None,""))

    def test_no_gold_mutation_and_scoring_is_strict(self):
        self.cases()
        case=self.engine.cases()["items"][0]
        self.assertEqual(case["split"],"holdout")
        with self.assertRaises(ValueError):
            self.engine.remove_case(case["id"])
        self.assertTrue(self.engine.remove_case(case["id"],confirmed=True)["deleted"])
        self.assertEqual(self.engine.cases()["items"][0]["id"]!=case["id"],True)
        from self_improvement import _normalized
        self.assertEqual(_normalized(" Верно, 1! "),_normalized("верно 1"))
        self.assertNotEqual(_normalized("Верно 1, возможно"),_normalized("Верно 1"))
        self.assertNotEqual(_normalized("Неверно 1"),_normalized("Верно 1"))

    def test_auto_hypothesis_is_never_activated(self):
        convo=self.store.create_conversation()
        messages=self.store.append_messages(convo["id"],[
            {"role":"assistant","content":"Неполный ответ №"+str(i)}
            for i in range(3)
        ])
        for message in messages:
            self.core.feedback(message["id"],False,
                               "Проверь источники и точность")
        suggestions=self.engine.report()["proposals"]
        self.assertTrue(suggestions)
        self.assertEqual(suggestions[0]["state"],"proposed")
        self.assertIsNone(self.engine.report()["active_id"])
        self.assertEqual(self.engine.guidance(),(None,""))

    def test_low_live_ratings_trigger_automatic_suspension(self):
        self.cases()
        proposal=self.engine.propose("causal_steps",confirmed=True)["id"]
        self.engine.evaluate(proposal,FakeCloud(),{},
                             confirmed_send=True)
        self.engine.decide(proposal,approve=True,confirmed=True)
        convo=self.store.create_conversation()
        messages=self.store.append_messages(convo["id"],[
            {"role":"assistant","content":"Неполный ответ №"+str(i)}
            for i in range(8)
        ])
        for message in messages:
            self.engine.applied(message["id"],proposal)
            self.core.feedback(message["id"],False)
        self.assertIsNone(self.engine.report()["active_id"])
        self.assertEqual(self.engine.guidance(),(None,""))
        self.assertEqual(self.engine.report()["proposals"][0]["state"],"suspended")
        self.assertTrue(any(e["event"]=="automatic_suspension"
                            for e in self.engine.report()["recent_events"]))
        with self.assertRaises(ValueError):
            self.engine.decide(proposal,approve=True,confirmed=True)

    def test_failed_and_interrupted_runs_never_enable_strategy(self):
        self.cases()
        proposal=self.engine.propose("causal_steps",confirmed=True)["id"]
        with closing(self.store.connect()) as db, db:
            cursor=db.execute(
                "INSERT INTO improvement_runs(proposal_id,baseline_strategy,model,status)"
                " VALUES(?,?,?,'running')",(proposal,"","test")
            )
            interrupted=cursor.lastrowid
        with self.assertRaises(ValueError):
            self.engine.evaluate(proposal,FakeCloud(),{},confirmed_send=True)
        with self.assertRaises(ValueError):
            self.engine.recover_interrupted(interrupted)
        self.engine.recover_interrupted(interrupted,confirmed=True)
        with self.assertRaises(ValueError):
            self.engine.decide(proposal,approve=True,confirmed=True)
        self.assertEqual(self.engine.report()["proposals"][0]["last_run"]["status"],"failed")

    def test_report_does_not_expose_user_prompts_or_answer_text(self):
        self.cases()
        proposal=self.engine.propose("causal_steps",confirmed=True)["id"]
        self.engine.evaluate(proposal,FakeCloud(),{},confirmed_send=True)
        report=json.dumps(self.engine.report(),ensure_ascii=False)
        self.assertNotIn("Точный ответ на вопрос",report)
        self.assertNotIn("Верно 1",report)
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")


class ImprovementHttpTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db=Path(temp.name)/"data"/"memory.sqlite3"
        launcher.init_database(db)
        self.server=launcher.SoulServer(
            ("127.0.0.1",0), launcher.load_personality(),db)
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
        req=Request(self.url+path,method="POST",
                    data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type":"application/json",
                             "Origin":origin or self.url})
        with self.opener.open(req,timeout=10) as response:
            return json.load(response)

    def test_local_routes_require_consent_and_same_origin(self):
        for route in ("/api/soul/improvement",
                      "/api/soul/improvement/diagnostics",
                      "/api/soul/improvement/cases"):
            self.assertIsInstance(self.get(route),dict)
        with self.assertRaises(HTTPError) as invalid:
            self.post("/api/soul/improvement/propose",
                      {"strategy":"causal_steps","confirmed":False})
        self.assertEqual(invalid.exception.code,400)
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/soul/improvement/case",
                      {"question":"Проверка примера","expected":"Ответ эталона",
                       "confirmed":True},origin="https://evil.invalid")
        self.assertEqual(denied.exception.code,403)
        created=self.post("/api/soul/improvement/propose",
                          {"strategy":"causal_steps","confirmed":True})
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/soul/improvement/run",
                      {"proposal_id":created["id"],"confirm_cloud_calls":False})
        self.assertEqual(denied.exception.code,400)
        self.assertIsNone(self.get("/api/soul/improvement")["active_id"])

    def test_ui_wiring_and_no_cloud_request_on_startup(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"ui"/"index.html").read_text(encoding="utf-8")
        app=(root/"ui"/"app.ts").read_text(encoding="utf-8")
        for control in ("self-improvement","improve-question","improve-expected",
                        "improve-strategy","improve-add-case","improve-propose",
                        "improve-proposals","improve-feedback"):
            self.assertIn('id="'+control+'"',html)
        for endpoint in ("improvement/run","improvement/case",
                         "improvement/propose","improvement/decide"):
            self.assertIn(endpoint,(root/"launcher.py").read_text(encoding="utf-8"))
        self.assertIn("refreshImprovement",app)
        self.assertFalse(self.get("/api/system/selftest")["cloud_live_verified"])


if __name__=="__main__":
    unittest.main()
