"""RAG 4.0: deterministic multi-evidence retrieval without hidden model calls.

Combines local multi-pass FTS5/graph evidence with previously cached vectors,
when the user explicitly selected a vector model and enabled vector chat.
Uncached queries never trigger embeddings: the result states what was skipped.
Confidence describes retrieval *support*, not factual truth.
"""
from __future__ import annotations

from contextlib import closing
from collections import defaultdict
from pathlib import Path
from rag_pipeline import RagPipeline
from vector_memory import VectorMemory, digest, unpack_vector, similarity

KINDS = frozenset(("fact", "event", "preference", "biography"))


class RagFusion:
    def __init__(self, path: Path):
        self.pipeline = RagPipeline(path)
        self.vectors = VectorMemory(path)

    def search(self, query: str, limit: int = 12,
               *, model: str | None = None, use_cached_vectors: bool = False) -> dict:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
            raise ValueError("Query must be 1–500 characters.")
        if type(limit) is not int or not 1 <= limit <= 24:
            raise ValueError("Invalid result limit.")
        if type(use_cached_vectors) is not bool:
            raise ValueError("Invalid vector consent.")
        if model is not None and (not isinstance(model, str) or len(model) > 160):
            raise ValueError("Invalid embedding model.")
        base = self.pipeline.search(query, limit=24)
        seen = {}
        for rank, item in enumerate(base["items"], start=1):
            evidence = dict(item)
            evidence["channels"] = ["text", "graph"] if item.get("match") != "direct" else ["text"]
            evidence["rrf_score"] = 1 / (60 + rank)
            seen[item["id"]] = evidence

        cache_state = "disabled"
        vector_hits = []
        if use_cached_vectors and model:
            cache_state = "cache_miss"
            cached = self.vectors._cached(model, digest(query))
            if cached is not None:
                cache_state = "used"
                with closing(self.vectors.connect()) as db:
                    rows = db.execute(
                        "SELECT m.id,m.kind,m.content,m.source_type,m.source_ref,"
                        "m.verification_state,m.created_at,m.updated_at,c.vector,c.dimension,"
                        "e.text_sha256 FROM memory_embeddings e "
                        "JOIN memory_items m ON m.id=e.memory_id AND m.status='active' "
                        "JOIN embedding_cache c ON c.model=e.model "
                        "AND c.text_sha256=e.text_sha256 "
                        "WHERE e.model=? ORDER BY m.id DESC LIMIT 2500", (model,)
                    ).fetchall()
                    for row in rows:
                        if row["text_sha256"] != digest(row["content"]):
                            continue
                        try:
                            sim = similarity(cached, unpack_vector(row["vector"], row["dimension"]))
                        except ValueError:
                            continue
                        if sim > .20:
                            vector_hits.append((sim, dict(row)))
                vector_hits.sort(key=lambda x:(-x[0], -x[1]["id"]))
                for rank, (sim, row) in enumerate(vector_hits[:30], start=1):
                    item = seen.get(row["id"])
                    if item is None:
                        # Ground new vector-only hits with the same provenance
                        # checks as the lexical pipeline: source is not truth.
                        item = {field:row[field] for field in (
                            "id","kind","content","source_type","source_ref",
                            "verification_state","created_at","updated_at")}
                        item.update(score=round(sim,4),match="vector",
                                    possible_conflict=False,source_exists=None,
                                    potentially_stale=None,
                                    truth_verified=False,citation=f"[Память #{row['id']}]",
                                    source_state="requires_provenance_check",
                                    channels=[])
                        seen[item["id"]] = item
                    if "vector" not in item["channels"]:
                        item["channels"].append("vector")
                    item["rrf_score"] += 1 / (60 + rank)
        # A single channel cannot be mistaken for corroboration.
        items = sorted(seen.values(), key=lambda x:(-x["rrf_score"], -x["score"], -x["id"]))
        for item in items[:limit]:
            channels = item["channels"]
            item["retrieval_support"] = round(
                min(1.0, item["rrf_score"] * 30), 3)
            item["independent_sources_verified"] = 0
            item["factual_truth_verified"] = False
            item["needs_review"] = bool(
                item.get("possible_conflict") or
                item.get("source_exists") is False or
                item.get("source_exists") is None
            )
            if item["kind"] not in KINDS:
                item["needs_review"] = True
        return {
            "items": items[:limit], "mode":"rag4_local_evidence_fusion",
            "passes":base["passes"],"vector_cache":cache_state,
            "vector_candidates":len(vector_hits),
            "cloud_requests":0,"hidden_embedding_requests":0,
            "evidence_policy":"citations_are_provenance_not_truth",
        }
