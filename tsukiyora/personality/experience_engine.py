"""Experience-based, bounded, reversible persona tuning from explicit feedback.

The "emotional state" is a labelled interface tone simulation, not actual
feelings or conclusions about the user's emotional/medical condition.
"""
from __future__ import annotations

from contextlib import closing
import json
import re

from tsukiyora.memory.memory_store import MemoryStore


REASONS = {
    "accuracy": ("ошибк", "неверн", "неточн", "галлюцин", "выдум", "wrong", "incorrect"),
    "brevity": ("длинн", "короч", "кратк", "многослов", "long", "verbose"),
    "clarity": ("непонят", "сложн", "запута", "ясн", "confus", "unclear"),
    "sources": ("источник", "ссылк", "доказател", "цитат", "citation", "source"),
}
LESSONS = {
    "accuracy": "Проверяй фактические утверждения, помечай неопределённость и не придумывай источники.",
    "brevity": "Будь лаконичнее и избавляйся от повторов без потери необходимой информации.",
    "clarity": "Объясняй поэтапно, простой речью, без лишнего жаргона.",
    "sources": "Различай подтверждённые источники, записи пользователя и гипотезы.",
}
GOALS = ("accuracy", "brevity", "clarity", "sources")
BASE_TONE = {"warmth": 0.62, "focus": 0.74, "curiosity": 0.56}


class ExperienceEngine:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def connect(self):
        return self.store.connect()

    def initialize(self):
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS experience_feedback_tags (
                    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                    category TEXT NOT NULL CHECK(category IN
                      ('accuracy','brevity','clarity','sources','general')),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS experience_goals (
                    category TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    positive_count INTEGER NOT NULL DEFAULT 0,
                    negative_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'observe',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS experience_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    @staticmethod
    def classify(note: str) -> str:
        lower = note.casefold()
        for category, markers in REASONS.items():
            if any(marker in lower for marker in markers):
                return category
        return "general"

    def tag_feedback(self, message_id: int, note: str) -> str:
        category = self.classify(note)
        with closing(self.connect()) as db, db:
            db.execute("""
                INSERT INTO experience_feedback_tags(message_id,category)
                VALUES(?,?) ON CONFLICT(message_id) DO UPDATE SET
                category=excluded.category,updated_at=CURRENT_TIMESTAMP
            """, (message_id, category))
        self.consolidate()
        return category

    def consolidate(self) -> dict:
        """Produce safe fixed guidance; no LLM calls or raw feedback as instructions."""
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT t.category,f.helpful FROM experience_feedback_tags t "
                "JOIN cognitive_feedback f ON f.message_id=t.message_id "
                "ORDER BY f.updated_at DESC,t.message_id DESC LIMIT 200"
            ).fetchall()
        grouped = {x: [0, 0] for x in GOALS}
        for row in rows:
            cat = row["category"]
            if cat in grouped:
                grouped[cat][0 if row["helpful"] else 1] += 1
        with closing(self.connect()) as db, db:
            for category in GOALS:
                positive, negative = grouped[category]
                total = positive + negative
                status = "practice" if negative >= 2 and negative > positive else (
                    "improving" if positive >= 3 else "observe"
                )
                db.execute("""
                    INSERT INTO experience_goals(category,attempts,positive_count,negative_count,status)
                    VALUES (?,?,?,?,?) ON CONFLICT(category) DO UPDATE SET
                    attempts=excluded.attempts,positive_count=excluded.positive_count,
                    negative_count=excluded.negative_count,status=excluded.status,
                    updated_at=CURRENT_TIMESTAMP
                """, (category, total, positive, negative, status))
        return {"categories": len(grouped), "feedback": len(rows)}

    def guidance(self) -> str:
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT category FROM experience_goals WHERE status='practice' "
                "ORDER BY negative_count DESC, category LIMIT 2"
            ).fetchall()
        return " ".join(LESSONS[row["category"]] for row in rows)

    def state(self) -> dict:
        """Tone changes are slow and caused by explicit ratings, not inferred emotions."""
        with closing(self.connect()) as db:
            stats = db.execute(
                "SELECT COUNT(*) AS total,COALESCE(SUM(helpful),0) AS positive "
                "FROM cognitive_feedback"
            ).fetchone()
            goals = [dict(row) for row in db.execute(
                "SELECT category,attempts,positive_count,negative_count,status "
                "FROM experience_goals ORDER BY category"
            )]
            sessions = db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        total, likes = stats["total"], stats["positive"]
        confidence = (likes + 2) / (total + 4)
        return {
            "label": "симуляция манеры общения, не настоящие чувства",
            "warmth": round(BASE_TONE["warmth"], 2),
            "focus": round(min(0.90, BASE_TONE["focus"] + (1 - confidence) * 0.12), 2),
            "curiosity": round(BASE_TONE["curiosity"], 2),
            "confidence_in_answers": round(confidence, 3),
            "feedback_samples": total,
            "sessions": sessions,
            "goals": goals,
        }

    def snapshot(self, pending: int, conflicts: int) -> dict:
        state = self.state()
        summary = {
            "sessions": state["sessions"], "feedback": state["feedback_samples"],
            "pending": int(pending), "possible_conflicts": int(conflicts),
            "practice_goals": sum(x["status"] == "practice" for x in state["goals"]),
        }
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO experience_snapshots(summary) VALUES(?)",
                (json.dumps(summary, ensure_ascii=False),)
            )
            db.execute(
                "DELETE FROM experience_snapshots WHERE id NOT IN "
                "(SELECT id FROM experience_snapshots ORDER BY id DESC LIMIT 90)"
            )
        return summary

    def timeline(self, limit: int = 30) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid timeline limit.")
        with closing(self.connect()) as db:
            return [dict(row) for row in db.execute(
                "SELECT id,summary,created_at FROM experience_snapshots "
                "ORDER BY id DESC LIMIT ?", (limit,)
            )]
