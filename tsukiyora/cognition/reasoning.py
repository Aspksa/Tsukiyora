"""Local audit of model citations and possible source contradictions.

This is a provenance check, NOT a proof that model assertions are true.
No model calls, no unsafe self-modification, no automatic memory rewriting.
"""
from __future__ import annotations
from contextlib import closing
import json
import re
from tsukiyora.memory.memory_store import MemoryStore

REFERENCE = re.compile(r"\[Память\s*#(\d+)\]", re.I)


class ReasoningAudit:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS reasoning_audits (
                    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                    state TEXT NOT NULL CHECK(state IN
                        ('source_checked','needs_review','no_citations')),
                    cited_ids TEXT NOT NULL,
                    invalid_ids TEXT NOT NULL,
                    conflict_ids TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def verify(self, message_id: int, text: str) -> dict:
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("Expected assistant message ID.")
        if not isinstance(text, str) or len(text) > 32000:
            raise ValueError("Invalid answer text.")
        cited = list(dict.fromkeys(int(m.group(1)) for m in REFERENCE.finditer(text)))[:40]
        with closing(self.store.connect()) as db:
            role = db.execute(
                "SELECT role,content FROM messages WHERE id=?", (message_id,)
            ).fetchone()
            if role is None or role["role"] != "assistant" or role["content"] != text:
                raise ValueError("Audit must match a persisted assistant message.")
            valid, invalid, conflict = [], [], []
            for memory_id in cited:
                item = db.execute(
                    "SELECT id,status FROM memory_items WHERE id=?", (memory_id,)
                ).fetchone()
                if item is None or item["status"] != "active":
                    invalid.append(memory_id)
                    continue
                valid.append(memory_id)
                links = db.execute(
                    "SELECT source_id,target_id FROM memory_links WHERE relation='possible_conflict'"
                    " AND (source_id=? OR target_id=?)",
                    (memory_id, memory_id)
                ).fetchall()
                if links:
                    conflict.append(memory_id)
        if invalid or conflict:
            state = "needs_review"
        elif not cited:
            state = "no_citations"
        else:
            state = "source_checked"
        with closing(self.store.connect()) as db, db:
            db.execute(
                "INSERT INTO reasoning_audits"
                "(message_id,state,cited_ids,invalid_ids,conflict_ids) VALUES (?,?,?,?,?)"
                " ON CONFLICT(message_id) DO UPDATE SET"
                " state=excluded.state,cited_ids=excluded.cited_ids,"
                " invalid_ids=excluded.invalid_ids,conflict_ids=excluded.conflict_ids",
                (message_id, state, json.dumps(valid),
                 json.dumps(invalid), json.dumps(conflict))
            )
        return {
            "message_id": message_id, "state": state,
            "valid_citations": valid, "missing_citations": invalid,
            "possible_conflicts": conflict,
            "truth_verified": False,
            "explanation": "Проверены только существование источников и сигналы конфликтов, не истинность ответа."
        }

    def history(self, limit: int = 30) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid audit limit.")
        with closing(self.store.connect()) as db:
            return [dict(x) for x in db.execute(
                "SELECT message_id,state,cited_ids,invalid_ids,conflict_ids,created_at "
                "FROM reasoning_audits ORDER BY created_at DESC,message_id DESC LIMIT ?",
                (limit,)
            )]
