"""Tsukiyora v1.1 regressions: local documents, citation integrity, learning,
persona sources, offline safety, HTTP permissions and interactive surface.
Does NOT assert successful live Cloud.ru or user-device/browser behaviour.
"""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import build_opener,ProxyHandler,Request
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import launcher
from cognitive_core import CognitiveCore
from local_documents import LocalDocuments
from evidence_review import EvidenceReview
from academy_curriculum import AcademyCurriculum
from persona_arc import PersonaArc
from memory_store import MemoryStore
from migration_guard import SCHEMA_VERSION


class V11LocalTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db=Path(self.folder.name)/"data"/"tsukiyora.sqlite3"
        launcher.init_database(self.db)
        self.store=MemoryStore(self.db)
        self.core=CognitiveCore(self.db)

    def test_document_requires_consent_versioning_search_and_deletion(self):
        docs=LocalDocuments(self.db)
        with self.assertRaises(ValueError):
            docs.import_text("contract.md","Договор поставки")
        with self.assertRaises(ValueError):
            docs.import_text("../private.txt","Текст",confirmed=True)
        first=docs.import_text("contract.md","Договор поставки оборудования",
                               confirmed=True)
        self.assertTrue(first["changed"])
        self.assertEqual(first["revision"],1)
        self.assertEqual(len(docs.search("договор поставки")["items"]),1)
        self.assertFalse(docs.import_text("contract.md","Договор поставки оборудования",
                                          confirmed=True)["changed"])
        revised=docs.import_text("contract.md","Условия аренды помещения",
                                 confirmed=True)
        self.assertEqual(revised["revision"],2)
        self.assertEqual(docs.search("договор поставки")["items"],[])
        self.assertEqual(len(docs.search("аренды помещения")["items"]),1)
        self.assertEqual(docs.repair()["cloud_calls"],0)
        with self.assertRaises(ValueError):
            docs.remove(first["id"],confirmed=False)
        docs.remove(first["id"],confirmed=True)
        self.assertEqual(docs.search("аренды помещения")["items"],[])
        self.assertEqual(docs.list(),[])

    def test_rag5_links_document_and_approved_memory_without_network(self):
        fact=self.store.add_memory("fact","Компания заключила договор поставки")
        doc=self.core.evidence.documents.import_text(
            "agreement.txt","Договор поставки продукции между компаниями",confirmed=True)
        with patch("cloudru._request",side_effect=AssertionError("No hidden Cloud.ru")):
            result=self.core.evidence.search("договор поставки",limit=10)
        self.assertEqual(result["cloud_requests"],0)
        self.assertIn(fact["id"],[x["id"] for x in result["memories"]])
        self.assertEqual(result["documents"][0]["document_id"],doc["id"])
        self.assertGreaterEqual(result["provenance_sources"],2)
        self.assertEqual(result["independently_verified_claims"],0)
        self.assertFalse(result["documents"][0]["factual_truth_verified"])

    def test_answer_citations_are_checked_against_existing_source_rows(self):
        fact=self.store.add_memory("fact","Нужно соблюдать договор")
        doc=self.core.evidence.documents.import_text(
            "guide.md","Порядок оформления договора",confirmed=True)
        answer=(f"Факт [Память #{fact['id']}] подтверждён записью. "
                f"Документ [Документ #{doc['id']}, фрагмент 1] и "
                "[Документ #99999, фрагмент 1] требуют проверки.")
        conversation=self.store.create_conversation()
        message=self.store.append_messages(conversation["id"],[
            {"role":"assistant","content":answer,"client_message_id":"verified-answer"}
        ])[0]
        audit=self.core.evidence.verify_answer(message["id"],answer)
        self.assertEqual(audit["memory_citations"],1)
        self.assertEqual(audit["document_citations"],1)
        self.assertEqual(audit["missing_sources"],1)
        self.assertEqual(audit["label"],"review_required")
        self.assertFalse(audit["truth_verified"])
        self.assertEqual(len(self.core.evidence.status()["items"]),1)
        with self.assertRaises(ValueError):
            self.core.evidence.verify_answer(message["id"],"unrelated text")

    def test_academy_regression_goals_from_user_rating_only(self):
        for i in range(16):
            convo=self.store.create_conversation("Урок "+str(i))
            msg=self.store.append_messages(convo["id"],[
                {"role":"assistant","content":"Ответ "+str(i),
                 "client_message_id":"eval"+str(i)}
            ])[0]
            self.core.feedback(msg["id"],i<8,"неточный ответ")
        state=self.core.curriculum.status()
        accuracy=next(x for x in state["goals"] if x["skill"]=="accuracy")
        self.assertEqual(accuracy["state"],"regressing")
        self.assertEqual(accuracy["review_required"],1)
        self.assertLessEqual(accuracy["observed_change"],-15)
        self.assertFalse(state["automatic_strategy_rewrite"])
        before=len(state["regressions"])
        self.core.curriculum.advance()
        self.assertEqual(len(self.core.curriculum.status()["regressions"]),before)
        self.core.academy.set_enabled(False)
        self.assertFalse(self.core.curriculum.advance()["enabled"])

    def test_persona_arc_never_changes_protected_character(self):
        source=launcher.load_personality()
        arc=PersonaArc(self.db)
        self.assertTrue(arc.consolidate()["changed"])
        self.assertFalse(arc.consolidate()["changed"])
        self.store.add_memory("preference","Отвечай по-китайски на китайском")
        self.assertTrue(arc.consolidate()["changed"])
        self.assertEqual(arc.history()["snapshots"][0]["approved_habits"],1)
        self.assertTrue(arc.history()["protected_persona_unchanged"])
        self.assertEqual(launcher.load_personality(),source)

    def test_worker_is_local_and_defers_to_chat(self):
        lock=threading.Lock()
        lock.acquire()
        try:
            self.assertEqual(self.core.scheduler.tick(
                self.core,chat_lock=lock)["reason"],"chat_active")
        finally:
            lock.release()
        kinds={x["kind"] for x in self.core.scheduler.pending()["jobs"]}
        self.assertTrue({"academy_curriculum","persona_arc",
                         "document_index","source_audit"}<=kinds)
        with patch("cloudru._request",side_effect=AssertionError("No paid worker")):
            result=self.core.scheduler.tick(self.core)
        self.assertIn(result["status"],("ok","warning"))

    def test_migration_preserves_version_and_history(self):
        self.store.add_memory("fact","История до обновления")
        launcher.init_database(self.db)
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],
                             SCHEMA_VERSION)
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0],"ok")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0],1)


