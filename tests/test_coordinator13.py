"""Tsukiyora 13: source-backed coordination and safe runtime regressions."""
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
from tsukiyora.runtime.maintenance import priority_for
from tsukiyora.memory.memory_store import MemoryStore


class CoordinatedCognition13Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "data" / "memory.sqlite3"
        launcher.init_database(self.path)
        self.core = CognitiveCore(self.path)
        self.store = MemoryStore(self.path)

    def test_exact_selected_items_and_explicitly_omitted_5(self):
        info = self.core.coordinator13.status(self.core)
        self.assertEqual(info["selected_improvements"],
            [1,2,3,4,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,
             21,22,23,24,25,26,27,28,29,30])
        self.assertTrue(info["omitted_improvement_5"])
        self.assertTrue(info["one_worker_one_sqlite"])
        self.assertEqual(info["new_background_model_calls"], 0)
        self.assertFalse(info["automatic_skill_persona_or_memory_approval"])
        self.assertEqual(info["memory"]["truth_independently_verified"], False)

    def test_queue_prioritization_old_job_aging_and_only_one_job_per_tick(self):
        now = 123456789
        self.assertLess(priority_for("integrity",0,now),
                        priority_for("cog5_digest",0,now))
        self.assertEqual(priority_for("cog5_digest",now-4000,now),0)
        self.assertEqual(priority_for("cog5_digest",now-5,now),3)
        with closing(self.store.connect()) as db, db:
            db.execute(
                "UPDATE maintenance_queue SET due_at=4102444800,state='waiting'"
            )
            db.execute(
                "UPDATE maintenance_queue SET due_at=0,state='pending' "
                "WHERE kind IN ('cog5_digest','integrity')")
        result = self.core.scheduler.tick(self.core)
        self.assertEqual(result["status"],"ok")
        self.assertEqual(result["kind"],"integrity")
        with closing(self.store.connect()) as db:
            pending = db.execute(
                "SELECT state,executions FROM maintenance_queue WHERE kind='cog5_digest'"
            ).fetchone()
        self.assertEqual(pending["executions"],0)
        self.assertEqual(pending["state"],"pending")

    def test_background_notes_fair_paging_no_recreation_after_prune(self):
        convo = self.store.create_conversation("Test")
        sources = [
            self.store.append_messages(convo["id"], [{
                "role":"user", "content": f"Запомни уникальное событие {i:04d}"
            }])[0]["id"] for i in range(18)
        ]
        with closing(self.store.connect()) as db, db:
            for i, message_id in enumerate(sources):
                db.execute(
                    "INSERT INTO memory_suggestions "
                    "(message_id,kind,content,fingerprint) VALUES(?,'fact',?,?)",
                    (message_id,f"Уникальный тест {i}",f"cursor-{i}")
                )
        # A tiny notebook retention for the test makes re-creation visible
        # without running hundreds of maintenance cycles.
        with patch("tsukiyora.cognition.inner_world12.MAX_NOTES",5):
            newly = []
            for i in range(13):
                run = self.core.inner12.cycle(self.core)
                self.assertLessEqual(run["new_notes"],8)
                newly.append(run["new_notes"])
            self.assertEqual(sum(newly),18)
            self.assertTrue(all(x==0 for x in newly[-2:]))
            with closing(self.store.connect()) as db:
                cursor=db.execute(
                    "SELECT last_id FROM soul13_source_cursors "
                    "WHERE kind='check_memory'"
                ).fetchone()[0]
                notes=db.execute("SELECT COUNT(*) FROM soul12_notes").fetchone()[0]
            self.assertEqual(cursor,18)
            self.assertLessEqual(notes,5)
            before=self.core.inner12.cycle(self.core)
            self.assertEqual(before["new_notes"],0)
        launcher.init_database(self.path)
        self.assertEqual(self.core.inner12.cycle(self.core)["new_notes"],0)

    def test_disabled_local_practice_pauses_paged_notebook(self):
        self.core.living8.control(0,confirmed=True)
        paused = self.core.inner12.cycle(self.core)
        self.assertTrue(paused["paused"])
        with closing(self.store.connect()) as db:
            cursor = db.execute(
                "SELECT COUNT(*) FROM soul13_source_cursors"
            ).fetchone()[0]
        self.assertEqual(cursor,0)

    def test_bitemporal_dates_are_user_attested_and_version_bounded(self):
        memory=self.store.add_memory(
            "fact", "Tsukiyora применяет SQLite для памяти",
            source_type="manual",source_ref="decision-1")
        with self.assertRaises(ValueError):
            self.core.memory13.annotate(
                memory["id"], effective_from="2026-01-01",
                rationale="Решение от 2026 года")
        record=self.core.memory13.annotate(
            memory["id"],effective_from="2025-01-01",
            effective_until="2027-01-01",entity_key="Tsukiyora",
            rationale="Утверждено пользователем для проекта",confirmed=True)
        self.assertTrue(record["source_unchanged"])
        duplicate=self.core.memory13.annotate(
            memory["id"],effective_from="2025-01-01",
            effective_until="2027-01-01",entity_key="Tsukiyora",
            rationale="Утверждено пользователем для проекта",confirmed=True)
        self.assertTrue(duplicate["already_recorded"])
        timeline=self.core.memory13.timeline(memory["id"])
        self.assertEqual(len(timeline["validity_annotations"]),1)
        self.assertTrue(timeline["validity_annotations"][0][
            "applies_to_current_version"])
        old=self.core.memory11.search(
            self.core,"SQLite Tsukiyora",as_of="2024-01-01")
        self.assertNotIn(memory["id"],[r["memory_id"] for r in old["items"]])
        valid=self.core.memory11.search(
            self.core,"SQLite Tsukiyora",as_of="2026-12-01")
        self.assertIn(memory["id"],[r["memory_id"] for r in valid["items"]])
        self.store.correct_memory(memory["id"],
            "Tsukiyora использует SQLite для локальной базы")
        timeline=self.core.memory13.timeline(memory["id"])
        self.assertFalse(timeline["validity_annotations"][0][
            "applies_to_current_version"])
        valid=self.core.memory11.search(
            self.core,"SQLite Tsukiyora",as_of="2026-12-01")
        for item in valid["items"]:
            if item["memory_id"]==memory["id"]:
                self.assertEqual(item["time_basis"],"stale_annotation_excluded")

    def test_temporal_validation_and_delete_safety(self):
        memory=self.store.add_memory("fact","Старый локальный выбор SQLite")
        with self.assertRaises(ValueError):
            self.core.memory13.annotate(
                memory["id"],effective_from="2026-09-10",
                effective_until="2026-07-10",
                rationale="Подтверждено вручную",confirmed=True)
        for value in ("2026-02-30","2026-13-01","today",""):
            with self.assertRaises(ValueError):
                self.core.memory13.annotate(
                    memory["id"],effective_from=value,
                    rationale="Подтверждено вручную",confirmed=True)
        self.core.memory13.annotate(
            memory["id"],effective_from="2026-01-01",
            rationale="Подтверждено вручную",confirmed=True)
        self.store.delete_memory(memory["id"])
        timeline=self.core.memory13.timeline(memory["id"])
        self.assertFalse(timeline["validity_annotations"][0][
            "applies_to_current_version"])
        result=self.core.memory11.search(self.core,"локальный выбор SQLite")
        self.assertNotIn(memory["id"],[x["memory_id"] for x in result["items"]])

    def test_honest_user_attested_retrieval_benchmark(self):
        memory=self.store.add_memory(
            "fact","Только SQLite используется для Tsukiyora")
        with self.assertRaises(ValueError):
            self.core.coordinator13.add_eval_case(
                "Где хранится память?",memory["id"])
        saved=self.core.coordinator13.add_eval_case(
            "SQLite Tsukiyora",memory["id"],confirmed=True)
        repeated=self.core.coordinator13.add_eval_case(
            "SQLite Tsukiyora",memory["id"],confirmed=True)
        self.assertEqual(saved["id"],repeated["id"])
        with patch("tsukiyora.cloud.cloudru._request",
                   side_effect=AssertionError("No cloud")):
            bench=self.core.coordinator13.benchmark(self.core)
        self.assertEqual(bench["valid_cases"],1)
        self.assertEqual(bench["hits_at_k"],1)
        self.assertEqual(bench["recall_at_k"],1.0)
        self.assertEqual(bench["cloud_calls"],0)
        self.assertIn("not_llm",bench["metric"])
        self.store.delete_memory(memory["id"])
        bench=self.core.coordinator13.benchmark(self.core)
        self.assertEqual(bench["valid_cases"],0)

    def test_review_board_and_feedback_categories_redact_private_text(self):
        convo=self.store.create_conversation("Test")
        user=self.store.append_messages(convo["id"],[{
            "role":"user","content":"Запомни мой проект secret-never-in-board"
        }])[0]
        self.core.growth.propose(user["id"],user["content"])
        assistant=self.store.append_messages(convo["id"],[{
            "role":"assistant","content":"Тестовый ответ"
        }])[0]
        with closing(self.store.connect()) as db, db:
            db.execute(
                "INSERT INTO cognitive_feedback(message_id,helpful,note) "
                "VALUES(?,0,'неверная память secret-never-in-board')",
                (assistant["id"],)
            )
        board=self.core.coordinator13.review_board()
        self.assertIn("memory",[x["kind"] for x in board["items"]])
        self.assertNotIn("secret-never-in-board",str(board))
        signals=self.core.coordinator13.issue_signals()
        self.assertEqual(signals["possible_categories"]["memory"],1)
        self.assertTrue(signals["raw_feedback_exposed"] is False)
        self.assertNotIn("secret-never-in-board",str(signals))

    def test_contextual_short_reply_never_creates_permission(self):
        hint=self.core.coordinator13.followup_hint(
            "Продолжай",[{"id":8,"role":"user","content":
                          "Мы обсуждали восстановление памяти SQLite"}])
        self.assertIn("[Сообщение #8]",hint)
        self.assertIn("Уточни",hint)
        self.assertNotIn("выполнено",hint.casefold())
        self.assertEqual(self.core.coordinator13.followup_hint(
            "Да",[{"id":8,"role":"assistant","content":"Сделаю"}]),"")
        self.assertEqual(self.core.coordinator13.followup_hint(
            "Новый подробный вопрос",[]),"")

    def test_old_cached_vectors_reachable_via_incremental_buckets(self):
        target = self.store.add_memory(
            "fact", "Редкий драгоценный изумруд пустынного цвета")
        text = "Галактическая энергия космической орбиты"
        self.core.vectors._remember(
            "offline-test", target["content"], [1.0, 0.0, 0.0], target["id"])
        for i in range(178):
            other = self.store.add_memory(
                "fact", "Ежегодное заседание совета по протоколу "+str(i))
            self.core.vectors._remember(
                "offline-test", other["content"], [0.0, 1.0, 0.0], other["id"])
        self.core.vectors._remember(
            "offline-test", text, [1.0, 0.0, 0.0], None)
        # Without V13 buckets, the V11 144-most-recent pool misses target.
        with closing(self.store.connect()) as db:
            old = self.core.memory11._cached_vector_ranking(
                db, text, vector_index=None)
        self.assertNotIn(target["id"],old)
        batches = []
        for _ in range(15):
            step = self.core.vector13.index_step(limit=30)
            batches.append(step)
            if step["pass_complete"]:
                break
        self.assertTrue(batches[-1]["pass_complete"])
        self.assertGreaterEqual(
            self.core.vector13.status()["existing_indexed_vectors"],179)
        with patch("tsukiyora.cloud.cloudru._request",
                   side_effect=AssertionError("No model calls")):
            found = self.core.memory11.search(self.core,text,limit=8)
        self.assertIn(target["id"],
                      [r["memory_id"] for r in found["items"]])
        # A correction invalidates the SHA even before the next background pass.
        self.store.correct_memory(
            target["id"],"Исправленное актуальное описание камня")
        stale = self.core.memory11.search(self.core,text,limit=8)
        self.assertNotIn(target["id"],
                         [r["memory_id"] for r in stale["items"]])

    def test_composite_vector_cursor_covers_two_models_per_memory(self):
        memory = self.store.add_memory("fact","Разные модельные векторы")
        self.core.vectors._remember("a-model",memory["content"],
                                    [1.0,0.0],memory["id"])
        self.core.vectors._remember("z-model",memory["content"],
                                    [0.0,1.0],memory["id"])
        self.core.vector13.index_step(limit=1)
        with closing(self.store.connect()) as db:
            count=db.execute(
                "SELECT COUNT(*) FROM memory13_vector_buckets "
                "WHERE memory_id=?", (memory["id"],)
            ).fetchone()[0]
        self.assertEqual(count,1)
        self.core.vector13.index_step(limit=1)
        with closing(self.store.connect()) as db:
            count=db.execute(
                "SELECT COUNT(*) FROM memory13_vector_buckets "
                "WHERE memory_id=?", (memory["id"],)
            ).fetchone()[0]
        self.assertEqual(count,2)
        self.assertEqual(self.core.vector13.status()["cloud_calls"],0)

    def test_schema_v14_upgrades_existing_v13_with_preflight_backup(self):
        from tsukiyora.memory.migration_guard import SCHEMA_VERSION
        self.assertEqual(SCHEMA_VERSION,14)
        kept = self.store.add_memory(
            "fact","История SQLite до обновления схемы")
        with closing(self.store.connect()) as db, db:
            db.execute("PRAGMA user_version=13")
        launcher.init_database(self.path)
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute(
                "PRAGMA user_version").fetchone()[0],14)
            self.assertIsNotNone(db.execute(
                "SELECT id FROM memory_items WHERE id=?",
                (kept["id"],)).fetchone())
        backups=list((self.path.parent/"backups").glob("*.sqlite3"))
        self.assertTrue(backups)
        import sqlite3
        with closing(sqlite3.connect(backups[-1])) as db:
            row=db.execute(
                "SELECT id FROM memory_items WHERE id=?",(kept["id"],)
            ).fetchone()
        self.assertIsNotNone(row)

    def test_bat_and_protected_persona_not_modified(self):
        root=launcher.ROOT
        self.assertTrue((root/"Tsukiyora.bat").exists())
        before=(root/"soul"/"personality.json").read_bytes()
        self.core.coordinator13.status(self.core)
        self.assertEqual(before,(root/"soul"/"personality.json").read_bytes())


