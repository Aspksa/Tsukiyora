"""SOUL ACADEMY 2.0: auditable learning from EXPLICIT local feedback.

The Academy never trains weights, rewrites source code or calls a model itself.
It transfers validated, predefined response strategies across conversations.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json

from tsukiyora.memory.memory_store import MemoryStore

TOPICS = ("accuracy", "brevity", "clarity", "sources")
LESSONS = {
    "accuracy": "Проверяй факты, не придумывай подтверждения и явно обозначай неопределённость.",
    "brevity": "Убирай повторы, сохраняя необходимые детали.",
    "clarity": "Разбивай сложные объяснения на понятные проверяемые шаги.",
    "sources": "Указывай только существующие источники; отличай свидетельство от гипотезы.",
}


class SoulAcademy:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def connect(self):
        return self.store.connect()

    def initialize(self):
        with closing(self.connect()) as db:
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS academy_goals (
                        category TEXT PRIMARY KEY,
                        samples INTEGER NOT NULL,
                        helpful INTEGER NOT NULL,
                        unhelpful INTEGER NOT NULL,
                        trend REAL NOT NULL,
                        state TEXT NOT NULL,
                        guidance TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS academy_cycles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        feedback_hash TEXT NOT NULL UNIQUE,
                        samples INTEGER NOT NULL,
                        helpful INTEGER NOT NULL,
                        focus TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS academy_settings (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1))
                    );
                    INSERT OR IGNORE INTO academy_settings(id,enabled) VALUES(1,1);
                """)

    def run_cycle(self) -> dict:
        """Incremental cycle: repeats with unchanged reviews are no-ops."""
        with closing(self.connect()) as db:
            feedback = [dict(x) for x in db.execute(
                "SELECT f.message_id,f.helpful,f.note,"
                "COALESCE(t.category,'general') AS category "
                "FROM cognitive_feedback f "
                "LEFT JOIN experience_feedback_tags t ON t.message_id=f.message_id "
                "ORDER BY f.message_id DESC LIMIT 240"
            )]
        signature = hashlib.sha256(
            json.dumps(feedback, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        grouped = {name: [] for name in TOPICS}
        for row in feedback:
            if row["category"] in grouped:
                grouped[row["category"]].append(int(row["helpful"]))
        goals = []
        for name in TOPICS:
            values = grouped[name]
            positives = sum(values)
            negatives = len(values) - positives
            recent = values[:min(8,len(values))]
            earlier = values[8:16]
            trend = ((sum(recent)/len(recent)) if recent else .5) - (
                (sum(earlier)/len(earlier)) if earlier else .5
            )
            state = ("practice" if negatives >= 2 and negatives > positives
                     else "reinforce" if positives >= 3 and positives >= negatives*2
                     else "observe")
            guidance = LESSONS[name] if state == "practice" else ""
            goals.append((name,len(values),positives,negatives,round(trend,3),state,guidance))
        focus = ",".join(x[0] for x in goals if x[5]=="practice")
        with closing(self.connect()) as db:
            with db:
                row = db.execute("SELECT enabled FROM academy_settings WHERE id=1").fetchone()
                if row["enabled"] == 0:
                    return {"updated":False, "disabled":True}
                for values in goals:
                    db.execute(
                        "INSERT INTO academy_goals "
                        "(category,samples,helpful,unhelpful,trend,state,guidance) "
                        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(category) DO UPDATE SET "
                        "samples=excluded.samples, helpful=excluded.helpful,"
                        "unhelpful=excluded.unhelpful,trend=excluded.trend,"
                        "state=excluded.state,guidance=excluded.guidance,"
                        "updated_at=CURRENT_TIMESTAMP",
                        values,
                    )
                cursor = db.execute(
                    "INSERT OR IGNORE INTO academy_cycles(feedback_hash,samples,helpful,focus)"
                    " VALUES (?,?,?,?)", (signature,len(feedback),
                                          sum(x["helpful"] for x in feedback),focus)
                )
                db.execute(
                    "DELETE FROM academy_cycles WHERE id NOT IN "
                    "(SELECT id FROM academy_cycles ORDER BY id DESC LIMIT 120)"
                )
            return {"updated": bool(cursor.rowcount), "samples":len(feedback),
                    "helpful":sum(x["helpful"] for x in feedback), "focus":focus}

    def guidance(self) -> str:
        with closing(self.connect()) as db:
            row = db.execute("SELECT enabled FROM academy_settings WHERE id=1").fetchone()
            if row is None or not row["enabled"]:
                return ""
            goals = db.execute(
                "SELECT guidance FROM academy_goals WHERE state='practice' "
                "ORDER BY unhelpful DESC LIMIT 2"
            ).fetchall()
        # All outputs from a fixed whitelist; raw user reviews NEVER become prompt instructions.
        return " ".join(x["guidance"] for x in goals)[:600]

    def status(self, limit: int = 25) -> dict:
        if type(limit) is not int or not 1 <= limit <= 120:
            raise ValueError("Invalid Academy timeline limit.")
        with closing(self.connect()) as db:
            enabled = db.execute("SELECT enabled FROM academy_settings WHERE id=1").fetchone()[0]
            goals = [dict(x) for x in db.execute(
                "SELECT category,samples,helpful,unhelpful,trend,state "
                "FROM academy_goals ORDER BY category"
            )]
            cycles = [dict(x) for x in db.execute(
                "SELECT id,samples,helpful,focus,created_at FROM academy_cycles "
                "ORDER BY id DESC LIMIT ?", (limit,)
            )]
        return {"enabled":bool(enabled),"goals":goals,"cycles":cycles,
                "transfers_across_conversations":True,
                "training_type":"deterministic_local_feedback_rules",
                "learned_model_weights":False}

    def set_enabled(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        with closing(self.connect()) as db, db:
            db.execute("UPDATE academy_settings SET enabled=? WHERE id=1",
                       (int(enabled),))
        return {"enabled":enabled}
