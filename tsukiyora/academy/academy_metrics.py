"""Measurable SOUL ACADEMY 3.0 evaluations; entirely local and reversible.

Metrics describe explicit user ratings, not universal model quality. Strategies
remain the existing fixed allowlist and never rewrite the code or model weights.
"""
from __future__ import annotations
from contextlib import closing
import hashlib
import json

from tsukiyora.memory.memory_store import MemoryStore

SKILLS=("accuracy","brevity","clarity","sources")


class AcademyMetrics:
    def __init__(self,path):
        self.store=MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db,db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS academy_evaluations(
                    skill TEXT NOT NULL,
                    feedback_hash TEXT NOT NULL,
                    sample_count INTEGER NOT NULL,
                    recent_success REAL,
                    baseline_success REAL,
                    change_points REAL,
                    assessment TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(skill,feedback_hash)
                );
                CREATE INDEX IF NOT EXISTS academy_eval_recent
                    ON academy_evaluations(skill,created_at);
            """)

    def evaluate(self) -> dict:
        with closing(self.store.connect()) as db:
            enabled=db.execute(
                "SELECT enabled FROM academy_settings WHERE id=1"
            ).fetchone()[0]
            records=[dict(row) for row in db.execute(
                "SELECT f.message_id,f.helpful,COALESCE(t.category,'general') skill "
                "FROM cognitive_feedback f LEFT JOIN experience_feedback_tags t "
                "ON t.message_id=f.message_id ORDER BY f.message_id DESC LIMIT 400"
            )]
        if not enabled:
            return {"updated":False,"disabled":True,"evaluations":[]}
        evaluations=[]
        with closing(self.store.connect()) as db,db:
            for skill in SKILLS:
                selected=[row for row in records if row["skill"]==skill]
                recent=selected[:8]
                baseline=selected[8:16]
                recent_rate=(sum(x["helpful"] for x in recent)/len(recent)) if recent else None
                base_rate=(sum(x["helpful"] for x in baseline)/len(baseline)) if baseline else None
                delta=(recent_rate-base_rate)*100 if (
                    recent_rate is not None and base_rate is not None) else None
                status=(
                    "insufficient_evidence" if len(recent)<4 or len(baseline)<4 else
                    "improving" if delta>=15 else
                    "regressing" if delta<=-15 else "stable"
                )
                signature=hashlib.sha256(json.dumps(
                    [(x["message_id"],x["helpful"]) for x in selected],
                    ensure_ascii=False,separators=(",",":")
                ).encode()).hexdigest()
                values=(skill,signature,len(selected),
                        round(recent_rate,3) if recent_rate is not None else None,
                        round(base_rate,3) if base_rate is not None else None,
                        round(delta,1) if delta is not None else None,status)
                cursor=db.execute(
                    "INSERT OR IGNORE INTO academy_evaluations"
                    "(skill,feedback_hash,sample_count,recent_success,"
                    "baseline_success,change_points,assessment)"
                    " VALUES(?,?,?,?,?,?,?)",values
                )
                evaluations.append({
                    "skill":skill,"samples":len(selected),
                    "recent_success":values[3],"baseline_success":values[4],
                    "change_points":values[5],"assessment":status,
                    "new_snapshot":bool(cursor.rowcount)
                })
            db.execute(
                "DELETE FROM academy_evaluations WHERE rowid NOT IN "
                "(SELECT rowid FROM academy_evaluations ORDER BY created_at DESC,rowid DESC LIMIT 400)"
            )
        return {"updated":any(x["new_snapshot"] for x in evaluations),
                "evaluations":evaluations,"source":"explicit_user_feedback_only",
                "quality_claim":"measured_ratings_not_objective_truth"}

    def status(self) -> dict:
        with closing(self.store.connect()) as db:
            latest=[]
            for skill in SKILLS:
                row=db.execute(
                    "SELECT skill,sample_count,recent_success,baseline_success,"
                    "change_points,assessment,created_at FROM academy_evaluations "
                    "WHERE skill=? ORDER BY rowid DESC LIMIT 1",(skill,)
                ).fetchone()
                if row: latest.append(dict(row))
        return {"metrics":latest,"min_evidence_per_window":4,
                "window_size":8,"method":"compare_recent_vs_previous_explicit_ratings",
                "automated_model_training":False}
