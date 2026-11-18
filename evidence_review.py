"""RAG 5.0 evidence review: approved memories, local documents and cached vectors.

Citations are verified against CURRENT SQLite rows; coverage means available
support, not the truth of the generated answer. Document contents are untrusted
and are never executed as instructions or sent to Cloud.ru in background tasks.
"""
from __future__ import annotations
from contextlib import closing
import json
import re
import sqlite3

from rag_fusion import RagFusion
from local_documents import LocalDocuments
from memory_store import MemoryStore

MEMORY_CITE = re.compile(r"\[Память\s*#(\d+)\]",re.I)
DOCUMENT_CITE = re.compile(r"\[Документ\s*#(\d+)\s*,\s*фрагмент\s*(\d+)\]",re.I)


class EvidenceReview:
    def __init__(self,path):
        self.store=MemoryStore(path)
        self.fusion=RagFusion(path)
        self.documents=LocalDocuments(path)

    def initialize(self):
        self.documents.initialize()
        with closing(self.store.connect()) as db:
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS answer_evidence_reviews(
                        message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                        memory_citations INTEGER NOT NULL,
                        document_citations INTEGER NOT NULL,
                        unresolved INTEGER NOT NULL,
                        conflict_flags INTEGER NOT NULL,
                        label TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)

    def search(self,query,limit=12,*,model=None,use_cached_vectors=False):
        if type(limit) is not int or not 1<=limit<=20:
            raise ValueError("Неверный лимит доказательств.")
        memories=self.fusion.search(
            query,limit=limit,model=model,use_cached_vectors=use_cached_vectors)
        docs=self.documents.search(query,limit=min(6,limit))
        # Cross-document corroboration is not assumed: a single document can
        # repeat claims, so independent_source_count is only a provenance count.
        origins=set()
        for item in memories["items"]:
            identity=item.get("source_ref") or ("memory-"+str(item["id"]))
            origins.add(("memory",str(identity)))
        for item in docs["items"]:
            origins.add(("document",item["document_id"]))
        review_flags=[
            {"memory_id":x["id"],"reason":
             "possible_conflict" if x.get("possible_conflict") else
             "unresolved_source" if x.get("needs_review") else
             "potentially_stale"}
            for x in memories["items"]
            if x.get("possible_conflict") or x.get("needs_review")
            or x.get("potentially_stale")
        ]
        return {
            "memories":memories["items"],"documents":docs["items"],
            "flags":review_flags[:20],
            "provenance_sources":len(origins),
            "independently_verified_claims":0,
            "source_policy":"local_evidence_not_verified_truth",
            "vector_cache":memories["vector_cache"],
            "cloud_requests":0,
            "available_citations":[x["citation"] for x in memories["items"]]+
                                  [x["citation"] for x in docs["items"]],
            "method":"fts5_graph_cached_vectors_document_chunks",
        }

    def verify_answer(self,message_id:int,answer:str)->dict:
        if type(message_id) is not int or message_id<=0:
            raise ValueError("Invalid assistant ID")
        if not isinstance(answer,str) or len(answer)>32000:
            raise ValueError("Invalid answer")
        memory_ids=sorted({int(x) for x in MEMORY_CITE.findall(answer)})[:80]
        doc_refs=sorted({(int(a),int(b)) for a,b in DOCUMENT_CITE.findall(answer)})[:80]
        with closing(self.store.connect()) as db:
            message=db.execute(
                "SELECT role,content FROM messages WHERE id=?",(message_id,)
            ).fetchone()
            if message is None or message["role"]!="assistant" or message["content"]!=answer:
                raise ValueError("Answer does not match a persisted assistant reply")
            valid_memory=0
            valid_docs=0
            unresolved=0
            conflict=0
            for mem_id in memory_ids:
                item=db.execute("SELECT status FROM memory_items WHERE id=?",
                                (mem_id,)).fetchone()
                if item is None or item["status"]!="active":
                    unresolved+=1
                    continue
                valid_memory+=1
                conflict+=int(db.execute(
                    "SELECT 1 FROM memory_links WHERE relation='possible_conflict'"
                    " AND (source_id=? OR target_id=?) LIMIT 1",
                    (mem_id,mem_id)
                ).fetchone() is not None)
            for doc_id,ordinal in doc_refs:
                exists=db.execute(
                    "SELECT 1 FROM document_chunks WHERE document_id=? AND ordinal=?",
                    (doc_id,ordinal)
                ).fetchone()
                if exists is None:
                    unresolved+=1
                else:
                    valid_docs+=1
        label=("review_required" if unresolved or conflict else
               "sources_present_not_truth_checked" if valid_memory or valid_docs else
               "no_citations")
        with closing(self.store.connect()) as db,db:
            db.execute(
                "INSERT INTO answer_evidence_reviews"
                "(message_id,memory_citations,document_citations,unresolved,"
                "conflict_flags,label) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(message_id) DO UPDATE SET "
                "memory_citations=excluded.memory_citations,"
                "document_citations=excluded.document_citations,"
                "unresolved=excluded.unresolved,conflict_flags=excluded.conflict_flags,"
                "label=excluded.label",
                (message_id,valid_memory,valid_docs,unresolved,conflict,label))
        return {"message_id":message_id,"memory_citations":valid_memory,
                "document_citations":valid_docs,"missing_sources":unresolved,
                "possible_conflicts":conflict,"label":label,
                "truth_verified":False}

    def status(self,limit=25)->dict:
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError("Invalid audit limit")
        with closing(self.store.connect()) as db:
            items=[dict(x) for x in db.execute(
                "SELECT message_id,memory_citations,document_citations,"
                "unresolved,conflict_flags,label,created_at "
                "FROM answer_evidence_reviews ORDER BY message_id DESC LIMIT ?",
                (limit,)
            )]
        return {"items":items,"truth_verified":False,
                "meaning":"Проверено существование цитируемых источников, а не факты."}
