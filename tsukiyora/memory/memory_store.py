"""Durable local memory storage for Tsukiyora. Standard library only.

No model calls, background learning or automatic fact extraction in this module.
Private records stay in the local ignored data/ directory.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import uuid

KINDS = frozenset({"fact", "preference", "event", "biography"})
ROLES = frozenset({"user", "assistant"})


def _text(value, name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text.")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit:
        raise ValueError(f"{name} must have 1 to {limit} characters.")
    return cleaned


def _limit(value, maximum: int = 100) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= maximum):
        raise ValueError(f"limit must be between 1 and {maximum}.")
    return value


def _fingerprint(kind: str, content: str) -> str:
    normalized = " ".join(content.casefold().split())
    return hashlib.sha256((kind + "\0" + normalized).encode("utf-8")).hexdigest()


class MemoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id TEXT NOT NULL REFERENCES conversations(id),
                        role TEXT NOT NULL CHECK (role IN ('user','assistant')),
                        content TEXT NOT NULL,
                        client_message_id TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(conversation_id, client_message_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_messages_conversation
                        ON messages(conversation_id,id);
                    CREATE TABLE IF NOT EXISTS memory_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        kind TEXT NOT NULL CHECK (kind IN ('fact','preference','event','biography')),
                        content TEXT NOT NULL,
                        normalized_text TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        source_ref TEXT,
                        verification_state TEXT NOT NULL CHECK (verification_state IN ('user_reported','unverified')),
                        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','deleted')),
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_active_fingerprint
                        ON memory_items(fingerprint) WHERE status='active';
                    CREATE INDEX IF NOT EXISTS idx_memory_active
                        ON memory_items(status,kind,id DESC);
                    CREATE TABLE IF NOT EXISTS memory_revisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        memory_id INTEGER NOT NULL REFERENCES memory_items(id),
                        operation TEXT NOT NULL CHECK (operation IN ('create','correct','delete')),
                        before_content TEXT,
                        after_content TEXT,
                        reason TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)

    def create_conversation(self, title: str = "Новый диалог") -> dict:
        title = _text(title, "title", 128)
        conversation_id = uuid.uuid4().hex
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO conversations(id,title) VALUES (?,?)",
                (conversation_id, title),
            )
            result = db.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            return dict(result)

    def list_conversations(self, limit: int = 30) -> list[dict]:
        limit = _limit(limit)
        with closing(self.connect()) as db:
            return [dict(x) for x in db.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)
            )]

    def append_messages(self, conversation_id: str, messages: list[dict]) -> list[dict]:
        conversation_id = _text(conversation_id, "conversation_id", 64)
        if not isinstance(messages, list) or not 1 <= len(messages) <= 20:
            raise ValueError("messages must contain 1 to 20 items.")
        validated = []
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("Each message must be an object.")
            role = message.get("role")
            if role not in ROLES:
                raise ValueError("Message role must be user or assistant.")
            content = _text(message.get("content"), "message content", 32000)
            key = message.get("client_message_id")
            key = _text(key, "client_message_id", 128) if key is not None else uuid.uuid4().hex
            validated.append((role, content, key))

        with closing(self.connect()) as db, db:
            if db.execute("SELECT 1 FROM conversations WHERE id=?", (conversation_id,)).fetchone() is None:
                raise ValueError("Unknown conversation.")
            results = []
            inserted = False
            for role, content, key in validated:
                prior = db.execute(
                    "SELECT * FROM messages WHERE conversation_id=? AND client_message_id=?",
                    (conversation_id, key)
                ).fetchone()
                if prior is not None:
                    if prior["role"] != role or prior["content"] != content:
                        raise ValueError("client_message_id was already used for different content.")
                    results.append(dict(prior))
                    continue
                db.execute(
                    "INSERT INTO messages(conversation_id,role,content,client_message_id) VALUES (?,?,?,?)",
                    (conversation_id, role, content, key),
                )
                results.append(dict(db.execute(
                    "SELECT * FROM messages WHERE id=last_insert_rowid()"
                ).fetchone()))
                inserted = True
            if inserted:
                db.execute(
                    "UPDATE conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,)
                )
            return results

    def message_by_client_id(self, conversation_id: str, client_message_id: str) -> dict | None:
        """Idempotent chat retries must not purchase a second model response."""
        conversation_id = _text(conversation_id, "conversation_id", 64)
        client_message_id = _text(client_message_id, "client_message_id", 128)
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND client_message_id=?",
                (conversation_id, client_message_id),
            ).fetchone()
            return dict(row) if row else None

    def list_messages(self, conversation_id: str, before_id: int | None = None, limit: int = 100) -> list[dict]:
        conversation_id = _text(conversation_id, "conversation_id", 64)
        limit = _limit(limit, 200)
        if before_id is not None and (type(before_id) is not int or before_id <= 0):
            raise ValueError("before_id must be a positive integer.")
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND (? IS NULL OR id<?)"
                " ORDER BY id DESC LIMIT ?",
                (conversation_id, before_id, before_id, limit),
            ).fetchall()
            return [dict(x) for x in reversed(rows)]

    def add_memory(
        self, kind: str, content: str, source_type: str = "manual",
        source_ref: str | None = None, verification_state: str = "user_reported",
    ) -> dict:
        if kind not in KINDS:
            raise ValueError("Invalid memory kind.")
        content = _text(content, "content", 8000)
        if source_type not in ("manual", "message"):
            raise ValueError("Invalid memory source.")
        if verification_state not in ("user_reported", "unverified"):
            raise ValueError("Invalid verification state.")
        if source_ref is not None:
            source_ref = _text(source_ref, "source_ref", 128)
        fingerprint = _fingerprint(kind, content)
        with closing(self.connect()) as db, db:
            prior = db.execute(
                "SELECT * FROM memory_items WHERE fingerprint=? AND status='active'",
                (fingerprint,),
            ).fetchone()
            if prior is not None:
                return dict(prior)
            db.execute(
                "INSERT INTO memory_items(kind,content,normalized_text,fingerprint,"
                "source_type,source_ref,verification_state) VALUES (?,?,?,?,?,?,?)",
                (kind, content, content.casefold(), fingerprint, source_type, source_ref, verification_state),
            )
            row = db.execute("SELECT * FROM memory_items WHERE id=last_insert_rowid()").fetchone()
            db.execute(
                "INSERT INTO memory_revisions(memory_id,operation,before_content,after_content,reason)"
                " VALUES (?,'create',NULL,?,'recorded')",
                (row["id"], content),
            )
            return dict(row)

    def find_memories(self, query: str = "", kind: str | None = None, limit: int = 50) -> list[dict]:
        limit = _limit(limit)
        if kind not in (None, "", *KINDS):
            raise ValueError("Invalid memory kind.")
        if not isinstance(query, str) or len(query) > 300:
            raise ValueError("query is too long.")
        pattern = query.strip().casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with closing(self.connect()) as db:
            return [dict(x) for x in db.execute(
                "SELECT * FROM memory_items WHERE status='active' "
                "AND (?='' OR normalized_text LIKE ? ESCAPE '\\') "
                "AND (?='' OR kind=?) ORDER BY id DESC LIMIT ?",
                (pattern, "%" + pattern + "%", kind or "", kind or "", limit),
            )]

    def get_memory(self, memory_id: int) -> dict | None:
        if type(memory_id) is not int or not 1 <= memory_id <= 2**63 - 1:
            raise ValueError("Invalid memory ID.")
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT id, kind, content, created_at, updated_at, source_type, "
                "source_ref, verification_state FROM memory_items WHERE id=? AND status='active'",
                (memory_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def correct_memory(self, memory_id: int, content: str, reason: str = "user correction") -> dict:
        if type(memory_id) is not int or memory_id <= 0:
            raise ValueError("Invalid memory ID.")
        content = _text(content, "content", 8000)
        reason = _text(reason, "reason", 250)
        with closing(self.connect()) as db, db:
            before = db.execute(
                "SELECT * FROM memory_items WHERE id=? AND status='active'", (memory_id,)
            ).fetchone()
            if before is None:
                raise ValueError("Memory not found.")
            fingerprint = _fingerprint(before["kind"], content)
            conflict = db.execute(
                "SELECT id FROM memory_items WHERE fingerprint=? AND status='active' AND id<>?",
                (fingerprint, memory_id),
            ).fetchone()
            if conflict is not None:
                raise ValueError("This memory already exists.")
            db.execute(
                "UPDATE memory_items SET content=?, normalized_text=?, fingerprint=?,"
                " updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (content, content.casefold(), fingerprint, memory_id),
            )
            db.execute(
                "INSERT INTO memory_revisions(memory_id,operation,before_content,after_content,reason)"
                " VALUES (?,'correct',?,?,?)",
                (memory_id, before["content"], content, reason),
            )
            return dict(db.execute("SELECT * FROM memory_items WHERE id=?", (memory_id,)).fetchone())

    def delete_memory(self, memory_id: int) -> None:
        if type(memory_id) is not int or memory_id <= 0:
            raise ValueError("Invalid memory ID.")
        with closing(self.connect()) as db, db:
            before = db.execute(
                "SELECT * FROM memory_items WHERE id=? AND status='active'", (memory_id,)
            ).fetchone()
            if before is None:
                raise ValueError("Memory not found.")
            db.execute("UPDATE memory_items SET status='deleted', updated_at=CURRENT_TIMESTAMP WHERE id=?", (memory_id,))
            db.execute(
                "INSERT INTO memory_revisions(memory_id,operation,before_content,after_content,reason)"
                " VALUES (?,'delete',?,NULL,'user requested')",
                (memory_id, before["content"]),
            )

    def stats(self) -> dict:
        with closing(self.connect()) as db:
            return {
                "memories": db.execute("SELECT count(*) FROM memory_items WHERE status='active'").fetchone()[0],
                "conversations": db.execute("SELECT count(*) FROM conversations").fetchone()[0],
                "messages": db.execute("SELECT count(*) FROM messages").fetchone()[0],
                "private_storage": "local",
            }

    def backup(self, folder: Path | None = None) -> Path:
        """Online SQLite backup, safe while WAL writers are active; never copy .db directly."""
        folder = Path(folder) if folder is not None else self.path.parent / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        name = datetime.now(timezone.utc).strftime("memory-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + ".sqlite3"
        target = folder / name
        try:
            with closing(self.connect()) as source, closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
                result = destination.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    raise sqlite3.DatabaseError("Backup integrity check failed.")
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return target
