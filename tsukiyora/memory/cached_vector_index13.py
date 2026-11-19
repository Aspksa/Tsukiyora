"""Tsukiyora 13: incremental SQLite-only coarse vector candidate index.

Not a true ANN library or guaranteed global cosine nearest neighbor search.
Pre-indexes existing embedding cache vectors into deterministic 10-bit
multi-coordinate sign buckets; retrieves bucket-nearby candidates across
the *indexed portion of history* instead of only 144 most recent memories.

The original lexical/graph search still indexes the entire FTS5 history.
Never generates embeddings, sends private content externally, or installs
native libraries. Reuses the existing vector_repair queue slot.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from tsukiyora.memory.memory_store import MemoryStore
from tsukiyora.memory.vector_memory import digest, similarity, unpack_vector

BITS = 10
MAX_CANDIDATES = 384
MAX_BUCKETS = 56


def signature(values):
    """Deterministic coarse *candidate* locator, not an embedding score."""
    n = len(values)
    if n < 1:
        raise ValueError("Empty cached embedding.")
    result = 0
    for i in range(BITS):
        total = 0.0
        for j in range(10):
            index = (i * 7919 + j * 1543 + i * j * 17) % n
            sign = 1 if ((i * 3 + j * 7) % 4) < 2 else -1
            total += sign * values[index]
        if total >= 0:
            result |= (1 << i)
    return result


def neighbor_buckets(target):
    """Nearby signatures, with a small fixed local search budget."""
    one = [target ^ (1 << i) for i in range(BITS)]
    two = [target ^ (1 << i) ^ (1 << j)
           for i in range(BITS) for j in range(i+1,BITS)]
    return [target, *one, *two[:MAX_BUCKETS-1-len(one)]]


class CachedVectorIndex13:
    def __init__(self,path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memory13_vector_buckets(
                    memory_id INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    text_sha256 TEXT NOT NULL,
                    signature INTEGER NOT NULL,
                    PRIMARY KEY(memory_id,model)
                );
                CREATE INDEX IF NOT EXISTS idx_memory13_vector_signature
                    ON memory13_vector_buckets(model,signature,memory_id);
                CREATE TABLE IF NOT EXISTS memory13_vector_cursor(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    last_memory_id INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO memory13_vector_cursor(id,last_memory_id)
                    VALUES(1,0);
            """)

    def index_step(self, *, limit=300):
        if type(limit) is not int or not 1<=limit<=1200:
            raise ValueError("Invalid local vector index batch.")
        with closing(self.store.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                cursor = db.execute(
                    "SELECT last_memory_id FROM memory13_vector_cursor WHERE id=1"
                ).fetchone()[0]
                rows = db.execute(
                    "SELECT e.memory_id,e.model,e.text_sha256,"
                    "m.content,c.dimension,c.vector "
                    "FROM memory_embeddings e "
                    "JOIN memory_items m ON m.id=e.memory_id "
                    "JOIN embedding_cache c ON c.model=e.model "
                    "AND c.text_sha256=e.text_sha256 "
                    "WHERE e.memory_id>? ORDER BY e.memory_id,e.model LIMIT ?",
                    (cursor,limit)
                ).fetchall()
                made = stale = 0
                for row in rows:
                    if digest(row["content"]) != row["text_sha256"]:
                        bucket = -1  # Expired embedding; never a candidate.
                        stale += 1
                    else:
                        try:
                            vector=unpack_vector(row["vector"],row["dimension"])
                            bucket=signature(vector)
                        except (ValueError,TypeError):
                            bucket=-1
                            stale += 1
                    db.execute(
                        "INSERT INTO memory13_vector_buckets "
                        "(memory_id,model,text_sha256,signature) VALUES(?,?,?,?) "
                        "ON CONFLICT(memory_id,model) DO UPDATE SET "
                        "text_sha256=excluded.text_sha256,"
                        "signature=excluded.signature",
                        (row["memory_id"],row["model"],
                         row["text_sha256"],bucket)
                    )
                    made += 1
                # When multiple models share a memory_id, the safe cursor
                # must not advance past a partially processed memory.
                if rows:
                    last = rows[-1]["memory_id"]
                    tail = db.execute(
                        "SELECT COUNT(*) FROM memory_embeddings "
                        "WHERE memory_id=?",(last,)
                    ).fetchone()[0]
                    seen_tail = sum(x["memory_id"]==last for x in rows)
                    advance = last if seen_tail >= tail else last - 1
                    db.execute(
                        "UPDATE memory13_vector_cursor SET last_memory_id=? "
                        "WHERE id=1", (max(0,advance),)
                    )
                completed = len(rows)<limit
                if completed:
                    # Cycling lets re-embedded older memories refresh later;
                    # previous SHA is rechecked again at query time.
                    db.execute(
                        "UPDATE memory13_vector_cursor SET last_memory_id=0 "
                        "WHERE id=1"
                    )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"indexed": made, "stale": stale, "pass_complete": completed,
                "cloud_calls": 0, "new_workers": 0}

    def lookup(self, db, text, *, limit=40):
        if not isinstance(text,str) or not 1<=len(text)<=500 or (
            type(limit) is not int or not 1<=limit<=60):
            raise ValueError("Invalid local vector lookup.")
        qsha = digest(text)
        vectors = db.execute(
            "SELECT model,dimension,vector FROM embedding_cache "
            "WHERE text_sha256=? ORDER BY model LIMIT 2", (qsha,)
        ).fetchall()
        if not vectors:
            return []
        scores = {}
        for query in vectors:
            try:
                query_vec=unpack_vector(query["vector"],query["dimension"])
                bucket=signature(query_vec)
            except (ValueError,TypeError):
                continue
            buckets=neighbor_buckets(bucket)
            marks=",".join("?" for _ in buckets)
            # Current- and oldest-first half-pools avoid a pure recency bias.
            base=(
                "SELECT b.memory_id,b.text_sha256,m.content,c.dimension,c.vector "
                "FROM memory13_vector_buckets b "
                "JOIN memory_embeddings e ON e.memory_id=b.memory_id "
                "AND e.model=b.model AND e.text_sha256=b.text_sha256 "
                "JOIN embedding_cache c ON c.model=b.model "
                "AND c.text_sha256=b.text_sha256 "
                "JOIN memory_items m ON m.id=b.memory_id AND m.status='active' "
                "WHERE b.model=? AND b.signature IN ("+marks+") "
            )
            params=(query["model"],*buckets)
            for direction in ("DESC","ASC"):
                rows=db.execute(
                    base+"ORDER BY b.memory_id "+direction+" LIMIT ?",
                    (*params,MAX_CANDIDATES//2))
                for row in rows:
                    if digest(row["content"]) != row["text_sha256"]:
                        continue
                    try:
                        candidate=unpack_vector(row["vector"],row["dimension"])
                        value=similarity(query_vec,candidate)
                    except (ValueError,TypeError):
                        continue
                    if value>.25:
                        scores[row["memory_id"]]=max(
                            scores.get(row["memory_id"],0.0),value)
        return [i for i,_ in sorted(
            scores.items(), key=lambda item:(-item[1],-item[0]))[:limit]]

    def status(self):
        with closing(self.store.connect()) as db:
            total=db.execute(
                "SELECT COUNT(*) FROM memory_embeddings"
            ).fetchone()[0]
            indexed=db.execute(
                "SELECT COUNT(*) FROM memory13_vector_buckets "
                "WHERE signature>=0"
            ).fetchone()[0]
            cursor=db.execute(
                "SELECT last_memory_id FROM memory13_vector_cursor WHERE id=1"
            ).fetchone()[0]
        return {"existing_indexed_vectors":indexed,
                "memory_embedding_links":total,
                "cursor":cursor,
                "index_recall_guaranteed":False,
                "index_only_cached_embeddings":True,
                "cloud_calls":0}