class V11HttpTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db=Path(self.folder.name)/"data"/"memory.sqlite3"
        launcher.init_database(self.db)
        self.server=launcher.SoulServer(("127.0.0.1",0),launcher.load_personality(),self.db)
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()
        self.addCleanup(self.close)
        self.url="http://127.0.0.1:"+str(self.server.server_address[1])
        self.opener=build_opener(ProxyHandler({}))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)

    def get(self,path):
        with self.opener.open(self.url+path,timeout=8) as response:
            return json.load(response)

    def post(self,path,body,origin=None):
        req=Request(self.url+path,method="POST",
                    data=json.dumps(body,ensure_ascii=False).encode("utf-8"),
                    headers={"Origin":origin or self.url,
                             "Content-Type":"application/json"})
        with self.opener.open(req,timeout=8) as response:
            return json.load(response)

    def test_same_origin_document_consent_and_delete(self):
        self.assertEqual(self.get("/api/soul/documents")["items"],[])
        with self.assertRaises(HTTPError) as denied:
            self.post("/api/soul/documents/import",
                      {"name":"source.md","text":"Договор поставки",
                       "confirmed":True},
                      origin="https://attacker.invalid")
        self.assertEqual(denied.exception.code,403)
        with self.assertRaises(HTTPError) as no_consent:
            self.post("/api/soul/documents/import",
                      {"name":"source.md","text":"Договор поставки"})
        self.assertEqual(no_consent.exception.code,400)
        doc=self.post("/api/soul/documents/import",{
            "name":"source.md","text":"Договор поставки","confirmed":True
        })
        self.assertEqual(len(self.get("/api/soul/documents")["items"]),1)
        evidence=self.post("/api/soul/evidence",{"query":"договор поставки"})
        self.assertEqual(evidence["documents"][0]["document_id"],doc["id"])
        self.assertFalse(self.get("/api/soul/academy/curriculum")["model_weights_updated"]
                         if "model_weights_updated" in self.get("/api/soul/academy/curriculum")
                         else self.get("/api/soul/academy/curriculum")["goals"])
        self.assertTrue(self.get("/api/soul/persona/arc")["protected_persona_unchanged"])
        self.assertEqual(self.get("/api/soul/evidence/audits")["items"],[])
        self.post("/api/soul/documents/delete",{"id":doc["id"],"confirmed":True})
        self.assertEqual(self.get("/api/soul/documents")["items"],[])

    def test_ui_has_handlers_and_cloud_live_status_stays_unverified(self):
        html=(ROOT/"ui"/"index.html").read_text(encoding="utf-8")
        js=(ROOT/"ui"/"app.ts").read_text(encoding="utf-8")
        for element in ("document-import","document-refresh","document-file",
                        "academy-curriculum","persona-arc","evidence-audit-summary",
                        "stop-message","send-message","cloud-diagnose"):
            self.assertIn('id="'+element+'"',html)
            self.assertIn('"'+element+'"',js)
        for route in ("/api/soul/evidence","/api/soul/documents/import",
                      "/api/soul/academy/curriculum","/api/soul/persona/arc"):
            self.assertIn(route,js)
        self.assertFalse(self.get("/api/system/selftest")["cloud_live_verified"])

if __name__=="__main__":
    unittest.main()
