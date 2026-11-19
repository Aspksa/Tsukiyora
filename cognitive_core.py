"""Tsukiyora's bounded cognitive maintenance and experience-based behaviour.

Not consciousness or unattended remote agents. A single local worker runs only
while the Python server is open, never sends data to a provider on its own.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import time

from memory_store import MemoryStore
from soul_growth import SoulGrowth
from vector_memory import VectorMemory
from memory_quality import MemoryQuality
from experience_engine import ExperienceEngine
from layered_memory import LayeredMemory
from reliability import Reliability
from maintenance import Maintenance
from reasoning import ReasoningAudit
from precision_memory import PrecisionMemory
from soul_academy import SoulAcademy
from persona_continuity import PersonaContinuity
from brain_atlas import BrainAtlas
from rag_pipeline import RagPipeline
from rag_fusion import RagFusion
from academy_experiments import AcademyExperiments
from academy_metrics import AcademyMetrics
from migration_guard import MigrationGuard
from conversation_memory import ConversationMemory
from semantic_groups import SemanticGroups
from evidence_review import EvidenceReview
from academy_curriculum import AcademyCurriculum
from persona_arc import PersonaArc
from atlas_materializer import AtlasMaterializer
from contract_insights import ContractInsights
from academy_rollback import AcademyRollback
from academy_practice import AcademyPractice
from reflective_recall import ReflectiveRecall
from experience_skills import ExperienceSkills
from reflective_optimization import ReflectiveOptimization
from cognitive_next import CognitiveNext
from self_improvement import SelfImprovement
from cognitive_evolution import CognitiveEvolution
from unified_cognitive import UnifiedCognitiveCore
from skill_forge import SkillForge


class CognitiveCore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.store = MemoryStore(path)
        self.growth = SoulGrowth(path)
        self.vectors = VectorMemory(path)
        self.quality = MemoryQuality(path)
        self.experience = ExperienceEngine(path)
        self.layers = LayeredMemory(path)
        self.reliability = Reliability(path)
        self.scheduler = Maintenance(path)
        self.audit = ReasoningAudit(path)
        self.precision = PrecisionMemory(path)
        self.academy = SoulAcademy(path)
        self.continuity = PersonaContinuity(path)
        self.atlas = BrainAtlas(path)
        self.rag = RagPipeline(path)
        self.fusion = RagFusion(path)
        self.experiments = AcademyExperiments(path)
        self.metrics = AcademyMetrics(path)
        self.migrations = MigrationGuard(path)
        self.dialogues = ConversationMemory(path)
        self.semantic_groups = SemanticGroups(path)
        self.evidence = EvidenceReview(path)
        self.curriculum = AcademyCurriculum(path)
        self.persona_arc = PersonaArc(path)
        self.materializer = AtlasMaterializer(path)
        self.contracts = ContractInsights(path)
        self.rollback = AcademyRollback(path)
        self.practice = AcademyPractice(path)
        self.reflection = ReflectiveRecall(path)
        self.skills = ExperienceSkills(path)
        self.evolution = ReflectiveOptimization(path)
        self.next = CognitiveNext(path, core=self)
        self.improvement = SelfImprovement(path)
        self.evolution4 = CognitiveEvolution(path)
        self.orchestrator = UnifiedCognitiveCore(path)
        self.forge = SkillForge(path)
        self.chat_priority = None

    def connect(self):
        return self.store.connect()

    def initialize(self):
        self.vectors.initialize()
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cognitive_feedback (
                    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                    helpful INTEGER NOT NULL CHECK (helpful IN (0,1)),
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS cognitive_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL CHECK(status IN ('ok','warning')),
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS cognitive_lessons (
                    name TEXT PRIMARY KEY,
                    guidance TEXT NOT NULL,
                    evidence_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
        self.experience.initialize()
        self.layers.initialize()
        self.reliability.initialize()
        self.scheduler.initialize()
        self.audit.initialize()
        self.precision.initialize()
        self.academy.initialize()
        self.continuity.initialize()
        self.metrics.initialize()
        self.experiments.initialize()
        self.evidence.initialize()
        self.curriculum.initialize()
        self.persona_arc.initialize()
        self.rollback.initialize()
        self.practice.initialize()
        self.materializer.initialize()
        self.dialogues.initialize()
        self.semantic_groups.initialize()
        self.skills.initialize()
        self.evolution.initialize()
        self.next.initialize()
        self.improvement.initialize()
        self.evolution4.initialize()
        self.orchestrator.initialize()
        self.forge.initialize()

    def feedback(self, message_id: int, helpful: bool, note: str = "") -> dict:
        if type(message_id) is not int or message_id <= 0 or type(helpful) is not bool:
            raise ValueError("Некорректная оценка ответа.")
        if not isinstance(note, str) or len(note) > 400:
            raise ValueError("Отзыв должен быть не длиннее 400 символов.")
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT role FROM messages WHERE id=?", (message_id,)).fetchone()
            if row is None or row["role"] != "assistant":
                raise ValueError("Оценить можно только сохранённый ответ Цукиёры.")
            db.execute("""
                INSERT INTO cognitive_feedback(message_id,helpful,note)
                VALUES(?,?,?) ON CONFLICT(message_id) DO UPDATE SET
                helpful=excluded.helpful, note=excluded.note, updated_at=CURRENT_TIMESTAMP
            """, (message_id, int(helpful), note.strip()))
        category = self.experience.tag_feedback(message_id, note)
        self.update_lessons()
        self.academy.run_cycle()
        self.metrics.evaluate()
        experiment = self.experiments.record(message_id, helpful)
        curriculum = self.curriculum.advance()
        rollback = self.rollback.evaluate()
        skill = self.skills.capture(message_id)
        evolution = self.evolution.record(message_id, helpful)
        improvement = self.improvement.feedback(message_id, helpful)
        evolved = self.evolution4.feedback(message_id, helpful)
        forge_result = self.forge.feedback(message_id, helpful)
        evolution4_drafts = self.evolution4.observe()
        hypothesis = self.improvement.observe()
        return {"message_id": message_id, "helpful": helpful, "category": category,
                "skill_candidate": skill, "strategy_trial": evolution,
                "self_improvement": improvement, "new_hypothesis": hypothesis,
                "evolution4_feedback": evolved,
                "skill_forge": forge_result,
                "evolution4_drafts": evolution4_drafts,
                "suspended_styles":rollback.get("suspended",[]),
                "curriculum_review_required": len(curriculum.get("regressions",[])),
                "experiment":experiment,
                "recorded": True}

    def update_lessons(self):
        """Restrained, reversible response-format learning from explicit feedback."""
        with closing(self.connect()) as db:
            rows = [dict(x) for x in db.execute(
                "SELECT helpful FROM cognitive_feedback ORDER BY updated_at DESC,"
                " message_id DESC LIMIT 12"
            )]
        negative = sum(x["helpful"] == 0 for x in rows)
        positive = sum(x["helpful"] == 1 for x in rows)
        guidance = None
        if len(rows) >= 4 and negative >= 3 and negative > positive:
            guidance = "Отвечай точнее, короче, указывай допущения и задавай только необходимые вопросы."
        elif len(rows) >= 5 and positive >= 4 and positive > negative:
            guidance = "Сохраняй ясную структуру и конкретные примеры в ответах."
        with closing(self.connect()) as db, db:
            if guidance:
                db.execute("""
                    INSERT INTO cognitive_lessons(name,guidance,evidence_count)
                    VALUES('answer_style',?,?) ON CONFLICT(name) DO UPDATE SET
                    guidance=excluded.guidance, evidence_count=excluded.evidence_count,
                    updated_at=CURRENT_TIMESTAMP
                """, (guidance, max(negative, positive)))
            else:
                db.execute("DELETE FROM cognitive_lessons WHERE name='answer_style'")

    def guidance(self) -> str:
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT guidance FROM cognitive_lessons WHERE name='answer_style'"
            ).fetchone()
        baseline = row["guidance"] if row else ""
        tagged = self.experience.guidance()
        return (baseline + " " + tagged + " " + self.academy.guidance()
                + " " + self.skills.guidance()).strip()[:1250]

    def biography(self) -> dict:
        """Explicit approved biography, stable persona and dated experience."""
        with closing(self.connect()) as db:
            approved = [dict(x) for x in db.execute(
                "SELECT id,kind,content,source_type,source_ref,created_at "
                "FROM memory_items WHERE status='active' "
                "AND kind IN ('biography','preference') "
                "ORDER BY id DESC LIMIT 80"
            )]
            events = [dict(x) for x in db.execute(
                "SELECT id,event,detail,created_at FROM soul_journal "
                "ORDER BY id DESC LIMIT 35"
            )]
        return {"identity": "Tsukiyora", "persona_version": "0.1.0",
                "approved_history": approved, "experience": events,
                "relationship_history": [
                    {"event": e["event"], "created_at": e["created_at"], "source_ref": e.get("source_ref")}
                    for e in events if e["event"] in ("conversation_completed", "memory_approved")
                ],
                "tone_model": self.experience.state(),
                "learned_guidance": self.guidance()}

    def graph(self, node_limit: int = 2400) -> dict:
        if type(node_limit) is not int or not 1 <= node_limit <= 4000:
            raise ValueError("Некорректный размер графа.")
        with closing(self.connect()) as db:
            nodes = [dict(x) for x in db.execute(
                "SELECT id,kind,substr(content,1,150) AS preview,created_at "
                "FROM memory_items WHERE status='active' ORDER BY id DESC LIMIT ?",
                (node_limit,)
            )]
            ids = {n["id"] for n in nodes}
            links = [dict(x) for x in db.execute(
                "SELECT source_id,target_id,relation FROM memory_links "
                "ORDER BY created_at DESC LIMIT 3500"
            )]
            total = db.execute(
                "SELECT count(*) FROM memory_items WHERE status='active'"
            ).fetchone()[0]
            pending = db.execute(
                "SELECT count(*) FROM memory_suggestions WHERE state='pending'"
            ).fetchone()[0]
        kinds = {}
        for node in nodes:
            kinds[node["kind"]] = kinds.get(node["kind"], 0) + 1
        return {"nodes": nodes,
                "edges": [x for x in links if x["source_id"] in ids and x["target_id"] in ids][:2400],
                "pending": pending, "total": total, "clusters": kinds,
                "truncated": total > len(nodes)}

    def status(self) -> dict:
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT id,status,summary,created_at FROM cognitive_cycles "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            reviews = db.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(helpful),0) AS likes "
                "FROM cognitive_feedback"
            ).fetchone()
            conflicts = db.execute(
                "SELECT count(*) FROM memory_links WHERE relation='possible_conflict'"
            ).fetchone()[0]
            pending = db.execute(
                "SELECT count(*) FROM memory_suggestions WHERE state='pending'"
            ).fetchone()[0]
        return {"last_cycle": dict(row) if row else None,
                "feedback": {"total": reviews["n"], "helpful": reviews["likes"]},
                "possible_conflicts": conflicts, "pending": pending,
                "guidance": self.guidance(), "vectors": self.vectors.stats(),
                "experience": self.experience.state(),
                "academy": self.academy.status(limit=5),
                "academy_progress": self.metrics.status(),
                "academy_experiments": self.experiments.report(),
                "academy_curriculum": self.curriculum.status(),
                "academy_rollback": self.rollback.status(),
                "academy_practice": self.practice.status(),
                "experience_skills": self.skills.list(limit=10),
                "prompt_evolution": self.evolution.report(),
                "cognitive_next": self.next.working_state(),
                "self_improvement": self.improvement.report(),
                "evolution4": self.evolution4.report(),
                "unified_cognitive": self.orchestrator.status(self.scheduler),
                "skill_forge": self.forge.status(),
                "atlas_aggregate": self.materializer.status(),
                "persona_arc": self.persona_arc.history(limit=3),
                "character": {
                    "continuity": "source_grounded",
                    "real_emotions": False,
                    "stable_habits": len(self.continuity.state()["stable_habits"])
                },
                "precision": {"fts5": self.precision.has_fts},
                "dialogue_memory": self.dialogues.health(),
                "semantic_groups": self.semantic_groups.health(),
                "maintenance": self.scheduler.pending()}

    def cycle(self) -> dict:
        """Compatibility entrypoint: one idempotent local aggregate, not a
        second full maintenance pass. Actual repairs/learning have one
        persistent owner: Maintenance.JOBS via UnifiedCognitiveCore.
        """
        try:
            result=self.orchestrator.digest(self)
            return {"status":"ok",
                    "summary":json.dumps(result,ensure_ascii=False)}
        except (sqlite3.Error,ValueError,OSError):
            return {"status":"warning",
                    "summary":"Не удалось сохранить локальную сводку."}


class CognitiveWorker:
    """One daemon thread per local server, with responsive shutdown."""
    def __init__(self, core: CognitiveCore, interval_seconds: int = 900):
        self.core = core
        self.interval = max(30, int(interval_seconds))
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="tsukiyora-cognitive", daemon=True)
        self._thread.start()

    def _run(self):
        # ONE worker, ONE persistent maintenance queue. Startup is delayed
        # to prioritize HTTP; all tasks are local and stop with this process.
        if self._stop.wait(3):
            return
        while not self._stop.is_set():
            try:
                self.core.orchestrator.tick(
                    self.core,chat_lock=self.core.chat_priority,
                    max_millis=850)
            except (OSError,sqlite3.Error,ValueError):
                # Status and backoff are tracked by the SQLite queue.
                pass
            if self._stop.wait(15):
                return

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
