"""Tsukiyora Cognitive Soul 2.0: bounded, source-aware decision support.

Ten composable capabilities (not autonomous consciousness or neural training).
The user authorizes writes; none of these functions call an external model,
make remote requests, rewrite approved memories, or modify protected persona.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from tsukiyora.academy.academy_practice import QUESTIONS
from tsukiyora.memory.memory_quality import MemoryQuality
from tsukiyora.memory.memory_store import MemoryStore
from tsukiyora.documents.rag_pipeline import RagPipeline
from tsukiyora.cognition.reasoning import ReasoningAudit
from tsukiyora.cognition.reflective_optimization import STRATEGIES

CAUSAL = re.compile(r"\b(потому что|поскольку|так как|причин[а-я]*|из-за|вследствие|because|due to)\b", re.I)
VALID_GOAL_STATES = ("proposed", "active", "done", "paused")
MAX_TEXT = 500


def checked_text(value, label: str, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not 2 <= len(value.strip()) <= maximum:
        raise ValueError(label + ": требуется текст длиной от 2 до " + str(maximum) + " символов.")
    return " ".join(value.split())


class CognitiveNext:
    def __init__(self, path, core=None):
        self.store = MemoryStore(path)
        self.rag = RagPipeline(path)
        self.audit = ReasoningAudit(path)
        self.quality = MemoryQuality(path)
        self.core = core

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cognitive_next_goals(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'proposed'
                        CHECK(state IN ('proposed','active','done','paused')),
                    source TEXT NOT NULL DEFAULT 'explicit_user',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS cognitive_next_questions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    question TEXT NOT NULL,
                    source_note TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending'
                        CHECK(state IN ('pending','approved','dismissed')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS cognitive_next_simulations(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    skill TEXT NOT NULL,
                    answer_hash TEXT NOT NULL,
                    passed INTEGER NOT NULL CHECK(passed IN(0,1)),
                    checked_rules TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    # 1. Causal intelligence: organize what sources actually say.
    def causal_map(self, question: str, limit: int = 8) -> dict:
        question = checked_text(question, "Вопрос")
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError("Некорректное количество доказательств.")
        result = self.rag.search(question, limit=limit)
        evidence = []
        claimed_causes = []
        for item in result["items"]:
            row = {"memory_id": item["id"], "content": item["content"][:600],
                   "source_type": item.get("source_type"),
                   "source_ref": item.get("source_ref"),
                   "source_exists": item.get("source_exists"),
                   "possible_conflict": bool(item.get("possible_conflict")),
                   "stale": bool(item.get("potentially_stale")),
                   "citation": item.get("citation")}
            evidence.append(row)
            if CAUSAL.search(item["content"]):
                claimed_causes.append(
                    {"memory_id": item["id"], "source_statement": item["content"][:400],
                     "certainty": "attributed_claim_not_causally_verified"})
        return {"question": question, "evidence": evidence,
                "claimed_causes": claimed_causes,
                "unknown": (["В сохранённых источниках нет подходящих фактов."]
                            if not evidence else
                            ["Связь во времени и слова о причине не доказывают причинность."]),
                "verified_causality": False, "cloud_requests": 0}

    # 2. Metacognition: checks source provenance of an actual saved answer.
    def self_check(self, message_id: int) -> dict:
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("Нужен ID сохранённого ответа.")
        with closing(self.store.connect()) as db:
            row = db.execute(
                "SELECT role,content FROM messages WHERE id=?", (message_id,)
            ).fetchone()
        if row is None or row["role"] != "assistant":
            raise ValueError("Проверить можно только сохранённый ответ ассистента.")
        report = self.audit.verify(message_id, row["content"])
        problems = []
        if report["missing_citations"]:
            problems.append("Ссылки на отсутствующую или удалённую память.")
        if report["possible_conflicts"]:
            problems.append("У цитируемых воспоминаний есть конфликтующие связи.")
        if report["state"] == "no_citations":
            problems.append("В ответе нет ссылок на память; это не доказывает ошибку.")
        return {"message_id": message_id, "audit": report,
                "review_questions": problems,
                "source_tracing_only": True, "truth_verified": False,
                "rewrite_answer": False, "cloud_requests": 0}

    # 3. Skill consolidation: only user-rated, positive, traceable experience.
    def skill_synthesis(self) -> dict:
        with closing(self.store.connect()) as db:
            rows = [dict(x) for x in db.execute(
                "SELECT f.message_id,f.note,s.id AS candidate_id,s.state "
                "FROM cognitive_feedback f "
                "LEFT JOIN experience_skill_candidates s ON s.message_id=f.message_id "
                "WHERE f.helpful=1 AND length(trim(f.note))>=8 "
                "ORDER BY f.message_id DESC LIMIT 300"
            )]
        grouped = {}
        for x in rows:
            note = " ".join(x["note"].split())[:280]
            fingerprint = note.casefold()
            group = grouped.setdefault(fingerprint,
                {"guidance": note, "source_message_ids": [],
                 "approved_candidates": [], "unapproved_candidates": []})
            group["source_message_ids"].append(x["message_id"])
            if x["candidate_id"] is not None:
                key = "approved_candidates" if x["state"] == "approved" else "unapproved_candidates"
                group[key].append(x["candidate_id"])
        groups = sorted(grouped.values(),
                        key=lambda x: (-len(x["source_message_ids"]), x["guidance"]))
        return {"groups": groups[:30],
                "repeatable_candidates": [g for g in groups if len(g["source_message_ids"])>=2][:20],
                "automatic_activation": False,
                "source": "explicit_positive_feedback_only"}

    # 4. Understanding a person's actual stated and approved context.
    def understanding(self) -> dict:
        with closing(self.store.connect()) as db:
            items = [dict(x) for x in db.execute(
                "SELECT id,kind,content,source_type,source_ref,created_at,updated_at "
                "FROM memory_items WHERE status='active' AND "
                "kind IN ('preference','biography','event') "
                "ORDER BY updated_at DESC,id DESC LIMIT 100"
            )]
            goals = [dict(x) for x in db.execute(
                "SELECT id,title,state,created_at,updated_at FROM cognitive_next_goals "
                "WHERE state IN ('proposed','active') ORDER BY id DESC LIMIT 25"
            )]
        return {"approved_stated_context": items, "user_defined_goals": goals,
                "infer_unstated_motives": False, "truth_verified": False,
                "privacy": "local_approved_information"}

    def add_goal(self, title: str, *, confirmed: bool = False) -> dict:
        title = checked_text(title, "Цель", 280)
        if confirmed is not True:
            raise ValueError("Требуется явное подтверждение сохранения цели.")
        with closing(self.store.connect()) as db, db:
            cursor = db.execute(
                "INSERT INTO cognitive_next_goals(title,state) VALUES(?,'active')",
                (title,))
        return {"id": cursor.lastrowid, "state": "active", "source": "explicit_user"}

    def goal_state(self, goal_id: int, state: str, *, confirmed: bool = False) -> dict:
        if (type(goal_id) is not int or goal_id <= 0 or state not in VALID_GOAL_STATES
                or confirmed is not True):
            raise ValueError("Некорректное изменение цели или нет подтверждения.")
        with closing(self.store.connect()) as db, db:
            cursor = db.execute(
                "UPDATE cognitive_next_goals SET state=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (state,goal_id))
        if cursor.rowcount != 1:
            raise ValueError("Цель не найдена.")
        return {"id": goal_id, "state": state}

    # 5. Operational state, not sentience or simulated emotions.
    def working_state(self) -> dict:
        with closing(self.store.connect()) as db:
            goal_count = db.execute(
                "SELECT COUNT(*) FROM cognitive_next_goals WHERE state='active'"
            ).fetchone()[0]
            pending = db.execute(
                "SELECT COUNT(*) FROM memory_suggestions WHERE state='pending'"
            ).fetchone()[0]
            questions = db.execute(
                "SELECT COUNT(*) FROM cognitive_next_questions WHERE state='pending'"
            ).fetchone()[0]
            last = db.execute(
                "SELECT status,created_at FROM cognitive_cycles ORDER BY id DESC LIMIT 1"
            ).fetchone()
            rated = db.execute(
                "SELECT COUNT(*) FROM cognitive_feedback"
            ).fetchone()[0]
        return {"active_goals": goal_count, "pending_memory_reviews": pending,
                "pending_research_questions": questions,
                "last_maintenance": dict(last) if last else None,
                "rated_answers": rated,
                "subjective_emotions": False, "consciousness_claimed": False,
                "confidence_is_not_self_awareness": True}

    # 6. Curiosity proposes local questions; no unattended internet research.
    def knowledge_gap(self, question: str) -> dict:
        question = checked_text(question, "Тема исследования")
        evidence = self.rag.search(question, limit=5)["items"]
        uncertainty = bool(not evidence or any(
            x.get("possible_conflict") or x.get("potentially_stale")
            or not x.get("source_exists") for x in evidence
        ))
        return {"question": question, "supporting_memory_ids": [x["id"] for x in evidence],
                "research_suggested": uncertainty,
                "reason": ("Нет достаточной опоры или источники требуют проверки."
                           if uncertainty else
                           "Найдены подходящие записи, но их истинность не проверена."),
                "research_started": False, "cloud_requests": 0}

    def save_question(self, question: str, *, confirmed: bool = False) -> dict:
        question = checked_text(question, "Вопрос для исследования")
        if confirmed is not True:
            raise ValueError("Нужно согласие на сохранение исследовательского вопроса.")
        gap = self.knowledge_gap(question)
        fingerprint = hashlib.sha256(question.casefold().encode("utf-8")).hexdigest()
        with closing(self.store.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO cognitive_next_questions(fingerprint,question,source_note)"
                " VALUES(?,?,?)", (fingerprint,question,gap["reason"]))
            row = db.execute(
                "SELECT id,state FROM cognitive_next_questions WHERE fingerprint=?",
                (fingerprint,)).fetchone()
        return {"id": row["id"], "state": row["state"], "research_started": False}

    def questions(self, limit: int = 30) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Некорректный лимит.")
        with closing(self.store.connect()) as db:
            rows = [dict(x) for x in db.execute(
                "SELECT id,question,source_note,state,created_at "
                "FROM cognitive_next_questions ORDER BY id DESC LIMIT ?", (limit,)
            )]
        return {"items": rows, "remote_actions": 0}

    def question_decide(self, question_id: int, state: str, *,
                        confirmed: bool = False) -> dict:
        if (type(question_id) is not int or question_id <= 0
                or state not in ("approved","dismissed") or confirmed is not True):
            raise ValueError("Некорректное решение или нет подтверждения.")
        with closing(self.store.connect()) as db, db:
            cursor = db.execute(
                "UPDATE cognitive_next_questions SET state=? WHERE id=?",
                (state,question_id))
        if cursor.rowcount != 1:
            raise ValueError("Вопрос не найден.")
        return {"id": question_id, "state": state,
                "external_research_started": False}

    # 7. What-if scenarios. The alternatives are supplied by the user.
    def scenario(self, decision: str, option_a: str, option_b: str) -> dict:
        decision = checked_text(decision, "Решение")
        option_a = checked_text(option_a, "Первый сценарий")
        option_b = checked_text(option_b, "Второй сценарий")
        if option_a.casefold() == option_b.casefold():
            raise ValueError("Сценарии должны отличаться.")
        evidence = self.rag.search(decision, limit=6)["items"]
        ids = [x["id"] for x in evidence]
        return {"decision": decision,
                "options": [
                    {"name": "A", "assumption": option_a,
                     "conditional_outcome": "Если выбран A, потребуется проверить последствия и ограничения A."},
                    {"name": "B", "assumption": option_b,
                     "conditional_outcome": "Если выбран B, потребуется проверить последствия и ограничения B."},
                ],
                "supporting_memory_ids": ids,
                "unknowns": ["Вероятности и последствия не вычислялись.",
                             "Для причинного вывода нужны измерения, ограничения и подтверждённые данные."],
                "predictions_verified": False, "external_calls": 0}

    # 8. Memory immunity suggests action; never silently merges or deletes.
    def memory_review(self, limit: int = 100) -> dict:
        if type(limit) is not int or not 2 <= limit <= 200:
            raise ValueError("Некорректный размер проверки.")
        report = self.quality.review(limit=limit)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).date().isoformat()
        with closing(self.store.connect()) as db:
            stale = [dict(x) for x in db.execute(
                "SELECT id,kind,source_type,source_ref,updated_at "
                "FROM memory_items WHERE status='active' AND updated_at<? "
                "ORDER BY updated_at LIMIT 25", (cutoff,)
            )]
        return {"possible_duplicates": report["duplicates"][:15],
                "semantic_duplicates": report["semantic_duplicates"][:15],
                "possible_conflicts": report["conflicts"][:15],
                "possibly_stale": stale,
                "auto_deleted": 0, "auto_corrected": 0, "human_review_required": True,
                "truth_verified": False, "cloud_requests": 0}

    # 9. Identity continuity without writing protected character data.
    def personality_growth(self) -> dict:
        if self.core is None:
            raise ValueError("Personality continuity needs the cognitive core.")
        state = self.core.continuity.state()
        arc = self.core.persona_arc.history(limit=8)
        with closing(self.store.connect()) as db:
            confirmed = [dict(x) for x in db.execute(
                "SELECT s.id,s.guidance,s.message_id,s.updated_at "
                "FROM experience_skill_candidates s "
                "JOIN cognitive_feedback f ON f.message_id=s.message_id "
                "WHERE s.state='approved' AND f.helpful=1 "
                "ORDER BY s.id DESC LIMIT 15"
            )]
        return {"identity": state["identity"],
                "approved_habits": state["stable_habits"],
                "approved_skills": confirmed,
                "historical_snapshots": arc["snapshots"],
                "character_profile_unchanged": True,
                "real_emotions_claimed": False,
                "source": "approved_memories_and_explicit_feedback"}

    # 10. Offline control-task simulator. Caller supplies real candidate answer.
    def simulate(self, strategy: str, skill: str, answer: str,
                 *, confirmed: bool = False) -> dict:
        if not isinstance(strategy, str) or strategy not in STRATEGIES:
            raise ValueError("Неизвестная стратегия.")
        if not isinstance(skill, str) or skill not in QUESTIONS:
            raise ValueError("Неизвестный контрольный тест.")
        answer = checked_text(answer, "Ответ для теста", 2000)
        if confirmed is not True:
            raise ValueError("Для сохранения результата требуется подтверждение.")
        question, keywords, maximum = QUESTIONS[skill]
        normalized = answer.casefold()
        checks = {
            "expected_terms": all(term in normalized for term in keywords),
            "answer_length": len(answer) <= maximum,
        }
        passed = all(checks.values())
        digest = hashlib.sha256(answer.encode("utf-8")).hexdigest()
        with closing(self.store.connect()) as db, db:
            cursor = db.execute(
                "INSERT INTO cognitive_next_simulations"
                "(strategy,skill,answer_hash,passed,checked_rules)"
                " VALUES(?,?,?,?,?)",
                (strategy,skill,digest,int(passed),
                 json.dumps(checks,ensure_ascii=False,sort_keys=True)))
            db.execute(
                "DELETE FROM cognitive_next_simulations WHERE id NOT IN "
                "(SELECT id FROM cognitive_next_simulations ORDER BY id DESC LIMIT 200)")
        return {"id": cursor.lastrowid, "strategy":strategy, "skill":skill,
                "question": question, "passed": passed, "checks": checks,
                "method": "deterministic_keyword_length_smoke_test",
                "real_model_evaluated": False, "real_world_truth_verified": False,
                "auto_activated": False, "cloud_requests": 0}

    def simulations(self, limit: int = 20) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Некорректный лимит.")
        with closing(self.store.connect()) as db:
            runs = [dict(x) for x in db.execute(
                "SELECT id,strategy,skill,passed,created_at "
                "FROM cognitive_next_simulations ORDER BY id DESC LIMIT ?", (limit,)
            )]
        return {"items": runs, "model_evaluated": False,
                "stored_answer_text": False, "cloud_requests": 0}
