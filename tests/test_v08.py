"""Tsukiyora v0.8: tested, bounded local intelligence and 20k-node atlas.

All cloud calls are mocked/forbidden. These tests cannot claim the user's PC,
Cloud.ru credentials, or an external browser have been tested.
"""
from __future__ import annotations
from contextlib import closing
import json
import sqlite3
import hashlib
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,build_opener,ProxyHandler

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import launcher
from tsukiyora.memory.memory_store import MemoryStore,_fingerprint
from tsukiyora.personality.soul_growth import SoulGrowth
from tsukiyora.memory.precision_memory import PrecisionMemory
from tsukiyora.academy.soul_academy import SoulAcademy
from tsukiyora.personality.persona_continuity import PersonaContinuity
from tsukiyora.memory.brain_atlas import BrainAtlas
from tsukiyora.cognition.cognitive_core import CognitiveCore
from tsukiyora.runtime.reliability import Reliability


class V08LocalTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        self.path=self.root/"data"/"tsukiyora.sqlite3"
        launcher.init_database(self.path)
        self.store=MemoryStore(self.path)
        self.core=CognitiveCore(self.path)

    def test_precision_prioritizes_terms_updates_and_never_calls_cloud(self):
        vague=self.store.add_memory("fact","Старинное описание морской пристани")
        exact=self.store.add_memory("fact","Лексус фары ремонт и проверка герметичности")
        unrelated=self.store.add_memory("fact","Рецепт вкусного китайского чая")
        engine=PrecisionMemory(self.path)
        with patch("tsukiyora.cloud.cloudru._request", side_effect=AssertionError("Offline must not call Cloud.ru")):
            out=engine.search("ремонт фар лексус")
        self.assertEqual(out["cloud_requests"],0)
        self.assertEqual(out["items"][0]["id"],exact["id"])
        self.assertNotIn(unrelated["id"],[x["id"] for x in out["items"][:2]])
        self.assertFalse(out["items"][0]["source_verified"])
        self.store.correct_memory(exact["id"],"В автомобильном салоне меняли лампу")
        self.assertNotIn(exact["id"],[x["id"] for x in engine.search("лексус герметичности")["items"]])
        self.store.delete_memory(vague["id"])
        self.assertNotIn(vague["id"],[x["id"] for x in engine.search("морской пристани")["items"]])
        self.assertEqual(engine.repair()["cloud_requests"],0)

    def test_one_hop_graph_expansion_provenance_and_conflicts(self):
        first=self.store.add_memory("fact","Наш гараж обслуживает автомобили")
        second=self.store.add_memory("event","Вчера завершили юридический договор")
        lo,hi=sorted((first["id"],second["id"]))
        with closing(self.store.connect()) as db,db:
            db.execute("INSERT INTO memory_links(source_id,target_id,relation)"
                       " VALUES(?,?,'possible_conflict')",(lo,hi))
        result=self.core.precision.search("гараж обслуживает автомобили",limit=8)
        self.assertEqual(result["items"][0]["id"],first["id"])
        self.assertTrue(result["items"][0]["possible_conflict"])
        expanded=[x for x in result["items"] if x["id"]==second["id"]]
        self.assertEqual(len(expanded),1)
        self.assertEqual(expanded[0]["match"],"related")
        self.assertEqual(expanded[0]["related_to"],first["id"])

    def test_experience_transfer_across_conversations_and_opt_out(self):
        feedback=[]
        for i in range(4):
            convo=self.store.create_conversation("Тест "+str(i))
            answer=self.store.append_messages(convo["id"],[
                {"role":"assistant","content":"Результат "+str(i),"client_message_id":"reply"+str(i)}
            ])[0]
            feedback.append(answer["id"])
        for message_id in feedback[:3]:
            self.core.feedback(message_id,False,"неточный ответ")
        academy=SoulAcademy(self.path)
        state=academy.status()
        goal=next(x for x in state["goals"] if x["category"]=="accuracy")
        self.assertEqual(goal["state"],"practice")
        self.assertGreaterEqual(goal["unhelpful"],3)
        self.assertIn("Проверяй факты",academy.guidance())
        self.assertFalse(state["learned_model_weights"])
        count=len(state["cycles"])
        self.assertFalse(academy.run_cycle()["updated"])
        self.assertEqual(len(academy.status()["cycles"]),count)
        academy.set_enabled(False)
        self.assertEqual(academy.guidance(),"")
        academy.set_enabled(True)
        self.assertIn("Проверяй факты",academy.guidance())

    def test_character_uses_only_approved_memory_and_never_changes_protected_persona(self):
        version=launcher.load_personality()
        preference=self.store.add_memory("preference","Говори кратко, с понятными примерами")
        bio=self.store.add_memory("biography","Я разрабатываю проект Цукиёра")
        self.store.create_conversation("Разговор 1")
        continuity=PersonaContinuity(self.path)
        result=continuity.consolidate()
        self.assertEqual(result["stable_habits"],1)
        snapshot=continuity.state()
        self.assertEqual(snapshot["stable_habits"][0]["id"],preference["id"])
        self.assertFalse(snapshot["feelings_claimed"])
        self.assertFalse(snapshot["protected_character_mutable"])
        self.assertIn(str(preference["id"]),continuity.context())
        self.assertEqual(continuity.consolidate(),result)
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM persona_epochs").fetchone()[0],1)
        self.store.delete_memory(preference["id"])
        self.assertFalse(continuity.state()["stable_habits"])
        self.assertEqual(launcher.load_personality(),version)

    def test_backup_validation_finds_corruption_and_schedules_new_snapshot(self):
        self.store.add_memory("fact","Надёжная запись")
        recovery=Reliability(self.path,ROOT)
        first=recovery.ensure_snapshot(force=True)
        valid=recovery.verify_snapshots(limit=3)
        self.assertEqual(valid["healthy"],1)
        (recovery.folder/first["name"]).write_bytes(b"damaged backup")
        damaged=recovery.verify_snapshots(limit=3)
        self.assertEqual(damaged["invalid"],1)
        self.assertFalse(damaged["restoration_performed"])
        next_copy=recovery.ensure_snapshot(interval_hours=5)
        self.assertTrue(next_copy["created"])
        self.assertNotEqual(next_copy["name"],first["name"])
        self.assertGreaterEqual(recovery.verify_snapshots(limit=3)["healthy"],1)
        self.assertEqual(self.store.stats()["memories"],1)

    def test_local_cognitive_jobs_are_bounded_and_never_use_cloud(self):
        with patch("tsukiyora.cloud.cloudru._request", side_effect=AssertionError("Background requested network")):
            for _ in range(12):
                result=self.core.scheduler.tick(self.core,max_millis=1000)
                self.assertIn(result["status"],("ok","idle","warning"))
                self.assertNotIn("password",json.dumps(result).lower())
        kinds={x["kind"] for x in self.core.scheduler.pending()["jobs"]}
        self.assertTrue({"precision_index","academy_cycle","persona_continuity",
                         "backup_verify"}<=kinds)
        self.assertEqual(self.core.precision.repair()["cloud_requests"],0)


