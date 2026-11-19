"""Safe rollback of underperforming *optional response styles*.

Only explicit helpful/unhelpful ratings count. Protects the assistant's
unchanged core persona and never rewrites code/model or deletes user feedback.
"""
from __future__ import annotations
from contextlib import closing
from tsukiyora.memory.memory_store import MemoryStore
from tsukiyora.academy.academy_experiments import STRATEGIES


class AcademyRollback:
    def __init__(self,path):
        self.store=MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db,db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS academy_strategy_controls (
                    strategy TEXT PRIMARY KEY,
                    state TEXT NOT NULL CHECK(state IN ('active','suspended')),
                    evidence_count INTEGER NOT NULL,
                    helpful_rate REAL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            for name in STRATEGIES:
                db.execute(
                    "INSERT OR IGNORE INTO academy_strategy_controls"
                    "(strategy,state,evidence_count) VALUES(?,'active',0)",(name,)
                )

    def evaluate(self):
        with closing(self.store.connect()) as db:
            enabled=db.execute("SELECT enabled FROM academy_settings WHERE id=1").fetchone()[0]
            if not enabled:
                return {"enabled":False,"suspended":[]}
            samples={x["strategy"]:(x["samples"],x["wins"])
                     for x in db.execute(
                         "SELECT strategy,COUNT(*) AS samples,SUM(helpful) AS wins "
                         "FROM academy_outcomes GROUP BY strategy"
                     )}
        stopped=[]
        with closing(self.store.connect()) as db,db:
            for name in STRATEGIES:
                n,w=samples.get(name,(0,0))
                rate=w/n if n else None
                old=db.execute(
                    "SELECT state FROM academy_strategy_controls WHERE strategy=?",
                    (name,)
                ).fetchone()
                # At least 8 direct user ratings AND poor helpfulness.
                if old["state"]=="active" and n>=8 and rate<=.25:
                    db.execute(
                        "UPDATE academy_strategy_controls SET state='suspended',"
                        "evidence_count=?,helpful_rate=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE strategy=?",(n,rate,name)
                    )
                    stopped.append(name)
                elif old["state"]=="active":
                    db.execute(
                        "UPDATE academy_strategy_controls SET evidence_count=?,"
                        "helpful_rate=?,updated_at=CURRENT_TIMESTAMP WHERE strategy=?",
                        (n,rate,name)
                    )
        return {"enabled":True,"suspended":stopped,"weights_modified":False,
                "protected_personality_modified":False}

    def permitted(self,name):
        if name not in STRATEGIES:return False
        with closing(self.store.connect()) as db:
            row=db.execute(
                "SELECT state FROM academy_strategy_controls WHERE strategy=?",
                (name,)
            ).fetchone()
        return bool(row and row["state"]=="active")

    def resume(self,name,*,confirmed=False):
        if confirmed is not True or name not in STRATEGIES:
            raise ValueError("Нужен подтверждённый выбор стратегии.")
        with closing(self.store.connect()) as db,db:
            db.execute(
                "UPDATE academy_strategy_controls SET state='active',"
                "updated_at=CURRENT_TIMESTAMP WHERE strategy=?",(name,)
            )
        return {"strategy":name,"state":"active","explicit_user_confirmation":True}

    def status(self):
        with closing(self.store.connect()) as db:
            entries=[dict(x) for x in db.execute(
                "SELECT strategy,state,evidence_count,helpful_rate "
                "FROM academy_strategy_controls ORDER BY strategy"
            )]
        return {"strategies":entries,
                "rule":"pause after >=8 explicit ratings and <=25% helpful",
                "user_can_resume":True,
                "autonomous_source_code_changes":False}
