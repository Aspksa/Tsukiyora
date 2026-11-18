"""SOUL ACADEMY 4.0: small, explainable A/B response-format experiments.

Fixed safe response strategies only. Assignment is stable per conversation;
only explicit helpful/unhelpful ratings are measured. No code/model tuning,
autonomous prompt creation, or claims of causal improvement from small samples.
"""
from __future__ import annotations
from contextlib import closing
import hashlib
from memory_store import MemoryStore

STRATEGIES={
    "clear_steps":"Если вопрос сложный, объясняй краткими понятными шагами.",
    "concise_first":"Начни с краткого ответа, затем добавь детали при необходимости.",
}


class AcademyExperiments:
    def __init__(self,path):
        self.store=MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db:
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS academy_assignment(
                        conversation_id TEXT PRIMARY KEY,
                        strategy TEXT NOT NULL CHECK(strategy IN ('clear_steps','concise_first')),
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS academy_outcomes(
                        message_id INTEGER PRIMARY KEY,
                        strategy TEXT NOT NULL CHECK(strategy IN ('clear_steps','concise_first')),
                        helpful INTEGER NOT NULL CHECK(helpful IN (0,1)),
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)

    def assign(self,conversation_id:str,enabled:bool=True)->str:
        if not isinstance(conversation_id,str) or not 1<=len(conversation_id)<=64:
            raise ValueError("Invalid conversation ID")
        if not enabled:
            return ""
        with closing(self.store.connect()) as db:
            with db:
                exists=db.execute(
                    "SELECT 1 FROM conversations WHERE id=?",(conversation_id,)
                ).fetchone()
                if not exists:raise ValueError("Unknown conversation")
                selected=list(STRATEGIES)[int(hashlib.sha256(
                    conversation_id.encode("utf-8")).hexdigest(),16)%len(STRATEGIES)]
                db.execute(
                    "INSERT OR IGNORE INTO academy_assignment(conversation_id,strategy)"
                    " VALUES(?,?)",(conversation_id,selected))
                row=db.execute(
                    "SELECT strategy FROM academy_assignment WHERE conversation_id=?",
                    (conversation_id,)).fetchone()
                return row["strategy"]

    def guidance(self,conversation_id:str,enabled:bool=True)->str:
        assigned=self.assign(conversation_id,enabled=enabled)
        return STRATEGIES.get(assigned,"")

    def record(self,message_id:int,helpful:bool)->dict:
        if type(message_id) is not int or message_id<=0 or type(helpful) is not bool:
            raise ValueError("Invalid user rating")
        with closing(self.store.connect()) as db:
            with db:
                row=db.execute(
                    "SELECT m.role,a.strategy FROM messages m "
                    "JOIN academy_assignment a ON a.conversation_id=m.conversation_id "
                    "WHERE m.id=?",(message_id,)).fetchone()
                if not row or row["role"]!="assistant":
                    return {"measured":False}
                db.execute(
                    "INSERT INTO academy_outcomes(message_id,strategy,helpful)"
                    " VALUES(?,?,?) ON CONFLICT(message_id) DO UPDATE SET"
                    " helpful=excluded.helpful,updated_at=CURRENT_TIMESTAMP",
                    (message_id,row["strategy"],int(helpful)))
                return {"measured":True,"strategy":row["strategy"]}

    def report(self)->dict:
        with closing(self.store.connect()) as db:
            rows=[dict(x) for x in db.execute(
                "SELECT strategy,COUNT(*) AS samples,SUM(helpful) AS successes "
                "FROM academy_outcomes GROUP BY strategy"
            )]
            assigned=db.execute("SELECT COUNT(*) FROM academy_assignment").fetchone()[0]
        outcome={}
        # Always show both strategies, even if no one rated either.
        for name in STRATEGIES:
            row=next((r for r in rows if r["strategy"]==name),None)
            n=row["samples"] if row else 0
            wins=row["successes"] if row else 0
            outcome[name]={"samples":n,"successes":wins,
                           "helpful_rate":round(wins/n,3) if n else None}
        eligible=all(x["samples"]>=8 for x in outcome.values())
        leader=(max(outcome,key=lambda x:outcome[x]["helpful_rate"])
                if eligible else None)
        delta=(abs(outcome["clear_steps"]["helpful_rate"]-
                   outcome["concise_first"]["helpful_rate"]) if eligible else None)
        return {"assigned_conversations":assigned,"strategies":outcome,
                "comparison_ready":eligible,
                "observed_leader":leader if eligible and delta>=.15 else None,
                "observed_difference":round(delta,3) if delta is not None else None,
                "interpretation":"Пользовательские оценки, не причинное доказательство эффективности.",
                "model_training":False}
