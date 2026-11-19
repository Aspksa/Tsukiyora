"""Hindsight-inspired source-grounded retrospective recall.

Original local implementation: does not bundle or connect the Hindsight server.
No provider calls, no inferred motives presented as historical facts.
"""
from __future__ import annotations

from contextlib import closing
from tsukiyora.memory.memory_store import MemoryStore
from tsukiyora.documents.rag_pipeline import RagPipeline


class ReflectiveRecall:
    def __init__(self, path):
        self.store = MemoryStore(path)
        self.rag = RagPipeline(path)

    def inspect(self, query: str, limit: int = 8) -> dict:
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError("Количество воспоминаний должно быть от 1 до 12.")
        # Same consent and source policies as the existing local RAG.
        found = self.rag.search(query, limit=limit)
        entries = [dict(item) for item in found["items"]]
        if not entries:
            return {"items": [], "connections": [], "chronology": [],
                    "uncertainties": ["Подтверждённых записей по запросу не найдено."],
                    "cloud_requests": 0, "truth_verified": False,
                    "mode": "source_grounded_reflection"}
        ids = [row["id"] for row in entries]
        marks = ",".join("?" for _ in ids)
        with closing(self.store.connect()) as db:
            revisions = [dict(row) for row in db.execute(
                "SELECT memory_id,operation,before_content,after_content,reason,created_at "
                "FROM memory_revisions WHERE memory_id IN (" + marks + ") "
                "ORDER BY created_at,id LIMIT 120", ids
            )]
            edges = [dict(row) for row in db.execute(
                "SELECT source_id,target_id,relation FROM memory_links "
                "WHERE source_id IN (" + marks + ") "
                "AND target_id IN (" + marks + ") LIMIT 120", ids * 2
            )]
        by_id = {mid: [] for mid in ids}
        chronology = []
        for row in revisions:
            by_id[row["memory_id"]].append(row)
            chronology.append({
                "memory_id": row["memory_id"], "operation": row["operation"],
                "created_at": row["created_at"], "reason": row["reason"]
            })
        uncertainties = []
        for item in entries:
            item["revisions"] = by_id[item["id"]][:25]
            item["truth_verified"] = False
            if item.get("possible_conflict"):
                uncertainties.append("Воспоминание #" + str(item["id"]) + ": возможное противоречие.")
            if item.get("potentially_stale"):
                uncertainties.append("Воспоминание #" + str(item["id"]) + ": возможное устаревание.")
            if not item.get("source_exists"):
                uncertainties.append("Воспоминание #" + str(item["id"]) + ": источник не найден.")
        chronology.sort(key=lambda x: (x["created_at"], x["memory_id"]))
        return {
            "items": entries, "connections": edges,
            "chronology": chronology[:120], "uncertainties": uncertainties[:24],
            "cloud_requests": 0, "truth_verified": False,
            "no_causal_claims_without_evidence": True,
            "mode": "source_grounded_reflection",
        }
