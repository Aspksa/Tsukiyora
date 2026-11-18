"""Cognitive Soul 2.0: source, consent, persistence and HTTP integration."""
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


class CognitiveNextTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.path)
        self.store=MemoryStore(self.path)
        self.core=CognitiveCore(self.path)
        self.next=self.core.next

    def test_causal_map_is_attributed_and_never_proves_causes(self):
        source=self.store.add_memory(
            "fact","Доставка Титан задержана потому что водитель сообщил о ремонте")
        result=self.next.causal_map("Доставка Титан",limit=5)
        self.assertFalse(result["verified_causality"])
        self.assertEqual(result["cloud_requests"],0)
        self.assertTrue(any(x["memory_id"]==source["id"]
                            for x in result["claimed_causes"]))
        with self.assertRaises(ValueError):
            self.next.causal_map(" ")
        with self.assertRaises(ValueError):
            self.next.causal_map("Доставка",limit=500)

    def test_self_check_requires_saved_assistant_answer(self):
        memory=self.store.add_memory("fact","Согласован проект Ракета")
        convo=self.store.create_conversation()
        reply=self.store.append_messages(convo["id"],[
            {"role":"assistant","content":"Подтверждение: [Память #"+str(memory["id"])+"]"}
        ])[0]
        check=self.next.self_check(reply["id"])
        self.assertEqual(check["audit"]["state"],"source_checked")
        self.assertFalse(check["truth_verified"])
        user=self.store.append_messages(convo["id"],[
            {"role":"user","content":"Привет"}
        ])[0]
        with self.assertRaises(ValueError):
            self.next.self_check(user["id"])

    def test_skills_only_from_explicit_positive_feedback(self):
        convo=self.store.create_conversation()
        notes="Сначала указывай основания решения, затем перечисляй риски."
        responses=self.store.append_messages(convo["id"],[
            {"role":"assistant","content":"Оценённый ответ "+str(i)}
            for i in range(2)
        ])
        for item in responses:
            self.core.feedback(item["id"],True,notes)
        result=self.next.skill_synthesis()
        self.assertEqual(len(result["repeatable_candidates"]),1)
        self.assertEqual(len(result["repeatable_candidates"][0]["source_message_ids"]),2)
        self.assertFalse(result["automatic_activation"])
        self.assertNotIn(notes,self.core.skills.guidance())

    def test_stated_understanding_goals_and_working_state(self):
        self.store.add_memory("preference","Предпочитаю уточнять детали проектов")
        self.assertEqual(len(self.next.understanding()["approved_stated_context"]),1)
        with self.assertRaises(ValueError):
            self.next.add_goal("Проверить договоры")
        added=self.next.add_goal("Проверить договоры",confirmed=True)
        self.assertEqual(added["state"],"active")
        self.assertEqual(self.next.working_state()["active_goals"],1)
        self.assertFalse(self.next.working_state()["subjective_emotions"])
        with self.assertRaises(ValueError):
            self.next.goal_state(added["id"],"done")
        self.assertEqual(self.next.goal_state(added["id"],"done",
                                              confirmed=True)["state"],"done")
        self.assertEqual(self.next.working_state()["active_goals"],0)

    def test_curiosity_never_researches_without_permission(self):
        gap=self.next.knowledge_gap("Проверить неизвестный алгоритм Сириус")
        self.assertTrue(gap["research_suggested"])
        self.assertFalse(gap["research_started"])
        with self.assertRaises(ValueError):
            self.next.save_question("Проверить неизвестный алгоритм Сириус")
        question=self.next.save_question("Проверить неизвестный алгоритм Сириус",
                                         confirmed=True)
        duplicate=self.next.save_question("Проверить неизвестный алгоритм Сириус",
                                          confirmed=True)
        self.assertEqual(question["id"],duplicate["id"])
        self.assertEqual(len(self.next.questions()["items"]),1)
        with self.assertRaises(ValueError):
            self.next.question_decide(question["id"],"approved")
        answer=self.next.question_decide(question["id"],"approved",confirmed=True)
        self.assertFalse(answer["external_research_started"])

    def test_scenarios_are_conditional_and_input_checked(self):
        experiment=self.next.scenario(
            "Как развивать Цукиёру","Сначала тестировать на копии",
            "Сразу включать в рабочую память")
        self.assertEqual(len(experiment["options"]),2)
        self.assertFalse(experiment["predictions_verified"])
        with self.assertRaises(ValueError):
            self.next.scenario("Решение","Один вариант","Один вариант")

    def test_memory_immunity_is_read_only(self):
        memory=self.store.add_memory("fact","Память системы с историей правок")
        before=self.store.stats()["memories"]
        reviewed=self.next.memory_review(limit=50)
        self.assertEqual(reviewed["auto_deleted"],0)
        self.assertEqual(reviewed["auto_corrected"],0)
        self.assertTrue(reviewed["human_review_required"])
        self.assertEqual(before,self.store.stats()["memories"])
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute("SELECT status FROM memory_items WHERE id=?",
                                        (memory["id"],)).fetchone()[0],"active")

    def test_personality_is_grounded_and_protected(self):
        pref=self.store.add_memory("preference","Пиши понятными короткими предложениями")
        state=self.next.personality_growth()
        self.assertEqual(state["identity"],"Tsukiyora")
        self.assertTrue(state["character_profile_unchanged"])
        self.assertFalse(state["real_emotions_claimed"])
        self.assertTrue(any(x["id"]==pref["id"] for x in state["approved_habits"]))

    def test_simulator_requires_permission_and_does_not_train(self):
        answer="Следует проверить возможные противоречия источников"
        with self.assertRaises(ValueError):
            self.next.simulate("uncertainty","accuracy",answer)
        simulated=self.next.simulate("uncertainty","accuracy",answer,confirmed=True)
        self.assertTrue(simulated["passed"])
        self.assertFalse(simulated["real_model_evaluated"])
        self.assertFalse(simulated["auto_activated"])
        self.assertEqual(len(self.next.simulations()["items"]),1)
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM cognitive_next_simulations WHERE answer_hash=?",
                (answer,)).fetchone()[0],0)
        with self.assertRaises(ValueError):
            self.next.simulate("unsafe-new-prompt","accuracy",answer,confirmed=True)


class CognitiveNextHttpTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db=Path(temp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.db)
        self.server=launcher.SoulServer(("127.0.0.1",0),
                                        launcher.load_personality(),self.db)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.close)
        self.url="http://127.0.0.1:"+str(self.server.server_address[1])
        self.opener=build_opener(ProxyHandler({}))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=6)

    def get(self,endpoint):
        with self.opener.open(self.url+endpoint,timeout=10) as response:
            return json.load(response)

    def post(self,endpoint,body,origin=None):
        req=Request(self.url+endpoint,method="POST",
                    data=json.dumps(body,ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type":"application/json",
                             "Origin":origin or self.url})
        with self.opener.open(req,timeout=10) as response:
            return json.load(response)

    def test_routes_consent_and_origin_guards(self):
        for name in ("state","understanding","skills","personality",
                     "questions","simulations"):
            self.assertIsInstance(self.get("/api/soul/next/"+name),dict)
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/soul/next/goal/save",{"title":"Проверить память"})
        self.assertEqual(denied.exception.code,400)
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/soul/next/goal/save",
                      {"title":"Проверить память","confirmed":True},
                      origin="https://evil.invalid")
        self.assertEqual(denied.exception.code,403)
        saved=self.post("/api/soul/next/goal/save",
                        {"title":"Проверить память","confirmed":True})
        self.assertEqual(saved["state"],"active")
        self.assertEqual(self.get("/api/soul/next/state")["active_goals"],1)
        gap=self.post("/api/soul/next/gap",{"query":"Нет данных о Сатурне"})
        self.assertFalse(gap["research_started"])

    def test_ui_controls_match_api_and_no_new_deps(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"ui"/"index.html").read_text(encoding="utf-8")
        app=(root/"ui"/"app.ts").read_text(encoding="utf-8")
        for id_ in ("cognitive-next","next-causal","next-gap",
                    "next-save-question","next-selfcheck","next-review",
                    "next-summary","next-add-goal","next-scenario","next-simulate"):
            self.assertIn('id="'+id_+'"',html)
        self.assertIn('"/api/soul/next/"',app)
        backend=(root/"launcher.py").read_text(encoding="utf-8")
        for route in ("next/causal","next/selfcheck","next/gap",
                      "next/goal/save","next/question/save","next/simulate"):
            self.assertIn(route,backend)
        self.assertFalse(self.get("/api/system/selftest")["cloud_live_verified"])


if __name__=="__main__":
    unittest.main()
