"""v1.1 gap-completion tests on current v1.2 main: non-destructive history and semantic links.

Everything runs locally on TEMP SQLite, never calls Cloud.ru or user storage.
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
from unittest.mock import patch
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import launcher
from memory_store import MemoryStore
from conversation_memory import ConversationMemory
from semantic_groups import SemanticGroups
from migration_guard import SCHEMA_VERSION


class HistoryLocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="Tsukiyora Кириллица & ")
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"Рабочий стол" / "тест памяти.sqlite3"
        launcher.init_database(self.db)
        self.store=MemoryStore(self.db)
        self.history=ConversationMemory(self.db)
        self.groups=SemanticGroups(self.db)

    def test_search_full_history_with_provenance_and_no_cloud(self):
        old=self.store.create_conversation("Очень старый диалог")
        recent=self.store.create_conversation("Новый диалог")
        for i in range(41):
            self.store.append_messages(old["id"],[{
                "role":"user","content":"Обсуждаем договор поставки номер "+str(i),
                "client_message_id":"query-"+str(i)}])
        self.store.append_messages(recent["id"],[{
            "role":"user","content":"Совсем другая тема", "client_message_id":"new"}])
        with patch("cloudru._request",side_effect=AssertionError("Cloud call forbidden")):
            result=self.history.search("договор поставки",limit=15)
        self.assertEqual(result["cloud_requests"],0)
        self.assertEqual(len(result["items"]),15)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["items"][0]["conversation_id"],old["id"])
        self.assertTrue(all(x["id"]>0 and "договор" in x["excerpt"].lower()
                            for x in result["items"]))
        continuation=self.history.search("договор поставки",limit=15,
                                           before_id=result["next_before_id"])
        self.assertTrue(all(x["id"]<result["items"][-1]["id"] for x in continuation["items"]))
        self.assertEqual(self.history.search("несуществующий запрос")["items"],[])
        hit=result["items"][-1]
        context=self.history.context(hit["id"])
        self.assertEqual(context["selected_message_id"],hit["id"])
        self.assertTrue(any(x["id"]==hit["id"] for x in context["messages"]))
        self.assertLessEqual(len(context["messages"]),17)
        self.assertEqual(context["cloud_requests"],0)
        for query in ("", " ", "x"*251):
            with self.assertRaises(ValueError):
                self.history.search(query)

    def test_extractive_recap_requires_long_history_and_refreshes_by_id(self):
        conv=self.store.create_conversation("Длинный договор")
        self.assertFalse(self.history.summary(conv["id"])["ready"])
        for i in range(14):
            self.store.append_messages(conv["id"],[{
                "role":"user" if i%2==0 else "assistant",
                "content":"Вопрос о сроке поставки "+str(i),
                "client_message_id":"key-"+str(i)}])
        summary=self.history.summary(conv["id"])
        self.assertTrue(summary["ready"])
        self.assertEqual(summary["message_count"],14)
        self.assertTrue(summary["not_an_llm_summary"])
        self.assertGreater(len(summary["sources"]),3)
        self.assertTrue(all("[Сообщение #"+str(x["message_id"])+"]" in summary["summary"]
                            for x in summary["sources"]))
        self.assertEqual(self.history.summary(conv["id"])["summary"],summary["summary"])
        self.store.append_messages(conv["id"],[{
            "role":"user","content":"Новый вопрос о сроке поставки",
            "client_message_id":"extra"}])
        updated=self.history.summary(conv["id"])
        self.assertEqual(updated["message_count"],15)
        self.assertNotEqual(updated["summary"],summary["summary"])
        self.assertEqual(updated["cloud_requests"],0)

    def test_fts5_backfill_existing_archive_and_parallel_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/"папка с пробелами"/"old.sqlite3"
            store=MemoryStore(db)
            store.initialize()
            conv=store.create_conversation()
            store.append_messages(conv["id"],[{
                "role":"user","content":"Старинное слово для поиска",
                "client_message_id":"old"}])
            service=ConversationMemory(db)
            service.initialize()
            self.assertEqual(len(service.search("Старинное слово")["items"]),1)
            def add(i):
                return store.append_messages(conv["id"],[{
                    "role":"assistant","content":"Новая запись номер "+str(i),
                    "client_message_id":"new"+str(i)}])[0]["id"]
            with ThreadPoolExecutor(max_workers=5) as pool:
                ids=list(pool.map(add,range(25)))
            self.assertEqual(len(set(ids)),25)
            self.assertEqual(len(service.search("Новая запись",limit=30)["items"]),25)
            self.assertEqual(len(service.search("Старинное")["items"]),1)

    def test_confirm_groups_preserves_original_and_revisions(self):
        left=self.store.add_memory("fact","Организация подписала договор поставки оборудования")
        right=self.store.add_memory("fact","Организация подписала договор поставки оборудования вчера")
        first_count=self.store.stats()["memories"]
        with self.assertRaises(ValueError):
            self.groups.confirm(left["id"],right["id"])
        confirmed=self.groups.confirm(left["id"],right["id"],confirmed=True)
        self.assertTrue(confirmed["originals_kept"])
        self.assertEqual(self.store.stats()["memories"],first_count)
        self.assertTrue(self.groups.list()["items"][0]["current"])
        self.store.correct_memory(right["id"],
            "Организация подписала договор поставки оборудования позавчера")
        self.assertFalse(self.groups.list()["items"][0]["current"])
        self.groups.confirm(left["id"],right["id"],confirmed=True)
        self.assertTrue(self.groups.list()["items"][0]["current"])
        self.groups.revoke(left["id"],right["id"],confirmed=True)
        self.assertEqual(self.groups.list()["items"],[])
        with closing(self.store.connect()) as db:
            self.assertGreater(db.execute("SELECT COUNT(*) FROM memory_revisions "
                                           "WHERE memory_id=?",(right["id"],)).fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM semantic_group_events").fetchone()[0],3)

    def test_conflicts_and_cross_kind_cannot_be_equated(self):
        a=self.store.add_memory("fact","Мы никогда не заключали договор")
        b=self.store.add_memory("fact","Мы заключали договор")
        p=self.store.add_memory("preference","Мы заключали договор")
        with self.assertRaises(ValueError):
            self.groups.confirm(a["id"],p["id"],confirmed=True)
        with self.assertRaises(ValueError):
            self.groups.confirm(a["id"],b["id"],confirmed=True)
        self.assertEqual(self.groups.list()["items"],[])

    def test_schema_13_upgrade_creates_snapshot_and_keeps_dialogues(self):
        conv=self.store.create_conversation("Оригинал")
        self.store.append_messages(conv["id"],[{
            "role":"user","content":"Привет из прошлой версии",
            "client_message_id":"old-version"}])
        with closing(self.store.connect()) as db, db:
            db.execute("PRAGMA user_version=12")
        launcher.init_database(self.db)
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],SCHEMA_VERSION)
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0],"ok")
        self.assertEqual(len(self.history.search("Привет из прошлой")["items"]),1)
        self.assertTrue(list((self.db.parent/"backups").glob("memory-*.sqlite3")))


class HistoryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.db)
        self.server=launcher.SoulServer(("127.0.0.1",0),launcher.load_personality(),self.db)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.url="http://127.0.0.1:"+str(self.server.server_address[1])
        self.open=build_opener(ProxyHandler({}))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self,path):
        with self.open.open(self.url+path,timeout=8) as r:
            return json.load(r)

    def post(self,path,body,origin=None):
        req=Request(self.url+path,method="POST",
                    data=json.dumps(body,ensure_ascii=False).encode("utf-8"),
                    headers={"Origin":origin or self.url,"Content-Type":"application/json"})
        with self.open.open(req,timeout=8) as r:
            return json.load(r)

    def test_history_search_summary_and_semantic_same_origin(self):
        store=MemoryStore(self.db)
        convo=store.create_conversation()
        store.append_messages(convo["id"],[{
            "role":"user","content":"Ищу договор поставки",
            "client_message_id":"user1"}])
        result=self.get("/api/memory/history/search?query=%D0%B4%D0%BE%D0%B3%D0%BE%D0%B2%D0%BE%D1%80")
        self.assertEqual(len(result["items"]),1)
        context=self.get("/api/memory/history/context?message_id="+str(result["items"][0]["id"]))
        self.assertEqual(context["messages"][0]["id"],result["items"][0]["id"])
        self.assertFalse(self.get("/api/memory/history/recap?conversation_id="+
                                  convo["id"])["ready"])
        a=store.add_memory("fact","Стандартная гарантия составляет один год")
        b=store.add_memory("fact","Стандартная гарантия составляет ровно один год")
        data=self.post("/api/soul/semantic/link",{
            "left_id":a["id"],"right_id":b["id"],"confirmed":True})
        self.assertTrue(data["originals_kept"])
        self.assertEqual(len(self.get("/api/soul/semantic/groups")["items"]),1)
        with self.assertRaises(HTTPError) as failure:
            self.post("/api/soul/semantic/unlink",{
                "left_id":a["id"],"right_id":b["id"],"confirmed":True},
                origin="https://attacker.invalid")
        self.assertEqual(failure.exception.code,403)
        self.post("/api/soul/semantic/unlink",{
            "left_id":a["id"],"right_id":b["id"],"confirmed":True})
        self.assertEqual(self.get("/api/soul/semantic/groups")["items"],[])

    def test_browser_surface_present_no_fake_e2e(self):
        html=(ROOT/"ui"/"index.html").read_text(encoding="utf-8")
        script=(ROOT/"ui"/"app.ts").read_text(encoding="utf-8")
        for element in ("dialogue-query","dialogue-search","dialogue-recap",
                        "dialogue-results","dialogue-summary"):
            self.assertIn('id="'+element+'"',html)
            self.assertIn('"'+element+'"',script)
        self.assertIn("/api/memory/history/search",script)
        self.assertIn("/api/soul/semantic/link",script)
        self.assertFalse(self.get("/api/system/selftest")["cloud_live_verified"])


if __name__=="__main__":
    unittest.main()