class CoordinatedCognition13HttpTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path=Path(temp.name)/"data"/"memory.sqlite3"
        launcher.init_database(path)
        self.memory=MemoryStore(path).add_memory(
            "fact","SQLite единственная локальная база данных")
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
        with self.http.open(self.base+path,timeout=15) as r:
            return json.load(r)

    def post(self,path,obj,origin=None):
        req=Request(
            self.base+path,data=json.dumps(obj).encode(),method="POST",
            headers={"Content-Type":"application/json",
                     "Origin":origin or self.base})
        with self.http.open(req,timeout=15) as r:
            return json.load(r)

    def test_status_time_annotation_and_explicit_consent(self):
        state=self.get("/api/soul/coordinator13")
        self.assertEqual(state["version"],"13.0")
        self.assertTrue(self.get("/api/soul/coordinator13/reviews")[
            "duplicate_keys_hidden"])
        self.assertFalse(self.get("/api/soul/coordinator13/issues")[
            "raw_feedback_exposed"])
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/temporal13/annotate",{
                "memory_id":self.memory["id"],
                "effective_from":"2026-01-01",
                "rationale":"Подтверждённое решение"})
        self.assertEqual(err.exception.code,400)
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/temporal13/annotate",{
                "memory_id":self.memory["id"],
                "effective_from":"2026-01-01",
                "rationale":"Подтверждённое решение",
                "confirmed":True},origin="https://attacker.invalid")
        self.assertEqual(err.exception.code,403)
        result=self.post("/api/soul/temporal13/annotate",{
            "memory_id":self.memory["id"],
            "effective_from":"2026-01-01",
            "rationale":"Подтверждённое решение",
            "confirmed":True})
        self.assertTrue(result["source_unchanged"])
        timeline=self.get(
            "/api/soul/temporal13/timeline?memory_id="+
            str(self.memory["id"]))
        self.assertEqual(len(timeline["validity_annotations"]),1)

    def test_eval_case_and_ui_wiring(self):
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/coordinator13/benchmark",{})
        self.assertEqual(err.exception.code,400)
        response=self.post("/api/soul/coordinator13/eval-case",{
            "question":"SQLite локальная база",
            "expected_memory_id":self.memory["id"],
            "confirmed":True})
        self.assertGreater(response["id"],0)
        bench=self.post("/api/soul/coordinator13/benchmark",{
            "confirmed":True})
        self.assertEqual(bench["valid_cases"],1)
        root=launcher.ROOT
        html=(root/"ui"/"index.html").read_text(encoding="utf-8")
        js=(root/"ui"/"app.ts").read_text(encoding="utf-8")
        for marker in ("coordinator13","coordinator13-refresh",
                       "coordinator13-annotate","coordinator13-benchmark",
                       "coordinator13-add-case"):
            self.assertIn('id="'+marker+'"',html)
        self.assertIn("/api/soul/coordinator13/benchmark",js)


if __name__=="__main__":
    unittest.main()
