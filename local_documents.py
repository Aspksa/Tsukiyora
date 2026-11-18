"""Explicitly approved, local TXT/Markdown document snippets for grounded RAG.

The user must deliberately import text. No background filesystem crawling,
OCR, PDF parsing, hidden provider requests or external dependencies. Every
chunk is linked to an actual local document; document text is untrusted DATA.
"""
from __future__ import annotations
from contextlib import closing
import hashlib
import re
import sqlite3

from memory_store import MemoryStore
from memory_quality import terms

CHUNK_SIZE = 840
MAX_TEXT = 44000
MAX_RESULTS = 12


def _name(name):
    if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 120
            or "/" in name or "\\" in name or "\x00" in name
            or name.strip().startswith(".")):
        raise ValueError("Неверное имя документа.")
    return name.strip()


class LocalDocuments:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db:
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS source_documents (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        sha256 TEXT NOT NULL,
                        bytes INTEGER NOT NULL,
                        revision INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS document_chunks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        document_id INTEGER NOT NULL REFERENCES source_documents(id)
                            ON DELETE CASCADE,
                        ordinal INTEGER NOT NULL,
                        content TEXT NOT NULL,
                        UNIQUE(document_id, ordinal)
                    );
                    CREATE INDEX IF NOT EXISTS idx_doc_chunk_parent
                        ON document_chunks(document_id,ordinal);
                """)
                try:
                    db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS document_fts
                        USING fts5(content, tokenize='unicode61 remove_diacritics 2')""")
                    db.executescript("""
                        CREATE TRIGGER IF NOT EXISTS doc_chunks_ai
                        AFTER INSERT ON document_chunks BEGIN
                            INSERT INTO document_fts(rowid,content)
                            VALUES(new.id,new.content);
                        END;
                        CREATE TRIGGER IF NOT EXISTS doc_chunks_ad
                        AFTER DELETE ON document_chunks BEGIN
                            DELETE FROM document_fts WHERE rowid=old.id;
                        END;
                    """)
                    # Older local docs imported without FTS can be repaired.
                    missing = db.execute(
                        "SELECT c.id,c.content FROM document_chunks c "
                        "WHERE NOT EXISTS(SELECT 1 FROM document_fts f WHERE f.rowid=c.id) "
                        "ORDER BY c.id LIMIT 500"
                    ).fetchall()
                    for row in missing:
                        db.execute("INSERT INTO document_fts(rowid,content) VALUES(?,?)",
                                   (row["id"],row["content"]))
                except sqlite3.OperationalError as error:
                    if "fts5" not in str(error).lower():
                        raise

    def repair(self,limit=300):
        """Idempotent, bounded local FTS repair with no cloud requests."""
        if type(limit) is not int or not 1<=limit<=1000:
            raise ValueError("Invalid indexing batch limit")
        with closing(self.store.connect()) as db:
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_fts'"
            ).fetchone() is None:
                return {"fts":False,"indexed":0,"removed":0,"cloud_calls":0}
            with db:
                orphaned=db.execute(
                    "DELETE FROM document_fts WHERE rowid IN "
                    "(SELECT f.rowid FROM document_fts f LEFT JOIN document_chunks c "
                    "ON c.id=f.rowid WHERE c.id IS NULL LIMIT ?)",(limit,)
                ).rowcount
                missing=db.execute(
                    "SELECT c.id,c.content FROM document_chunks c "
                    "WHERE NOT EXISTS(SELECT 1 FROM document_fts f WHERE f.rowid=c.id) "
                    "ORDER BY c.id LIMIT ?",(limit,)
                ).fetchall()
                for row in missing:
                    db.execute("INSERT INTO document_fts(rowid,content) VALUES(?,?)",
                               (row["id"],row["content"]))
        return {"fts":True,"indexed":len(missing),"removed":orphaned,
                "cloud_calls":0}

    def import_text(self, name, text, *, confirmed=False):
        name = _name(name)
        if confirmed is not True:
            raise ValueError("Для индексации текста требуется подтверждение пользователя.")
        if not isinstance(text,str) or not 1 <= len(text.strip()) <= MAX_TEXT:
            raise ValueError("TXT/Markdown должен содержать 1–44000 символов.")
        data=text.encode("utf-8")
        if len(data)>50000:
            raise ValueError("Размер текста превышает 50 КБ.")
        digest=hashlib.sha256(data).hexdigest()
        # Save exact source text in bounded chunks, not a model-generated summary.
        chunks=[text[i:i+CHUNK_SIZE] for i in range(0,len(text),CHUNK_SIZE)]
        with closing(self.store.connect()) as db:
            with db:
                row=db.execute(
                    "SELECT id,sha256,revision FROM source_documents WHERE name=?",
                    (name,)
                ).fetchone()
                if row is not None and row["sha256"]==digest:
                    return {"id":row["id"],"name":name,"revision":row["revision"],
                            "chunks":db.execute(
                                "SELECT COUNT(*) FROM document_chunks WHERE document_id=?",
                                (row["id"],)).fetchone()[0],
                            "changed":False,"cloud_calls":0}
                if row is None:
                    cur=db.execute(
                        "INSERT INTO source_documents(name,sha256,bytes) VALUES(?,?,?)",
                        (name,digest,len(data)))
                    doc_id=cur.lastrowid
                    revision=1
                else:
                    doc_id=row["id"]
                    revision=row["revision"]+1
                    db.execute("DELETE FROM document_chunks WHERE document_id=?",
                               (doc_id,))
                    db.execute(
                        "UPDATE source_documents SET sha256=?,bytes=?,revision=?,"
                        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (digest,len(data),revision,doc_id))
                db.executemany(
                    "INSERT INTO document_chunks(document_id,ordinal,content) "
                    "VALUES(?,?,?)",
                    ((doc_id,ordinal,chunk) for ordinal,chunk in enumerate(chunks,1))
                )
        return {"id":doc_id,"name":name,"revision":revision,
                "chunks":len(chunks),"changed":True,"cloud_calls":0}

    def list(self, limit=50):
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError("Неверный лимит документов.")
        with closing(self.store.connect()) as db:
            return [dict(x) for x in db.execute(
                "SELECT id,name,sha256,bytes,revision,created_at,updated_at "
                "FROM source_documents ORDER BY updated_at DESC,id DESC LIMIT ?",
                (limit,)
            )]

    def remove(self, document_id, *, confirmed=False):
        if confirmed is not True or type(document_id) is not int or document_id<1:
            raise ValueError("Требуется подтверждённый ID документа.")
        with closing(self.store.connect()) as db, db:
            count=db.execute("DELETE FROM source_documents WHERE id=?",
                             (document_id,)).rowcount
            if count==0:
                raise ValueError("Документ не найден.")
        return {"deleted":document_id,"cloud_calls":0}

    def search(self, query, limit=8):
        if not isinstance(query,str) or not 1<=len(query.strip())<=500:
            raise ValueError("Неверный поисковый запрос.")
        if type(limit) is not int or not 1<=limit<=MAX_RESULTS:
            raise ValueError("Неверный лимит поиска.")
        words=sorted(terms(query),key=len,reverse=True)[:10]
        if not words:
            return {"items":[],"mode":"empty_tokens","cloud_calls":0}
        expression=" OR ".join('"'+word.replace('"','""')+'"' for word in words)
        with closing(self.store.connect()) as db:
            try:
                rows=db.execute(
                    "SELECT d.id AS document_id,d.name,d.revision,c.ordinal,c.content,"
                    "bm25(document_fts) AS relevance FROM document_fts "
                    "JOIN document_chunks c ON c.id=document_fts.rowid "
                    "JOIN source_documents d ON d.id=c.document_id "
                    "WHERE document_fts MATCH ? ORDER BY relevance LIMIT ?",
                    (expression,limit)
                ).fetchall()
                mode="fts5"
            except sqlite3.OperationalError:
                # FTS5 isn't universal; bounded fallback without raw SQL interpolation.
                allrows=db.execute(
                    "SELECT d.id AS document_id,d.name,d.revision,c.ordinal,c.content "
                    "FROM document_chunks c JOIN source_documents d ON d.id=c.document_id "
                    "ORDER BY c.id DESC LIMIT 3000"
                ).fetchall()
                scored=sorted(
                    ((sum(word in x["content"].casefold() for word in words),x)
                     for x in allrows),
                    key=lambda pair:-pair[0]
                )
                rows=[x for count,x in scored if count>0][:limit]
                mode="bounded_lexical"
        items=[{"document_id":x["document_id"],"name":x["name"],
                "revision":x["revision"],"ordinal":x["ordinal"],
                "content":x["content"],
                "citation":f"[Документ #{x['document_id']}, фрагмент {x['ordinal']}]",
                "source_exists":True,"factual_truth_verified":False}
               for x in rows]
        return {"items":items,"mode":mode,"cloud_calls":0}
