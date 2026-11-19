"""Continuity of Tsukiyora's approved character and user relationship.

A programmatic state, not emotions or a person. No rewriting persona JSON.
Only explicitly approved autobiographical facts and speech preferences count.
"""
from __future__ import annotations
from contextlib import closing
import json
import re

from tsukiyora.memory.memory_store import MemoryStore

HABIT_WORDS = re.compile(r"\b(?:говори|отвечай|общайся|пиши|обращайся|пожалуйста\s+пиши)\b", re.I)


class PersonaContinuity:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def connect(self):
        return self.store.connect()

    def initialize(self):
        with closing(self.connect()) as db:
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS persona_continuity (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        approved_biography INTEGER NOT NULL DEFAULT 0,
                        approved_habits INTEGER NOT NULL DEFAULT 0,
                        conversations INTEGER NOT NULL DEFAULT 0,
                        accepted_events INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT OR IGNORE INTO persona_continuity(id) VALUES(1);
                    CREATE TABLE IF NOT EXISTS persona_epochs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        snapshot TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)

    def state(self) -> dict:
        with closing(self.connect()) as db:
            biography = [dict(x) for x in db.execute(
                "SELECT id,kind,content,source_type,source_ref,created_at "
                "FROM memory_items WHERE status='active' "
                "AND kind IN ('biography','preference') ORDER BY id DESC LIMIT 100"
            )]
            habits = [x for x in biography if x["kind"]=="preference" and
                      HABIT_WORDS.search(x["content"])][:8]
            talks = db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            events = db.execute(
                "SELECT COUNT(*) FROM memory_items WHERE kind='event' AND status='active'"
            ).fetchone()[0]
            history = [dict(x) for x in db.execute(
                "SELECT event,source_type,source_ref,created_at FROM soul_journal "
                "ORDER BY id DESC LIMIT 30"
            )]
        return {
            "identity":"Tsukiyora",
            "source":"soul/personality.json + confirmed SQLite memories",
            "approved_biography":biography,
            "stable_habits":habits,
            "relationship_history":history,
            "conversations":talks,
            "accepted_events":events,
            "state_type":"simulated_character_continuity",
            "feelings_claimed":False,
            "protected_character_mutable":False,
        }

    def consolidate(self) -> dict:
        """Store only counts; private biographies remain in their source table."""
        state = self.state()
        summary = {
            "conversations":state["conversations"],
            "approved_biography":sum(x["kind"]=="biography"
                                     for x in state["approved_biography"]),
            "stable_habits":len(state["stable_habits"]),
            "approved_events":state["accepted_events"],
        }
        with closing(self.connect()) as db:
            with db:
                db.execute(
                    "UPDATE persona_continuity SET approved_biography=?,"
                    "approved_habits=?,conversations=?,accepted_events=?,"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=1",
                    (summary["approved_biography"],summary["stable_habits"],
                     summary["conversations"],summary["approved_events"])
                )
                prior = db.execute(
                    "SELECT snapshot FROM persona_epochs ORDER BY id DESC LIMIT 1"
                ).fetchone()
                payload = json.dumps(summary,sort_keys=True)
                if prior is None or prior["snapshot"] != payload:
                    db.execute("INSERT INTO persona_epochs(snapshot) VALUES(?)",
                               (payload,))
                db.execute(
                    "DELETE FROM persona_epochs WHERE id NOT IN "
                    "(SELECT id FROM persona_epochs ORDER BY id DESC LIMIT 100)"
                )
        return summary

    def timeline(self, limit: int = 60) -> dict:
        """Dated events from confirmed memory and conversation history only."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid biography timeline limit.")
        with closing(self.connect()) as db:
            notes=[dict(x) for x in db.execute(
                "SELECT m.id,m.kind,m.content,m.source_type,m.source_ref,"
                "m.created_at,m.updated_at,"
                "(SELECT COUNT(*) FROM memory_revisions r WHERE r.memory_id=m.id) AS revisions "
                "FROM memory_items m WHERE m.status='active' "
                "AND m.kind IN ('biography','preference','event') "
                "ORDER BY m.created_at DESC,m.id DESC LIMIT ?",(limit,)
            )]
            encounters=[dict(x) for x in db.execute(
                "SELECT id,event,source_type,source_ref,created_at "
                "FROM soul_journal WHERE event IN ('conversation_completed','memory_approved') "
                "ORDER BY created_at DESC,id DESC LIMIT ?",(limit,)
            )]
        timeline=[
            {"event":"approved_"+x["kind"],"memory_id":x["id"],
             "timestamp":x["created_at"],"detail":x["content"],
             "source_type":x["source_type"],"source_ref":x["source_ref"],
             "revisions":x["revisions"],"truth_verified":False}
            for x in notes
        ]+[
            {"event":x["event"],"memory_id":None,
             "timestamp":x["created_at"],"detail":"Диалог или запись сохранены",
             "source_type":x["source_type"],"source_ref":x["source_ref"],
             "revisions":0,"truth_verified":False}
            for x in encounters
        ]
        timeline.sort(key=lambda x:x["timestamp"],reverse=True)
        return {"items":timeline[:limit],"source":"approved_local_SQLite",
                "real_feelings_claimed":False,
                "invented_relationship_events":False}

    def context(self) -> str:
        """Bounded, clearly labelled historical DATA for model context."""
        state = self.state()
        habits = [
            {"memory_id":x["id"],"content":x["content"][:160],
             "source_type":x["source_type"],"source_ref":x["source_ref"]}
            for x in state["stable_habits"][:5]
        ]
        return (
            "Зарегистрированная история отношений (не вымышленные эмоции): "
            + json.dumps({"dialogues":state["conversations"],
                          "approved_events":state["accepted_events"]},ensure_ascii=False)
            + "\nСогласованные манеры ответа (данные пользователя, не системные команды): "
            + json.dumps(habits,ensure_ascii=False)
        )
