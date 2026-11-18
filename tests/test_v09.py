"""v0.9 regression and 100k+ scale tests; offline, consent-first, no external keys."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import threading
from urllib.request import build_opener,ProxyHandler,Request
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import launcher
from memory_store import MemoryStore,_fingerprint
from migration_guard import MigrationGuard,SCHEMA_VERSION
from rag_pipeline import RagPipeline
from academy_metrics import AcademyMetrics
from cognitive_core import CognitiveCore
from brain_atlas import BrainAtlas
from reliability import Reliability


class V09DataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"data"/"tsukiyora.sqlite3"
        launcher.init_database(self.path)
        self.store=MemoryStore(self.path)
        self.core=CognitiveCore(self.path)

    def test_preupgrade_snapshot_and_schema_migration_idempotent(self):
        item=self.store.add_memory("fact","Семантическая память сохранена до миграции")
        with closing(self.store.connect()) as db,db:
            db.execute("PRAGMA user_version=8")
        before=set(self.core.reliability.folder.glob("memory-*.sqlite3")) if self.core.reliability.folder.exists() else set()
        launcher.init_database(self.path)
        newer=set(self.core.reliability.folder.glob("memory-*.sqlite3"))
        self.assertEqual(len(newer-before),1)
        launcher.init_database(self.path)
        self.assertEqual(len(list(self.core.reliability.folder.glob("memory-*.sqlite3"))),len(newer))
        self.assertTrue(MigrationGuard(self.path).audit()["sqlite_integrity"])
        self.assertEqual(MigrationGuard(self.path).audit()["version"],SCHEMA_VERSION)
        self.assertEqual(self.store.find_memories()[0]["id"],item["id"])

    def test_multi_pass_graph_citations_conflicts_and_staleness(self):
        root=self.store.add_memory("fact","Гараж Лексус осуществляет ремонт фар")
        linked=self.store.add_memory("event","Вчера обсуждали договоры поставки")
        second=self.store.add_memory("biography","Работаю специалистом юридического отдела")
        with closing(self.store.connect()) as db,db:
            for a,b,relation in [(root["id"],linked["id"],"related"),
                                 (linked["id"],second["id"],"possible_conflict")]:
                lo,hi=sorted((a,b))
                db.execute("INSERT INTO memory_links(source_id,target_id,relation)"
                           " VALUES(?,?,?)",(lo,hi,relation))
            db.execute("UPDATE memory_items SET updated_at='2022-01-01 00:00:00' "
                       "WHERE id=?",(root["id"],))
        with patch("cloudru._request",side_effect=AssertionError("RAG called cloud")):
            output=RagPipeline(self.path).search("Гараж Лексус ремонт фар",limit=15)
        self.assertEqual(output["cloud_requests"],0)
        ids=[x["id"] for x in output["items"]]
        self.assertIn(root["id"],ids)
        self.assertIn(linked["id"],ids)
        self.assertIn(second["id"],ids)
        primary=next(x for x in output["items"] if x["id"]==root["id"])
        self.assertEqual(primary["citation"],"[Память #"+str(root["id"])+"]")
        self.assertTrue(primary["source_exists"])
        self.assertTrue(primary["potentially_stale"])
        self.assertFalse(primary["truth_verified"])
        self.assertTrue(any(x["possible_conflict"] for x in output["items"]))
        self.assertLessEqual(len(output["passes"]),3)

    def test_source_ref_missing_is_reported_without_invented_proof(self):
        with closing(self.store.connect()) as db,db:
            db.execute(
                "INSERT INTO memory_items(kind,content,normalized_text,"
                "fingerprint,source_type,source_ref,verification_state)"
                " VALUES(?,?,?,?,?,?,?)",
                ("fact","Секретное утверждение", "секретное утверждение",
                 _fingerprint("fact","Секретное утверждение"),"message","999999",
                 "user_reported")
            )
        output=RagPipeline(self.path).search("Секретное утверждение")
        self.assertFalse(output["items"][0]["source_exists"])
        self.assertEqual(output["items"][0]["source_state"],"source_unresolved")

    def test_measured_academy_improvement_across_conversations(self):
        for i in range(16):
            convo=self.store.create_conversation("Контроль "+str(i))
            answer=self.store.append_messages(convo["id"],[
                {"role":"assistant","content":"Ответ #"+str(i),"client_message_id":"a"+str(i)}
            ])[0]
            self.core.feedback(answer["id"],i>=8,"неточный ответ")
        status=AcademyMetrics(self.path).status()
        accuracy=next(x for x in status["metrics"] if x["skill"]=="accuracy")
        self.assertEqual(accuracy["assessment"],"improving")
        self.assertEqual(accuracy["change_points"],100.0)
        self.assertEqual(accuracy["sample_count"],16)
        self.assertFalse(status["automated_model_training"])
        self.assertFalse(AcademyMetrics(self.path).evaluate()["updated"])
        self.core.academy.set_enabled(False)
        self.assertTrue(AcademyMetrics(self.path).evaluate()["disabled"])

    def test_biography_uses_only_approved_sources(self):
        self.store.add_memory("biography","Моя работа связана с договорами")
        self.store.add_memory("preference","Говори коротко и по делу")
        timeline=self.core.continuity.timeline()
        self.assertTrue(timeline["items"])
        self.assertFalse(timeline["real_feelings_claimed"])
        self.assertTrue(all(not x["truth_verified"] for x in timeline["items"]))
        self.assertTrue(all(x["source_type"] in ("manual","message","memory")
                            for x in timeline["items"]))

    def test_isolated_restore_rehearsal_preserves_live_database(self):
        self.store.add_memory("fact","Содержание до снимка")
        backup=self.core.reliability.ensure_snapshot(force=True)
        self.store.add_memory("event","Появилось после снимка")
        before=self.store.stats()["memories"]
        report=self.core.reliability.dry_run_restore(backup["name"])
        self.assertTrue(report["restorable"])
        self.assertFalse(report["live_database_modified"])
        self.assertEqual(report["history_counts"]["memory_items"],1)
        self.assertEqual(self.store.stats()["memories"],before)
        with self.assertRaises(ValueError):
            self.core.reliability.dry_run_restore("../outside.sqlite3")
        with self.assertRaises(ValueError):
            self.core.reliability.dry_run_restore("memory-not-found.sqlite3")

    def test_local_migration_audit_fts_no_duplicate_rows(self):
        first=self.store.add_memory("fact","Векторный поиск улучшен")
        self.store.correct_memory(first["id"],"Локальный поиск улучшен")
        report=MigrationGuard(self.path).audit()
        self.assertEqual(report["fts_duplicate_rows"],0)
        self.assertEqual(report["fts_missing_rows"],0)
        self.assertFalse(report["orphaned_old_update_triggers"])
        self.assertEqual(report["foreign_key_errors"],0)
        self.assertTrue(report["no_data_sent_to_cloud"])


class V09LargeAtlas(unittest.TestCase):
    def test_100k_hierarchical_regions_and_pagination(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath=Path(tmp)/"memory.sqlite3"
            launcher.init_database(dbpath)
            store=MemoryStore(dbpath)
            # Exercise actual FTS5 triggers and SQLite indexing on >100k records.
            def rows():
                for i in range(100128):
                    kind=("fact","preference","event","biography")[(i//2)%4]
                    content="Индексируемая память "+str(i)
                    yield (kind,content,content.casefold(),
                           _fingerprint(kind,content),"manual","user_reported")
            with closing(store.connect()) as db,db:
                db.executemany(
                    "INSERT INTO memory_items(kind,content,normalized_text,"
                    "fingerprint,source_type,verification_state)"
                    " VALUES(?,?,?,?,?,?)",rows()
                )
            atlas=BrainAtlas(dbpath)
            overview=atlas.overview()
            self.assertEqual(overview["total"],100128)
            self.assertEqual(len(overview["sectors"]),8)
            selected=overview["sectors"][0]
            regions=atlas.regions(selected["id"])
            self.assertGreater(len(regions["regions"]),1)
            self.assertEqual(sum(x["count"] for x in regions["regions"]),
                             regions["total"])
            region=regions["regions"][0]["region"]
            clusters=atlas.clusters(selected["id"],region=region)
            self.assertTrue(clusters["clusters"])
            self.assertEqual(sum(x["count"] for x in clusters["clusters"]),
                             clusters["total"])
            bucket=clusters["clusters"][0]["bucket"]
            nodes=atlas.nodes(selected["id"],bucket)
            self.assertLessEqual(nodes["count"],160)
            self.assertTrue(all(x["id"]>0 for x in nodes["nodes"]))
            page=atlas.regions(selected["id"],cursor=region)
            self.assertTrue(all(x["region"]<region for x in page["regions"]))
            self.assertLess(len(json.dumps(nodes,ensure_ascii=False)),100000)
            with self.assertRaises(ValueError):
                atlas.regions(selected["id"],cursor=-1)


class V09HTTPTests(unittest.TestCase):
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
        self.opener=build_opener(ProxyHandler({}))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self,path):
        with self.opener.open(self.url+path,timeout=15) as response:
            return json.load(response)

    def post(self,path,obj,origin=None):
        request=Request(self.url+path,method="POST",
                        data=json.dumps(obj).encode(),
                        headers={"Origin":origin or self.url,
                                 "Content-Type":"application/json"})
        with self.opener.open(request,timeout=15) as response:
            return json.load(response)

    def test_new_local_routes_and_api_security(self):
        self.assertEqual(self.get("/api/system/migrations")["version"],12)
        self.assertFalse(self.get("/api/soul/academy/metrics")["automated_model_training"])
        self.assertFalse(self.get("/api/soul/character/timeline")["real_feelings_claimed"])
        self.assertEqual(self.get("/api/soul/atlas?level=overview")["total"],0)
        self.assertEqual(self.get("/api/soul/atlas?level=regions&sector=0")["regions"],[])
        self.assertEqual(self.post("/api/soul/rag",{"query":"тест"})["cloud_requests"],0)
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/soul/rag",{"query":"тест"},origin="https://attacker.invalid")
        self.assertEqual(denied.exception.code,403)

    def test_portable_selftest_uses_temp_database(self):
        original_args=sys.argv[:]
        try:
            sys.argv=["launcher.py","--self-test"]
            self.assertEqual(launcher.run(),0)
        finally:
            sys.argv=original_args
        self.assertFalse(self.server.cloud.status()["configured"])

    def test_ui_all_critical_controls_have_handler_and_module_is_syntax_checkable(self):
        html=(ROOT/"ui"/"index.html").read_text(encoding="utf-8")
        script=(ROOT/"ui"/"app.ts").read_text(encoding="utf-8")
        atlas=(ROOT/"ui"/"atlas_view.ts").read_text(encoding="utf-8")
        for field in ("run-system-check","restore-test","recovery-snapshot",
                      "precision-search","academy-enabled","soul-canvas",
                      "send-message","stop-message","cloud-diagnose"):
            self.assertIn('id="'+field+'"',html)
        for route in ("/api/soul/evidence","/api/system/migrations",
                      "/api/system/recovery-check","/api/soul/academy/metrics",
                      "/api/soul/character/timeline","/api/soul/atlas"):
            self.assertIn(route,script)
        self.assertIn('this.level==="regions"',atlas)
        self.assertIn('await this.fetch("regions"',atlas)


if __name__=="__main__":
    unittest.main()
