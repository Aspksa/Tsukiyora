"""SOUL ACADEMY 5.0: measurable local curriculum with regression alerts.

A goal is a testable *response-format preference*, never a claim that model
weights improve. Regression alerts NEVER silently rewrite personality/code.
"""
from __future__ import annotations
from contextlib import closing
import json
import sqlite3

from tsukiyora.academy.academy_metrics import AcademyMetrics
from tsukiyora.academy.academy_experiments import AcademyExperiments
from tsukiyora.memory.memory_store import MemoryStore

GOALS = {
    "accuracy":"Проверять существование источников в ответах",
    "clarity":"Давать проверяемое объяснение понятными шагами",
    "brevity":"Сокращать повторы без потери важных фактов",
    "sources":"Различать источник и недоказанное утверждение",
}


class AcademyCurriculum:
    def __init__(self,path):
        self.store=MemoryStore(path)
        self.metrics=AcademyMetrics(path)
        self.experiments=AcademyExperiments(path)

    def initialize(self):
        with closing(self.store.connect()) as db:
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS academy_curriculum(
                        skill TEXT PRIMARY KEY,
                        objective TEXT NOT NULL,
                        state TEXT NOT NULL,
                        observed_change REAL,
                        samples INTEGER NOT NULL,
                        review_required INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS academy_regressions(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        skill TEXT NOT NULL,
                        observed_change REAL NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS academy_regressions_skill
                        ON academy_regressions(skill,id);
                """)

    def advance(self)->dict:
        metrics=self.metrics.status()["metrics"]
        assessments={row["skill"]:row for row in metrics}
        exp=self.experiments.report()
        warnings=[]
        with closing(self.store.connect()) as db,db:
            active=db.execute("SELECT enabled FROM academy_settings WHERE id=1").fetchone()
            if active is None or not active["enabled"]:
                return {"enabled":False,"updated":False,"regressions":[]}
            for skill,objective in GOALS.items():
                row=assessments.get(skill)
                state=row["assessment"] if row else "insufficient_evidence"
                samples=int(row["sample_count"]) if row else 0
                change=row["change_points"] if row else None
                alarm=state=="regressing" and samples>=8
                old=db.execute(
                    "SELECT review_required FROM academy_curriculum WHERE skill=?",
                    (skill,)
                ).fetchone()
                db.execute(
                    "INSERT INTO academy_curriculum(skill,objective,state,"
                    "observed_change,samples,review_required) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(skill) DO UPDATE SET "
                    "state=excluded.state,observed_change=excluded.observed_change,"
                    "samples=excluded.samples,review_required=excluded.review_required,"
                    "updated_at=CURRENT_TIMESTAMP",
                    (skill,objective,state,change,samples,int(alarm))
                )
                if alarm:
                    warnings.append({"skill":skill,"delta":change,
                                     "action":"review_feedback_before_changing_strategy"})
                    if old is None or not old["review_required"]:
                        db.execute(
                            "INSERT INTO academy_regressions(skill,observed_change)"
                            " VALUES(?,?)",(skill,change)
                        )
            db.execute(
                "DELETE FROM academy_regressions WHERE id NOT IN "
                "(SELECT id FROM academy_regressions ORDER BY id DESC LIMIT 100)"
            )
        return {"enabled":True,"updated":True,
                "regressions":warnings,
                "ab_experiment_ready":exp["comparison_ready"],
                "weights_modified":False,"personality_modified":False}

    def status(self)->dict:
        with closing(self.store.connect()) as db:
            goals=[dict(x) for x in db.execute(
                "SELECT skill,objective,state,observed_change,samples,review_required "
                "FROM academy_curriculum ORDER BY skill"
            )]
            regressions=[dict(x) for x in db.execute(
                "SELECT skill,observed_change,created_at "
                "FROM academy_regressions ORDER BY id DESC LIMIT 12"
            )]
        return {"goals":goals,"regressions":regressions,
                "evaluation":"explicit_user_ratings_only",
                "model_weights_updated":False,"automatic_strategy_rewrite":False,
                "controls":"user_can_disable_academy"}
