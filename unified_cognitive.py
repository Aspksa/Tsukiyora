"""Cognitive Soul 5.0: ONE safe local coordinator over the existing SQLite queue.

The 1–4 engines keep their public APIs and historical tables.  The existing
Maintenance queue is the *only* recurring scheduler, so no second full-cycle
worker repeats its indexing, RAG, Academy, backups or persona consolidation.

This module does not accept a CloudSession and has no network code.  It never
approves memories or prompts, changes character files, runs LLM tests, or
activates a strategy. Reports contain aggregate counts and record IDs only.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
import threading
import time

from memory_store import MemoryStore
from cognitive_evolution import METHODS


class UnifiedCognitiveCore:
    def __init__(self, path):
        self.store = MemoryStore(path)
        self._tick_lock = threading.Lock()

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cognitive_v5_config(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    mode TEXT NOT NULL DEFAULT 'safe_local' CHECK(mode='safe_local'),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
                );
                INSERT OR IGNORE INTO cognitive_v5_config(id,mode,enabled)
                    VALUES(1,'safe_local',1);
                CREATE TABLE IF NOT EXISTS cognitive_v5_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK(outcome IN ('ok','warning')),
                    duration_ms INTEGER NOT NULL,
                    counts TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS cognitive_v5_reviews(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_key TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL CHECK(kind IN ('possible_conflict','stale_memory')),
                    memory_id INTEGER NOT NULL,
                    related_id INTEGER,
                    state TEXT NOT NULL DEFAULT 'pending'
                        CHECK(state IN ('pending','reviewed')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS cognitive_v5_review_state
                    ON cognitive_v5_reviews(state,id DESC);
                CREATE TABLE IF NOT EXISTS cognitive_v5_digests(
                    day TEXT PRIMARY KEY,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def _enabled(self):
        with closing(self.store.connect()) as db:
            row = db.execute(
                "SELECT enabled FROM cognitive_v5_config WHERE id=1"
            ).fetchone()
        return bool(row["enabled"])

    def configure(self, enabled, *, confirmed=False):
        if type(enabled) is not bool or confirmed is not True:
            raise ValueError("Включение/пауза требуют явного подтверждения.")
        with closing(self.store.connect()) as db, db:
            db.execute(
                "UPDATE cognitive_v5_config SET enabled=? WHERE id=1",(int(enabled),)
            )
        return {"mode":"safe_local","enabled":enabled,
                "cloud_calls":0,"approval_automatic":False}

    def reviews(self, limit=30):
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError("Некорректный лимит.")
        with closing(self.store.connect()) as db:
            items=[dict(x) for x in db.execute(
                "SELECT id,kind,memory_id,related_id,state,created_at "
                "FROM cognitive_v5_reviews ORDER BY id DESC LIMIT ?",(limit,)
            )]
        return {"items":items,"automatic_memory_edits":False}

    def mark_reviewed(self, review_id, *, confirmed=False):
        if type(review_id) is not int or review_id<=0 or confirmed is not True:
            raise ValueError("Требуется подтверждение отметки о проверке.")
        with closing(self.store.connect()) as db, db:
            updated=db.execute(
                "UPDATE cognitive_v5_reviews SET state='reviewed' WHERE id=?",
                (review_id,)
            )
        if updated.rowcount!=1:
            raise ValueError("Предупреждение не найдено.")
        return {"id":review_id,"state":"reviewed",
                "memory_changed":False,"source_changed":False}

    def status(self, scheduler=None):
        with closing(self.store.connect()) as db:
            config=db.execute(
                "SELECT mode,enabled FROM cognitive_v5_config WHERE id=1"
            ).fetchone()
            recent=[dict(x) for x in db.execute(
                "SELECT kind,outcome,duration_ms,counts,created_at "
                "FROM cognitive_v5_events ORDER BY id DESC LIMIT 12"
            )]
            digest=db.execute(
                "SELECT day,summary_json FROM cognitive_v5_digests "
                "ORDER BY day DESC LIMIT 1"
            ).fetchone()
            pending=db.execute(
                "SELECT COUNT(*) FROM cognitive_v5_reviews WHERE state='pending'"
            ).fetchone()[0]
        for event in recent:
            event["counts"]=json.loads(event["counts"])
        jobs=scheduler.pending() if scheduler is not None else None
        return {"version":"5.0","mode":config["mode"],
                "enabled":bool(config["enabled"]),"single_queue":True,
                "worker_count":1,"cloud_calls":0,
                "paid_experiments_in_background":False,
                "auto_promotions":False,"memory_auto_delete":False,
                "protected_persona_unchanged":True,
                "pending_reviews":pending,"recent_jobs":recent,
                "last_digest":({"day":digest["day"],
                                "summary":json.loads(digest["summary_json"])}
                               if digest else None),
                "maintenance":jobs}

    def tick(self, core, *, chat_lock=None, max_millis=850):
        """One bounded queue dispatch. No second thread, scan or paid provider."""
        if chat_lock is not None and chat_lock.locked():
            return {"status":"deferred","reason":"chat_active"}
        if not self._enabled():
            return {"status":"paused","mode":"safe_local"}
        if not self._tick_lock.acquire(blocking=False):
            return {"status":"deferred","reason":"already_running"}
        try:
            if chat_lock is not None and chat_lock.locked():
                return {"status":"deferred","reason":"chat_active"}
            result=core.scheduler.tick(core,chat_lock=chat_lock,
                                       max_millis=max_millis)
            if result["status"] in ("ok","warning"):
                safe={}
                for key,value in result.get("details",{}).items():
                    # Keep only aggregate metrics and flags; never persist
                    # error strings, prompts, raw memories or document text.
                    if type(value) in (int,bool,float):
                        safe[key]=value
                with closing(self.store.connect()) as db, db:
                    db.execute(
                        "INSERT INTO cognitive_v5_events(kind,outcome,duration_ms,counts)"
                        " VALUES(?,?,?,?)",
                        (result["kind"],result["status"],result["duration_ms"],
                         json.dumps(safe,sort_keys=True))
                    )
                    db.execute(
                        "DELETE FROM cognitive_v5_events WHERE id NOT IN "
                        "(SELECT id FROM cognitive_v5_events ORDER BY id DESC LIMIT 350)"
                    )
            return result
        except Exception as error:
            # Unexpected bugs must not kill the only background worker or
            # leave a claimed SQLite task forever stuck in 'running'.
            # No exception message or user data is persisted or exposed.
            with closing(self.store.connect()) as db, db:
                db.execute(
                    "UPDATE maintenance_queue SET state='waiting',due_at=?,"
                    "failures=failures+1,last_status='warning' "
                    "WHERE state='running'",
                    (int(time.time())+300,)
                )
            return {"status":"warning","reason":"local_job_failed",
                    "error_type":type(error).__name__,"cloud_calls":0}
        finally:
            self._tick_lock.release()

    def run_job(self, kind, core):
        """New jobs are part of the same Maintenance.JOBS SQLite queue."""
        if kind=="cog5_links":
            with closing(self.store.connect()) as db:
                ids=[r[0] for r in db.execute(
                    "SELECT id FROM memory_items WHERE status='active' "
                    "ORDER BY id DESC LIMIT 4"
                )]
            for memory_id in ids:
                core.growth.build_links(memory_id)
            return {"recent_memories":len(ids)}

        if kind=="cog5_skills":
            summary=core.next.skill_synthesis()
            return {"distinct_skills":len(summary["groups"]),
                    "repeatable_skills":len(summary["repeatable_candidates"])}

        if kind=="cog5_hypotheses":
            old=core.improvement.observe()
            new=core.evolution4.observe()
            return {"v3_drafts":int(old.get("created",False)),
                    "v4_drafts":int(new.get("proposed",0)),
                    "auto_activated":False}

        if kind=="cog5_simulator":
            # Only validate existing fixed recipes locally. This is
            # *not* testing or optimizing a live language model.
            rows=core.evolution4.report()["candidates"][:12]
            invalid=0
            for candidate in rows:
                recipe=candidate["recipe"]
                if (not isinstance(recipe,list) or len(recipe) not in (2,3)
                        or len(set(recipe))!=len(recipe)
                        or any(k not in METHODS for k in recipe)):
                    invalid+=1
            return {"recipes_checked":len(rows),
                    "invalid_recipes":invalid,
                    "real_model_evaluated":False}

        if kind=="cog5_insights":
            # Source-grounded, strictly local review: issue IDs, no
            # inferred personal motives and no edits to original memory.
            with closing(self.store.connect()) as db, db:
                pairs=[tuple(x) for x in db.execute(
                    "SELECT source_id,target_id FROM memory_links "
                    "WHERE relation='possible_conflict' ORDER BY created_at DESC LIMIT 30"
                )]
                stale=[x[0] for x in db.execute(
                    "SELECT id FROM memory_items "
                    "WHERE status='active' AND updated_at < date('now','-365 days') "
                    "ORDER BY updated_at LIMIT 30"
                )]
                question_count=db.execute(
                    "SELECT COUNT(*) FROM cognitive_next_questions "
                    "WHERE state='pending'"
                ).fetchone()[0]
            fresh=0
            with closing(self.store.connect()) as db, db:
                for left,right in pairs:
                    a,b=sorted((left,right))
                    cur=db.execute(
                        "INSERT OR IGNORE INTO cognitive_v5_reviews"
                        "(issue_key,kind,memory_id,related_id) VALUES(?,?,?,?)",
                        ("conflict:"+str(a)+":"+str(b),"possible_conflict",a,b)
                    )
                    fresh+=cur.rowcount
                for mid in stale:
                    cur=db.execute(
                        "INSERT OR IGNORE INTO cognitive_v5_reviews"
                        "(issue_key,kind,memory_id) VALUES(?,?,?)",
                        ("stale:"+str(mid),"stale_memory",mid)
                    )
                    fresh+=cur.rowcount
            return {"new_reviews":fresh,"unanswered_research_questions":question_count,
                    "automatic_facts_changed":0}

        if kind=="cog5_budget":
            plan=core.evolution4.budget_plan()
            return {"cloud_calls":0,"calls_remaining":plan["today_calls_remaining"],
                    "suggested_experiments":len(plan["suggestions"])}

        if kind=="cog5_digest":
            return self.digest(core)

        raise ValueError("Unknown Cognitive Soul 5.0 local job.")

    def digest(self, core):
        """Idempotent daily aggregate; never reruns the legacy full indexing cycle."""
        with closing(self.store.connect()) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0]!="ok":
                raise sqlite3.DatabaseError("SQLite quick_check failed.")
            today=datetime.now(timezone.utc).date().isoformat()
            prior=db.execute(
                "SELECT summary_json FROM cognitive_v5_digests WHERE day=?",
                (today,)
            ).fetchone()
            if prior is not None:
                return {"already_recorded":True,"day":today}
            summary={
                "feedback":db.execute(
                    "SELECT COUNT(*) FROM cognitive_feedback"
                ).fetchone()[0],
                "pending_memories":db.execute(
                    "SELECT COUNT(*) FROM memory_suggestions WHERE state='pending'"
                ).fetchone()[0],
                "pending_questions":db.execute(
                    "SELECT COUNT(*) FROM cognitive_next_questions WHERE state='pending'"
                ).fetchone()[0],
                "pending_reviews":db.execute(
                    "SELECT COUNT(*) FROM cognitive_v5_reviews WHERE state='pending'"
                ).fetchone()[0],
                "skill_candidates":db.execute(
                    "SELECT COUNT(*) FROM experience_skill_candidates"
                ).fetchone()[0],
                "v3_hypotheses":db.execute(
                    "SELECT COUNT(*) FROM improvement_proposals WHERE state='proposed'"
                ).fetchone()[0],
                "v4_hypotheses":db.execute(
                    "SELECT COUNT(*) FROM evol4_candidates WHERE state='proposed'"
                ).fetchone()[0],
            }
        serialized=json.dumps(summary,ensure_ascii=False,sort_keys=True)
        with closing(self.store.connect()) as db, db:
            cur=db.execute(
                "INSERT OR IGNORE INTO cognitive_v5_digests(day,summary_json)"
                " VALUES(?,?)",(today,serialized)
            )
            if cur.rowcount:
                db.execute(
                    "INSERT INTO cognitive_cycles(status,summary) VALUES('ok',?)",
                    (serialized,)
                )
                db.execute(
                    "DELETE FROM cognitive_cycles WHERE id NOT IN "
                    "(SELECT id FROM cognitive_cycles ORDER BY id DESC LIMIT 120)"
                )
            db.execute(
                "DELETE FROM cognitive_v5_digests WHERE day NOT IN "
                "(SELECT day FROM cognitive_v5_digests ORDER BY day DESC LIMIT 90)"
            )
        if cur.rowcount:
            # Preserve the original experience timeline expected by
            # established installations and V1–V4 API clients, while
            # recording only once per day (not from a second timer).
            with closing(self.store.connect()) as db:
                conflicts=db.execute(
                    "SELECT COUNT(*) FROM memory_links "
                    "WHERE relation='possible_conflict'"
                ).fetchone()[0]
            core.experience.snapshot(summary["pending_memories"],conflicts)
        return {"already_recorded":not bool(cur.rowcount),"day":today,
                "aggregate_fields":len(summary)}
