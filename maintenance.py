"""Persistent, bounded queue for LOCAL maintenance; chat takes priority.

No external network calls are permitted in jobs. Token 'usage' is only an
approximation based on text size, never provider billing telemetry.
"""
from __future__ import annotations
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

JOBS = {
    "integrity": 3600,
    "layer_index": 1800,
    "vector_repair": 1800,
    "quality": 3600,
    "lessons": 1800,
    "backup": 1800,
    "precision_index": 90,
    "academy_cycle": 300,
    "persona_continuity": 1200,
    "backup_verify": 3600,
}
MAX_HISTORY_DAYS = 30


class Maintenance:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def initialize(self):
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS maintenance_queue (
                    kind TEXT PRIMARY KEY,
                    state TEXT NOT NULL CHECK(state IN ('pending','running','waiting')),
                    due_at INTEGER NOT NULL,
                    executions INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    last_status TEXT NOT NULL DEFAULT 'never',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS usage_estimates (
                    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                    model TEXT NOT NULL,
                    approx_prompt_tokens INTEGER NOT NULL,
                    approx_answer_tokens INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Fix stale 'running' jobs after an abrupt process death.
            db.execute("UPDATE maintenance_queue SET state='pending' WHERE state='running'")
            for kind in JOBS:
                db.execute(
                    "INSERT OR IGNORE INTO maintenance_queue(kind,state,due_at)"
                    " VALUES(?,'pending',0)", (kind,)
                )

    def pending(self) -> dict:
        with closing(self.connect()) as db:
            rows = [dict(x) for x in db.execute(
                "SELECT kind,state,due_at,executions,failures,last_status "
                "FROM maintenance_queue ORDER BY due_at,kind"
            )]
            totals = db.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(approx_prompt_tokens),0) AS prompt,"
                "COALESCE(SUM(approx_answer_tokens),0) AS answer FROM usage_estimates"
            ).fetchone()
        return {
            "jobs": rows, "queue_depth": sum(x["state"] == "pending" for x in rows),
            "usage": {"answered_requests": totals["n"],
                      "approx_prompt_tokens": totals["prompt"],
                      "approx_answer_tokens": totals["answer"],
                      "provider_billed_tokens_known": False},
            "policy": "local maintenance only; active chat has absolute priority",
        }

    def record_answer(self, message_id: int, model: str, prompt_chars: int,
                      answer_chars: int):
        if (type(message_id) is not int or message_id <= 0 or
            not isinstance(model, str) or not 1 <= len(model) <= 200 or
            type(prompt_chars) is not int or type(answer_chars) is not int or
            prompt_chars < 0 or answer_chars < 0):
            raise ValueError("Invalid token accounting input.")
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO usage_estimates"
                "(message_id,model,approx_prompt_tokens,approx_answer_tokens)"
                " VALUES (?,?,?,?)",
                (message_id, model, (prompt_chars + 3)//4, (answer_chars + 3)//4)
            )

    def tick(self, core, chat_lock=None, max_millis: int = 900) -> dict:
        """Run at most one due job; skip immediately while chatting."""
        if type(max_millis) is not int or not 100 <= max_millis <= 10000:
            raise ValueError("Invalid background budget.")
        if chat_lock is not None and chat_lock.locked():
            return {"status": "deferred", "reason": "chat_active"}
        now = int(time.time())
        with closing(self.connect()) as db, db:
            candidate = db.execute(
                "SELECT kind FROM maintenance_queue WHERE due_at<=? "
                "AND state IN ('pending','waiting') ORDER BY due_at,kind LIMIT 1",
                (now,)
            ).fetchone()
            if candidate is None:
                return {"status": "idle"}
            kind = candidate["kind"]
            db.execute(
                "UPDATE maintenance_queue SET state='running' WHERE kind=?", (kind,)
            )
        if chat_lock is not None and chat_lock.locked():
            with closing(self.connect()) as db, db:
                db.execute(
                    "UPDATE maintenance_queue SET state='pending' WHERE kind=?", (kind,)
                )
            return {"status": "deferred", "reason": "chat_active"}
        start = time.monotonic()
        try:
            if kind == "integrity":
                with closing(self.connect()) as db:
                    ok = db.execute("PRAGMA quick_check").fetchone()[0]
                if ok != "ok":
                    raise sqlite3.DatabaseError("SQLite integrity check failed")
                result = {"integrity": "ok"}
            elif kind == "layer_index":
                result = core.layers.repair(limit=1800)
            elif kind == "vector_repair":
                result = core.vectors.repair(limit=1800)
            elif kind == "quality":
                result = core.quality.review(limit=140)
                result = {"possible_duplicates": len(result["duplicates"]) +
                          len(result.get("semantic_duplicates", [])),
                          "possible_conflicts": len(result["conflicts"])}
            elif kind == "lessons":
                result = core.experience.consolidate()
            elif kind == "backup":
                result = core.reliability.ensure_snapshot(interval_hours=5, keep=12)
            elif kind == "precision_index":
                result = core.precision.repair(limit=450)
            elif kind == "academy_cycle":
                result = core.academy.run_cycle()
            elif kind == "persona_continuity":
                result = core.continuity.consolidate()
            elif kind == "backup_verify":
                report = core.reliability.verify_snapshots(limit=3)
                result = {"checked":report["checked"],"healthy":report["healthy"],
                          "invalid":report["invalid"]}
            else:
                raise ValueError("Unknown local maintenance task")
            state = "ok"
            interval = JOBS[kind]
        except (sqlite3.Error, ValueError, OSError) as error:
            result = {"error": type(error).__name__}  # No sensitive content in logs.
            state = "warning"
            interval = min(300, JOBS[kind])
        elapsed = int((time.monotonic() - start) * 1000)
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE maintenance_queue SET state='waiting',due_at=?,"
                "executions=executions+1,failures=failures+?,last_status=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE kind=?",
                (int(time.time()) + interval,
                 int(state == "warning"), state, kind)
            )
        return {"kind": kind, "status": state, "duration_ms": elapsed,
                "over_budget": elapsed > max_millis, "details": result}
