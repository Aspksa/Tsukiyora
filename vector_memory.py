"""Persistent SQLite embedding cache and deterministic hybrid retrieval.

No third-party libraries or local LLMs. Embeddings are opt-in and generated
only by an explicit request; private text goes to the selected Cloud.ru model.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import math
from pathlib import Path
import sqlite3
import struct

from soul_growth import score

MAX_DIM = 4096


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pack_vector(values: list[float]) -> bytes:
    if not isinstance(values, list) or not 2 <= len(values) <= MAX_DIM:
        raise ValueError("Embedding dimension must be between 2 and 4096.")
    if any(type(x) not in (float, int) or not math.isfinite(x) for x in values):
        raise ValueError("Embedding contains non-finite values.")
    return struct.pack("<" + "f" * len(values), *values)


def unpack_vector(data: bytes, dimension: int) -> tuple[float, ...]:
    if not 2 <= dimension <= MAX_DIM or len(data) != dimension * 4:
        raise ValueError("Stored embedding has an invalid dimension.")
    return struct.unpack("<" + "f" * dimension, data)


def similarity(left, right) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    a = math.sqrt(sum(x * x for x in left))
    b = math.sqrt(sum(x * x for x in right))
    return max(-1.0, min(1.0, dot / (a * b))) if a and b else 0.0


class VectorMemory:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def initialize(self):
        with closing(self.connect()) as db:
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS embedding_cache (
                        model TEXT NOT NULL,
                        text_sha256 TEXT NOT NULL,
                        dimension INTEGER NOT NULL,
                        vector BLOB NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(model, text_sha256)
                    );
                    CREATE TABLE IF NOT EXISTS memory_embeddings (
                        memory_id INTEGER NOT NULL,
                        model TEXT NOT NULL,
                        text_sha256 TEXT NOT NULL,
                        PRIMARY KEY(memory_id, model)
                    );
                    CREATE INDEX IF NOT EXISTS idx_memory_vectors_model
                        ON memory_embeddings(model, memory_id);
                """)

    def _cached(self, model: str, sha: str):
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT dimension,vector FROM embedding_cache WHERE model=? AND text_sha256=?",
                (model, sha),
            ).fetchone()
            return unpack_vector(row["vector"], row["dimension"]) if row else None

    def _remember(self, model: str, text: str, values: list[float], memory_id: int | None):
        encoded = pack_vector(values)
        sha = digest(text)
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO embedding_cache(model,text_sha256,dimension,vector) "
                "VALUES (?,?,?,?)",
                (model, sha, len(values), encoded),
            )
            if memory_id is not None:
                db.execute(
                    "INSERT INTO memory_embeddings(memory_id,model,text_sha256) VALUES (?,?,?) "
                    "ON CONFLICT(memory_id,model) DO UPDATE SET text_sha256=excluded.text_sha256",
                    (memory_id, model, sha),
                )

    def sync(self, cloud, limit: int = 40) -> dict:
        """Explicit batch operation; only user-approved active memories."""
        model = cloud.embedding_model()
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 80:
            raise ValueError("Invalid embedding batch size.")
        with closing(self.connect()) as db:
            records = [dict(row) for row in db.execute(
                "SELECT m.id,m.content FROM memory_items m "
                "LEFT JOIN memory_embeddings e ON e.memory_id=m.id AND e.model=? "
                "WHERE m.status='active' AND (e.text_sha256 IS NULL OR e.text_sha256<>?) "
                "ORDER BY m.id LIMIT ?",
                # An actual content hash must be compared per row below, not to a literal.
                (model, "", limit * 4),
            )]
        pending = []
        for row in records:
            sha = digest(row["content"])
            with closing(self.connect()) as db:
                mapping = db.execute(
                    "SELECT text_sha256 FROM memory_embeddings WHERE memory_id=? AND model=?",
                    (row["id"], model),
                ).fetchone()
            if mapping is None or mapping["text_sha256"] != sha:
                pending.append(row)
                if len(pending) >= limit:
                    break
        created = 0
        hits = 0
        for i in range(0, len(pending), 8):
            group = pending[i:i + 8]
            unknown = []
            for row in group:
                cached = self._cached(model, digest(row["content"]))
                if cached is None:
                    unknown.append(row)
                else:
                    self._remember(model, row["content"], list(cached), row["id"])
                    hits += 1
            if unknown:
                vectors = cloud.embed([x["content"][:8000] for x in unknown])
                if len(vectors) != len(unknown):
                    raise ValueError("Embedding provider returned an incomplete batch.")
                for row, vector in zip(unknown, vectors):
                    self._remember(model, row["content"], vector, row["id"])
                    created += 1
        return {"model": model, "processed": created + hits,
                "remote_vectors": created, "cache_hits": hits,
                "note": "Only explicitly confirmed memories were indexed."}

    def query(self, text: str, cloud, limit: int = 12, lexical_pool: int = 1500) -> dict:
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 800:
            raise ValueError("Invalid query.")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 30:
            raise ValueError("Invalid result limit.")
        model = cloud.embedding_model()
        query_hash = digest(text)
        qvec = self._cached(model, query_hash)
        if qvec is None:
            qvec = cloud.embed([text])[0]
            self._remember(model, text, qvec, None)
        with closing(self.connect()) as db:
            entries = [dict(x) for x in db.execute(
                "SELECT m.id,m.kind,m.content,m.source_type,m.source_ref,"
                "m.verification_state,m.created_at,e.text_sha256,c.dimension,c.vector "
                "FROM memory_items m "
                "LEFT JOIN memory_embeddings e ON e.memory_id=m.id AND e.model=? "
                "LEFT JOIN embedding_cache c ON c.model=e.model AND c.text_sha256=e.text_sha256 "
                "WHERE m.status='active' ORDER BY m.id DESC LIMIT ?",
                (model, lexical_pool),
            )]
        matched = []
        for row in entries:
            lexical = score(text, row["content"])
            vector_score = 0.0
            valid = row["text_sha256"] == digest(row["content"]) and row["vector"] is not None
            if valid:
                try:
                    vector_score = max(0.0, similarity(qvec, unpack_vector(row["vector"], row["dimension"])))
                except ValueError:
                    valid = False
            combined = (0.67 * vector_score + 0.33 * lexical) if valid else 0.33 * lexical
            if combined >= 0.12:
                result = {key: row[key] for key in (
                    "id", "kind", "content", "source_type", "source_ref",
                    "verification_state", "created_at"
                )}
                result["score"] = round(combined, 4)
                result["vector_matched"] = bool(valid)
                matched.append(result)
        matched.sort(key=lambda x: (x["score"], x["id"]), reverse=True)
        return {"items": matched[:limit], "mode": "vector_hybrid",
                "model": model, "covered": sum(x.get("vector_matched", False) for x in matched)}

    def stats(self, model: str | None = None) -> dict:
        with closing(self.connect()) as db:
            size = db.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0]
            indexed = db.execute(
                "SELECT COUNT(*) FROM memory_embeddings e JOIN memory_items m "
                "ON m.id=e.memory_id WHERE m.status='active' AND (? IS NULL OR e.model=?)",
                (model, model),
            ).fetchone()[0]
            total = db.execute(
                "SELECT COUNT(*) FROM memory_items WHERE status='active'"
            ).fetchone()[0]
            return {"cached_vectors": size, "indexed_memories": indexed, "active_memories": total}

    def prune(self):
        """Remove stale bindings, never delete approved memory or history."""
        with closing(self.connect()) as db, db:
            db.execute(
                "DELETE FROM memory_embeddings WHERE memory_id NOT IN "
                "(SELECT id FROM memory_items WHERE status='active')"
            )
            # Stale content mappings are filtered at query time and reindexed on demand.
