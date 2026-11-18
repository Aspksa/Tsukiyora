"""Source-grounded, bounded multi-pass RAG over approved SQLite memories.

No background cloud traffic, no fabricated citations and no unverified facts.
Semantic embeddings remain an explicitly configured/consented separate feature.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone, timedelta
import re

from memory_store import MemoryStore
from precision_memory import PrecisionMemory
from memory_quality import terms

MAX_VISITS = 120
MAX_DAYS = 365


class RagPipeline:
    def __init__(self, path):
        self.store = MemoryStore(path)
        self.precision = PrecisionMemory(path)

    @staticmethod
    def passes(query: str) -> list[str]:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
            raise ValueError("Query must be 1–500 characters.")
        queries = [query.strip()]
        words = sorted(terms(query), key=len, reverse=True)[:8]
        if len(words) >= 2:
            compact = " ".join(words[:5])
            if compact.casefold() != query.strip().casefold():
                queries.append(compact)
        for chunk in re.split(r"[?!.;]+", query):
            chunk = chunk.strip()
            if len(chunk) >= 6 and chunk not in queries:
                queries.append(chunk[:200])
            if len(queries) >= 3:
                break
        return queries[:3]

    def search(self, query: str, limit: int = 12, max_age_days: int = MAX_DAYS) -> dict:
        if type(limit) is not int or not 1 <= limit <= 24:
            raise ValueError("Invalid result count.")
        if type(max_age_days) is not int or not 1 <= max_age_days <= 3650:
            raise ValueError("Invalid freshness window.")
        queries = self.passes(query)
        results = {}
        signals = []
        for attempt, variant in enumerate(queries):
            stage = self.precision.search(variant, limit=24, include_links=True)
            signals.append({"pass": attempt + 1, "candidates": len(stage["items"]),
                            "local_only": True})
            for result in stage["items"]:
                item = results.get(result["id"])
                weighted = max(0., min(1., float(result["score"])*(1.-attempt*.12)))
                if item is None or weighted > item["score"]:
                    results[result["id"]] = dict(result, score=round(weighted, 4),
                                                   query_pass=attempt+1)
        seed = sorted(results.values(), key=lambda x:x["score"], reverse=True)[:16]
        with closing(self.store.connect()) as db:
            frontier = {x["id"] for x in seed}
            visited = set(frontier)
            # An additional bounded hop beyond the one already in PrecisionMemory.
            for _depth in range(2):
                if not frontier or len(visited) >= MAX_VISITS:
                    break
                markers = ",".join("?" for _ in frontier)
                edges = db.execute(
                    "SELECT source_id,target_id,relation FROM memory_links "
                    "WHERE source_id IN ("+markers+") OR target_id IN ("+markers+") "
                    "LIMIT 120", tuple(frontier)*2
                ).fetchall()
                next_frontier = set()
                for edge in edges:
                    left, right = edge["source_id"], edge["target_id"]
                    for near, far in ((left,right),(right,left)):
                        if near not in frontier or far in visited:
                            continue
                        if len(visited) >= MAX_VISITS:
                            break
                        source = db.execute(
                            "SELECT id,kind,content,source_type,source_ref,"
                            "verification_state,created_at,updated_at "
                            "FROM memory_items WHERE id=? AND status='active'",
                            (far,)
                        ).fetchone()
                        visited.add(far)
                        if source is None:
                            continue
                        parent = results.get(near)
                        if parent is None:
                            continue
                        candidate = dict(source)
                        candidate.update(
                            score=round(parent["score"]*.38,4),
                            match="graph_hop", related_to=near,
                            possible_conflict=edge["relation"]=="possible_conflict",
                            query_pass=parent["query_pass"],
                            priority=2,revision_id=0,
                            source_verified=False
                        )
                        results[far] = candidate
                        next_frontier.add(far)
                    if edge["relation"] == "possible_conflict":
                        for node_id in (left,right):
                            if node_id in results:
                                results[node_id]["possible_conflict"]=True
                frontier = next_frontier
            # Verify provenance references, not truth. Only fetch metadata.
            items = sorted(results.values(),
                           key=lambda x:(-x["score"], x["id"]))[:limit]
            cutoff = datetime.now(timezone.utc)-timedelta(days=max_age_days)
            for item in items:
                source_type = item.get("source_type")
                origin = item.get("source_ref")
                source_found = source_type == "manual"
                if source_type == "message" and str(origin or "").isdigit():
                    source_found = db.execute(
                        "SELECT 1 FROM messages WHERE id=?", (int(origin),)
                    ).fetchone() is not None
                item["source_exists"] = bool(source_found)
                item["citation"] = "[Память #"+str(item["id"])+"]"
                item["truth_verified"] = False
                item["source_state"] = (
                    "manual_user_entry" if source_type=="manual" else
                    "message_linked" if source_found else "source_unresolved"
                )
                updated = item.get("updated_at") or item.get("created_at")
                try:
                    seen = datetime.fromisoformat(updated.replace("Z","+00:00"))
                    if seen.tzinfo is None:
                        seen = seen.replace(tzinfo=timezone.utc)
                    item["potentially_stale"] = seen < cutoff
                except (AttributeError, TypeError, ValueError):
                    item["potentially_stale"] = True
        return {
            "items":items,"passes":signals,"mode":"local_multi_pass_graph",
            "cloud_requests":0,
            "sources_are_provenance_not_fact_verification":True,
            "graph_depth_limit":2,
        }
