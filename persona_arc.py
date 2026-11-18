"""Auditable, reversible personality continuity from explicitly approved memory.

No subjective feelings. Protected identity in soul/personality.json is never
written; changes are tracked by source IDs, revision IDs and counts only.
"""
from __future__ import annotations
from contextlib import closing
import hashlib
import json

from memory_store import MemoryStore


class PersonaArc:
    def __init__(self,path):
        self.store=MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db,db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS persona_arc(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_digest TEXT NOT NULL UNIQUE,
                    approved_facts INTEGER NOT NULL,
                    approved_habits INTEGER NOT NULL,
                    corrected_items INTEGER NOT NULL,
                    conversations INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def consolidate(self)->dict:
        with closing(self.store.connect()) as db:
            approved=[dict(row) for row in db.execute(
                "SELECT m.id,m.kind,COALESCE(MAX(r.id),0) AS revision,"
                "SUM(CASE WHEN r.operation='correct' THEN 1 ELSE 0 END) AS corrections "
                "FROM memory_items m LEFT JOIN memory_revisions r ON r.memory_id=m.id "
                "WHERE m.status='active' AND m.kind IN ('biography','preference') "
                "GROUP BY m.id ORDER BY m.id LIMIT 20000"
            )]
            conversations=db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        contents=json.dumps([[x["id"],x["kind"],x["revision"],x["corrections"]] for x in approved],
                            separators=(",",":")).encode()
        digest=hashlib.sha256(contents).hexdigest()
        habits=sum(x["kind"]=="preference" for x in approved)
        corrected=sum(x["corrections"]>0 for x in approved)
        with closing(self.store.connect()) as db,db:
            cursor=db.execute(
                "INSERT OR IGNORE INTO persona_arc"
                "(source_digest,approved_facts,approved_habits,corrected_items,conversations)"
                " VALUES(?,?,?,?,?)",
                (digest,sum(x["kind"]=="biography" for x in approved),
                 habits,corrected,conversations)
            )
            db.execute(
                "DELETE FROM persona_arc WHERE id NOT IN "
                "(SELECT id FROM persona_arc ORDER BY id DESC LIMIT 120)"
            )
        return {"changed":bool(cursor.rowcount),"approved_habits":habits,
                "approved_biography":len(approved)-habits,
                "protected_persona_unchanged":True,"real_emotions":False}

    def history(self,limit=24)->dict:
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError("Invalid arc limit")
        with closing(self.store.connect()) as db:
            rows=[dict(x) for x in db.execute(
                "SELECT approved_facts,approved_habits,corrected_items,"
                "conversations,created_at FROM persona_arc "
                "ORDER BY id DESC LIMIT ?",(limit,)
            )]
        return {"snapshots":rows,"privacy":"counts_and_source_versions_only",
                "source":"approved_local_memory","protected_persona_unchanged":True}
