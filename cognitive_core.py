"""Tsukiyora's bounded cognitive maintenance and experience-based behaviour.

Not consciousness or unattended remote agents. A single local worker runs only
while the Python server is open, never sends data to a provider on its own.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading

from memory_store import MemoryStore
from soul_growth import SoulGrowth
from vector_memory import VectorMemory


class CognitiveCore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.store = MemoryStore(path)
        self.growth = SoulGrowth(path)
        self.vectors = VectorMemory(path)

    def connect(self):
        return self.store.connect()

    def initialize(self):
        self.vectors.initialize()
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cognitive_feedback (
                    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                    helpful INTEGER NOT NULL CHECK (helpful IN (0,1)),
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS cognitive_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL CHECK(status IN ('ok','warning')),
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS cognitive_lessons (
                    name TEXT PRIMARY KEY,
                    guidance TEXT NOT NULL,
                    evidence_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def feedback(self, message_id: int, helpful: bool, note: str = "") -> dict:
        if type(message_id) is not int or message_id <= 0 or type(helpful) is not bool:
            raise ValueError("Некорректная оценка ответа.")
        if not isinstance(note, str) or len(note) > 400:
            raise ValueError("Отзыв должен быть не длиннее 400 символов.")
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT role FROM messages WHERE id=?", (message_id,)).fetchone()
            if row is None or row["role"] != "assistant":
                raise ValueError("Оценить можно только сохранённый ответ Цукиёры.")
            db.execute("""
                INSERT INTO cognitive_feedback(message_id,helpful,note)
                VALUES(?,?,?) ON CONFLICT(message_id) DO UPDATE SET
                helpful=excluded.helpful, note=excluded.note, updated_at=CURRENT_TIMESTAMP
            """, (message_id, int(helpful), note.strip()))
        self.update_lessons()
        return {"message_id": message_id, "helpful": helpful, "recorded": True}

    def update_lessons(self):
        """Restrained, reversible response-format learning from explicit feedback."""
        with closing(self.connect()) as db:
            rows = [dict(x) for x in db.execute(
                "SELECT helpful FROM cognitive_feedback ORDER BY updated_at DESC,"
                " message_id DESC LIMIT 12"
            )]
        negative = sum(x["helpful"] == 0 for x in rows)
        positive = sum(x["helpful"] == 1 for x in rows)
        guidance = None
        if len(rows) >= 4 and negative >= 3 and negative > positive:
            guidance = "Отвечай точнее, короче, указывай допущения и задавай только необходимые вопросы."
        elif len(rows) >= 5 and positive >= 4 and positive > negative:
            guidance = "Сохраняй ясную структуру и конкретные примеры в ответах."
        with closing(self.connect()) as db, db:
            if guidance:
                db.execute("""
                    INSERT INTO cognitive_lessons(name,guidance,evidence_count)
                    VALUES('answer_style',?,?) ON CONFLICT(name) DO UPDATE SET
                    guidance=excluded.guidance, evidence_count=excluded.evidence_count,
                    updated_at=CURRENT_TIMESTAMP
                """, (guidance, max(negative, positive)))
            else:
                db.execute("DELETE FROM cognitive_lessons WHERE name='answer_style'")

    def guidance(self) -> str:
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT guidance FROM cognitive_lessons WHERE name='answer_style'"
            ).fetchone()
        return row["guidance"] if row else ""

    def biography(self) -> dict:
        """Explicit approved biography, stable persona and dated experience."""
        with closing(self.connect()) as db:
            approved = [dict(x) for x in db.execute(
                "SELECT id,kind,content,source_type,source_ref,created_at "
                "FROM memory_items WHERE status='active' "
                "AND kind IN ('biography','preference') "
                "ORDER BY id DESC LIMIT 80"
            )]
            events = [dict(x) for x in db.execute(
                "SELECT id,event,detail,created_at FROM soul_journal "
                "ORDER BY id DESC LIMIT 35"
            )]
        return {"identity": "Tsukiyora", "persona_version": "0.1.0",
                "approved_history": approved, "experience": events,
                "learned_guidance": self.guidance()}

    def graph(self, node_limit: int = 180) -> dict:
        if type(node_limit) is not int or not 1 <= node_limit <= 300:
            raise ValueError("Некорректный размер графа.")
        with closing(self.connect()) as db:
            nodes = [dict(x) for x in db.execute(
                "SELECT id,kind,substr(content,1,150) AS preview,created_at "
                "FROM memory_items WHERE status='active' ORDER BY id DESC LIMIT ?",
                (node_limit,)
            )]
            ids = {n["id"] for n in nodes}
            links = [dict(x) for x in db.execute(
                "SELECT source_id,target_id,relation FROM memory_links "
                "ORDER BY created_at DESC LIMIT 1500"
            )]
            pending = db.execute(
                "SELECT count(*) FROM memory_suggestions WHERE state='pending'"
            ).fetchone()[0]
        return {"nodes": nodes,
                "edges": [x for x in links if x["source_id"] in ids and x["target_id"] in ids][:400],
                "pending": pending}

    def status(self) -> dict:
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT id,status,summary,created_at FROM cognitive_cycles "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            reviews = db.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(helpful),0) AS likes "
                "FROM cognitive_feedback"
            ).fetchone()
            conflicts = db.execute(
                "SELECT count(*) FROM memory_links WHERE relation='possible_conflict'"
            ).fetchone()[0]
            pending = db.execute(
                "SELECT count(*) FROM memory_suggestions WHERE state='pending'"
            ).fetchone()[0]
        return {"last_cycle": dict(row) if row else None,
                "feedback": {"total": reviews["n"], "helpful": reviews["likes"]},
                "possible_conflicts": conflicts, "pending": pending,
                "guidance": self.guidance(), "vectors": self.vectors.stats()}

    def cycle(self) -> dict:
        """Local maintenance only: bounded and repeatable, no cloud/key access."""
        summary = ""
        state = "ok"
        try:
            with closing(self.connect()) as db:
                integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise sqlite3.DatabaseError("SQLite integrity check did not pass.")
            self.vectors.prune()
            self.update_lessons()
            stats = self.status()
            # Avoid logging private content: only aggregate counts and coarse status.
            summary = json.dumps({
                "conflicts": stats["possible_conflicts"],
                "pending": stats["pending"],
                "approved": stats["vectors"]["active_memories"],
                "indexed": stats["vectors"]["indexed_memories"],
            })
            with closing(self.connect()) as db:
                db.execute("PRAGMA optimize")
        except (sqlite3.Error, ValueError, OSError):
            state = "warning"
            summary = "Локальная проверка не завершилась; посмотри диагностику."
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO cognitive_cycles(status,summary) VALUES (?,?)",
                (state, summary)
            )
            db.execute(
                "DELETE FROM cognitive_cycles WHERE id NOT IN "
                "(SELECT id FROM cognitive_cycles ORDER BY id DESC LIMIT 120)"
            )
        return {"status": state, "summary": summary}


class CognitiveWorker:
    """One daemon thread per local server, with responsive shutdown."""
    def __init__(self, core: CognitiveCore, interval_seconds: int = 900):
        self.core = core
        self.interval = max(30, int(interval_seconds))
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="tsukiyora-cognitive", daemon=True)
        self._thread.start()

    def _run(self):
        # Delayed slightly to let startup serve requests; then every 15 min.
        if self._stop.wait(3):
            return
        while not self._stop.is_set():
            try:
                self.core.cycle()
            except (OSError, sqlite3.Error, ValueError):
                # A failed iteration never kills the application.
                pass
            if self._stop.wait(self.interval):
                return

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