class V08AtlasScaleTest(unittest.TestCase):
    def test_20000_real_memories_load_by_layer_not_all_at_once(self):
        with tempfile.TemporaryDirectory() as folder:
            dbpath=Path(folder)/"large.sqlite3"
            launcher.init_database(dbpath)
            store=MemoryStore(dbpath)
            rows=[]
            for i in range(20000):
                kind=("fact","preference","event","biography")[i%4]
                content=f"Факт большой тестовой базы #{i}: запись памяти"
                rows.append((kind,content,content.casefold(),
                             _fingerprint(kind,content),"manual","user_reported"))
            with closing(store.connect()) as db,db:
                db.executemany(
                    "INSERT INTO memory_items(kind,content,normalized_text,fingerprint,"
                    "source_type,verification_state) VALUES(?,?,?,?,?,?)",rows
                )
            atlas=BrainAtlas(dbpath)
            overall=atlas.overview()
            self.assertEqual(overall["total"],20000)
            self.assertEqual(sum(x["count"] for x in overall["sectors"]),20000)
            self.assertLessEqual(len(overall["sectors"]),8)
            sector=overall["sectors"][0]["id"]
            groups=atlas.clusters(sector)
            self.assertGreater(len(groups["clusters"]),5)
            self.assertEqual(sum(x["count"] for x in groups["clusters"]),groups["total"])
            first=groups["clusters"][0]
            details=atlas.nodes(sector,first["bucket"])
            self.assertLessEqual(len(details["nodes"]),160)
            self.assertTrue(all(x["kind"]==groups["kind"] for x in details["nodes"]))
            self.assertFalse(any("content" in x for x in details["nodes"]))
            self.assertLess(len(json.dumps(details,ensure_ascii=False)),75000)
            with self.assertRaises(ValueError):
                atlas.nodes(9,1)
            with self.assertRaises(ValueError):
                atlas.clusters(1,cursor=-99)


