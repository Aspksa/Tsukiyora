"""GREY MATTER 14 tests: explicit symbolic premises, bounded local reflection.

The exercises certify a deterministic inference engine, NOT a new intelligence
score, real-world truth, private LLM reasoning or model-weight training.
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
from urllib.request import ProxyHandler, Request, build_opener

import launcher
from tsukiyora.cognition.cognitive_core import CognitiveCore
from tsukiyora.cognition.grey_matter14 import solve
from tsukiyora.memory.memory_store import MemoryStore


class GreyMatter14Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.path)
        self.core=CognitiveCore(self.path)
        self.store=MemoryStore(self.path)

    def add(self, name="a", expected="entailed", *, source_memory_id=None):
        return self.core.grey14.create_case(
            [name],[{"when":[name],"then":"b"},
                    {"when":["b"],"then":"c"}],
            "c",expected,source_memory_id=source_memory_id,confirmed=True)

    def test_forward_chaining_multi_hop_and_explicit_explanation(self):
        r=solve(["a"],[{"when":["a"],"then":"b"},
                       {"when":["b"],"then":"c"}],"c")
        self.assertEqual(r["conclusion"],"entailed")
        self.assertEqual(r["support"]["positive"],"rule #2")
        self.assertEqual(r["derived_literals"],2)
        self.assertFalse(r["truth_in_real_world_verified"])
        self.assertEqual(r["method"],
                         "bounded_forward_chaining_not_llm_reasoning")

    def test_refutation_unknown_conflict_and_counterevidence(self):
        rules=[{"when":["a"],"then":"b"},
               {"when":["b"],"then":"!c"}]
        r=solve(["a"],rules,"c")
        self.assertEqual(r["conclusion"],"refuted")
        self.assertEqual(r["support"]["negative"],"rule #2")
        r=solve(["a"],[],"b")
        self.assertEqual(r["conclusion"],"unknown")
        r=solve(["a","!a"],[],"a")
        self.assertEqual(r["conclusion"],"conflict")
        r=solve(["a"],[{"when":["a"],"then":"!a"}],"a")
        self.assertEqual(r["conclusion"],"conflict")

    def test_safe_small_symbolic_language_and_no_code_execution(self):
        bad=[
            ([],[],"a"), (["a"],[],"!a"),
            (["a; DROP TABLE memory_items"],[],"a"),
            (["a"],[{"when":[],"then":"b"}],"b"),
            (["a"],[{"when":["a"],"then":"__import__"}],"b"),
            (["a"],[{"when":["a"],"then":"b","action":"run"}],"b"),
        ]
        for facts,rules,target in bad:
            with self.subTest(facts=facts,rules=rules,target=target):
                with self.assertRaises(ValueError):
                    solve(facts,rules,target)
        many_rules=[{"when":["a"],"then":"b"}]*33
        with self.assertRaises(ValueError):
            solve(["a"],many_rules,"b")

    def test_explicit_consent_idempotent_case_and_repeatable_local_evaluation(self):
        with self.assertRaises(ValueError):
            self.core.grey14.create_case(
                ["a"],[],"a","entailed",confirmed=False)
        data=self.add()
        self.assertEqual(data["case_id"],self.add()["case_id"])
        with patch("tsukiyora.cloud.cloudru._request",
                   side_effect=AssertionError("Unexpected cloud call")):
            benchmark=self.core.grey14.benchmark(self.core)
        self.assertEqual(benchmark["tested"],1)
        self.assertEqual(benchmark["passed"],1)
        self.assertEqual(benchmark["accuracy_on_user_attested_cases"],1.0)
        self.assertTrue(benchmark["not_llm_or_world_truth_benchmark"])
        self.assertEqual(benchmark["cloud_calls"],0)
        self.assertFalse(self.core.grey14.status(self.core)[
            "automatic_strategy_approval"])

    def test_mismatch_cannot_train_model_or_auto_correct_answer(self):
        self.add(expected="unknown")
        result=self.core.grey14.benchmark(self.core)
        self.assertEqual(result["tested"],1)
        self.assertEqual(result["passed"],0)
        self.assertEqual(result["items"][0]["expected"],"unknown")
        self.assertEqual(result["items"][0]["actual"],"entailed")
        self.assertFalse(result["items"][0]["truth_in_real_world_verified"])
        with closing(self.store.connect()) as db:
            active=db.execute(
                "SELECT COUNT(*) FROM evolution_trials WHERE state='active'"
            ).fetchone()[0]
        self.assertEqual(active,0)

    def test_source_revision_invalidates_outcome_without_rewriting_memory(self):
        memory=self.store.add_memory(
            "fact","Локальная база SQLite для Tsukiyora")
        self.add(source_memory_id=memory["id"])
        self.assertTrue(self.core.grey14.cases()["items"][0]["source_current"])
        self.core.grey14.benchmark(self.core)
        self.store.correct_memory(
            memory["id"],"SQLite используется только локально в Tsukiyora")
        self.assertFalse(self.core.grey14.cases()["items"][0]["source_current"])
        result=self.core.grey14.benchmark(self.core)
        self.assertEqual(result["tested"],0)
        self.assertEqual(result["skipped_stale"],1)
        self.assertIsNone(self.core.grey14.cases()["items"][0]["passed"])
        self.assertEqual(self.store.find_memories(
            "SQLite")[0]["id"],memory["id"])

    def test_one_existing_worker_and_cursor_resume_after_restart(self):
        for name in ("a","d","e","f","g","h","i","j"):
            self.add(name=name)
        with closing(self.store.connect()) as db, db:
            db.execute(
                "UPDATE maintenance_queue SET due_at=4102444800,state='waiting' "
                "WHERE kind<>'cog12_reflect'"
            )
            db.execute(
                "UPDATE maintenance_queue SET due_at=0,state='pending' "
                "WHERE kind='cog12_reflect'"
            )
        result=self.core.scheduler.tick(self.core)
        self.assertEqual(result["kind"],"cog12_reflect")
        self.assertEqual(result["status"],"ok")
        self.assertEqual(result["details"]["grey14_checked"],6)
        self.assertEqual(result["details"]["grey14_passed"],6)
        with closing(self.store.connect()) as db:
            cursor=db.execute(
                "SELECT last_case_id FROM grey14_cursor WHERE id=1"
            ).fetchone()[0]
            n=db.execute("SELECT COUNT(*) FROM grey14_results").fetchone()[0]
        self.assertEqual(n,6)
        self.assertEqual(cursor,6)
        launcher.init_database(self.path)
        next_cycle=self.core.grey14.cycle(self.core)
        self.assertEqual(next_cycle["tested"],2)
        with closing(self.store.connect()) as db:
            completed=db.execute(
                "SELECT COUNT(*) FROM grey14_results"
            ).fetchone()[0]
            days=db.execute(
                "SELECT COUNT(*) FROM soul8_journal "
                "WHERE event='grey14_daily'"
            ).fetchone()[0]
        self.assertEqual(completed,8)
        self.assertEqual(days,1)
        self.assertEqual(next_cycle["cloud_calls"],0)

    def test_background_respects_autonomy_and_local_practice(self):
        self.add()
        self.core.living8.control(0,confirmed=True)
        r=self.core.grey14.cycle(self.core)
        self.assertTrue(r["paused"])
        self.assertEqual(r["tested"],0)
        self.core.living8.control(3,confirmed=True)
        self.core.living8.permission(
            "local_practice",False,confirmed=True)
        self.assertTrue(self.core.grey14.cycle(self.core)["paused"])
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM grey14_results"
            ).fetchone()[0],0)

    def test_existing_experiment_proposals_reused_and_not_promoted(self):
        proposal=self.core.evolution.propose("uncertainty")
        self.assertEqual(proposal["state"],"draft")
        status=self.core.grey14.status(self.core)
        self.assertEqual(status["active_prompt_trials"],0)
        self.assertEqual(status["prompt_trials_waiting_review"],0)
        self.assertTrue(status["v7_guard_active"])
        self.assertEqual(status["additional_paid_calls"],0)
        self.assertFalse(status["real_consciousness_proven"])

    def test_schema_migration_and_persona_file(self):
        from tsukiyora.memory.migration_guard import SCHEMA_VERSION
        self.assertEqual(SCHEMA_VERSION,15)
        saved=self.store.add_memory("fact","Неприкосновенная проверка памяти")
        original=(launcher.ROOT/"soul"/"personality.json").read_bytes()
        with closing(self.store.connect()) as db, db:
            db.execute("PRAGMA user_version=14")
        launcher.init_database(self.path)
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute(
                "PRAGMA user_version").fetchone()[0],15)
            self.assertIsNotNone(db.execute(
                "SELECT id FROM memory_items WHERE id=?",(saved["id"],)
            ).fetchone())
        self.assertTrue(list((self.path.parent/"backups").glob("*.sqlite3")))
        self.assertEqual(
            original,(launcher.ROOT/"soul"/"personality.json").read_bytes())


class GreyMatter14HttpTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path=Path(tmp.name)/"data"/"memory.sqlite3"
        launcher.init_database(path)
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
        with self.http.open(self.base+path,timeout=15) as response:
            return json.load(response)

    def post(self,path,data,origin=None):
        req=Request(self.base+path,data=json.dumps(data).encode(),
                    method="POST",headers={
                        "Content-Type":"application/json",
                        "Origin":origin or self.base})
        with self.http.open(req,timeout=15) as response:
            return json.load(response)

    def test_api_preview_confirmation_origin_and_history(self):
        status=self.get("/api/soul/grey14")
        self.assertEqual(status["version"],"14.0")
        self.assertFalse(status["real_consciousness_proven"])
        payload={"facts":["a"],"rules":[{"when":["a"],"then":"b"}],
                 "target":"b","expected":"entailed"}
        preview=self.post("/api/soul/grey14/preview",payload)
        self.assertEqual(preview["conclusion"],"entailed")
        with self.assertRaises(HTTPError) as error:
            self.post("/api/soul/grey14/case",payload)
        self.assertEqual(error.exception.code,400)
        with self.assertRaises(HTTPError) as error:
            self.post("/api/soul/grey14/case",
                      {**payload,"confirmed":True},
                      origin="https://attacker.invalid")
        self.assertEqual(error.exception.code,403)
        saved=self.post("/api/soul/grey14/case",
                        {**payload,"confirmed":True})
        self.assertGreater(saved["case_id"],0)
        self.assertEqual(len(self.get("/api/soul/grey14/cases")["items"]),1)
        with self.assertRaises(HTTPError) as error:
            self.post("/api/soul/grey14/benchmark",{})
        self.assertEqual(error.exception.code,400)
        benchmark=self.post("/api/soul/grey14/benchmark",
                            {"confirmed":True})
        self.assertEqual(benchmark["tested"],1)
        self.assertEqual(benchmark["passed"],1)

    def test_ui_and_bat_remain_consistent(self):
        html=(launcher.ROOT/"ui"/"index.html").read_text(encoding="utf-8")
        js=(launcher.ROOT/"ui"/"app.ts").read_text(encoding="utf-8")
        for marker in ("grey14","grey14-refresh","grey14-benchmark",
                       "grey14-facts","grey14-rules","grey14-target",
                       "grey14-save","grey14-preview"):
            self.assertIn('id="'+marker+'"',html)
        for route in ("/api/soul/grey14",
                      "/api/soul/grey14/case",
                      "/api/soul/grey14/benchmark"):
            self.assertIn(route,js)
        self.assertTrue((launcher.ROOT/"Tsukiyora.bat").exists())


if __name__=="__main__":
    unittest.main()
