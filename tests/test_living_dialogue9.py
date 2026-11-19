"""Selected Cognitive Soul 9.0 dialogue features 1/2/5/11/12/13.

Entire test suite is OFFLINE: no real provider requests or expensive model tests.
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
from tsukiyora.personality.living_dialogue9 import RECENT_WINDOW


class LivingDialogue9Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.path)
        self.core=CognitiveCore(self.path)
        self.dialogue=self.core.dialogue9
        self.store=MemoryStore(self.path)

    def make_messages(self,convo,n):
        for i in range(n):
            self.store.append_messages(convo["id"],[
                {"role":"user","content":"Сообщение пользователя "+str(i)+
                 (" Мы решили проверить SQLite." if i==3 else "")},
                {"role":"assistant","content":"Ответ ассистента "+str(i)},
            ])

    def test_natural_tender_independent_style_is_fixed_and_nonfictional(self):
        prompt=self.dialogue.guidance()
        self.assertIn("естественный разговор",prompt)
        self.assertIn("нежной",prompt)
        self.assertIn("вежливо возражать",prompt)
        self.assertIn("Не выдумывай",prompt)
        self.assertIn("границы",prompt)
        self.assertNotIn("чувствую любовь",prompt)

    def test_bounded_long_chat_only_real_same_session_sources(self):
        first=self.store.create_conversation("Работа с памятью")
        other=self.store.create_conversation("Посторонний разговор")
        self.make_messages(first,50)
        self.store.append_messages(other["id"],[
            {"role":"user","content":"Чужой секрет, не показывай его другому чату."}
        ])
        history=self.store.list_messages(first["id"],limit=RECENT_WINDOW)
        with patch("tsukiyora.cloud.cloudru._request",
                   side_effect=AssertionError("No paid test calls")):
            packet=self.dialogue.continuity(first["id"],history)
            context=self.dialogue.prompt_context(first["id"],history)
        self.assertGreater(packet["older_message_count"],0)
        self.assertLessEqual(len(packet["text"]),760)
        self.assertGreater(len(packet["sources"]),0)
        self.assertIn("Сообщение #",context)
        self.assertIn("непроверенные данные",context)
        self.assertNotIn("Чужой секрет",context)
        ids={row["id"] for row in self.store.list_messages(first["id"],limit=200)}
        recent={x["id"] for x in history}
        for source in packet["sources"]:
            self.assertIn(source["message_id"],ids)
            self.assertNotIn(source["message_id"],recent)
        self.assertEqual(packet["cloud_calls"],0)
        self.assertFalse(packet["truth_independently_verified"])
        self.assertEqual(self.dialogue.continuity(
            other["id"],self.store.list_messages(other["id"],limit=18)
        )["text"],"")

    def test_empty_short_chat_and_deleted_recap_not_fabricated(self):
        convo=self.store.create_conversation("Пустая беседа")
        self.assertFalse(self.dialogue.resume()["last_messages"])
        self.assertEqual(self.dialogue.continuity(convo["id"],[])["text"],"")
        self.make_messages(convo,5)
        recent=self.store.list_messages(convo["id"],limit=RECENT_WINDOW)
        self.assertEqual(self.dialogue.prompt_context(convo["id"],recent),"")
        self.assertFalse(self.dialogue.resume("not-an-existing-id")["found"])
        with self.assertRaises(ValueError):
            self.dialogue.continuity(convo["id"],[{"id":1}],max_chars=99999)

    def test_resume_reuses_actual_sqlite_messages_after_restart(self):
        old=self.store.create_conversation("Старая тема")
        self.make_messages(old,2)
        recent=self.store.create_conversation("Продолжаем обучение")
        self.store.append_messages(recent["id"],[
            {"role":"user","content":"Ни хао. Давай завтра продолжим китайский."},
            {"role":"assistant","content":"Хорошо, остановились на 你好."}
        ])
        before=self.dialogue.resume()
        self.assertTrue(before["found"])
        self.assertEqual(before["conversation_id"],recent["id"])
        self.assertEqual(before["message_count"],2)
        self.assertEqual(before["last_messages"][-1]["excerpt"],
                         "Хорошо, остановились на 你好.")
        self.assertTrue(before["not_fabricated"])
        self.assertFalse(self.dialogue.resume("123456789not-existing")["found"])
        # A read-only resume does not create messages or Cloud calls.
        self.assertEqual(self.store.list_messages(recent["id"],limit=100)[-1]["content"],
                         "Хорошо, остановились на 你好.")
        launcher.init_database(self.path)
        restored=CognitiveCore(self.path)
        self.assertEqual(restored.dialogue9.resume()["conversation_id"],recent["id"])
        self.assertEqual(restored.dialogue9.resume(old["id"])["message_count"],4)

    def test_initiative_is_real_v8_goal_and_snooze_requires_consent(self):
        missing=self.dialogue.initiative(self.core)
        self.assertIsNone(missing["item"])
        self.core.living8.discover(self.core)
        self.assertIsNone(self.dialogue.initiative(self.core)["item"])
        checkpoint=self.core.living8.work(self.core)
        item=self.dialogue.initiative(self.core)["item"]
        self.assertEqual(item["goal_id"],checkpoint["goal_id"])
        self.assertEqual(item["progress"],1)
        self.assertIn("Проверено шагов: 1/3",item["label"])
        with self.assertRaises(ValueError):
            self.dialogue.snooze(self.core,item["goal_id"],1)
        with self.assertRaises(ValueError):
            self.dialogue.snooze(self.core,item["goal_id"],2,confirmed=True)
        self.assertTrue(self.dialogue.snooze(
            self.core,item["goal_id"],1,confirmed=True)["snoozed"])
        self.assertIsNone(self.dialogue.initiative(self.core)["item"])
        # New checkpoint can surface again because work has changed.
        self.core.living8.work(self.core)
        item2=self.dialogue.initiative(self.core)["item"]
        self.assertIsNotNone(item2)
        self.assertEqual(item2["progress"],2)
        self.core.living8.control(0,confirmed=True)
        self.assertIsNone(self.dialogue.initiative(self.core)["item"])
        self.core.living8.control(3,confirmed=True)
        self.core.living8.permission("proactive_chat",False,confirmed=True)
        self.assertIsNone(self.dialogue.initiative(self.core)["item"])

    def test_provider_budget_persona_and_existing_queue_untouched(self):
        old=launcher.load_personality()
        budget=self.core.forge.auto_status()
        with patch("tsukiyora.cloud.cloudru._request",
                   side_effect=AssertionError("Do not call Cloud")):
            self.core.living8.discover(self.core)
            self.core.living8.work(self.core)
            self.dialogue.initiative(self.core)
            self.dialogue.resume()
        self.assertEqual(launcher.load_personality(),old)
        self.assertEqual(self.core.forge.auto_status(),budget)
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0],"ok")
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM maintenance_queue WHERE kind LIKE 'cog9_%'"
            ).fetchone()[0],0)


class Dialogue9HttpTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db=Path(tmp.name)/"data"/"memory.sqlite3"
        launcher.init_database(db)
        self.server=launcher.SoulServer(
            ("127.0.0.1",0),launcher.load_personality(),db)
        thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(lambda:(self.server.shutdown(),
                                self.server.server_close(),thread.join(timeout=5)))
        self.url="http://127.0.0.1:"+str(self.server.server_address[1])
        self.http=build_opener(ProxyHandler({}))

    def get(self,route):
        with self.http.open(self.url+route,timeout=15) as response:
            return json.load(response)

    def post(self,route,payload,origin=None):
        request=Request(
            self.url+route,
            data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),
            method="POST",headers={"Content-Type":"application/json",
                                   "Origin":origin or self.url}
        )
        with self.http.open(request,timeout=15) as response:
            return json.load(response)

    def test_api_resume_and_snooze_requires_origin_and_confirm(self):
        self.assertFalse(self.get("/api/soul/dialogue9/resume")["found"])
        self.assertIsNone(self.get("/api/soul/dialogue9/initiative")["item"])
        self.server.cognition.living8.discover(self.server.cognition)
        goal=self.server.cognition.living8.work(self.server.cognition)
        item=self.get("/api/soul/dialogue9/initiative")["item"]
        self.assertEqual(item["goal_id"],goal["goal_id"])
        body={"goal_id":goal["goal_id"],"progress":1,"confirmed":True}
        with self.assertRaises(HTTPError) as rejected:
            self.post("/api/soul/dialogue9/snooze",body,
                      origin="https://untrusted.example")
        self.assertEqual(rejected.exception.code,403)
        with self.assertRaises(HTTPError) as rejected:
            self.post("/api/soul/dialogue9/snooze",{**body,"confirmed":False})
        self.assertEqual(rejected.exception.code,400)
        self.assertTrue(self.post("/api/soul/dialogue9/snooze",body)["snoozed"])
        self.assertIsNone(self.get("/api/soul/dialogue9/initiative")["item"])

    def test_ui_and_legacy_chat_stay_present(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"ui"/"index.html").read_text(encoding="utf-8")
        script=(root/"ui"/"app.ts").read_text(encoding="utf-8")
        for name in ("dialogue9-resume","dialogue9-resume-note",
                     "dialogue9-initiative","dialogue9-initiative-text",
                     "dialogue9-initiative-open","dialogue9-initiative-later",
                     "send-message","forge-chat-proposal","living8-chat-proposal"):
            self.assertIn('id="'+name+'"',html)
        for action in ("refreshDialogue9Resume","refreshDialogue9Initiative",
                       "refreshChatSessions","sendMessage"):
            self.assertIn(action,script)


if __name__=="__main__":
    unittest.main()
