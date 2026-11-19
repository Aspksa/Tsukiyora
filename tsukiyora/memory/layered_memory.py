"""Layered, provenance-aware memory built entirely on existing local SQLite.

Short-term = bounded recent user/assistant dialogue, never silently promoted.
Episodic = approved event facts. Semantic = approved knowledge. Autobiographic
= approved biography and preferences. Every layer is traceable to the original
memory item/revision, and local confidence is explicitly NOT real-world truth.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from tsukiyora.memory.memory_store import MemoryStore, _limit

LAYER_BY_KIND = {"event": "episodic", "biography": "autobiographical",
                 "preference": "autobiographical", "fact": "semantic"}
LAYERS = frozenset((*LAYER_BY_KIND.values(), "short_term"))
PRIORITY_BY_KIND = {"event": 2, "biography": 3, "preference": 3, "fact": 2}


class LayeredMemory:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memory_layer_index (
                    memory_id INTEGER PRIMARY KEY REFERENCES memory_items(id),
                    layer TEXT NOT NULL CHECK(layer IN
                        ('episodic','autobiographical','semantic')),
                    priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 5),
                    revision_id INTEGER NOT NULL DEFAULT 0,
                    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_memory_layer_priority
                    ON memory_layer_index(layer,priority DESC,memory_id DESC);
            """)

    def repair(self, limit: int = 10000) -> dict:
        """Idempotent repair of derived metadata, no deletion of raw memory."""
        if type(limit) is not int or not 1 <= limit <= 50000:
            raise ValueError("Invalid index limit.")
        with closing(self.store.connect()) as db, db:
            removed = db.execute(
                "DELETE FROM memory_layer_index WHERE memory_id NOT IN "
                "(SELECT id FROM memory_items WHERE status='active')"
            ).rowcount
            records = db.execute(
                "SELECT m.id,m.kind,COALESCE(MAX(r.id),0) revision "
                "FROM memory_items m LEFT JOIN memory_revisions r ON r.memory_id=m.id "
                "WHERE m.status='active' GROUP BY m.id ORDER BY m.id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            updated = 0
            for row in records:
                layer = LAYER_BY_KIND[row["kind"]]
                priority = PRIORITY_BY_KIND[row["kind"]]
                current = db.execute(
                    "SELECT layer,priority,revision_id FROM memory_layer_index WHERE memory_id=?",
                    (row["id"],)
                ).fetchone()
                if (current and current["layer"] == layer
                    and current["priority"] == priority
                    and current["revision_id"] == row["revision"]):
                    continue
                db.execute(
                    "INSERT INTO memory_layer_index(memory_id,layer,priority,revision_id)"
                    " VALUES (?,?,?,?) ON CONFLICT(memory_id) DO UPDATE SET"
                    " layer=excluded.layer,priority=excluded.priority,"
                    " revision_id=excluded.revision_id,indexed_at=CURRENT_TIMESTAMP",
                    (row["id"], layer, priority, row["revision"])
                )
                updated += 1
            return {"examined": len(records), "updated": updated,
                    "inactive_removed": removed, "cloud_calls": 0}

    def inspect(self, layer: str = "all", limit: int = 50, conversation_id: str | None = None) -> dict:
        if layer not in ("all", *LAYERS):
            raise ValueError("Invalid memory layer.")
        limit = _limit(limit, 200)
        self.repair(min(10000, limit * 100))
        with closing(self.store.connect()) as db:
            if layer == "short_term":
                if conversation_id is None:
                    convo = db.execute(
                        "SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1"
                    ).fetchone()
                    conversation_id = convo["id"] if convo else None
                if not conversation_id:
                    return {"layer": layer, "items": [], "count": 0}
                messages = [dict(row) for row in db.execute(
                    "SELECT id,role,content,created_at FROM messages "
                    "WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (conversation_id, limit)
                )]
                return {"layer": layer, "items": [
                    {"id": x["id"], "layer": "short_term", "source_type": "message",
                     "role": x["role"], "content": x["content"],
                     "created_at": x["created_at"], "priority": 1}
                    for x in messages], "count": len(messages), "links": []}
            items = [dict(row) for row in db.execute(
                "SELECT m.id,m.kind,m.content,m.source_type,m.source_ref,"
                "m.verification_state,m.created_at,i.layer,i.priority,i.revision_id "
                "FROM memory_items m JOIN memory_layer_index i ON i.memory_id=m.id "
                "WHERE m.status='active' AND (?='all' OR i.layer=?)"
                " ORDER BY i.priority DESC,m.id DESC LIMIT ?",
                (layer, layer, limit)
            )]
            revisions = db.execute(
                "SELECT COUNT(*) FROM memory_revisions"
            ).fetchone()[0]
            links = [dict(x) for x in db.execute(
                "SELECT l.source_id,l.target_id,l.relation,"
                "a.layer AS source_layer,b.layer AS target_layer "
                "FROM memory_links l "
                "JOIN memory_layer_index a ON a.memory_id=l.source_id "
                "JOIN memory_layer_index b ON b.memory_id=l.target_id "
                "JOIN memory_items m ON m.id=l.source_id AND m.status='active' "
                "JOIN memory_items n ON n.id=l.target_id AND n.status='active' "
                "WHERE (?='all' OR a.layer=? OR b.layer=?)"
                " ORDER BY l.created_at DESC LIMIT 120",
                (layer,layer,layer),
            )]
        return {"layer": layer, "items": items, "links": links,
                "count": len(items), "revision_events": revisions}

    def confidence(self, memory_id: int) -> dict:
        if type(memory_id) is not int or memory_id <= 0:
            raise ValueError("Invalid memory ID.")
        with closing(self.store.connect()) as db:
            item = db.execute(
                "SELECT id,source_type,source_ref,verification_state,status "
                "FROM memory_items WHERE id=?", (memory_id,)
            ).fetchone()
            if item is None or item["status"] != "active":
                raise ValueError("Memory not active.")
            revisions = db.execute(
                "SELECT COUNT(*) FROM memory_revisions WHERE memory_id=?",
                (memory_id,)
            ).fetchone()[0]
        # A numeric indicator represents traceability, not the factual truth.
        traceability = 0.85 if item["source_type"] == "message" else 0.65
        return {"memory_id": memory_id, "traceability": traceability,
                "source_type": item["source_type"],
                "source_ref": item["source_ref"], "revision_count": revisions,
                "truth_verified": False,
                "explanation": "Оценка полноты источника, а не истинности факта."}
