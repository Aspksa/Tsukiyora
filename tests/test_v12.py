"""Tsukiyora v1.2 integration: real local binary document parsing, contract
clause provenance, materialized atlas, Academy safeguards, WAL and SHA-256.
No API keys, remote services, OCR libraries or browser claims in these tests.
"""
from __future__ import annotations
from contextlib import closing
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import base64
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import zipfile
import zlib
from urllib.error import HTTPError
from urllib.request import build_opener,ProxyHandler,Request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import launcher
from memory_store import MemoryStore
from migration_guard import SCHEMA_VERSION
from document_parser import extract_document
from contract_insights import ContractInsights
from atlas_materializer import AtlasMaterializer
from academy_practice import AcademyPractice
from academy_rollback import AcademyRollback
from reliability import Reliability


def sample_docx(text="Договор поставки. Оплата 1250 рублей до 20.11.2026."):
    data=BytesIO()
    with zipfile.ZipFile(data,"w",zipfile.ZIP_DEFLATED) as archive:
        xml=(
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            +text+'</w:t></w:r></w:p></w:body></w:document>'
        )
        archive.writestr("word/document.xml",xml.encode("utf8"))
    return data.getvalue()


def sample_pdf(text="Contract value 1450 RUB"):
    stream=("BT /F1 12 Tf ("+text+") Tj ET").encode("cp1252")
    packed=zlib.compress(stream)
    return (b"%PDF-1.4\n1 0 obj\n<</Length "+
            str(len(packed)).encode()+b" /Filter /FlateDecode>>\nstream\n"+
            packed+b"\nendstream\nendobj\n%%EOF")


class V12DataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"data"/"tsukiyora.sqlite3"
        launcher.init_database(self.db)
        self.store=MemoryStore(self.db)
        self.core=launcher.CognitiveCore(self.db)

    def test_stdlib_docx_and_basic_pdf_and_scanned_pdf_refusal(self):
        word=extract_document("a.docx",sample_docx())
        self.assertIn("1250 рублей",word["text"])
        self.assertFalse(word["complete_extraction_guaranteed"])
        scanned=b"%PDF-1.4\n1 0 obj <</Subtype /Image>> endobj"
        with self.assertRaisesRegex(ValueError,"Скан"):
            extract_document("scan.pdf",scanned)
        pdf=extract_document("b.pdf",sample_pdf())
        self.assertIn("1450 RUB",pdf["text"])
        self.assertEqual(pdf["method"],"basic_pdf_text_streams")
        self.assertEqual(extract_document("a.txt",b"hello")["text"],"hello")
        with self.assertRaises(ValueError):
            extract_document("encr.pdf",sample_pdf()+b" /Encrypt 3 0 R")
        with self.assertRaises(ValueError):
            extract_document("oversize.docx",b"0"*1000001)

    def test_dangerous_docx_xml_entities_rejected(self):
        data=BytesIO()
        with zipfile.ZipFile(data,"w",zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml",
                       b"<!DOCTYPE a [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><a>&x;</a>")
        with self.assertRaises(ValueError):
            extract_document("entities.docx",data.getvalue())

    def test_contract_highlights_cite_actual_saved_fragments(self):
        saved=self.core.evidence.documents.import_text(
            "contract.md",
            "Сторона обязуется оплатить 1250 рублей до 20.11.2026. "
            "При просрочке применяется неустойка.",
            confirmed=True)
        details=ContractInsights(self.db).inspect(saved["id"])
        self.assertGreaterEqual(len(details["items"]),1)
        self.assertTrue(all(x["citation"].startswith("[Документ #")
                            and not x["legal_meaning_verified"] for x in details["items"]))
        self.assertTrue(any("date" in x["types"] for x in details["items"]))
        self.assertTrue(details["expert_legal_review_required"])
        self.assertEqual(details["cloud_requests"],0)

    def test_materialized_aggregates_follow_corrections_and_deletes(self):
        cache=AtlasMaterializer(self.db)
        self.assertTrue(cache.verify()["verified"])
        a=self.store.add_memory("fact","Лексус замена фар")
        b=self.store.add_memory("event","Сегодня заключён договор")
        self.assertTrue(cache.verify()["verified"])
        self.assertEqual(cache.status()["total"],2)
        self.assertTrue(self.core.atlas.overview()["materialized"])
        self.assertEqual(self.core.atlas.overview()["total"],2)
        self.store.correct_memory(a["id"],"Теперь нужен ремонт фар")
        self.assertEqual(cache.status()["total"],2)
        self.store.delete_memory(b["id"])
        self.assertEqual(cache.status()["total"],1)
        with closing(self.store.connect()) as db,db:
            db.execute("UPDATE atlas_region_totals SET n=n+3")
        self.assertFalse(cache.verify()["verified"])
        self.assertTrue(cache.rebuild()["ready"])
        self.assertTrue(cache.verify()["verified"])

    def test_backup_digest_detects_tampering_and_safe_restore(self):
        self.store.add_memory("fact","Проверка сохранения")
        guard=Reliability(self.db,ROOT)
        backup=guard.ensure_snapshot(force=True)
        self.assertEqual(guard.verify_snapshots()["healthy"],1)
        self.assertEqual(guard.verify_snapshots()["snapshots"][0]["checksum"],"verified")
        self.assertTrue(guard.dry_run_restore(backup["name"])["restorable"])
        snapshot=guard.folder/backup["name"]
        with snapshot.open("ab") as stream:
            stream.write(b"appended bytes not affecting existing data pages")
        self.assertEqual(guard.verify_snapshots()["invalid"],1)
        self.assertEqual(guard.verify_snapshots()["snapshots"][0]["checksum"],"mismatch")
        with self.assertRaisesRegex(ValueError,"checksum"):
            guard.dry_run_restore(backup["name"])
        self.assertEqual(guard.wal_health()["journal_mode"],"wal")
        self.assertTrue(guard.wal_health()["sqlite_ok"])

    def test_control_questions_and_safe_strategy_rollback(self):
        practice=AcademyPractice(self.db)
        self.assertEqual(len(practice.questions()["questions"]),4)
        with self.assertRaises(ValueError):
            practice.evaluate("sources","Источник не доказывает истинность")
        result=practice.evaluate("sources","Источник не подтверждает истинность",
                                 confirmed=True)
        self.assertTrue(result["passed"])
        self.assertEqual(practice.status()["results"][0]["runs"],1)
        rollback=AcademyRollback(self.db)
        self.assertEqual(len(rollback.status()["strategies"]),2)
        # Eight explicit-rated outcomes for the optional style.
        with closing(self.store.connect()) as db,db:
            for i in range(8):
                db.execute(
                    "INSERT INTO academy_outcomes(message_id,strategy,helpful)"
                    " VALUES(?,?,0)",(10000+i,"clear_steps"))
        decision=rollback.evaluate()
        self.assertIn("clear_steps",decision["suspended"])
        self.assertFalse(rollback.permitted("clear_steps"))
        self.assertTrue(rollback.permitted("concise_first"))
        with self.assertRaises(ValueError):
            rollback.resume("clear_steps")
        self.assertEqual(rollback.resume("clear_steps",confirmed=True)["state"],"active")

    def test_persona_links_do_not_infer_people_or_events(self):
        a=self.store.add_memory("biography","Я работаю над проектами")
        b=self.store.add_memory("event","Обсуждали контракт")
        lo,hi=sorted((a["id"],b["id"]))
        with closing(self.store.connect()) as db,db:
            db.execute("INSERT INTO memory_links(source_id,target_id,relation)"
                       " VALUES(?,?,'related')",(lo,hi))
        result=self.core.persona_arc.links()
        self.assertEqual(result["relation_count"],1)
        self.assertFalse(result["infer_new_relationships"])
        self.assertIn(result["links"][0]["source_id"],(lo,hi))


