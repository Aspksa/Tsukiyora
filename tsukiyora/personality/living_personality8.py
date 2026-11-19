"""Cognitive Soul 8.0 — bounded, auditable *local* autonomy.

Reuses V1–V7 evidence, Academy, PersonaContinuity, PersonaArc and SkillForge.
No second worker, no new service, remote calls, executable plans, memory
promotion, skill activation, or edits to soul/personality.json.
This is a character simulation and task-management system, not sentience.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import re

from tsukiyora.memory.memory_store import MemoryStore

# Fixed non-executable operations: independent of user-provided prompt text.
GOALS = {
    "academy": ("Учиться на повторных ошибках", "academy_curriculum"),
    "skill": ("Проверить кандидата навыка", "skill_forge7"),
    "memory": ("Разобрать неопределённые воспоминания", "memory_review"),
    "curiosity": ("Уточнить пробел в знаниях", "existing_questions"),
    "documents": ("Проверить источники документов", "indexed_docs"),
    "daily": ("Оценить собственное развитие", "local_summary"),
}
PERSONA_STYLES = {
    "clarity": ("Ясные объяснения", "Отвечай ясно и конкретно, с короткими проверяемыми шагами."),
    "sources": ("Осторожность с источниками", "Отделяй проверенные источники от предположений."),
    "brevity": ("Меньше повторов", "Излагай короче, не теряя значимых условий."),
    "gentle": ("Тёплый тон", "Используй доброжелательный, спокойный тон без выдуманных чувств."),
}
STYLE_HINTS = {
    "clarity": ("понятн", "ясн", "объяснил"),
    "sources": ("источник", "проверил", "ссылк"),
    "brevity": ("кратк", "коротк", "без повтор"),
    "gentle": ("нежн", "тепл", "бережн"),
}
# Only these additional local permissions may be enabled via the V8 UI.
LOCAL_PERMISSIONS = ("proactive_chat", "indexed_documents", "local_practice")
FORBIDDEN_PERMISSIONS = ("paid_cloud", "external_actions", "rewrite_persona",
                         "activate_skills", "modify_user_memory")


def _utc_day():
    return datetime.now(timezone.utc).date().isoformat()


def _key(kind, source):
    return hashlib.sha256((kind + "\0" + source).encode("utf-8")).hexdigest()


class LivingPersonality8:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS soul8_settings(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 3),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT OR IGNORE INTO soul8_settings(id,level) VALUES(1,3);
                CREATE TABLE IF NOT EXISTS soul8_permissions(
                    scope TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS soul8_goals(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    category TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'active'
                        CHECK(state IN ('active','waiting_review','reviewed','dismissed','paused')),
                    progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 3),
                    evidence_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS soul8_checkpoints(
                    goal_id INTEGER NOT NULL REFERENCES soul8_goals(id),
                    stage INTEGER NOT NULL CHECK(stage BETWEEN 1 AND 3),
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(goal_id,stage)
                );
                CREATE TABLE IF NOT EXISTS soul8_proposals(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL CHECK(kind IN ('goal_review','style')),
                    target TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending'
                        CHECK(state IN ('pending','approved','rejected')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS soul8_styles(
                    name TEXT PRIMARY KEY,
                    approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    origin_id INTEGER NOT NULL REFERENCES soul8_proposals(id)
                );
                CREATE TABLE IF NOT EXISTS soul8_research(
                    question_id INTEGER NOT NULL REFERENCES cognitive_next_questions(id)
                        ON DELETE CASCADE,
                    document_id INTEGER NOT NULL REFERENCES source_documents(id)
                        ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    excerpt_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(question_id,document_id,ordinal)
                );
                CREATE INDEX IF NOT EXISTS soul8_research_source
                    ON soul8_research(document_id,ordinal);
                CREATE TABLE IF NOT EXISTS soul8_journal(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    counters_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS soul8_goals_status
                    ON soul8_goals(state,id);
                CREATE INDEX IF NOT EXISTS soul8_proposals_status
                    ON soul8_proposals(state,id);
            """)
            for scope in LOCAL_PERMISSIONS:
                db.execute(
                    "INSERT OR IGNORE INTO soul8_permissions(scope,enabled) "
                    "VALUES(?,?)",
                    (scope, 0 if scope=="indexed_documents" else 1)
                )
            for scope in FORBIDDEN_PERMISSIONS:
                db.execute(
                    "INSERT OR IGNORE INTO soul8_permissions(scope,enabled) "
                    "VALUES(?,0)",(scope,)
                )
            # Never inherit elevated V8 rights from malformed/legacy rows.
            db.execute(
                "UPDATE soul8_permissions SET enabled=0 "
                "WHERE scope IN ('paid_cloud','external_actions','rewrite_persona',"
                "'activate_skills','modify_user_memory')"
            )

    def _settings(self, db):
        level=db.execute(
            "SELECT level FROM soul8_settings WHERE id=1"
        ).fetchone()["level"]
        perms={row["scope"]:bool(row["enabled"]) for row in db.execute(
            "SELECT scope,enabled FROM soul8_permissions"
        )}
        return level,perms

    def control(self, level, *, confirmed=False):
        if type(level) is not int or not 0<=level<=3 or confirmed is not True:
            raise ValueError("Для уровня автономности 0–3 нужно явное подтверждение.")
        with closing(self.store.connect()) as db, db:
            db.execute("UPDATE soul8_settings SET level=?,updated_at=CURRENT_TIMESTAMP "
                       "WHERE id=1",(level,))
            self._journal(db,"level_changed","setting","level",{"level":level})
        return self.settings()

    def permission(self, scope, enabled, *, confirmed=False):
        if (scope not in LOCAL_PERMISSIONS or type(enabled) is not bool
                or confirmed is not True):
            raise ValueError("Разрешения на внешние действия, платные запросы,"
                             " память, навыки и ядро характера здесь недоступны.")
        with closing(self.store.connect()) as db, db:
            db.execute("UPDATE soul8_permissions SET enabled=? WHERE scope=?",
                       (int(enabled),scope))
            self._journal(db,"permission_changed","setting",scope,{"enabled":enabled})
        return self.settings()

    def settings(self):
        with closing(self.store.connect()) as db:
            level,perms=self._settings(db)
            counts={r["state"]:r["n"] for r in db.execute(
                "SELECT state,COUNT(*) n FROM soul8_goals GROUP BY state"
            )}
            pending=db.execute(
                "SELECT COUNT(*) FROM soul8_proposals WHERE state='pending'"
            ).fetchone()[0]
        return {"version":"8.0","autonomy_level":level,
                "local_permissions":perms,
                "nonlocal_actions_permitted":False,
                "new_paid_cloud_calls":0,"new_external_actions":0,
                "skill_auto_activation":False,"persona_json_modified":False,
                "subjective_feelings_claimed":False,
                "counts":counts,"pending_proposals":pending}

    def _journal(self, db, event, source_type, source_ref, counters):
        # Fixed names and numeric counters only. Never persist user text.
        safe={k:v for k,v in counters.items()
              if isinstance(k,str) and len(k)<60 and type(v) in (int,bool,float)}
        db.execute(
            "INSERT INTO soul8_journal(event,source_type,source_ref,counters_json)"
            " VALUES(?,?,?,?)",
            (event,source_type,str(source_ref)[:120],
             json.dumps(safe,ensure_ascii=False,sort_keys=True))
        )
        db.execute("DELETE FROM soul8_journal WHERE id NOT IN "
                   "(SELECT id FROM soul8_journal ORDER BY id DESC LIMIT 250)")

    def _signals(self,db):
        # Reuse existing sources. No copying private dialogue, PDF text or
        # biographical claims into our new tables.
        academy=[dict(row) for row in db.execute(
            "SELECT skill,samples,review_required FROM academy_curriculum "
            "WHERE review_required=1 LIMIT 6"
        )]
        skills=[dict(x) for x in db.execute(
            "SELECT id FROM forge_skills WHERE state='shadow' "
            "ORDER BY id DESC LIMIT 6"
        )]
        memory=db.execute(
            "SELECT COUNT(*) FROM memory_suggestions WHERE state='pending'"
        ).fetchone()[0]
        conflicts=db.execute(
            "SELECT COUNT(*) FROM memory_links WHERE relation='possible_conflict'"
        ).fetchone()[0]
        curiosity=db.execute(
            "SELECT COUNT(*) FROM cognitive_next_questions WHERE state='pending'"
        ).fetchone()[0]
        documents=db.execute(
            "SELECT COUNT(*) FROM source_documents"
        ).fetchone()[0]
        return {"academy":academy,"skills":skills,"memory":memory,
                "conflicts":conflicts,"curiosity":curiosity,
                "documents":documents}

    def _insert_goal(self,db,category,ref):
        if category not in GOALS:
            raise ValueError("Unknown V8 goal category.")
        fp=_key(category,ref)
        return db.execute(
            "INSERT OR IGNORE INTO soul8_goals(fingerprint,category,source_ref)"
            " VALUES(?,?,?)",(fp,category,ref)
        ).rowcount

    def discover(self, core):
        """Prioritize only grounded work that can be done without tools/model."""
        with closing(self.store.connect()) as db, db:
            level,perms=self._settings(db)
            if level < 1:
                return {"new_goals":0,"paused":True,"cloud_calls":0}
            sig=self._signals(db)
            candidates=[]
            candidates.extend(("academy",x["skill"]) for x in sig["academy"])
            candidates.extend(("skill",str(x["id"])) for x in sig["skills"])
            if sig["memory"] or sig["conflicts"]:
                candidates.append(("memory","pending_approved_source_review"))
            if sig["curiosity"]:
                candidates.append(("curiosity","existing_questions"))
            if level>=3 and perms["indexed_documents"] and sig["documents"]:
                candidates.append(("documents","approved_local_index"))
            candidates.append(("daily",_utc_day()))
            created=0
            for category,ref in candidates[:8]:
                created+=self._insert_goal(db,category,ref)
            if created:
                self._journal(db,"goals_discovered","local_evidence",_utc_day(),
                              {"new_goals":created})
            return {"new_goals":created,"signals_checked":len(candidates),
                    "cloud_calls":0,"persona_modified":False}

    def _evidence(self,db,goal):
        category=goal["category"]
        if category=="skill":
            skill_id=int(goal["source_ref"])
            x=db.execute(
                "SELECT COUNT(*) n,COALESCE(SUM(candidate_correct),0) score "
                "FROM soul_v7_cases WHERE skill_id=?",(skill_id,)
            ).fetchone()
            return {"attested_cases":x["n"],"attested_correct":x["score"]}
        if category=="academy":
            x=db.execute(
                "SELECT samples,review_required FROM academy_curriculum "
                "WHERE skill=?",(goal["source_ref"],)
            ).fetchone()
            return {"ratings":x["samples"] if x else 0,
                    "review_required":bool(x["review_required"]) if x else False}
        if category=="memory":
            return {"pending":db.execute(
                "SELECT COUNT(*) FROM memory_suggestions WHERE state='pending'"
            ).fetchone()[0],
                    "conflicts":db.execute(
                "SELECT COUNT(*) FROM memory_links WHERE relation='possible_conflict'"
            ).fetchone()[0]}
        if category=="curiosity":
            return {"open_questions":db.execute(
                "SELECT COUNT(*) FROM cognitive_next_questions WHERE state='pending'"
            ).fetchone()[0]}
        if category=="documents":
            x=db.execute(
                "SELECT COUNT(*) n,COALESCE(SUM(revision),0) rev "
                "FROM source_documents"
            ).fetchone()
            # Local metadata only; no private body text, file writes or uploads.
            return {"indexed_documents":x["n"],
                    "source_revisions":x["rev"],"raw_documents_accessed":0}
        return {"rated_answers":db.execute(
            "SELECT COUNT(*) FROM cognitive_feedback"
        ).fetchone()[0]}

    def agenda(self,limit=20):
        if type(limit) is not int or not 1<=limit<=50:
            raise ValueError("Некорректный лимит.")
        with closing(self.store.connect()) as db:
            rows=[dict(x) for x in db.execute(
                "SELECT id,category,source_ref,state,progress,evidence_count,"
                "created_at,updated_at FROM soul8_goals ORDER BY id DESC LIMIT ?",
                (limit,)
            )]
        for row in rows:
            row["label"]=GOALS[row["category"]][0]
            row["checkpoint_count"]=row.pop("progress")
        return {"items":rows,"automatic_approval":False,
                "user_source_data_changed":False}

    def _proposal(self,db,kind,target,source_id):
        if kind=="style" and target not in PERSONA_STYLES:
            raise ValueError("Unknown style.")
        fp=_key(kind,target+":"+str(source_id))
        return db.execute(
            "INSERT OR IGNORE INTO soul8_proposals(fingerprint,kind,target,source_id)"
            " VALUES(?,?,?,?)",(fp,kind,target,source_id)
        ).rowcount

    def work(self,core):
        """Complete just one predetermined offline checkpoint, idempotently."""
        with closing(self.store.connect()) as db,db:
            level,perms=self._settings(db)
            if level < 2 or not perms["local_practice"]:
                return {"processed":0,"reason":"local_practice_not_permitted",
                        "cloud_calls":0}
            row=db.execute(
                "SELECT id,category,source_ref,state,progress "
                "FROM soul8_goals WHERE state='active' "
                "ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return {"processed":0,"reason":"no_active_goal","cloud_calls":0}
            goal=dict(row)
            stage=goal["progress"]+1
            evidence=self._evidence(db,goal)
            db.execute(
                "INSERT OR IGNORE INTO soul8_checkpoints(goal_id,stage,evidence_json)"
                " VALUES(?,?,?)",
                (goal["id"],stage,json.dumps(evidence,sort_keys=True))
            )
            if stage>=3:
                db.execute(
                    "UPDATE soul8_goals SET progress=3,state='waiting_review',"
                    "evidence_count=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (len(evidence),goal["id"])
                )
                if level>=3 and perms["proactive_chat"]:
                    self._proposal(db,"goal_review",goal["category"],goal["id"])
            else:
                db.execute(
                    "UPDATE soul8_goals SET progress=?,evidence_count=?,"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (stage,len(evidence),goal["id"])
                )
            self._journal(db,"checkpoint","goal",str(goal["id"]),
                          {"stage":stage,"goal_id":goal["id"]})
            return {"processed":1,"goal_id":goal["id"],
                    "checkpoint":stage,"waiting_user_review":stage>=3,
                    "cloud_calls":0,"source_modified":False}

    def _style_sources(self,db):
        # Positive explicit ratings with a written explanation. Style plans
        # are from a fixed allowlist; no untrusted text becomes prompt code.
        notes=[x["note"].casefold() for x in db.execute(
            "SELECT f.note FROM cognitive_feedback f JOIN messages m "
            "ON m.id=f.message_id WHERE f.helpful=1 AND m.role='assistant' "
            "AND length(trim(f.note))>=8 ORDER BY f.message_id DESC LIMIT 160"
        )]
        return {trait:sum(any(h in n for h in hints) for n in notes)
                for trait,hints in STYLE_HINTS.items()}

    def reflect(self, core):
        """Reuse sourced habits/biography, without editing personality JSON."""
        with closing(self.store.connect()) as db,db:
            level,perms=self._settings(db)
            if level<1:
                return {"paused":True,"cloud_calls":0}
            state=core.continuity.state()
            counts=self._style_sources(db)
            proposed=0
            for name,count in counts.items():
                if count>=3:
                    proposed+=self._proposal(db,"style",name,0)
            # Read-only characteristics; not self-awareness or feelings.
            self._journal(db,"character_observed","existing_persona",_utc_day(),
                          {"approved_habits":len(state["stable_habits"]),
                           "conversations":state["conversations"],
                           "proposed_styles":proposed})
            return {"approved_habits":len(state["stable_habits"]),
                    "proposed_styles":proposed,
                    "real_emotions":False,"persona_modified":False,"cloud_calls":0}

    def proposals(self,limit=5):
        if type(limit) is not int or not 1<=limit<=20:
            raise ValueError("Некорректный лимит.")
        with closing(self.store.connect()) as db:
            level,perms=self._settings(db)
            rows=[dict(x) for x in db.execute(
                "SELECT id,kind,target,source_id,created_at FROM soul8_proposals "
                "WHERE state='pending' ORDER BY id ASC LIMIT ?",(limit,)
            )] if level>=1 and perms["proactive_chat"] else []
        for row in rows:
            if row["kind"]=="style":
                row["label"]="Предлагаю уточнить манеру ответа: "+PERSONA_STYLES[row["target"]][0]
                row["effect"]="Изменение только разрешённого стиля ответов, не исходного характера."
            else:
                row["label"]="Я проверила локальные данные по цели: "+GOALS[row["target"]][0]
                row["effect"]="Посмотреть результат и отметить, что отчёт прочитан."
        return {"items":rows,"model_generated":False,"cloud_calls":0,
                "automatic_approval":False}

    def decide(self,proposal_id,approve,*,confirmed=False):
        if (type(proposal_id) is not int or proposal_id<=0
                or type(approve) is not bool or confirmed is not True):
            raise ValueError("Нужно явно подтвердить решение по предложению.")
        with closing(self.store.connect()) as db,db:
            row=db.execute(
                "SELECT id,kind,target,source_id,state FROM soul8_proposals WHERE id=?",
                (proposal_id,)
            ).fetchone()
            if row is None or row["state"]!="pending":
                raise ValueError("Предложение уже обработано или не найдено.")
            if approve and row["kind"]=="style":
                source_count=self._style_sources(db)[row["target"]]
                if source_count<3:
                    raise ValueError("Три подтверждённых положительных отзыва больше не действуют.")
                db.execute(
                    "INSERT INTO soul8_styles(name,origin_id) VALUES(?,?) "
                    "ON CONFLICT(name) DO UPDATE SET "
                    "origin_id=excluded.origin_id,approved_at=CURRENT_TIMESTAMP",
                    (row["target"],proposal_id)
                )
            if row["kind"]=="goal_review":
                current=db.execute(
                    "SELECT state FROM soul8_goals WHERE id=?",
                    (row["source_id"],)
                ).fetchone()
                if current and current["state"]=="waiting_review":
                    db.execute(
                        "UPDATE soul8_goals SET state=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=?",
                        ("reviewed" if approve else "dismissed",row["source_id"])
                    )
            db.execute(
                "UPDATE soul8_proposals SET state=?,reviewed_at=CURRENT_TIMESTAMP "
                "WHERE id=?",
                ("approved" if approve else "rejected",proposal_id)
            )
            self._journal(db,"proposal_decision","proposal",str(proposal_id),
                          {"approved":approve})
        return {"id":proposal_id,"state":"approved" if approve else "rejected",
                "persona_core_unchanged":True,"new_skill_activated":False,
                "cloud_calls":0}

    def approved_style(self):
        """Fixed trusted advice only; source ratings checked on every use."""
        with closing(self.store.connect()) as db:
            counts=self._style_sources(db)
            names=[x["name"] for x in db.execute(
                "SELECT name FROM soul8_styles ORDER BY approved_at DESC LIMIT 4"
            )]
        return " ".join(PERSONA_STYLES[name][1] for name in names
                        if name in PERSONA_STYLES and counts[name]>=3)[:340]

    def research(self,core):
        """Offline retrieval ONLY for explicitly approved research questions.

        Reads existing indexed documents with a separately enabled local
        permission. Persists only pointers, revisions and excerpt hashes;
        does not invent interpretations, trust claims or modify RAG sources.
        """
        with closing(self.store.connect()) as db:
            level,perm=self._settings(db)
            if level<3 or not perm["indexed_documents"]:
                return {"searched":0,"reason":"no_document_permission",
                        "cloud_calls":0}
            questions=[dict(x) for x in db.execute(
                "SELECT id,question FROM cognitive_next_questions "
                "WHERE state='approved' ORDER BY id DESC LIMIT 3"
            )]
        total=0
        for question in questions:
            result=core.evidence.documents.search(question["question"],limit=4)
            # Recheck mutable consent before persisting any local pointers.
            with closing(self.store.connect()) as db,db:
                level,perm=self._settings(db)
                if level<3 or not perm["indexed_documents"]:
                    return {"searched":0,"reason":"permission_revoked",
                            "cloud_calls":0}
                for item in result["items"]:
                    fp=hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
                    total+=db.execute(
                        "INSERT INTO soul8_research(question_id,document_id,ordinal,"
                        "revision,excerpt_sha256) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(question_id,document_id,ordinal) DO UPDATE SET "
                        "revision=excluded.revision,"
                        "excerpt_sha256=excluded.excerpt_sha256",
                        (question["id"],item["document_id"],item["ordinal"],
                         item["revision"],fp)
                    ).rowcount
        if questions:
            with closing(self.store.connect()) as db,db:
                self._journal(db,"approved_local_research","existing_questions",
                              _utc_day(),{"questions":len(questions),
                                          "source_links":total})
        return {"searched":len(questions),"source_links":total,
                "cloud_calls":0,"factual_truth_verified":False,
                "raw_text_copied_to_v8":False}

    def research_sources(self,limit=30):
        if type(limit) is not int or not 1<=limit<=50:
            raise ValueError("Некорректный лимит.")
        with closing(self.store.connect()) as db:
            rows=[dict(x) for x in db.execute(
                "SELECT r.question_id,r.document_id,r.ordinal,r.revision,"
                "d.name,d.revision AS current_revision,r.created_at "
                "FROM soul8_research r JOIN source_documents d "
                "ON d.id=r.document_id "
                "ORDER BY r.created_at DESC,r.document_id LIMIT ?",(limit,)
            )]
        for item in rows:
            item["citation"]="[Документ #"+str(item["document_id"])+", фрагмент "+str(item["ordinal"])+"]"
            item["source_updated_since_review"]=item["revision"]!=item["current_revision"]
        return {"items":rows,"model_semantic_conclusions":False,
                "cloud_calls":0,"approved_questions_only":True}

    def journal(self,limit=25):
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError("Некорректный лимит.")
        with closing(self.store.connect()) as db:
            rows=[dict(x) for x in db.execute(
                "SELECT event,source_type,source_ref,counters_json,created_at "
                "FROM soul8_journal ORDER BY id DESC LIMIT ?",(limit,)
            )]
        for row in rows:
            row["counts"]=json.loads(row.pop("counters_json"))
        return {"items":rows,"private_message_text_stored":False,
                "simulation_not_subjective_life":True}

    def daily(self,core):
        today=_utc_day()
        with closing(self.store.connect()) as db,db:
            level,_=self._settings(db)
            if level<1:
                return {"paused":True,"cloud_calls":0}
            already=db.execute(
                "SELECT 1 FROM soul8_journal WHERE event='daily_digest' "
                "AND source_ref=? LIMIT 1",(today,)
            ).fetchone()
            if already:
                return {"already_recorded":True,"cloud_calls":0}
            counters={
                "goals":db.execute("SELECT COUNT(*) FROM soul8_goals").fetchone()[0],
                "waiting_review":db.execute(
                    "SELECT COUNT(*) FROM soul8_goals WHERE state='waiting_review'"
                ).fetchone()[0],
                "approved_styles":db.execute(
                    "SELECT COUNT(*) FROM soul8_styles"
                ).fetchone()[0],
                "feedback":db.execute(
                    "SELECT COUNT(*) FROM cognitive_feedback"
                ).fetchone()[0],
                "open_memory_reviews":db.execute(
                    "SELECT COUNT(*) FROM memory_suggestions WHERE state='pending'"
                ).fetchone()[0],
            }
            self._journal(db,"daily_digest","day",today,counters)
        return {**counters,"already_recorded":False,"cloud_calls":0,
                "self_report_not_proof":True}

    def run_job(self,kind,core):
        if kind=="cog8_discover":
            return self.discover(core)
        if kind=="cog8_practice":
            return self.work(core)
        if kind=="cog8_reflect":
            return self.reflect(core)
        if kind=="cog8_daily":
            return self.daily(core)
        if kind=="cog8_research":
            return self.research(core)
        if kind=="cog8_health":
            # Reuse the existing supervisor; do not run a second extensive scan.
            with closing(self.store.connect()) as db:
                warnings=db.execute(
                    "SELECT COUNT(*) FROM maintenance_queue WHERE failures>0"
                ).fetchone()[0]
                pending=db.execute(
                    "SELECT COUNT(*) FROM soul8_proposals WHERE state='pending'"
                ).fetchone()[0]
            return {"queue_jobs_with_warnings":warnings,
                    "pending_proposals":pending,"cloud_calls":0,
                    "additional_workers":0}
        raise ValueError("Неизвестное локальное задание 8.0.")

    def state(self,core):
        with closing(self.store.connect()) as db:
            row=db.execute(
                "SELECT COUNT(*) total,SUM(state='active') active,"
                "SUM(state='waiting_review') reviews FROM soul8_goals"
            ).fetchone()
            habits=db.execute(
                "SELECT approved_habits,conversations FROM persona_continuity "
                "WHERE id=1"
            ).fetchone()
            ready=db.execute(
                "SELECT COUNT(*) FROM soul8_styles"
            ).fetchone()[0]
        return {
            **self.settings(),
            "identity":"Tsukiyora","state_type":"simulated_character_continuity",
            "working_state":{"confidence":"depends_on_evidence",
                             "curiosity":"tracks_existing_questions",
                             "cognitive_load":"single_bounded_queue",
                             "emotions_are_real":False},
            "history":{"habits":habits["approved_habits"] if habits else 0,
                       "conversations":habits["conversations"] if habits else 0},
            "progress":{"total_goals":row["total"],
                        "active":row["active"] or 0,
                        "waiting_review":row["reviews"] or 0,
                        "approved_style_adjustments":ready},
            "existing_v7_evaluation_preserved":True,
            "external_actions_executed":0,
            "additional_cloud_calls":0,
            "self_modifying_code":False
        }
