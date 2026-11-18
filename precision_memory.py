"""High-precision, local-first retrieval over user-approved SQLite memories.

Uses SQLite FTS5 when bundled in Python; clean lexical fallback otherwise.
Ranking is explainable and provenance-aware. Links help retrieve associated
facts, but neither similarity nor a citation is a proof of real-world truth.
No external network, secrets, or third-party dependencies.
"""
from __future__ import annotations

from contextlib import closing
from collections import Counter
import sqlite3
from pathlib import Path

from memory_quality import terms, normalized
from soul_growth import score as lexical_score
from memory_store import MemoryStore

PRIORITIES = {"biography": 3, "preference": 3, "fact": 2, "event": 2}
STOP_AT = 16


class PrecisionMemory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.store = MemoryStore(path)
        self.has_fts = False

    def connect(self):
        return self.store.connect()

    def initialize(self):
        """Install triggers, then backfill older records without changing history."""
        with closing(self.connect()) as db:
            try:
                with db:
                    db.executescript("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                            USING fts5(content, tokenize='unicode61 remove_diacritics 2');
                        CREATE TRIGGER IF NOT EXISTS memory_fts_ai
                        AFTER INSERT ON memory_items WHEN new.status='active'
                        BEGIN
                            INSERT INTO memory_fts(rowid,content)
                            VALUES(new.id,new.content);
                        END;
                        DROP TRIGGER IF EXISTS memory_fts_au_del;
                        DROP TRIGGER IF EXISTS memory_fts_au_ins;
                        CREATE TRIGGER IF NOT EXISTS memory_fts_au
                        AFTER UPDATE OF content,status ON memory_items
                        BEGIN
                            DELETE FROM memory_fts WHERE rowid=old.id;
                            INSERT INTO memory_fts(rowid,content)
                            SELECT new.id,new.content WHERE new.status='active';
                        END;
                        CREATE TRIGGER IF NOT EXISTS memory_fts_ad
                        AFTER DELETE ON memory_items WHEN old.status='active'
                        BEGIN
                            DELETE FROM memory_fts WHERE rowid=old.id;
                        END;
                    """)
                self.has_fts = True
            except sqlite3.OperationalError as error:
                if "fts5" not in str(error).lower():
                    raise
                self.has_fts = False
        if self.has_fts:
            self.repair(limit=500)

    def repair(self, limit: int = 600) -> dict:
        """Incremental repair; no Cloud.ru or destructive changes to memory_items."""
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("Invalid indexing limit.")
        with closing(self.connect()) as db:
            try:
                available = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='memory_fts' AND type='table'"
                ).fetchone() is not None
                if not available:
                    return {"fts": False, "indexed": 0, "cloud_requests": 0}
                with db:
                    db.execute(
                        "DELETE FROM memory_fts WHERE rowid IN "
                        "(SELECT f.rowid FROM memory_fts f LEFT JOIN memory_items m "
                        "ON m.id=f.rowid WHERE m.id IS NULL OR m.status<>'active' LIMIT ?)",
                        (limit,),
                    )
                    rows = db.execute(
                        "SELECT m.id,m.content FROM memory_items m "
                        "WHERE m.status='active' "
                        "AND NOT EXISTS(SELECT 1 FROM memory_fts f WHERE f.rowid=m.id) "
                        "ORDER BY m.id LIMIT ?",
                        (limit,),
                    ).fetchall()
                    for row in rows:
                        db.execute(
                            "INSERT INTO memory_fts(rowid,content) VALUES(?,?)",
                            (row["id"], row["content"]),
                        )
                self.has_fts = True
                return {"fts": True, "indexed": len(rows), "cloud_requests": 0}
            except sqlite3.OperationalError as error:
                if "fts5" not in str(error).lower() and "no such table: memory_fts" not in str(error):
                    raise
                return {"fts": False, "indexed": 0, "cloud_requests": 0}

    def search(self, query: str, limit: int = 12, include_links: bool = True) -> dict:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
            raise ValueError("Запрос должен содержать от 1 до 500 символов.")
        if type(limit) is not int or not 1 <= limit <= 40:
            raise ValueError("Неверное количество результатов.")
        if type(include_links) is not bool:
            raise ValueError("Неверный режим поиска.")
        words = sorted(terms(query), key=len, reverse=True)[:STOP_AT]
        with closing(self.connect()) as db:
            ids = []
            rank = {}
            method = "local_lexical"
            if words:
                expression = " OR ".join('"' + w.replace('"', '""') + '"' for w in words)
                try:
                    matches = db.execute(
                        "SELECT rowid,bm25(memory_fts) AS rank FROM memory_fts "
                        "WHERE memory_fts MATCH ? ORDER BY rank LIMIT 400",
                        (expression,),
                    ).fetchall()
                    ids = [x["rowid"] for x in matches]
                    rank = {x["rowid"]: -float(x["rank"]) for x in matches}
                    method = "fts5_hybrid"
                except sqlite3.OperationalError:
                    pass
            # Fallback / supplemental recent material if FTS5 is unavailable
            # or the query has no eligible word tokens.
            if not ids:
                ids = [x["id"] for x in db.execute(
                    "SELECT id FROM memory_items WHERE status='active' "
                    "ORDER BY id DESC LIMIT 2500"
                ).fetchall()]
            placeholder = ",".join("?" for _ in ids)
            records = [] if not ids else [dict(x) for x in db.execute(
                "SELECT m.id,m.kind,m.content,m.source_type,m.source_ref,"
                "m.verification_state,m.created_at,m.updated_at,"
                "COALESCE(l.priority,2) AS priority,COALESCE(l.revision_id,0) AS revision_id "
                "FROM memory_items m LEFT JOIN memory_layer_index l ON l.memory_id=m.id "
                "WHERE m.status='active' AND m.id IN (" + placeholder + ")",
                tuple(ids),
            )]
            candidates = {}
            wordset = set(words)
            for x in records:
                tokens = terms(x["content"])
                overlap = len(wordset & tokens) / max(1, len(wordset))
                fuzzy = lexical_score(query, x["content"])
                strong = 1.0 if normalized(query) in normalized(x["content"]) else 0
                relevance = .64 * overlap + .24 * fuzzy + .12 * strong
                if x["id"] in rank:
                    relevance += .04
                if relevance < .115:
                    continue
                # Priority is a small tie-breaker, never beats relevance.
                relevance = min(1.0, relevance + .016 * (x["priority"] - 2))
                x.update(score=round(relevance,4), match="direct",
                         possible_conflict=False, source_verified=False)
                candidates[x["id"]] = x
            seeds = sorted(candidates.values(), key=lambda x: (-x["score"], -x["id"]))[:min(20, limit*2)]
            if include_links and seeds:
                seed_ids = [x["id"] for x in seeds]
                marks = ",".join("?" for _ in seed_ids)
                edges = db.execute(
                    "SELECT source_id,target_id,relation FROM memory_links "
                    "WHERE source_id IN ("+marks+") OR target_id IN ("+marks+") "
                    "LIMIT 200", tuple(seed_ids)*2,
                ).fetchall()
                seed_scores = {x["id"]:x["score"] for x in seeds}
                for edge in edges:
                    a, b = edge["source_id"], edge["target_id"]
                    if a in candidates and edge["relation"] == "possible_conflict":
                        candidates[a]["possible_conflict"] = True
                    if b in candidates and edge["relation"] == "possible_conflict":
                        candidates[b]["possible_conflict"] = True
                    for near, target in ((a,b),(b,a)):
                        if near not in seed_scores or target in candidates:
                            continue
                        related = db.execute(
                            "SELECT id,kind,content,source_type,source_ref,"
                            "verification_state,created_at,updated_at FROM memory_items "
                            "WHERE id=? AND status='active'", (target,)
                        ).fetchone()
                        if related is None:
                            continue
                        item = dict(related)
                        item.update(score=round(seed_scores[near]*.46,4),
                                    priority=PRIORITIES[item["kind"]],
                                    revision_id=0,match="related",
                                    related_to=near,
                                    possible_conflict=edge["relation"]=="possible_conflict",
                                    source_verified=False)
                        candidates[target] = item
                        if len(candidates) >= 220:
                            break
            items = sorted(candidates.values(), key=lambda x: (-x["score"], -x["id"]))[:limit]
        return {"items":items,"mode":method,"source_policy":"user_reported_not_independently_verified",
                "cloud_requests":0,"graph_expansion":include_links}