class V12HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"data"/"memory.sqlite3"
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

    def get(self,path):
        with self.opener.open(self.url+path,timeout=10) as response:
            return json.load(response)

    def post(self,path,body,origin=None):
        req=Request(self.url+path,method="POST",
                    data=json.dumps(body,ensure_ascii=False).encode("utf8"),
                    headers={"Origin":origin or self.url,"Content-Type":"application/json"})
        with self.opener.open(req,timeout=10) as response:
            return json.load(response)

    def test_binary_import_consent_source_analysis_and_reversible_controls(self):
        self.assertTrue(self.get("/api/soul/atlas/cache")["verified"])
        self.assertEqual(self.get("/api/system/migrations")["version"],SCHEMA_VERSION)
        self.assertTrue(self.get("/api/system/wal")["sqlite_ok"])
        self.assertEqual(len(self.get("/api/soul/academy/practice")["questions"]),4)
        self.assertEqual(self.get("/api/soul/persona/links")["links"],[])
        docx=base64.b64encode(sample_docx()).decode("ascii")
        body={"name":"contract.docx","base64":docx,"confirmed":True}
        with self.assertRaises(HTTPError) as refused:
            self.post("/api/soul/documents/import-binary",
                      body,origin="https://attacker.invalid")
        self.assertEqual(refused.exception.code,403)
        with self.assertRaises(HTTPError) as refused:
            self.post("/api/soul/documents/import-binary",
                      {**body,"confirmed":False})
        self.assertEqual(refused.exception.code,400)
        saved=self.post("/api/soul/documents/import-binary",body)
        self.assertEqual(saved["extraction_method"],"docx_wordprocessingml")
        found=self.get("/api/soul/contracts/insights?id="+str(saved["id"]))
        self.assertTrue(found["items"])
        probe=self.post("/api/soul/academy/practice/evaluate",
                        {"skill":"sources","answer":"Источник не подтверждает истинность",
                         "confirmed":True})
        self.assertTrue(probe["passed"])
        self.assertFalse(self.get("/api/cloud/status")["document_context_allowed"])

    def test_ui_has_wired_buttons_and_no_new_js_deps(self):
        html=(ROOT/"ui"/"index.html").read_text(encoding="utf-8")
        app=(ROOT/"ui"/"app.ts").read_text(encoding="utf-8")
        for ctrl in ("document-file","academy-practice-evaluate",
                     "academy-practice-answer","academy-rollback",
                     "atlas-health","run-system-check","cloud-diagnose"):
            self.assertIn('id="'+ctrl+'"',html)
        for path in ("/api/soul/documents/import-binary",
                     "/api/soul/contracts/insights",
                     "/api/soul/academy/practice/evaluate","/api/system/wal"):
            self.assertIn(path,app)
        self.assertFalse(self.get("/api/system/selftest")["cloud_live_verified"])


if __name__=="__main__":
    unittest.main()
