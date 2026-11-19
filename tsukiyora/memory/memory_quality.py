"""Local quality analysis of approved memories. Never deletes or alters facts.

Similarity is heuristic, so duplicates and contradictions remain *signals* that
the user can inspect, never automatic truth judgments.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from difflib import SequenceMatcher
import re
import unicodedata

from tsukiyora.memory.memory_store import MemoryStore
from tsukiyora.memory.vector_memory import digest, unpack_vector, similarity

NEGATION = re.compile(r"\b(?:не|нет|никогда|перестал(?:а)?|больше\s+не|ненавижу)\b", re.I)
STOP = frozenset(
    "я ты мы вы она они он это эти и но а в во на по для о об из за к ко до от у с со мне меня мой моя моё мои его её тот этой этого это".split()
)


def normalized(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("Only text can be compared.")
    raw = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return " ".join(re.findall(r"[a-zа-я0-9]+", raw))


def terms(text: str) -> set[str]:
    return {word for word in normalized(text).split() if len(word) >= 3 and word not in STOP}


def compare(a: str, b: str) -> dict:
    """A bounded, explainable similarity score, no external embeddings."""
    left, right = normalized(a), normalized(b)
    if not left or not right:
        return {"similarity": 0.0, "duplicate": False, "conflict": False}
    ta, tb = terms(a), terms(b)
    overlap = len(ta & tb) / max(1, len(ta | tb))
    coverage = len(ta & tb) / max(1, min(len(ta), len(tb)))
    ratio = SequenceMatcher(None, left[:320], right[:320]).ratio()
    # A negation by itself must not be treated as a duplicate!
    neg_left, neg_right = bool(NEGATION.search(left)), bool(NEGATION.search(right))
    opposing = neg_left != neg_right
    # Compare polarity-stripped text only to flag genuine likely opposites.
    bare_left = " ".join(NEGATION.sub("", left).split())
    bare_right = " ".join(NEGATION.sub("", right).split())
    bare_ratio = SequenceMatcher(None, bare_left[:320], bare_right[:320]).ratio()
    semantic_overlap = max(overlap, (coverage * 0.65 + ratio * 0.35))
    duplicate = (not opposing and (left == right or
                 (ratio >= 0.90 and coverage >= 0.65 and len(ta & tb) >= 2)))
    conflict = (opposing and coverage >= 0.62 and bare_ratio >= 0.78
                and len(ta & tb) >= 2)
    return {"similarity": round(semantic_overlap, 3),
            "duplicate": duplicate, "conflict": conflict}


class MemoryQuality:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def review(self, limit: int = 220) -> dict:
        """Inspect latest approved memories, keeping sources for human review."""
        if type(limit) is not int or not 2 <= limit <= 500:
            raise ValueError("Invalid review limit.")
        with closing(self.store.connect()) as db:
            items = [dict(x) for x in db.execute(
                "SELECT id,kind,content,source_type,source_ref,created_at "
                "FROM memory_items WHERE status='active' "
                "ORDER BY id DESC LIMIT ?", (limit,)
            )]
        inverted = defaultdict(list)
        pairs = set()
        for i, item in enumerate(items):
            for token in terms(item["content"]):
                for j in inverted[token][-50:]:
                    if item["kind"] == items[j]["kind"]:
                        pairs.add(tuple(sorted((i, j))))
                inverted[token].append(i)
        duplicates, conflicts = [], []
        for i, j in sorted(pairs):
            left, right = items[i], items[j]
            result = compare(left["content"], right["content"])
            if result["duplicate"] or result["conflict"]:
                entry = {"left": {k: left[k] for k in ("id", "kind", "content", "source_type", "source_ref")},
                         "right": {k: right[k] for k in ("id", "kind", "content", "source_type", "source_ref")},
                         "score": result["similarity"],
                         "evidence": "local heuristic; needs human review"}
                if result["duplicate"]:
                    duplicates.append(entry)
                elif result["conflict"]:
                    conflicts.append(entry)
        # Semantic duplicate signals use ONLY previously cached vectors.
        # Never request cloud embeddings while doing a quality check.
        semantic = []
        seen = {tuple(sorted((x["left"]["id"], x["right"]["id"]))) for x in duplicates}
        with closing(self.store.connect()) as db:
            rows = db.execute(
                "SELECT e.memory_id,e.model,e.text_sha256,c.dimension,c.vector,"
                "m.kind,m.content,m.source_type,m.source_ref "
                "FROM memory_embeddings e "
                "JOIN memory_items m ON m.id=e.memory_id "
                "JOIN embedding_cache c ON c.model=e.model AND c.text_sha256=e.text_sha256 "
                "WHERE m.status='active' ORDER BY e.memory_id DESC LIMIT 90"
            ).fetchall()
        verified = []
        for row in rows:
            if row["text_sha256"] != digest(row["content"]):
                continue
            try:
                vec = unpack_vector(row["vector"], row["dimension"])
            except (ValueError, TypeError):
                continue
            verified.append((row, vec))
        for i, (left, vec1) in enumerate(verified):
            for right, vec2 in verified[i + 1:]:
                if left["memory_id"] == right["memory_id"] or (
                        left["model"] != right["model"] or left["kind"] != right["kind"]):
                    continue
                pair = tuple(sorted((left["memory_id"], right["memory_id"])))
                if pair in seen:
                    continue
                # Polarity opposites are NOT duplicate claims.
                if bool(NEGATION.search(normalized(left["content"]))) != bool(
                        NEGATION.search(normalized(right["content"]))):
                    continue
                cosine = similarity(vec1, vec2)
                if cosine >= .96:
                    seen.add(pair)
                    semantic.append({
                        "left": {k: left[k] for k in ("kind", "content", "source_type", "source_ref")}
                                | {"id": left["memory_id"]},
                        "right": {k: right[k] for k in ("kind", "content", "source_type", "source_ref")}
                                 | {"id": right["memory_id"]},
                        "score": round(cosine, 3),
                        "evidence": "cached vector similarity; needs human review",
                    })
                if len(semantic) >= 35:
                    break
            if len(semantic) >= 35:
                break
        duplicates.sort(key=lambda x: x["score"], reverse=True)
        conflicts.sort(key=lambda x: x["score"], reverse=True)
        semantic.sort(key=lambda x: x["score"], reverse=True)
        return {"duplicates": duplicates[:40], "semantic_duplicates": semantic[:35],
                "conflicts": conflicts[:40], "scanned": len(items),
                "automatic_deletions": 0, "outbound_cloud_requests": 0}
