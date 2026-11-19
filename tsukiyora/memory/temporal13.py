"""Tsukiyora 13.0: user-attested bitemporal context for existing facts.

This augments MemoryStore revisions without rewriting source records. The
two time axes are *recorded_at* (database timestamp) and user-specified
effective dates. Date boundaries and rationales are assertions by the user,
NOT independently established truth or a historical model snapshot.

No provider calls, network access, extra DB, background workers, or packages.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date
import hashlib
import re

from tsukiyora.memory.memory_store import MemoryStore

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ENTITY = re.compile(r"^[\wа-яёА-ЯЁ:./\- ]{1,100}$", re.UNICODE)


def _date(value, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError("Дата должна быть YYYY-MM-DD.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError("Несуществующая календарная дата.") from None


def _hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class TemporalMemory13:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memory13_validity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL REFERENCES memory_items(id),
                    content_sha256 TEXT NOT NULL,
                    effective_from TEXT,
                    effective_until TEXT,
                    entity_key TEXT,
                    rationale TEXT NOT NULL,
                    source_type TEXT NOT NULL DEFAULT 'user_attested',
                    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK (effective_from IS NOT NULL OR effective_until IS NOT NULL)
                );
                CREATE INDEX IF NOT EXISTS idx_memory13_validity_memory
                    ON memory13_validity(memory_id,id DESC);
                CREATE INDEX IF NOT EXISTS idx_memory13_validity_entity
                    ON memory13_validity(entity_key,id DESC);
            """)

    def annotate(self, memory_id, *, effective_from=None,
                 effective_until=None, entity_key=None, rationale="",
                 confirmed=False):
        """Explicitly tag a CURRENT fact's effective interval; append-only."""
        if type(memory_id) is not int or memory_id < 1 or confirmed is not True:
            raise ValueError("Требуется явное подтверждение и ID воспоминания.")
        start = _date(effective_from, optional=True)
        end = _date(effective_until, optional=True)
        if start is None and end is None:
            raise ValueError("Укажи хотя бы одну границу действительности.")
        if start and end and start >= end:
            raise ValueError("Конечная дата должна быть позже начальной.")
        if entity_key is not None:
            if not isinstance(entity_key, str) or not _ENTITY.fullmatch(entity_key):
                raise ValueError("Некорректное название сущности.")
            entity_key = entity_key.strip().casefold()
        if not isinstance(rationale, str) or not 8 <= len(rationale.strip()) <= 280:
            raise ValueError("Нужно краткое объяснение с 8–280 символами.")
        with closing(self.store.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT content FROM memory_items WHERE id=? AND status='active'",
                    (memory_id,)
                ).fetchone()
                if row is None:
                    raise ValueError("Активное воспоминание не найдено.")
                finger = _hash(row["content"])
                old = db.execute(
                    "SELECT id,content_sha256,effective_from,effective_until,"
                    "entity_key,rationale FROM memory13_validity "
                    "WHERE memory_id=? ORDER BY id DESC LIMIT 1", (memory_id,)
                ).fetchone()
                if old and (old["content_sha256"],old["effective_from"],
                            old["effective_until"],old["entity_key"],
                            old["rationale"]) == (
                                finger,start,end,entity_key,rationale.strip()):
                    db.commit()
                    return {"annotation_id": old["id"], "already_recorded": True,
                            "source_unchanged": True, "cloud_calls": 0}
                cursor = db.execute(
                    "INSERT INTO memory13_validity "
                    "(memory_id,content_sha256,effective_from,effective_until,"
                    "entity_key,rationale) VALUES (?,?,?,?,?,?)",
                    (memory_id,finger,start,end,entity_key,rationale.strip())
                )
                result = cursor.lastrowid
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"annotation_id": result, "already_recorded": False,
                "source_unchanged": True, "cloud_calls": 0}

    def timeline(self, memory_id, *, limit=30):
        if type(memory_id) is not int or memory_id < 1 or (
            type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError("Некорректный идентификатор или лимит.")
        with closing(self.store.connect()) as db:
            row = db.execute(
                "SELECT id,kind,content,status,created_at,updated_at "
                "FROM memory_items WHERE id=?", (memory_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Воспоминание не найдено.")
            versions = [dict(x) for x in db.execute(
                "SELECT id,operation,before_content,after_content,reason,created_at "
                "FROM memory_revisions WHERE memory_id=? ORDER BY id DESC LIMIT ?",
                (memory_id, limit)
            )]
            annotations = [dict(x) for x in db.execute(
                "SELECT id,effective_from,effective_until,entity_key,rationale,"
                "source_type,content_sha256,recorded_at FROM memory13_validity "
                "WHERE memory_id=? ORDER BY id DESC LIMIT ?",
                (memory_id, limit)
            )]
            current_hash = _hash(row["content"]) if row["status"] == "active" else None
        for note in annotations:
            note["applies_to_current_version"] = (
                current_hash is not None and note.pop("content_sha256") == current_hash
            )
        return {
            "memory_id": memory_id,
            "kind": row["kind"], "status": row["status"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "revisions": versions, "validity_annotations": annotations,
            "truth_independently_verified": False,
            "snapshot_reconstruction_claimed": False, "cloud_calls": 0,
        }

    def validity(self, db, rows, as_of):
        """Annotate results, never promote old/incorrect versions.

        Annotations on corrected/deleted fact versions are STALE. An ordinary
        V11 created_at filter still applies when it is requested; effective
        time is a separate user-attested dimension.
        """
        day = _date(as_of)
        if len(rows) > 100:
            raise ValueError("Слишком много записей.")
        ids = [row["memory_id"] for row in rows]
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        q = db.execute(
            "SELECT v.memory_id,v.content_sha256,v.effective_from,"
            "v.effective_until,v.entity_key,v.recorded_at,v.id "
            "FROM memory13_validity v WHERE v.memory_id IN ("+marks+") "
            "ORDER BY v.id DESC", ids
        ).fetchall()
        latest = {}
        for a in q:
            latest.setdefault(a["memory_id"], a)
        original = {r["id"]: r["content"] for r in db.execute(
            "SELECT id,content FROM memory_items WHERE status='active' "
            "AND id IN ("+marks+")", ids
        )}
        out = []
        for row in rows:
            a = latest.get(row["memory_id"])
            if a is None:
                out.append({**row, "time_basis": "created_at_only",
                            "validity_asserted": False})
                continue
            current = (_hash(original.get(row["memory_id"], "")) ==
                       a["content_sha256"])
            if not current:
                # Never use time metadata from an earlier corrected fact.
                out.append({**row, "time_basis": "stale_annotation_excluded",
                            "validity_asserted": False})
                continue
            within = ((a["effective_from"] is None or
                       a["effective_from"] <= day) and
                      (a["effective_until"] is None or
                       day < a["effective_until"]))
            if within:
                out.append({**row, "time_basis": "user_attested_effective_interval",
                            "validity_asserted": True,
                            "entity_key": a["entity_key"]})
        return out

    def status(self):
        with closing(self.store.connect()) as db:
            a = db.execute(
                "SELECT COUNT(*) FROM memory13_validity"
            ).fetchone()[0]
            b = db.execute(
                "SELECT COUNT(DISTINCT memory_id) FROM memory13_validity"
            ).fetchone()[0]
        return {
            "version": "13.0", "validity_annotations": a,
            "annotated_memories": b, "source_of_effective_dates": "explicit_user",
            "append_only": True, "automatic_fact_changes": False,
            "truth_independently_verified": False,
            "cloud_calls": 0
        }
