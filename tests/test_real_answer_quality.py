"""Real-answer quality regressions: existing saved replies, no self-judge LLM.

Live models, personal files and remote requests are never needed.
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
import tsukiyora.cloud.cloudru as cloudru
from tsukiyora.cognition.cognitive_core import CognitiveCore
from tsukiyora.memory.memory_store import MemoryStore


class RealAnswerQualityTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.path)
        self.core=CognitiveCore(self.path)
        self.store=MemoryStore(self.path)

    def exchange(self, prompt, reply, conversation_id=None):
        if not conversation_id:
            conversation_id=self.store.create_conversation("Проверка")["id"]
        self.store.append_messages(conversation_id,[{
            "role":"user","content":prompt
        }])
        assistant=self.store.append_messages(conversation_id,[{
            "role":"assistant","content":reply
        }])[0]
        return conversation_id,assistant

    def test_real_saved_answer_with_good_and_bad_citations(self):
        fact=self.store.add_memory("fact","Подтверждённое решение SQLite")
        convo,a=self.exchange("Укажи ссылку и источник.",
            "Сохранили в SQLite [Память #"+str(fact["id"])+"]")
        with patch("tsukiyora.cloud.cloudru._request",
                   side_effect=AssertionError("No cloud calls")):
            scan=self.core.quality15.observe(self.core,a["id"],a["content"])
        self.assertEqual(scan["valid_citations"],1)
        self.assertEqual(scan["missing_citations"],0)
        self.assertEqual(scan["source_state"],"source_checked")
        self.assertFalse(scan["truth_verified"])
        self.assertFalse(scan["citation_gap_signal"])
        bad=self.store.append_messages(convo,[{
            "role":"assistant","content":"Выдуманный источник [Память #999999]"
        }])[0]
        scan=self.core.quality15.observe(self.core,bad["id"],bad["content"])
        self.assertEqual(scan["missing_citations"],1)
        self.assertEqual(scan["source_state"],"needs_review")
        with closing(self.store.connect()) as db:
            rows=[dict(r) for r in db.execute(
                "SELECT * FROM answer15_audits ORDER BY message_id")]
        self.assertEqual(len(rows),2)
        self.assertNotIn("Подтверждённое решение",str(rows))
        self.assertNotIn("Выдуманный источник",str(rows))

    def test_explicit_source_request_gap_is_only_a_signal(self):
        convo,answer=self.exchange("Где источник этой информации?",
                                   "Точного источника сейчас нет.")
        r=self.core.quality15.observe(self.core,answer["id"],answer["content"])
        self.assertTrue(r["asked_for_citations"])
        self.assertTrue(r["citation_gap_signal"])
        self.assertFalse(r["truth_verified"])
        with self.assertRaises(ValueError):
            self.core.quality15.observe(self.core,answer["id"],"Другой текст")
        with self.assertRaises(ValueError):
            self.core.quality15.observe(self.core,123456,"Отсутствует")

    def test_explicit_user_ratings_change_only_next_turn_same_conversation(self):
        convo,answer=self.exchange("Скажи о проекте.","Неправильный факт.")
        other=self.store.create_conversation("Соседний диалог")["id"]
        self.assertEqual(self.core.quality15.guidance(convo),"")
        with self.assertRaises(ValueError):
            self.core.quality15.review(answer["id"],accuracy=False)
        with self.assertRaises(ValueError):
            self.core.quality15.review(answer["id"],confirmed=True)
        with self.assertRaises(ValueError):
            self.core.quality15.review(answer["id"],accuracy=0,confirmed=True)
        self.core.quality15.review(answer["id"],accuracy=False,confirmed=True)
        self.assertIn("фактическая ошибка",self.core.quality15.guidance(convo))
        self.assertIn(str(answer["id"]),self.core.quality15.guidance(convo))
        self.assertEqual(self.core.quality15.guidance(other),"")
        new_user=self.store.append_messages(convo,[{
            "role":"user","content":"Продолжай с проверкой проекта."
        }])[0]
        context=self.core.life10.chat_context(
            self.core,new_user["content"],convo,
            self.store.list_messages(convo,limit=18),max_chars=1650)
        self.assertIn("фактическая ошибка",context)
        other_user=self.store.append_messages(other,[{
            "role":"user","content":"Продолжай с проектом."
        }])[0]
        other_context=self.core.life10.chat_context(
            self.core,other_user["content"],other,
            self.store.list_messages(other,limit=18),max_chars=1650)
        self.assertNotIn("фактическая ошибка",other_context)
        # Positive revision of the explicit rating stops the hint.
        self.core.quality15.review(answer["id"],accuracy=True,confirmed=True)
        self.assertEqual(self.core.quality15.guidance(convo),"")

    def test_real_answers_grouped_by_actual_model_without_claim_of_causality(self):
        convo,a=self.exchange("Что улучшить?","Базовый ответ.")
        _,b=self.exchange("Теперь лучше?","Вторая версия.",convo)
        self.core.scheduler.record_answer(a["id"],"test/model",200,12)
        self.core.scheduler.record_answer(b["id"],"test/model",180,16)
        self.core.quality15.review(
            a["id"],accuracy=False,clarity=False,confirmed=True)
        self.core.quality15.review(
            b["id"],accuracy=True,clarity=True,confirmed=True)
        report=self.core.quality15.report()
        self.assertEqual(report["reviewed_real_answers"],2)
        grouped=report["by_model"]["test/model"]
        self.assertEqual(grouped["dimensions"]["accuracy"]["samples"],2)
        self.assertEqual(grouped["dimensions"]["accuracy"]["positive_rate"],0.5)
        self.assertFalse(grouped["reliable_comparison"])
        self.assertTrue(report["no_claimed_causal_improvement"])
        self.assertTrue(report["source_check_not_truth_verification"])
        self.assertEqual(len(self.core.quality15.recent(convo)["items"]),2)

    def test_user_review_validation_and_protected_persona(self):
        original=(launcher.ROOT/"soul"/"personality.json").read_bytes()
        convo,assistant=self.exchange("Проверь факты","Есть информация")
        user=self.store.list_messages(convo)[0]
        for candidate in (0,-5,"1",True,user["id"],99999):
            with self.assertRaises(ValueError):
                self.core.quality15.review(
                    candidate,accuracy=False,confirmed=True)
        for bad in ("wrong",2,{},[]):
            with self.assertRaises(ValueError):
                self.core.quality15.review(
                    assistant["id"],clarity=bad,confirmed=True)
        self.core.quality15.review(
            assistant["id"],clarity=True,grounding=False,confirmed=True)
        self.assertIn("не хватало подтверждений",self.core.quality15.guidance(convo))
        self.assertEqual(original,(launcher.ROOT/"soul"/"personality.json").read_bytes())
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM evolution_trials WHERE state='active'"
            ).fetchone()[0],0)
        self.assertTrue(self.core.quality15.status(self.core)[
            "protected_persona_unchanged"])

    def test_schema16_preupgrade_backup_and_reply_integrity(self):
        convo,answer=self.exchange("Вопрос до миграции","Старый ответ")
        with closing(self.store.connect()) as db, db:
            db.execute("PRAGMA user_version=15")
        launcher.init_database(self.path)
        from tsukiyora.memory.migration_guard import SCHEMA_VERSION
        self.assertEqual(SCHEMA_VERSION,16)
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute(
                "PRAGMA user_version").fetchone()[0],16)
            self.assertEqual(db.execute(
                "SELECT content FROM messages WHERE id=?",
                (answer["id"],)).fetchone()[0],"Старый ответ")
        self.assertTrue(list((self.path.parent/"backups").glob("*.sqlite3")))


class RealAnswerQualityApiTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path=Path(temp.name)/"data"/"memory.sqlite3"
        launcher.init_database(path)
        self.path=path
        self.server=launcher.SoulServer(
            ("127.0.0.1",0),launcher.load_personality(),path)
        self.thread=threading.Thread(
            target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(lambda:(self.server.shutdown(),
                         self.server.server_close(),self.thread.join(timeout=6)))
        self.base="http://127.0.0.1:"+str(self.server.server_address[1])
        self.http=build_opener(ProxyHandler({}))

    def get(self,path):
        with self.http.open(self.base+path,timeout=12) as r:
            return json.load(r)

    def post(self,path,payload,origin=None):
        request=Request(
            self.base+path,data=json.dumps(payload).encode(),
            method="POST",headers={
                "Content-Type":"application/json",
                "Origin":origin or self.base
            })
        with self.http.open(request,timeout=12) as r:
            return json.load(r)

    def test_real_answer_api_consent_and_no_other_conversation(self):
        store=MemoryStore(self.path)
        convo=store.create_conversation("Разговор")
        message=store.append_messages(convo["id"],[{
            "role":"assistant","content":"Реальный сохранённый ответ"
        }])[0]
        status=self.get("/api/soul/answers15")
        self.assertEqual(status["version"],"14.1")
        rec=self.get("/api/soul/answers15/recent?conversation_id="+convo["id"])
        self.assertEqual(rec["items"][0]["id"],message["id"])
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/answers15/review",{
                "message_id":message["id"],"accuracy":False})
        self.assertEqual(err.exception.code,400)
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/answers15/review",{
                "message_id":message["id"],"accuracy":False,"confirmed":True
            },origin="https://attacker.invalid")
        self.assertEqual(err.exception.code,403)
        reviewed=self.post("/api/soul/answers15/review",{
            "message_id":message["id"],"accuracy":False,"confirmed":True})
        self.assertTrue(reviewed["recorded"])
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/answers15/inspect",{
                "message_id":message["id"]})
        self.assertEqual(err.exception.code,400)
        audited=self.post("/api/soul/answers15/inspect",{
            "message_id":message["id"],"confirmed":True})
        self.assertEqual(audited["source_state"],"no_citations")

    def test_nonstream_real_chat_is_audited_once_without_new_paid_call(self):
        def fake_provider(method, resource, key, payload=None):
            if resource=="models":
                return {"data":[{"id":"test/chat"}]}
            if resource=="chat/completions":
                return {"choices":[{"message":{
                    "content":"Ответ на настоящий вопрос без выдуманной ссылки."}}]}
            raise AssertionError("Unexpected resource "+str(resource))
        with patch.object(cloudru,"_request",side_effect=fake_provider) as remote:
            self.post("/api/cloud/key",{"api_key":"test-nonreal"})
            self.get("/api/cloud/models")
            self.post("/api/cloud/model",{"model":"test/chat"})
            answer=self.post("/api/chat",{
                "content":"Расскажи про проект, укажи источник",
                "client_message_id":"v14-1"})
            self.assertFalse(answer["cached"])
            self.assertEqual(remote.call_count,2,
                             "One model discovery + exactly one chat response")
            convo=answer["conversation_id"]
        stored=self.get("/api/soul/answers15/recent?conversation_id="+convo)
        self.assertEqual(len(stored["items"]),1)
        self.assertEqual(stored["items"][0]["source_state"],"no_citations")
        self.assertEqual(stored["items"][0]["asked_for_citations"],1)
        with closing(MemoryStore(self.path).connect()) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM answer15_audits").fetchone()[0],1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM usage_estimates").fetchone()[0],1)

    def test_ui_wiring_stream_and_bat_preserved(self):
        root=launcher.ROOT
        html=(root/"ui"/"index.html").read_text(encoding="utf-8")
        app=(root/"ui"/"app.ts").read_text(encoding="utf-8")
        stream=(root/"tsukiyora"/"cloud"/"stream_runtime.py"
                ).read_text(encoding="utf-8")
        for item in ("answers15","answers15-message","answers15-refresh",
                     "answers15-save","answers15-inspect"):
            self.assertIn('id="'+item+'"',html)
        self.assertIn("/api/soul/answers15/review",app)
        self.assertIn("quality15.observe",stream)
        self.assertTrue((root/"Tsukiyora.bat").exists())


if __name__=="__main__":
    unittest.main()
