"""Cognitive Soul 7.0: evidence-gated unified supervisor.

Reads original V1–V6 tables instead of copying skill/memory/persona records.
A benchmark is a USER ATTESTED, OFFLINE paired answer comparison, not a
provider-executed or independently authenticated LLM evaluation. Neither
background tasks nor this module make Cloud.ru/network calls or write code.
"""
from __future__ import annotations
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from tsukiyora.memory.memory_store import MemoryStore
from tsukiyora.cognition.cognitive_evolution import DOMAINS, canonical


MIN_CASES = 8
MIN_GAINS = 2
MAX_CASES = 64


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Некорректное поле "+label+".")
    return value.strip()


class CognitiveSupervisor7:
    """Coordinates existing engines and stores only evidence and aggregates."""

    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS soul_v7_policy (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    first_new_forge_id INTEGER NOT NULL CHECK(first_new_forge_id>=1),
                    introduced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS soul_v7_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_id INTEGER NOT NULL REFERENCES forge_skills(id),
                    question_hash TEXT NOT NULL,
                    model_label TEXT NOT NULL,
                    baseline_correct INTEGER NOT NULL CHECK(baseline_correct IN (0,1)),
                    candidate_correct INTEGER NOT NULL CHECK(candidate_correct IN (0,1)),
                    source TEXT NOT NULL CHECK(source='user_attested_pair'),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(skill_id,question_hash)
                );
                CREATE INDEX IF NOT EXISTS soul_v7_cases_skill
                    ON soul_v7_cases(skill_id,id);
                CREATE TABLE IF NOT EXISTS soul_v7_reports (
                    day TEXT PRIMARY KEY,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS soul_v7_health (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    healthy INTEGER NOT NULL CHECK(healthy IN (0,1)),
                    counters TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS soul_v7_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_id INTEGER NOT NULL REFERENCES forge_skills(id),
                    decision TEXT NOT NULL,
                    case_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Cutover is persisted exactly once. Pre-existing skills keep
            # V6 behavior; every newly inserted skill requires V7 proof.
            db.execute(
                "INSERT OR IGNORE INTO soul_v7_policy(id,first_new_forge_id)"
                " SELECT 1,COALESCE(MAX(id),0)+1 FROM forge_skills"
            )

    def _skill(self, db, skill_id):
        if type(skill_id) is not int or skill_id <= 0:
            raise ValueError("Некорректный ID навыка.")
        s=db.execute(
            "SELECT id,domain,state,origin,version,parent_id FROM forge_skills WHERE id=?",
            (skill_id,)
        ).fetchone()
        if s is None:
            raise ValueError("Навык не найден.")
        return dict(s)

    def _report_in_db(self, db, skill_id):
        skill=self._skill(db,skill_id)
        start=db.execute(
            "SELECT first_new_forge_id FROM soul_v7_policy WHERE id=1"
        ).fetchone()["first_new_forge_id"]
        metrics=db.execute(
            "SELECT COUNT(*) AS n,"
            "COALESCE(SUM(baseline_correct),0) AS baseline,"
            "COALESCE(SUM(candidate_correct),0) AS candidate,"
            "COALESCE(SUM(CASE WHEN baseline_correct=1 AND candidate_correct=0"
            " THEN 1 ELSE 0 END),0) AS regressions,"
            "COUNT(DISTINCT model_label) AS models "
            "FROM soul_v7_cases WHERE skill_id=?",
            (skill_id,)
        ).fetchone()
        n=metrics["n"]
        baseline=metrics["baseline"]
        candidate=metrics["candidate"]
        regressions=metrics["regressions"]
        gains=candidate-baseline
        # User-attested paired answers can be audited, but are not
        # independently authenticated and do not prove model intelligence.
        ready=(n>=MIN_CASES and gains>=MIN_GAINS and regressions==0
               and metrics["models"]==1)
        return {**skill,"new_policy":skill_id>=start,
                "cases":n,"min_cases":MIN_CASES,
                "baseline_correct":baseline,
                "candidate_correct":candidate,"net_gain":gains,
                "regressions":regressions,
                "one_model":metrics["models"]==1,
                "evidence_ready":bool(ready),
                "data_origin":"user_attested_pair",
                "independent_model_validation":False,
                "paid_calls_made_by_v7":0,
                "next_step":("ready_for_manual_approval" if ready else
                            "add_paired_holdout_answers")}

    def verdict(self, skill_id):
        with closing(self.store.connect()) as db:
            return self._report_in_db(db,skill_id)

    def add_case(self, skill_id, question, expected, baseline_answer,
                 candidate_answer, model, *, confirmed=False,
                 attested=False):
        """Save only local exact-match outcomes + question hash, no raw text.

        The user must confirm model/context parity and the sample's origin.
        This method NEVER sends the question or answers to any provider.
        """
        if confirmed is not True or attested is not True:
            raise ValueError("Требуется подтверждение независимого сравнения двух ответов.")
        question=_text(question,"вопрос",700)
        expected=_text(expected,"эталонный ответ",1500)
        baseline_answer=_text(baseline_answer,"старый ответ",1500)
        candidate_answer=_text(candidate_answer,"новый ответ",1500)
        model=_text(model,"модель",120)
        if canonical(baseline_answer)==canonical(candidate_answer):
            raise ValueError("Оба ответа одинаковы: это не сравнительный эксперимент.")
        qhash=_digest(question.casefold().strip())
        baseline_ok=int(canonical(baseline_answer)==canonical(expected))
        candidate_ok=int(canonical(candidate_answer)==canonical(expected))
        with closing(self.store.connect()) as db,db:
            skill=self._skill(db,skill_id)
            if skill["state"]!="shadow":
                raise ValueError("Сначала выполни локальную проверку кандидата.")
            structural=db.execute(
                "SELECT COUNT(*) AS n,COALESCE(SUM(candidate),0) AS passed "
                "FROM forge_trials WHERE skill_id=?",(skill_id,)
            ).fetchone()
            if structural["n"]!=4 or structural["passed"]!=4:
                raise ValueError("Не хватает четырёх структурных проверок.")
            if db.execute(
                "SELECT COUNT(*) FROM soul_v7_cases WHERE skill_id=?",
                (skill_id,)
            ).fetchone()[0]>=MAX_CASES:
                raise ValueError("Лимит контрольных примеров исчерпан.")
            existing=db.execute(
                "SELECT model_label FROM soul_v7_cases WHERE skill_id=? LIMIT 1",
                (skill_id,)
            ).fetchone()
            if existing and existing["model_label"]!=model:
                raise ValueError("Нельзя сравнивать разные модели в одной серии.")
            try:
                db.execute(
                    "INSERT INTO soul_v7_cases(skill_id,question_hash,model_label,"
                    "baseline_correct,candidate_correct,source)"
                    " VALUES(?,?,?,?,?,'user_attested_pair')",
                    (skill_id,qhash,model,baseline_ok,candidate_ok)
                )
            except sqlite3.IntegrityError:
                raise ValueError("Этот контрольный вопрос уже зарегистрирован.") from None
        return self.verdict(skill_id)

    def _approve(self, forge, skill_id, *, confirmed=False):
        if confirmed is not True:
            raise ValueError("Нужно ручное подтверждение.")
        proof=self.verdict(skill_id)
        if not proof["evidence_ready"]:
            raise ValueError("Навык не прошёл сравнение на независимых контрольных примерах.")
        result=forge.decide(skill_id,approve=True,confirmed=True)
        with closing(self.store.connect()) as db,db:
            db.execute(
                "INSERT INTO soul_v7_decisions(skill_id,decision,case_count)"
                " VALUES(?,'approved',?)",(skill_id,proof["cases"])
            )
        return {**result,"evaluation":proof,
                "manually_approved":True}

    def registry(self, limit=30):
        """One normalized view of existing V1–V6 tables; no new source copies."""
        if type(limit) is not int or not 1<=limit<=50:
            raise ValueError("Лимит должен быть 1–50.")
        with closing(self.store.connect()) as db:
            sources=[
                ("forge6","forge_skills","id,domain,state,version"),
                ("experience1","experience_skill_candidates","id,state"),
                ("evolution4","evol4_candidates","id,domain,state,generation"),
                ("academy","academy_evaluations","rowid AS id,skill AS domain,assessment AS state"),
            ]
            items=[]
            for origin,table,columns in sources:
                for item in db.execute(
                    f"SELECT {columns} FROM {table} ORDER BY id DESC LIMIT ?",
                    (limit,)
                ):
                    record=dict(item)
                    record["source"]=origin
                    record["key"]=origin+":"+str(record["id"])
                    items.append(record)
            cases=db.execute(
                "SELECT COUNT(*) FROM soul_v7_cases"
            ).fetchone()[0]
            pending=db.execute(
                "SELECT COUNT(*) FROM forge_skills WHERE state='shadow'"
            ).fetchone()[0]
        return {"version":"7.0","sources":4,"items":items[:4*limit],
                "count":len(items),"pending_proposals":pending,
                "user_attested_cases":cases,"source_tables_unchanged":True,
                "cross_module_merge":"read_only_registry",
                "autonomous_activation":False}

    def plan(self, core):
        """No external queries or changes. Prioritize missing evidence."""
        registry=self.registry(limit=15)
        with closing(self.store.connect()) as db:
            weak=[dict(x) for x in db.execute(
                "SELECT id,domain FROM forge_skills WHERE state='shadow' "
                "ORDER BY id DESC LIMIT 15"
            )]
            reviews=db.execute(
                "SELECT COUNT(*) FROM soul_v7_cases"
            ).fetchone()[0]
            conflicts=db.execute(
                "SELECT COUNT(*) FROM memory_links WHERE relation='possible_conflict'"
            ).fetchone()[0]
        pending=[]
        for row in weak:
            verdict=self.verdict(row["id"])
            pending.append({
                "skill_id":row["id"],"domain":row["domain"],
                "remaining_cases":max(0,MIN_CASES-verdict["cases"]),
                "regressions":verdict["regressions"],
                "ready":verdict["evidence_ready"]
            })
        pending.sort(key=lambda x:(not x["ready"],-x["regressions"],x["remaining_cases"]))
        return {"tasks":pending,"open_conflicts":conflicts,
                "rated_tests":reviews,"registry_entries":registry["count"],
                "recommendation":"paired_holdout_review",
                "cloud_requests":0,"automatic_skill_activation":False}

    def health(self, core):
        with closing(self.store.connect()) as db:
            sqlite_ok=db.execute("PRAGMA quick_check").fetchone()[0]=="ok"
            fk_count=len(db.execute("PRAGMA foreign_key_check LIMIT 8").fetchall())
            old=db.execute(
                "SELECT COUNT(*) FROM maintenance_queue "
                "WHERE state='running'"
            ).fetchone()[0]
            failures=db.execute(
                "SELECT COUNT(*) FROM maintenance_queue "
                "WHERE failures>0"
            ).fetchone()[0]
            pending=db.execute(
                "SELECT COUNT(*) FROM cognitive_v5_reviews "
                "WHERE state='pending'"
            ).fetchone()[0]
            if not sqlite_ok or fk_count:
                healthy=False
            else:
                healthy=True
        return {"healthy":healthy,"sqlite_quick_check":sqlite_ok,
                "foreign_keys_failed":fk_count,
                "running_queue_jobs":old,
                "jobs_with_historical_failures":failures,
                "pending_memory_reviews":pending,
                "python_api":"running","network_diagnostic":False,
                "cloud_status_not_probed":True,"persona_modified":False}

    def run_job(self, kind, core):
        if kind=="cog7_review":
            plan=self.plan(core)
            counters={"pending_skills":len(plan["tasks"]),
                      "conflicts":plan["open_conflicts"],
                      "user_attested_cases":plan["rated_tests"]}
            healthy=True
        elif kind=="cog7_health":
            health=self.health(core)
            counters={k:v for k,v in health.items()
                      if type(v) in (int,bool)}
            healthy=bool(health["healthy"])
        elif kind=="cog7_daily":
            return self.daily(core)
        else:
            raise ValueError("Неизвестная фоновая задача 7.0.")
        with closing(self.store.connect()) as db,db:
            db.execute(
                "INSERT INTO soul_v7_health(kind,healthy,counters)"
                " VALUES(?,?,?)",
                (kind,int(healthy),json.dumps(counters,sort_keys=True))
            )
            db.execute(
                "DELETE FROM soul_v7_health WHERE id NOT IN "
                "(SELECT id FROM soul_v7_health ORDER BY id DESC LIMIT 120)"
            )
        return {**counters,"cloud_calls":0,"safe":healthy}

    def daily(self,core):
        day=datetime.now(timezone.utc).date().isoformat()
        report=self.registry(limit=30)
        with closing(self.store.connect()) as db,db:
            if db.execute(
                "SELECT 1 FROM soul_v7_reports WHERE day=?",(day,)
            ).fetchone():
                return {"already_recorded":True,"cloud_calls":0}
            summary={"registry_entries":report["count"],
                     "ready_skills":sum(
                         self.verdict(x["id"])["evidence_ready"]
                         for x in report["items"] if x["source"]=="forge6"),
                     "user_attested_cases":report["user_attested_cases"],
                     "pending_proposals":report["pending_proposals"]}
            db.execute(
                "INSERT OR IGNORE INTO soul_v7_reports(day,summary_json)"
                " VALUES(?,?)",(day,json.dumps(summary,sort_keys=True))
            )
            db.execute(
                "DELETE FROM soul_v7_reports WHERE day NOT IN "
                "(SELECT day FROM soul_v7_reports ORDER BY day DESC LIMIT 60)"
            )
        return {"already_recorded":False,**summary,"cloud_calls":0}

    def status(self,core):
        with closing(self.store.connect()) as db:
            recent=[dict(x) for x in db.execute(
                "SELECT kind,healthy,counters,created_at FROM soul_v7_health "
                "ORDER BY id DESC LIMIT 5"
            )]
            day=db.execute(
                "SELECT day,summary_json FROM soul_v7_reports "
                "ORDER BY day DESC LIMIT 1"
            ).fetchone()
            policy=db.execute(
                "SELECT first_new_forge_id FROM soul_v7_policy WHERE id=1"
            ).fetchone()["first_new_forge_id"]
        for item in recent:
            item["counters"]=json.loads(item["counters"])
        return {"version":"7.0","unified_registry":self.registry(15),
                "learning_plan":self.plan(core),
                "health":self.health(core),"recent":recent,
                "daily":({"date":day["day"],"summary":json.loads(day["summary_json"])}
                         if day else None),
                "new_skill_proof_required_from_id":policy,
                "cloud_training_calls":0,
                "protected_persona_unchanged":True}