class V08HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.path)
        self.server=launcher.SoulServer(("127.0.0.1",0),launcher.load_personality(),self.path)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_server)
        self.url="http://127.0.0.1:%d" % self.server.server_address[1]
        self.opener=build_opener(ProxyHandler({}))

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)

    def get(self,path):
        with self.opener.open(self.url+path,timeout=8) as response:
            return response.status, json.load(response)

    def post(self,path,data,origin=None):
        payload=json.dumps(data,ensure_ascii=False).encode("utf-8")
        request=Request(self.url+path,method="POST",data=payload,
                        headers={"Origin":origin or self.url,
                                 "Content-Type":"application/json"})
        with self.opener.open(request,timeout=8) as response:
            return response.status,json.load(response)

    def test_http_precision_academy_character_atlas_and_selftest(self):
        store=MemoryStore(self.path)
        store.add_memory("fact","Ремонт фары Lexus")
        result=self.post("/api/soul/precision",{"query":"ремонт фары"})
        self.assertEqual(result[0],200)
        self.assertEqual(len(result[1]["items"]),1)
        self.assertEqual(result[1]["cloud_requests"],0)
        self.assertEqual(self.get("/api/soul/atlas?level=overview")[1]["total"],1)
        self.assertEqual(self.get("/api/soul/academy")[1]["learned_model_weights"],False)
        self.assertFalse(self.get("/api/soul/character")[1]["feelings_claimed"])
        self.assertFalse(self.get("/api/system/selftest")[1]["cloud_live_verified"])
        self.assertEqual(self.get("/api/soul/precision/status")[1]["cloud_calls"],0)
        with self.opener.open(self.url+"/ui/atlas_view.ts",timeout=8) as res:
            self.assertEqual(res.status,200)
            self.assertIn(b"BrainAtlasView",res.read())

    def test_untrusted_requests_rejected_without_hidden_network(self):
        with self.assertRaises(HTTPError) as err:
            self.post("/api/soul/precision",{"query":"secret"},
                      origin="https://attacker.example")
        self.assertEqual(err.exception.code,403)
        with self.assertRaises(HTTPError) as err:
            self.get("/api/soul/atlas?level=nodes&sector=bad&bucket=1")
        self.assertEqual(err.exception.code,400)

    def test_new_js_module_and_dom_ids_have_local_routes(self):
        html=(ROOT/"ui"/"index.html").read_text(encoding="utf-8")
        script=(ROOT/"ui"/"app.ts").read_text(encoding="utf-8")
        module=(ROOT/"ui"/"atlas_view.ts").read_text(encoding="utf-8")
        for control in ("precision-search","academy-enabled","character-progress",
                        "browser-selftest","soul-canvas","run-system-check"):
            self.assertIn('id="'+control+'"',html)
        for endpoint in ("/api/soul/evidence","/api/soul/academy",
                         "/api/soul/character","/api/soul/atlas",
                         "/api/system/selftest"):
            self.assertIn(endpoint,script)
        self.assertIn("export class BrainAtlasView",module)
        self.assertIn("draw(width,height",module)
        self.assertIn("atlas_view.ts",script)
        self.assertNotIn("localStorage.setItem",script)

if __name__=="__main__":
    unittest.main()
