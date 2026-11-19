"""Cognitive Soul 6.0 — consent-led skill forge.

Safe *procedural plans*, never Python, shell, JS, tools or untrusted prompt text.
Background jobs create candidates, classify feedback, run deterministic shadow
checks, and audit provenance only. They have no CloudSession and no network.
Paid model-assisted recipe proposals require a separately approved call budget
AND a new per-call confirmation; answers may only select allowlisted step IDs.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3

from cognitive_evolution import DOMAINS, detect_domain
from memory_store import MemoryStore

# Frozen, pure advisory skills. No file I/O, code execution or API actions.
STEPS = {
    "scope": "Уточни задачу, входные данные и критерий результата.",
    "inspect_code": "Прочти доступные фрагменты кода, не выполняя их.",
    "inspect_contract": "Выдели предмет, стороны и ключевые условия договора.",
    "inspect_document": "Определи структуру и границы доступного документа.",
    "retrieve_memory": "Ищи только подтверждённые записи и их источники.",
    "gather_evidence": "Собери факты с доступной проверяемой опорой.",
    "citations": "Отдели реальные цитаты от неподтверждённых утверждений.",
    "check_conflicts": "Отметь противоречия, не исправляя память автоматически.",
    "compare_alternatives": "Сопоставь варианты по явным критериям.",
    "test_boundaries": "Проверь пограничные случаи мысленно или на разрешённых тестах.",
    "note_uncertainty": "Укажи, чего не хватает для уверенного вывода.",
    "draft_answer": "Сформируй понятный ответ без действий во внешних системах.",
    "verify_answer": "Проверь полноту и согласованность перед выдачей ответа.",
}
BASE = {
    "logic": ("scope","gather_evidence","compare_alternatives","draft_answer","verify_answer"),
    "coding": ("scope","inspect_code","test_boundaries","draft_answer","verify_answer"),
    "legal": ("scope","inspect_contract","gather_evidence","citations","draft_answer","verify_answer"),
    "rag": ("scope","retrieve_memory","gather_evidence","citations","draft_answer","verify_answer"),
    "documents": ("scope","inspect_document","gather_evidence","citations","draft_answer","verify_answer"),
}
SCENARIOS = {
    "logic": (("scope","gather_evidence"),("compare_alternatives","draft_answer"),("draft_answer","verify_answer"),("scope","verify_answer")),
    "coding": (("scope","inspect_code"),("inspect_code","test_boundaries"),("test_boundaries","draft_answer"),("draft_answer","verify_answer")),
    "legal": (("scope","inspect_contract"),("inspect_contract","gather_evidence"),("gather_evidence","citations"),("draft_answer","verify_answer")),
    "rag": (("scope","retrieve_memory"),("retrieve_memory","gather_evidence"),("gather_evidence","citations"),("draft_answer","verify_answer")),
    "documents": (("scope","inspect_document"),("inspect_document","gather_evidence"),("gather_evidence","citations"),("draft_answer","verify_answer")),
}
MAX_CANDIDATES = 120
MAX_CLOUD_CALLS = 20


def _recipe(seq):
    if (not isinstance(seq,(tuple,list)) or not 3<=len(seq)<=10
            or any(not isinstance(s,str) or s not in STEPS for s in seq)
            or len(set(seq))!=len(seq) or seq[0]!="scope"):
        raise ValueError("Навык должен состоять из 3–10 разных разрешённых шагов, начиная с scope.")
    return list(seq)


def _fingerprint(domain,recipe):
    return hashlib.sha256((domain+"\0"+",".join(recipe)).encode("utf-8")).hexdigest()


def _score(recipe, required):
    # Structural coverage with order. NOT an LLM capability test.
    positions=[recipe.index(k) for k in required if k in recipe]
    return int(len(positions)==len(required) and positions==sorted(positions))


class SkillForge:
    def __init__(self,path):
        self.store=MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS forge_skills(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    recipe TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    origin TEXT NOT NULL,
                    parent_id INTEGER REFERENCES forge_skills(id),
                    version INTEGER NOT NULL DEFAULT 1,
                    state TEXT NOT NULL DEFAULT 'draft'
                        CHECK(state IN ('draft','shadow','approved','paused','archived')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS forge_evidence(
                    skill_id INTEGER NOT NULL REFERENCES forge_skills(id),
                    message_id INTEGER NOT NULL REFERENCES messages(id),
                    PRIMARY KEY(skill_id,message_id)
                );
                CREATE TABLE IF NOT EXISTS forge_trials(
                    skill_id INTEGER NOT NULL REFERENCES forge_skills(id),
                    scenario INTEGER NOT NULL,
                    baseline INTEGER NOT NULL,
                    candidate INTEGER NOT NULL,
                    PRIMARY KEY(skill_id,scenario)
                );
                CREATE TABLE IF NOT EXISTS forge_active(
                    domain TEXT PRIMARY KEY,
                    skill_id INTEGER REFERENCES forge_skills(id)
                );
                CREATE TABLE IF NOT EXISTS forge_applied(
                    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                    skill_id INTEGER NOT NULL REFERENCES forge_skills(id),
                    helpful INTEGER CHECK(helpful IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS forge_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_id INTEGER NOT NULL REFERENCES forge_skills(id),
                    kind TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS forge_budget(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    requested_calls INTEGER NOT NULL DEFAULT 0,
                    approved_calls INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'unconfigured'
                        CHECK(state IN ('unconfigured','pending','approved','revoked'))
                );
                INSERT OR IGNORE INTO forge_budget(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS forge_cloud_usage(
                    day TEXT PRIMARY KEY,
                    reserved_calls INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS forge_skill_state
                    ON forge_skills(domain,state,id DESC);
                CREATE INDEX IF NOT EXISTS forge_feedback
                    ON forge_applied(skill_id,helpful);
            """)
            for domain in DOMAINS:
                db.execute(
                    "INSERT OR IGNORE INTO forge_active(domain,skill_id) VALUES(?,NULL)",
                    (domain,)
                )

    def _insert(self,domain,steps,origin,parent_id=None,evidence=()):
        if domain not in DOMAINS or origin not in (
                "positive_feedback","gap","variant","composite",
                "transfer","cloud_draft"):
            raise ValueError("Некорректная категория навыка.")
        recipe=_recipe(steps)
        fp=_fingerprint(domain,recipe)
        with closing(self.store.connect()) as db,db:
            version=1
            if parent_id is not None:
                parent=db.execute(
                    "SELECT version FROM forge_skills WHERE id=?",(parent_id,)
                ).fetchone()
                if not parent:
                    raise ValueError("Исходный навык не найден.")
                version=parent["version"]+1
            cur=db.execute(
                "INSERT OR IGNORE INTO forge_skills"
                "(domain,recipe,fingerprint,origin,parent_id,version)"
                " VALUES(?,?,?,?,?,?)",
                (domain,json.dumps(recipe),fp,origin,parent_id,version)
            )
            row=db.execute(
                "SELECT id,state,version FROM forge_skills WHERE fingerprint=?",
                (fp,)
            ).fetchone()
            for mid in evidence:
                db.execute(
                    "INSERT OR IGNORE INTO forge_evidence(skill_id,message_id)"
                    " VALUES(?,?)",(row["id"],mid)
                )
            if cur.rowcount:
                db.execute(
                    "INSERT INTO forge_events(skill_id,kind) VALUES(?,'created')",
                    (row["id"],)
                )
            return {"id":row["id"],"created":bool(cur.rowcount),
                    "state":row["state"],"version":row["version"],
                    "domain":domain}

    def _row(self,db,skill_id):
        if type(skill_id) is not int or skill_id<=0:
            raise ValueError("Некорректный ID навыка.")
        row=db.execute(
            "SELECT id,domain,recipe,origin,parent_id,version,state,created_at "
            "FROM forge_skills WHERE id=?",(skill_id,)
        ).fetchone()
        if not row:
            raise ValueError("Навык не найден.")
        skill=dict(row)
        skill["steps"]=json.loads(skill.pop("recipe"))
        return skill

    # 1, 2, 6: learn from *explicitly rated positive examples* and propose
    # new bounded combinations. No answer content is stored in skills.
    def discover(self):
        with closing(self.store.connect()) as db:
            positive=[dict(x) for x in db.execute(
                "SELECT f.message_id,m.content,f.note FROM cognitive_feedback f "
                "JOIN messages m ON m.id=f.message_id "
                "WHERE f.helpful=1 AND length(trim(f.note))>=8 "
                "ORDER BY f.message_id DESC LIMIT 160"
            )]
            negative=[dict(x) for x in db.execute(
                "SELECT f.message_id,m.content FROM cognitive_feedback f "
                "JOIN messages m ON m.id=f.message_id "
                "WHERE f.helpful=0 ORDER BY f.message_id DESC LIMIT 120"
            )]
        good=defaultdict(list); bad=defaultdict(list)
        for row in positive:
            good[detect_domain((row["content"]+" "+row["note"])[:1400])].append(row["message_id"])
        for row in negative:
            bad[detect_domain(row["content"][:1200])].append(row["message_id"])
        created=0; weak=[]
        for domain in DOMAINS:
            sources=good[domain]
            if len(sources)>=2:
                outcome=self._insert(domain,BASE[domain],"positive_feedback",
                                     evidence=sources[:20])
                created+=int(outcome["created"])
            if len(bad[domain])>=3:
                with closing(self.store.connect()) as db:
                    approved=db.execute(
                        "SELECT skill_id FROM forge_active WHERE domain=?",
                        (domain,)
                    ).fetchone()["skill_id"]
                if approved is None:
                    weak.append(domain)
                    recipe=list(BASE[domain])
                    for step in ("note_uncertainty","check_conflicts","compare_alternatives"):
                        if step not in recipe:
                            recipe.insert(-1,step)
                            break
                    created+=int(self._insert(domain,recipe,"gap")["created"])
        return {"new_drafts":created,"positive_examples":len(positive),
                "weak_domains":weak,"cloud_calls":0,
                "automatic_approval":False}

    def propose(self,domain,*,confirmed=False):
        if domain not in DOMAINS or confirmed is not True:
            raise ValueError("Выбери область и подтверди создание кандидата.")
        return self._insert(domain,BASE[domain],"gap")

    # 2, 4, 5, 7: compose, revise, transfer, versions, provenance.
    def variant(self,skill_id,*,confirmed=False):
        if confirmed is not True:
            raise ValueError("Нужно подтверждение создания новой версии.")
        with closing(self.store.connect()) as db:
            parent=self._row(db,skill_id)
        recipe=list(parent["steps"])
        for extra in ("check_conflicts","note_uncertainty","compare_alternatives","test_boundaries"):
            if extra not in recipe:
                recipe.insert(-1,extra)
                break
        else:
            raise ValueError("Допустимые способы усиления уже использованы.")
        return self._insert(parent["domain"],recipe,"variant",parent_id=skill_id)

    def compose(self,left_id,right_id,*,confirmed=False):
        if confirmed is not True or left_id==right_id:
            raise ValueError("Выбери два разных проверенных навыка и подтверди.")
        with closing(self.store.connect()) as db:
            left=self._row(db,left_id);right=self._row(db,right_id)
            if left["state"] not in ("shadow","approved") or right["state"] not in ("shadow","approved"):
                raise ValueError("Составлять можно только локально проверенные процедуры.")
            if left["domain"]!=right["domain"]:
                raise ValueError("Для другой области используй тест переноса.")
            evidence=[r[0] for r in db.execute(
                "SELECT DISTINCT message_id FROM forge_evidence "
                "WHERE skill_id IN (?,?) LIMIT 25",(left_id,right_id)
            )]
        merged=list(left["steps"])
        for item in right["steps"]:
            if item not in merged:
                merged.insert(-1,item)
        if len(merged)>10:
            merged=merged[:9]+["verify_answer"] if "verify_answer" not in merged[:9] else merged[:10]
        return self._insert(left["domain"],merged,"composite",
                            parent_id=left_id,evidence=evidence)

    def transfer(self,skill_id,target,*,confirmed=False):
        if target not in DOMAINS or confirmed is not True:
            raise ValueError("Выбери область переноса и подтверди.")
        with closing(self.store.connect()) as db:
            source=self._row(db,skill_id)
        if source["state"] not in ("shadow","approved") or source["domain"]==target:
            raise ValueError("Нужен проверенный навык из другой области.")
        recipe=list(BASE[target])
        for extra in source["steps"]:
            if extra not in recipe and len(recipe)<10:
                recipe.insert(-1,extra)
        outcome=self._insert(target,recipe,"transfer",parent_id=skill_id)
        return {**outcome,"transfer_verified":False}

    # 8, 9, 10: deterministic offline shadow test, never pretend to run an LLM.
    def shadow(self,limit=8):
        if type(limit) is not int or not 1<=limit<=12:
            raise ValueError("Лимит локальных испытаний 1–12.")
        with closing(self.store.connect()) as db:
            rows=[dict(x) for x in db.execute(
                "SELECT id,domain,recipe FROM forge_skills WHERE state='draft'"
                " ORDER BY id LIMIT ?",(limit,)
            )]
        tested=0
        for row in rows:
            recipe=_recipe(json.loads(row["recipe"]))
            baseline=BASE[row["domain"]]
            with closing(self.store.connect()) as db,db:
                for i,requirements in enumerate(SCENARIOS[row["domain"]]):
                    db.execute(
                        "INSERT OR IGNORE INTO forge_trials"
                        "(skill_id,scenario,baseline,candidate) VALUES(?,?,?,?)",
                        (row["id"],i,_score(baseline,requirements),
                         _score(recipe,requirements))
                    )
                db.execute(
                    "UPDATE forge_skills SET state='shadow',updated_at=CURRENT_TIMESTAMP"
                    " WHERE id=? AND state='draft'",(row["id"],)
                )
                db.execute(
                    "INSERT INTO forge_events(skill_id,kind,details)"
                    " VALUES(?,'shadow','structural_only')",(row["id"],)
                )
            tested+=1
        return {"tested":tested,"provider_calls":0,
                "evaluation":"deterministic_structure_only",
                "model_answers_evaluated":False,"automatic_activation":False}

    def _metrics(self,db,skill_id):
        s=db.execute(
            "SELECT COUNT(*) n,COALESCE(SUM(candidate),0) wins,"
            "COALESCE(SUM(baseline),0) baseline FROM forge_trials WHERE skill_id=?",
            (skill_id,)
        ).fetchone()
        source=db.execute(
            "SELECT COUNT(*) FROM forge_evidence e JOIN cognitive_feedback f"
            " ON f.message_id=e.message_id WHERE e.skill_id=? AND f.helpful=1",
            (skill_id,)
        ).fetchone()[0]
        rated=db.execute(
            "SELECT COUNT(*) n,COALESCE(SUM(helpful),0) positive "
            "FROM forge_applied WHERE skill_id=? AND helpful IS NOT NULL",
            (skill_id,)
        ).fetchone()
        return {"scenario_count":s["n"],"structural_passes":s["wins"],
                "baseline_passes":s["baseline"],"positive_sources":source,
                "live_ratings":rated["n"],"live_positive":rated["positive"]}

    def skills(self,limit=70):
        if type(limit) is not int or not 1<=limit<=120:
            raise ValueError("Некорректный лимит.")
        with closing(self.store.connect()) as db:
            rows=[dict(x) for x in db.execute(
                "SELECT id,domain,recipe,origin,parent_id,version,state,created_at "
                "FROM forge_skills ORDER BY id DESC LIMIT ?",(limit,)
            )]
            for row in rows:
                row["steps"]=json.loads(row.pop("recipe"))
                row["metrics"]=self._metrics(db,row["id"])
        return {"items":rows,"auto_approval":False,
                "cloud_calls_from_background":0,
                "models_evaluated_in_shadow":False}

    def compete(self,domain):
        if domain not in DOMAINS:
            raise ValueError("Неизвестная область.")
        entries=[x for x in self.skills(120)["items"] if x["domain"]==domain
                 and x["state"] in ("shadow","approved")]
        entries.sort(key=lambda x:(-x["metrics"]["structural_passes"],
                                  -x["metrics"]["positive_sources"],x["id"]))
        return {"domain":domain,"ranked":[{"id":x["id"],"state":x["state"],
                "structural_passes":x["metrics"]["structural_passes"],
                "positive_sources":x["metrics"]["positive_sources"]} for x in entries],
                "rank_is_heuristic_not_quality_proof":True}

    def curriculum(self,domain):
        if domain not in DOMAINS:
            raise ValueError("Неизвестная область.")
        entries=[x for x in self.skills(120)["items"] if x["domain"]==domain]
        tested=sum(x["metrics"]["scenario_count"] for x in entries)
        active=sum(x["state"]=="approved" for x in entries)
        weak=not entries or not any(x["metrics"]["positive_sources"]>=2 for x in entries)
        level="foundation" if weak else "practice" if not active else "review"
        return {"domain":domain,"stage":level,
                "focus":([list(x) for x in SCENARIOS[domain]]),
                "locally_tested_scenarios":tested,"approved":active,
                "exercise_type":"structural_check_not_semantic_model_test",
                "automatic_cloud_training":False}

    def guidance(self,query):
        domain=detect_domain(query)
        with closing(self.store.connect()) as db:
            item=db.execute(
                "SELECT s.id,s.recipe FROM forge_active a JOIN forge_skills s"
                " ON s.id=a.skill_id WHERE a.domain=? AND s.state='approved'",
                (domain,)
            ).fetchone()
        if not item:
            return None,""
        steps=_recipe(json.loads(item["recipe"]))
        # Fixed, trusted text only; no user note, model output or code.
        guidance=" ".join(STEPS[x] for x in steps)
        return item["id"],guidance[:650]

    def decide(self,skill_id,*,approve,confirmed=False):
        if type(approve) is not bool or confirmed is not True:
            raise ValueError("Включение/откат навыка требует согласия.")
        with closing(self.store.connect()) as db,db:
            item=self._row(db,skill_id)
            current=db.execute(
                "SELECT skill_id FROM forge_active WHERE domain=?",(item["domain"],)
            ).fetchone()["skill_id"]
            metrics=self._metrics(db,skill_id)
            if approve:
                if item["state"]!="shadow" or metrics["scenario_count"]<4 or metrics["structural_passes"]<4:
                    raise ValueError("Нужны четыре успешные локальные проверки и ручное подтверждение.")
                if item["origin"]=="positive_feedback" and metrics["positive_sources"]<2:
                    raise ValueError("Подтверждённые положительные оценки были отозваны.")
                if current and current!=skill_id:
                    db.execute("UPDATE forge_skills SET state='paused' WHERE id=?",(current,))
                db.execute(
                    "UPDATE forge_skills SET state='approved' WHERE id=?",(skill_id,)
                )
                db.execute(
                    "UPDATE forge_active SET skill_id=? WHERE domain=?",
                    (skill_id,item["domain"])
                )
                event="approved"
            else:
                if current!=skill_id:
                    raise ValueError("Откатить можно только активный навык.")
                db.execute(
                    "UPDATE forge_skills SET state='paused' WHERE id=?",(skill_id,)
                )
                db.execute(
                    "UPDATE forge_active SET skill_id=NULL WHERE domain=?",
                    (item["domain"],)
                )
                event="rolled_back"
            db.execute(
                "INSERT INTO forge_events(skill_id,kind) VALUES(?,?)",
                (skill_id,event)
            )
        return {"skill_id":skill_id,"state":event,
                "active":skill_id if approve else None}

    def archive(self,skill_id,*,confirmed=False):
        if confirmed is not True:
            raise ValueError("Архивация требует подтверждения.")
        with closing(self.store.connect()) as db,db:
            item=self._row(db,skill_id)
            if item["state"]=="approved":
                raise ValueError("Сначала отключи действующий навык.")
            db.execute(
                "UPDATE forge_skills SET state='archived' WHERE id=?",(skill_id,)
            )
            db.execute(
                "INSERT INTO forge_events(skill_id,kind) VALUES(?,'archived')",
                (skill_id,)
            )
        return {"id":skill_id,"archived":True,"deleted":False}

    def applied(self,message_id,skill_id):
        if skill_id is None:
            return
        if type(message_id) is not int or message_id<=0 or type(skill_id) is not int:
            raise ValueError("Некорректный идентификатор.")
        with closing(self.store.connect()) as db,db:
            role=db.execute(
                "SELECT role FROM messages WHERE id=?",(message_id,)
            ).fetchone()
            skill=db.execute(
                "SELECT state FROM forge_skills WHERE id=?",(skill_id,)
            ).fetchone()
            if role is None or role["role"]!="assistant" or skill is None or skill["state"]!="approved":
                raise ValueError("Навык можно связать только с реальным ответом ассистента.")
            db.execute(
                "INSERT OR IGNORE INTO forge_applied(message_id,skill_id) VALUES(?,?)",
                (message_id,skill_id)
            )

    def feedback(self,message_id,helpful):
        if type(message_id) is not int or type(helpful) is not bool:
            raise ValueError("Некорректная оценка.")
        with closing(self.store.connect()) as db,db:
            item=db.execute(
                "SELECT skill_id FROM forge_applied WHERE message_id=?",(message_id,)
            ).fetchone()
            if item is None:
                return {"measured":False}
            db.execute(
                "UPDATE forge_applied SET helpful=? WHERE message_id=?",
                (int(helpful),message_id)
            )
            skill_id=item["skill_id"]
            metrics=self._metrics(db,skill_id)
            count=metrics["live_ratings"]; positive=metrics["live_positive"]
            paused=bool(count>=8 and positive*4<=count and db.execute(
                "SELECT 1 FROM forge_active WHERE skill_id=?",(skill_id,)
            ).fetchone())
            if paused:
                db.execute(
                    "UPDATE forge_skills SET state='paused' WHERE id=?",(skill_id,)
                )
                db.execute(
                    "UPDATE forge_active SET skill_id=NULL WHERE skill_id=?",(skill_id,)
                )
                db.execute(
                    "INSERT INTO forge_events(skill_id,kind) VALUES(?,'degraded')",
                    (skill_id,)
                )
            return {"measured":True,"samples":count,"positive":positive,
                    "suspended":paused}

    def audit(self):
        """Read-only dedupe + automatic *disable* if feedback is revoked."""
        with closing(self.store.connect()) as db,db:
            rows=[dict(x) for x in db.execute(
                "SELECT id FROM forge_skills WHERE origin='positive_feedback' "
                "AND state IN ('approved','shadow') ORDER BY id DESC LIMIT 80"
            )]
            disabled=0
            for item in rows:
                source=self._metrics(db,item["id"])["positive_sources"]
                if source>=2:
                    continue
                db.execute(
                    "UPDATE forge_skills SET state='paused' WHERE id=?",
                    (item["id"],)
                )
                db.execute(
                    "UPDATE forge_active SET skill_id=NULL WHERE skill_id=?",
                    (item["id"],)
                )
                db.execute(
                    "INSERT INTO forge_events(skill_id,kind)"
                    " VALUES(?,'source_revoked')",(item["id"],)
                )
                disabled+=1
            duplicate_count=db.execute(
                "SELECT COUNT(*) FROM forge_skills "
                "WHERE state='archived'"
            ).fetchone()[0]
        return {"revoked_skills_paused":disabled,
                "archived_retained":duplicate_count,
                "automatic_delete":False,"cloud_calls":0}

    def history(self,limit=60):
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError("Некорректный лимит.")
        with closing(self.store.connect()) as db:
            events=[dict(x) for x in db.execute(
                "SELECT skill_id,kind,details,created_at FROM forge_events "
                "ORDER BY id DESC LIMIT ?",(limit,)
            )]
        return {"items":events,"original_sources_unchanged":True}

    def budget_status(self):
        day=datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with closing(self.store.connect()) as db:
            row=db.execute(
                "SELECT requested_calls,approved_calls,state "
                "FROM forge_budget WHERE id=1"
            ).fetchone()
            spent=db.execute(
                "SELECT reserved_calls FROM forge_cloud_usage WHERE day=?",
                (day,)
            ).fetchone()
        return {**dict(row),"today_reserved":spent["reserved_calls"] if spent else 0,
                "currency_budget_verified":False,
                "price_per_request_known":False,
                "automatic_cloud_calls":False,
                "notice":"Платные вызовы блокируются до отдельного согласования. "
                         "Количество запросов не равно денежной стоимости."}

    def request_budget(self,calls,*,confirmed=False):
        if type(calls) is not int or not 1<=calls<=MAX_CLOUD_CALLS or confirmed is not True:
            raise ValueError("Запроси 1–20 вызовов в сутки и подтверди предложение бюджета.")
        with closing(self.store.connect()) as db,db:
            db.execute(
                "UPDATE forge_budget SET requested_calls=?,approved_calls=0,"
                "state='pending' WHERE id=1",(calls,)
            )
        return self.budget_status()

    def decide_budget(self,approve,*,confirmed=False,ack_unpriced=False):
        # Called in a separate deliberate step after the user has agreed.
        if type(approve) is not bool or confirmed is not True:
            raise ValueError("Бюджет требует отдельного явного решения.")
        with closing(self.store.connect()) as db,db:
            row=db.execute(
                "SELECT requested_calls,state FROM forge_budget WHERE id=1"
            ).fetchone()
            if approve:
                if row["state"]!="pending" or ack_unpriced is not True:
                    raise ValueError("Подтверди согласованный бюджет и неизвестную цену токенов.")
                db.execute(
                    "UPDATE forge_budget SET approved_calls=?,state='approved' WHERE id=1",
                    (row["requested_calls"],)
                )
            else:
                db.execute(
                    "UPDATE forge_budget SET approved_calls=0,state='revoked' WHERE id=1"
                )
        return self.budget_status()

    def _reserve_paid_call(self):
        day=datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with closing(self.store.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row=db.execute(
                    "SELECT approved_calls,state FROM forge_budget WHERE id=1"
                ).fetchone()
                used=db.execute(
                    "SELECT reserved_calls FROM forge_cloud_usage WHERE day=?",
                    (day,)
                ).fetchone()
                spent=used["reserved_calls"] if used else 0
                if row["state"]!="approved" or row["approved_calls"]<=spent:
                    raise ValueError("Cloud.ru заблокирован до отдельного согласования бюджета.")
                db.execute(
                    "INSERT INTO forge_cloud_usage(day,reserved_calls)"
                    " VALUES(?,?) ON CONFLICT(day) DO UPDATE "
                    "SET reserved_calls=excluded.reserved_calls",
                    (day,spent+1)
                )
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def cloud_draft(self,skill_id,cloud,persona,*,confirmed_send=False):
        """One opt-in paid call; never a worker task. No memory or gold answers."""
        if confirmed_send is not True:
            raise ValueError("Запрос Cloud.ru требует отдельного подтверждения.")
        with closing(self.store.connect()) as db:
            item=self._row(db,skill_id)
        if item["state"] not in ("draft","shadow","approved"):
            raise ValueError("Недоступный навык для облачного проекта.")
        status=cloud.status()
        if not(status.get("configured") and status.get("verified") and status.get("model")):
            raise ValueError("Сначала проверь подключение Cloud.ru и модель.")
        # The cloud's original ResourceGuard still enforces chat limits.
        self._reserve_paid_call()
        # Static domain and ID catalog, no user messages, notes or documents.
        task=("Сформируй один новый неисполняемый план навыка для области "
              +item["domain"]+". Разрешены только эти ID шагов: "
              +", ".join(STEPS)+". Ответ строго JSON объект "
              "{\"steps\":[\"scope\",...]} из 3–10 разных разрешённых шагов. "
              "Первый шаг scope. Никакого кода или команд.")
        answer=cloud.complete(
            persona,[{"role":"user","content":task}],[],extra_guidance="")
        try:
            obj=json.loads(answer.strip())
            if not isinstance(obj,dict) or set(obj)!={"steps"}:
                raise ValueError("Неверный формат ответа.")
            recipe=_recipe(obj["steps"])
        except (ValueError,TypeError,json.JSONDecodeError):
            return {"state":"rejected_cloud_output","cloud_calls_reserved":1,
                    "candidate_created":False}
        output=self._insert(item["domain"],recipe,"cloud_draft",parent_id=skill_id)
        return {**output,"cloud_calls_reserved":1,
                "auto_approved":False,"needs_shadow_test":True}

    def status(self):
        skills=self.skills(limit=MAX_CANDIDATES)["items"]
        by_state=defaultdict(int);by_domain=defaultdict(int)
        for item in skills:
            by_state[item["state"]]+=1
            by_domain[item["domain"]]+=1
        with closing(self.store.connect()) as db:
            live=[dict(x) for x in db.execute(
                "SELECT domain,skill_id FROM forge_active ORDER BY domain"
            )]
        return {"version":"6.0","title":"Autonomous Skill Forge",
                "skills":len(skills),"by_state":dict(by_state),
                "by_domain":dict(by_domain),"active":live,
                "cloud_budget":self.budget_status(),
                "local_worker_only":True,"auto_approval":False,
                "real_world_performance_verified":False}
