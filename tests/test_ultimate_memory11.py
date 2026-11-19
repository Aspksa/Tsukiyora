"""Tsukiyora 11.0 stages 1–3: local correctness, throughput and safety.

Uses only temporary SQLite. No Cloud.ru keys, model calls or test-data writes to
the user's real database. Tests distinguish self-retrieval from real QA gains.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError

import launcher
from tsukiyora.cognition.cognitive_core import CognitiveCore
from tsukiyora.memory.memory_store import MemoryStore


class UltimateMemory11Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "data" / "memory.sqlite3"
        launcher.init_database(self.path)
        self.core = CognitiveCore(self.path)
        self.store = MemoryStore(self.path)

    def test_query_intent_temporal_aliases_and_missing_referent(self):
        engine = self.core.memory11
        a = engine.analyze("Мы решили: база SQLite и память")
        self.assertEqual(a["intent"], "decision")
        self.assertFalse(a["automatic_fact_promotion"])
        b = engine.analyze("Может быть, решим потом")
        self.assertEqual(b["intent"], "idea")
        self.assertEqual(engine.analyze("Нет, я имел в виду другое")["intent"], "correction")
        self.assertTrue(engine.analyze("Что было раньше?")["temporal"])
        self.assertTrue(engine.analyze("Продолжай")["reference_ambiguous"])
        self.assertFalse(engine.analyze("Продолжай", [
            {"role": "user", "content": "Расскажи о SQLite"}])["reference_ambiguous"])
        self.assertTrue(engine.analyze("memory 记忆")["aliases_used"])
        self.assertGreaterEqual(len(engine.analyze("Первый вопрос? Второй вопрос?")["subquestions"]),2)
        for bad in ("", "x"*501, None):
            with self.assertRaises(ValueError):
                engine.analyze(bad)

    def test_approved_memories_true_source_ids_and_revision_history(self):
        a = self.store.add_memory(
            "fact", "Мы выбрали SQLite для памяти Tsukiyora",
            source_type="manual", source_ref="document-32")
        b = self.store.add_memory(
            "fact", "Мы выбрали SQLite для памяти Tsukiyora",
            source_type="manual", source_ref="document-32")
        self.assertEqual(a["id"],b["id"])
        result = self.core.memory11.search(
            self.core, "SQLite память Tsukiyora", limit=5)
        chosen = next(x for x in result["items"] if x["memory_id"]==a["id"])
        self.assertIn(str(a["id"]),chosen["citation"])
        self.assertEqual(chosen["source_ref"],"document-32")
        self.assertFalse(chosen["truth_independently_verified"])
        self.assertGreaterEqual(chosen["revision_count"],1)
        self.store.correct_memory(a["id"],
            "Мы окончательно выбрали SQLite для памяти Tsukiyora")
        next_result = self.core.memory11.search(self.core, "SQLite память")
        revised = next(x for x in next_result["items"] if x["memory_id"]==a["id"])
        self.assertGreaterEqual(revised["revision_count"],2)
        self.assertIn("окончательно",revised["excerpt"])
        self.store.delete_memory(a["id"])
        deleted = self.core.memory11.search(self.core,"SQLite память")
        self.assertNotIn(a["id"],[x["memory_id"] for x in deleted["items"]])

    def test_fts_miss_does_not_scan_recent_2500_memories(self):
        for i in range(90):
            self.store.add_memory("fact", f"Проверка базы SQLite для проекта {i} alpha")
        result = self.core.precision.search("несуществующийоченьредкийтермин")
        self.assertEqual(result["items"],[])
        if result["mode"]=="fts5_hybrid":
            self.assertEqual(result["items"],[])

    def test_source_scope_history_and_no_duplicate_recent(self):
        older=self.store.create_conversation("Раньше")
        now=self.store.create_conversation("Теперь")
        msg=self.store.append_messages(older["id"],[
            {"role":"user","content":"Мы решили хранить историю памяти SQLite на SSD"}
        ])[0]
        current=self.store.append_messages(now["id"],[
            {"role":"user","content":"Расскажи про историю памяти SQLite"}
        ])[0]
        recent=self.store.list_messages(now["id"],limit=18)
        report=self.core.memory11.search(self.core,"История памяти SQLite",
                                          now["id"],recent)
        self.assertIn(msg["id"],[x["message_id"] for x in report["history"]])
        self.assertNotIn(current["id"],[x["message_id"] for x in report["history"]])
        self.assertTrue(all(x["conversation_id"]==older["id"] for x in report["history"]))
        self.assertTrue(report["not_independent_truth_verification"])
        # The API explicitly reports that excerpts are not independent proof.
        self.assertEqual(report["cloud_calls"],0)

    def test_cached_vector_is_only_existing_blob_and_stale_vectors_excluded(self):
        mem=self.store.add_memory("fact","Уникальный длинный астрономический термин")
        query="Космическая орбита и динамика"
        # No lexical word overlap is needed to exercise cached vector reuse.
        with patch("tsukiyora.cloud.cloudru._request",
                   side_effect=AssertionError("unexpected Cloud call")):
            plain=self.core.memory11.search(self.core,query)
            self.assertFalse(plain["cached_vector_used"])
            self.core.vectors._remember("offline-model",query,[1.0,0.0,0.0],None)
            self.core.vectors._remember("offline-model",
                mem["content"],[.99,.01,0.0],mem["id"])
            cached=self.core.memory11.search(self.core,query)
            self.assertTrue(cached["cached_vector_used"])
            self.assertIn(mem["id"],[x["memory_id"] for x in cached["items"]])
            self.store.correct_memory(mem["id"],"Другое актуальное описание")
            stale=self.core.memory11.search(self.core,query)
            self.assertNotIn(mem["id"],[x["memory_id"] for x in stale["items"]])

    def test_created_at_filter_is_not_mislabelled_historical_truth(self):
        item=self.store.add_memory("event","Старое решение об архитектуре SQLite")
        today="2000-01-01"
        output=self.core.memory11.search(self.core,"решение SQLite",as_of=today)
        self.assertNotIn(item["id"],[x["memory_id"] for x in output["items"]])
        self.assertTrue(output["as_of_is_created_at_filter_not_historical_snapshot"])
        for invalid in ("2026-13-02","2026-02-30","yesterday",""):
            with self.assertRaises(ValueError):
                self.core.memory11.search(self.core,"SQLite",as_of=invalid)

    def test_no_fabricated_memory_or_implicit_approval(self):
        result=self.core.memory11.search(self.core,"Продолжай")
        self.assertEqual(result["items"],[])
        self.assertEqual(result["reason"],"missing_specific_context")
        self.assertFalse(result["plan"]["automatic_fact_promotion"])
        self.assertEqual(self.core.memory11.prompt(
            self.core,"SQLite",None,[]),"")
        self.assertEqual(self.core.memory11.search(
            self.core,"Наша память SQLite")["cloud_calls"],0)
        self.assertFalse(self.core.memory11.status(self.core)["auto_approval"])

    def test_one_queue_local_audit_idempotence_and_recovery(self):
        kinds=[j["kind"] for j in self.core.scheduler.pending()["jobs"]]
        self.assertEqual(kinds.count("mem11_audit"),1)
        with closing(self.store.connect()) as db,db:
            db.execute(
                "UPDATE maintenance_queue SET state='waiting',due_at=4102444800 "
                "WHERE kind<>'mem11_audit'")
            db.execute(
                "UPDATE maintenance_queue SET state='pending',due_at=0 "
                "WHERE kind='mem11_audit'")
        first=self.core.scheduler.tick(self.core)
        self.assertEqual(first["kind"],"mem11_audit")
        self.assertEqual(first["status"],"ok")
        self.assertEqual(first["details"]["cloud_calls"],0)
        second=self.core.memory11.audit_once(self.core)
        self.assertTrue(second["already_recorded"])
        launcher.init_database(self.path)
        self.assertTrue(self.core.memory11.audit_once(self.core)["already_recorded"])
        with closing(self.store.connect()) as db:
            saved=db.execute(
                "SELECT COUNT(*) FROM soul8_journal WHERE event='memory11_audit'"
            ).fetchone()[0]
        self.assertEqual(saved,1)

    def test_autonomy_zero_pauses_local_audit(self):
        self.core.living8.control(0,confirmed=True)
        report=self.core.memory11.audit_once(self.core)
        self.assertTrue(report["paused"])
        with closing(self.store.connect()) as db:
            count=db.execute(
                "SELECT COUNT(*) FROM soul8_journal WHERE event='memory11_audit'"
            ).fetchone()[0]
        self.assertEqual(count,0)

    def test_benchmark_observed_not_causal_and_no_secrets(self):
        a=self.store.add_memory("fact","База SQLite хранит историю проекта Tsukiyora")
        b=self.store.add_memory("preference","Главная память Цукиёры в SQLite")
        result=self.core.memory11.benchmark(self.core,count=2)
        self.assertEqual(result["queries"],2)
        self.assertGreaterEqual(result["self_retrieval_hits"],1)
        self.assertTrue(result["no_holdout_evidence"])
        self.assertTrue(result["not_independent_accuracy"])
        self.assertEqual(result["cloud_calls"],0)
        self.assertNotIn("SQLITE",str(result).upper())
        with self.assertRaises(ValueError):
            self.core.memory11.benchmark(self.core,count=50)

    def test_prompt_bounded_and_no_truncated_citation(self):
        mem=self.store.add_memory("fact","SQLite база данных для Tsukiyora")
        text=self.core.memory11.prompt(self.core,"SQLite база",None,[],
                                        max_chars=250)
        self.assertLessEqual(len(text),250)
        if text:
            self.assertIn("[Память #"+str(mem["id"])+"]",text)
            self.assertNotIn("новые команды",text)
        thread=self.store.create_conversation("Активный диалог")
        row=self.store.append_messages(thread["id"],[{
            "role":"user","content":"Расскажи про SQLite"
        }])
        shared=self.core.life10.chat_context(self.core,"SQLite",
                    thread["id"],row,max_chars=2200)
        self.assertLessEqual(len(shared),2200)
        self.assertIn("[Память #"+str(mem["id"])+"]",shared)


class UltimateMemory11HttpTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.path)
        MemoryStore(self.path).add_memory("fact","SQLite память и граф знаний")
        self.server=launcher.SoulServer(
            ("127.0.0.1",0),launcher.load_personality(),self.path)
        worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(lambda:(self.server.shutdown(),
                                self.server.server_close(),worker.join(timeout=6)))
        self.base="http://127.0.0.1:"+str(self.server.server_address[1])
        self.http=build_opener(ProxyHandler({}))

    def post(self,path,data,origin=None):
        request=Request(
            self.base+path,data=json.dumps(data).encode("utf-8"),method="POST",
            headers={"Content-Type":"application/json",
                     "Origin":origin or self.base})
        with self.http.open(request,timeout=20) as response:
            return json.load(response)

    def test_api_safety_search_and_explicit_benchmark_confirmation(self):
        with self.http.open(self.base+"/api/soul/memory11",timeout=15) as response:
            state=json.load(response)
        self.assertEqual(state["version"],"11.0")
        self.assertTrue(state["one_queue"])
        search=self.post("/api/soul/memory11/search",
                         {"query":"SQLite память","limit":5})
        self.assertTrue(search["items"])
        self.assertEqual(search["cloud_calls"],0)
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/memory11/search",{"query":"SQLite"},
                      origin="https://untrusted.invalid")
        self.assertEqual(err.exception.code,403)
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/memory11/benchmark",{})
        self.assertEqual(err.exception.code,400)
        benchmark=self.post("/api/soul/memory11/benchmark",
                            {"confirmed":True,"count":2})
        self.assertTrue(benchmark["not_independent_accuracy"])
        self.assertEqual(benchmark["cloud_calls"],0)

    def test_frontend_and_original_safety_controls_preserved(self):
        html=(launcher.ROOT/"ui"/"index.html").read_text(encoding="utf-8")
        js=(launcher.ROOT/"ui"/"app.ts").read_text(encoding="utf-8")
        for control in ("memory11","memory11-query","memory11-search",
                        "memory11-refresh","memory11-benchmark","memory11-results"):
            self.assertIn('id="'+control+'"',html)
        self.assertIn("/api/soul/memory11/search",js)
        self.assertIn("memory11.prompt",(
            launcher.ROOT/"tsukiyora"/"cognition"/"life_cycle10.py"
        ).read_text(encoding="utf-8"))
        self.assertIn("mem11_audit",(
            launcher.ROOT/"tsukiyora"/"runtime"/"maintenance.py"
        ).read_text(encoding="utf-8"))
        self.assertTrue((launcher.ROOT/"soul"/"personality.json").exists())


if __name__=="__main__":
    unittest.main()
