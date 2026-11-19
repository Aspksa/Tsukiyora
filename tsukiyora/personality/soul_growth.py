"""User-approved memory growth, sourced links and constrained local RAG.

SQLite only. A proposed fact never becomes trusted memory without confirmation.
No simulated consciousness, unchecked self-modifying code, or background daemons.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from tsukiyora.memory.memory_store import MemoryStore

STOP = frozenset("я ты он она оно мы вы они и в на для что это как когда то но или есть не уже мне моя мой мои его её сам свой о по от из за а ну".split())
FACT_TRIGGERS = [
    (re.compile(r"\b(?:я люблю|мне нравится|предпочитаю|мне удобнее)\b", re.I), "preference"),
    (re.compile(r"\b(?:я работаю|моя должность|меня зовут|у меня есть|я живу|я учусь)\b", re.I), "biography"),
    (re.compile(r"\b(?:запомни|важно помнить|запиши себе)\b", re.I), "fact"),
    (re.compile(r"\b(?:сегодня я|вчера я|мы решили|я сделал|я закончил)\b", re.I), "event"),
]
NEG = re.compile(r"\b(?:не|нет|никогда|перестал|больше не)\b", re.I)


def tokens(value: str) -> list[str]:
    return [x for x in re.findall(r"[a-zа-яё0-9]{3,}", value.lower()) if x not in STOP]


def score(query: str, text: str) -> float:
    """Lightweight local hybrid: word overlap + Unicode fuzzy similarity."""
    a, b = set(tokens(query)), set(tokens(text))
    overlap = len(a & b) / max(1, len(a))
    fuzzy = SequenceMatcher(None, query.casefold()[:160], text.casefold()[:320]).ratio()
    return 0.8 * overlap + 0.2 * fuzzy


class SoulGrowth:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.memory = MemoryStore(db_path)

    def connect(self):
        return self.memory.connect()

    def initialize(self):
        with closing(self.connect()) as db:
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS memory_suggestions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_id INTEGER NOT NULL REFERENCES messages(id),
                        kind TEXT NOT NULL,
                        content TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        state TEXT NOT NULL DEFAULT 'pending'
                            CHECK (state IN ('pending','approved','rejected')),
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(message_id,fingerprint)
                    );
                    CREATE TABLE IF NOT EXISTS memory_links (
                        source_id INTEGER NOT NULL REFERENCES memory_items(id),
                        target_id INTEGER NOT NULL REFERENCES memory_items(id),
                        relation TEXT NOT NULL CHECK (relation IN ('related','possible_conflict')),
                        source_note TEXT NOT NULL DEFAULT 'local_similarity',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(source_id,target_id,relation),
                        CHECK(source_id <> target_id)
                    );
                    CREATE TABLE IF NOT EXISTS soul_journal (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event TEXT NOT NULL,
                        detail TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        source_ref TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(event,source_type,source_ref)
                    );
                """)

    def propose(self, message_id: int, content: str) -> list[dict]:
        """Conservative candidate extraction; no unapproved persistence to memory."""
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("Invalid source message.")
        if not isinstance(content, str):
            raise ValueError("Source content must be text.")
        candidates = []
        for phrase in re.split(r"[.!?\n]+", content[:8000]):
            phrase = phrase.strip()[:380]
            if len(phrase) < 10 or len(phrase) > 380:
                continue
            for pattern, kind in FACT_TRIGGERS:
                if pattern.search(phrase):
                    candidates.append((kind, phrase))
                    break
            if len(candidates) >= 4:
                break
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT role FROM messages WHERE id=?", (message_id,)).fetchone()
            if row is None or row["role"] != "user":
                raise ValueError("Suggestions need a saved user message.")
            for kind, phrase in candidates:
                fp = hashlib.sha256((kind + ":" + " ".join(phrase.lower().split())).encode()).hexdigest()
                db.execute(
                    "INSERT OR IGNORE INTO memory_suggestions(message_id,kind,content,fingerprint)"
                    " VALUES (?,?,?,?)", (message_id, kind, phrase, fp),
                )
            return [dict(x) for x in db.execute(
                "SELECT * FROM memory_suggestions WHERE message_id=? ORDER BY id",
                (message_id,),
            )]

    def suggestions(self, state: str = "pending", limit: int = 30) -> list[dict]:
        if state not in ("pending", "approved", "rejected"):
            raise ValueError("Invalid suggestion state.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid limit.")
        with closing(self.connect()) as db:
            return [dict(x) for x in db.execute(
                "SELECT * FROM memory_suggestions WHERE state=? ORDER BY id DESC LIMIT ?",
                (state, limit),
            )]

    def review(self, suggestion_id: int, accept: bool) -> dict:
        if type(suggestion_id) is not int or suggestion_id <= 0 or type(accept) is not bool:
            raise ValueError("Invalid review.")
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM memory_suggestions WHERE id=?", (suggestion_id,)).fetchone()
            if row is None or row["state"] != "pending":
                raise ValueError("Suggestion does not exist or was already reviewed.")
            row = dict(row)
            if accept:
                item = self.memory.add_memory(
                    row["kind"], row["content"], source_type="message",
                    source_ref=str(row["message_id"]), verification_state="user_reported",
                )
                # Link creation is deterministic and labelled as a potential signal, not a fact.
                self.build_links(item["id"])
                self.journal("memory_approved", f"Подтверждена запись памяти №{item['id']}",
                             "memory", str(item["id"]))
                if row["kind"] == "preference" and re.search(
                        r"\b(?:общайся|говори|пиши|отвечай)\b", row["content"], re.I):
                    self.journal("style_preference", "Обновлены подтверждённые предпочтения стиля",
                                 "memory", str(item["id"]))
            with db:
                db.execute("UPDATE memory_suggestions SET state=? WHERE id=? AND state='pending'",
                           ("approved" if accept else "rejected", suggestion_id))
            return {"id": suggestion_id, "state": "approved" if accept else "rejected",
                    "memory_id": item["id"] if accept else None}

    def build_links(self, memory_id: int) -> list[dict]:
        if type(memory_id) is not int or memory_id <= 0:
            raise ValueError("Invalid memory ID.")
        with closing(self.connect()) as db:
            source = db.execute(
                "SELECT id,kind,content FROM memory_items WHERE id=? AND status='active'",
                (memory_id,),
            ).fetchone()
            if source is None:
                return []
            candidates = db.execute(
                "SELECT id,kind,content FROM memory_items WHERE status='active' AND id<>? ORDER BY id DESC LIMIT 1500",
                (memory_id,),
            ).fetchall()
            links = []
            for candidate in candidates:
                overlap = score(source["content"], candidate["content"])
                if overlap < 0.40:
                    continue
                conflict = (overlap >= 0.58 and source["kind"] == candidate["kind"]
                            and bool(NEG.search(source["content"])) != bool(NEG.search(candidate["content"])))
                relation = "possible_conflict" if conflict else "related"
                links.append((candidate["id"], relation, round(overlap, 3)))
                if len(links) >= 20:
                    break
            with db:
                # Corrections invalidate previous links; rebuild from current text.
                db.execute("DELETE FROM memory_links WHERE source_id=? OR target_id=?",
                           (memory_id, memory_id))
                for target_id, relation, overlap in links:
                    x, y = sorted((memory_id, target_id))
                    db.execute(
                        "INSERT OR IGNORE INTO memory_links(source_id,target_id,relation,source_note)"
                        " VALUES (?,?,?,?)", (x, y, relation, f"local_similarity:{overlap}"),
                    )
            return [{"target_id": item[0], "relation": item[1], "similarity": item[2]}
                    for item in links]

    def links(self, memory_id: int | None = None, limit: int = 100) -> list[dict]:
        if memory_id is not None and (type(memory_id) is not int or memory_id <= 0):
            raise ValueError("Invalid memory ID.")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Invalid limit.")
        with closing(self.connect()) as db:
            return [dict(x) for x in db.execute(
                "SELECT l.* FROM memory_links l JOIN memory_items a ON a.id=l.source_id"
                " JOIN memory_items b ON b.id=l.target_id"
                " WHERE a.status='active' AND b.status='active'"
                " AND (? IS NULL OR l.source_id=? OR l.target_id=?)"
                " ORDER BY l.created_at DESC LIMIT ?",
                (memory_id, memory_id, memory_id, limit),
            )]

    def retrieve(self, query: str, limit: int = 8, pool: int = 300) -> list[dict]:
        if not isinstance(query, str) or len(query) > 8000:
            raise ValueError("Invalid query.")
        if type(limit) is not int or not 1 <= limit <= 30:
            raise ValueError("Invalid limit.")
        with closing(self.connect()) as db:
            records = [dict(x) for x in db.execute(
                "SELECT id,kind,content,source_type,source_ref,verification_state,created_at"
                " FROM memory_items WHERE status='active' ORDER BY id DESC LIMIT ?", (pool,),
            )]
        ranked = [(score(query, x["content"]), x) for x in records]
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [dict(item, score=round(value, 3)) for value, item in ranked
                if value >= 0.14][:limit]

    def journal(self, event: str, detail: str, source_type: str, source_ref: str):
        if event not in ("memory_approved", "conversation_completed", "style_preference"):
            raise ValueError("Unknown journal event.")
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO soul_journal(event,detail,source_type,source_ref)"
                " VALUES (?,?,?,?)",
                (event, detail[:250], source_type, source_ref),
            )

    def journal_entries(self, limit: int = 40) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid limit.")
        with closing(self.connect()) as db:
            return [dict(x) for x in db.execute(
                "SELECT * FROM soul_journal ORDER BY id DESC LIMIT ?", (limit,),
            )]

    def style(self) -> str:
        """Only explicit, approved style preferences may shape the character."""
        with closing(self.connect()) as db:
            items = db.execute(
                "SELECT content FROM memory_items WHERE kind='preference' AND status='active'"
                " ORDER BY id DESC LIMIT 100"
            ).fetchall()
        choices = []
        for item in items:
            text = item["content"]
            if re.search(r"\b(?:общайся|говори|отвечай|пиши)\b", text.lower()):
                choices.append(text[:160])
        return "; ".join(choices[:5])[:600]
