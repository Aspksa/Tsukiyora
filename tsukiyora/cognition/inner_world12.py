"""Tsukiyora 12.0 — source-linked local inner-world notebook.

A deterministic, auditable *simulation of cognitive work*, not subjective
consciousness, a model's private chain of thought, an always-running agent or
autonomous neural-network training. Reuses the one existing SQLite job queue,
V7 skill gates, V8 goals/permissions/journal, V9 initiative, V10 dialogue,
V11 memory and PersonaContinuity/PersonaArc. No Cloud.ru access.

User-selected features: 1,2,3,5-20 (EXCLUDES #4, an emotional-state model).
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json

from tsukiyora.memory.memory_store import MemoryStore

MAX_NOTES = 280
MAX_NEW_PER_TICK = 8

# Fixed review templates only. Source text, personal messages, documents and
# generated LLM reasoning are never copied into the background notebook.
KINDS = {
    "check_memory": ("memory_suggestions", 90,
                     "Проверить предложение записи в память; нужен источник и твоё решение.",
                     "Какие доказательства нужны для этой записи?"),
    "check_conflict": ("cognitive_v5_reviews", 95,
                       "Проверить возможное противоречие в памяти; автоматического исправления нет.",
                       "Какие версии сведений необходимо сопоставить?"),
    "reflect_feedback": ("cognitive_feedback", 80,
                         "Изучить отрицательную оценку ответа и проверить подход к объяснению.",
                         "Какая проверка покажет, стало ли объяснение точнее?"),
    "review_academy": ("soul8_goals", 70,
                       "Учебная цель ожидает проверки; использовать прежнюю SOUL ACADEMY.",
                       "Есть ли результаты контрольных примеров без регрессий?"),
    "test_strategy": ("forge_skills", 75,
                      "Навык-кандидат нуждается в проверках V7 и ручном одобрении.",
                      "Собраны ли доказательства для испытания стратегии?"),
    "study_question": ("cognitive_next_questions", 65,
                       "Исследовательский вопрос ожидает решения; внешнее исследование не начато.",
                       "Каких проверяемых источников ещё не хватает?"),
    "review_style": ("soul8_proposals", 60,
                     "Предложение манеры общения требует одобрения; личность защищена.",
                     "Есть ли достаточная явная обратная связь о стиле?"),
    "continue_goal": ("soul8_goals", 55,
                      "Есть незавершённая разрешённая локальная цель.",
                      "Какой следующий ограниченный шаг уже предусмотрен планом?"),
    "continuity": ("memory_items", 40,
                   "Подтверждённая пользователем запись может участвовать в хронологии.",
                   "Как связать событие с исходным источником без выдуманных деталей?"),
}
# These are fixed SQL templates; no dynamically constructed table identifiers
# ever come from a request, a source row or untrusted chat.
SOURCES = {
    "check_memory": (
        "SELECT id FROM memory_suggestions WHERE state='pending' ORDER BY id DESC LIMIT 2",
        "SELECT 1 FROM memory_suggestions WHERE id=? AND state='pending'"),
    "check_conflict": (
        "SELECT id FROM cognitive_v5_reviews WHERE state='pending' ORDER BY id DESC LIMIT 2",
        "SELECT 1 FROM cognitive_v5_reviews WHERE id=? AND state='pending'"),
    "reflect_feedback": (
        "SELECT f.message_id AS id FROM cognitive_feedback f "
        "JOIN messages m ON m.id=f.message_id AND m.role='assistant' "
        "WHERE f.helpful=0 ORDER BY f.message_id DESC LIMIT 2",
        "SELECT 1 FROM cognitive_feedback f JOIN messages m "
        "ON m.id=f.message_id AND m.role='assistant' "
        "WHERE f.message_id=? AND f.helpful=0"),
    "review_academy": (
        "SELECT id FROM soul8_goals WHERE category='academy' "
        "AND state='waiting_review' ORDER BY id DESC LIMIT 2",
        "SELECT 1 FROM soul8_goals WHERE id=? AND category='academy' "
        "AND state='waiting_review'"),
    "test_strategy": (
        "SELECT id FROM forge_skills WHERE state='shadow' ORDER BY id DESC LIMIT 2",
        "SELECT 1 FROM forge_skills WHERE id=? AND state='shadow'"),
    "study_question": (
        "SELECT id FROM cognitive_next_questions WHERE state='pending' "
        "ORDER BY id DESC LIMIT 2",
        "SELECT 1 FROM cognitive_next_questions WHERE id=? AND state='pending'"),
    "review_style": (
        "SELECT id FROM soul8_proposals WHERE kind='style' "
        "AND state='pending' ORDER BY id DESC LIMIT 2",
        "SELECT 1 FROM soul8_proposals WHERE id=? AND kind='style' "
        "AND state='pending'"),
    "continue_goal": (
        "SELECT id FROM soul8_goals WHERE state='active' AND progress>=1 "
        "ORDER BY updated_at DESC,id DESC LIMIT 2",
        "SELECT 1 FROM soul8_goals WHERE id=? AND state='active' "
        "AND progress>=1"),
    "continuity": (
        "SELECT id FROM memory_items WHERE status='active' "
        "AND kind IN ('event','biography') ORDER BY id DESC LIMIT 2",
        "SELECT 1 FROM memory_items WHERE id=? AND status='active' "
        "AND kind IN ('event','biography')"),
}
# V13: paginated source scans. Fixed SQL only, monotonically increasing IDs:
# no repeated "newest two" starvation and no rescanning pruned notes.
SOURCE_STREAMS = {
    "check_memory":
        "SELECT id FROM memory_suggestions WHERE state='pending' AND id>? "
        "ORDER BY id LIMIT 2",
    "check_conflict":
        "SELECT id FROM cognitive_v5_reviews WHERE state='pending' AND id>? "
        "ORDER BY id LIMIT 2",
    "reflect_feedback":
        "SELECT f.message_id AS id FROM cognitive_feedback f "
        "JOIN messages m ON m.id=f.message_id AND m.role='assistant' "
        "WHERE f.helpful=0 AND f.message_id>? ORDER BY f.message_id LIMIT 2",
    "review_academy":
        "SELECT id FROM soul8_goals WHERE category='academy' "
        "AND state='waiting_review' AND id>? ORDER BY id LIMIT 2",
    "test_strategy":
        "SELECT id FROM forge_skills WHERE state='shadow' AND id>? "
        "ORDER BY id LIMIT 2",
    "study_question":
        "SELECT id FROM cognitive_next_questions WHERE state='pending' "
        "AND id>? ORDER BY id LIMIT 2",
    "review_style":
        "SELECT id FROM soul8_proposals WHERE kind='style' "
        "AND state='pending' AND id>? ORDER BY id LIMIT 2",
    "continue_goal":
        "SELECT id FROM soul8_goals WHERE state='active' AND progress>=1 "
        "AND id>? ORDER BY id LIMIT 2",
    "continuity":
        "SELECT id FROM memory_items WHERE status='active' "
        "AND kind IN ('event','biography') AND id>? ORDER BY id LIMIT 2",
}
TOPICS = {
    "check_memory": ("памят", "вспом", "факт"),
    "check_conflict": ("памят", "факт", "противореч"),
    "reflect_feedback": ("ответ", "ошиб", "качество"),
    "review_academy": ("обуч", "академ", "урок", "учеб"),
    "test_strategy": ("навык", "стратег", "тест", "провер"),
    "study_question": ("исслед", "вопрос", "изучи"),
    "review_style": ("личност", "характер", "тон", "стиль", "общен"),
    "continue_goal": ("проект", "задач", "продолж", "цель"),
    "continuity": ("биограф", "истори", "событ", "помни"),
}


class InnerWorld12:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS soul12_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    source_table TEXT NOT NULL,
                    source_id INTEGER NOT NULL CHECK(source_id>0),
                    status TEXT NOT NULL DEFAULT 'open'
                        CHECK(status IN ('open','dismissed')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TEXT,
                    UNIQUE(kind,source_id)
                );
                CREATE INDEX IF NOT EXISTS soul12_notes_status
                    ON soul12_notes(status,id DESC);
                CREATE TABLE IF NOT EXISTS soul13_source_cursors (
                    kind TEXT PRIMARY KEY,
                    last_id INTEGER NOT NULL DEFAULT 0 CHECK(last_id>=0)
                );
                CREATE TABLE IF NOT EXISTS soul13_turn (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    next_kind INTEGER NOT NULL DEFAULT 0 CHECK(next_kind>=0)
                );
                INSERT OR IGNORE INTO soul13_turn(id,next_kind) VALUES(1,0);
            """)

    def _source_current(self, db, kind, source_id):
        if kind not in SOURCES or type(source_id) is not int or source_id < 1:
            return False
        return db.execute(SOURCES[kind][1], (source_id,)).fetchone() is not None

    def cycle(self, core):
        """Single bounded, idempotent local task. Prioritizes actual evidence.

        An existing V8 permission can stop new local work. Nothing is promoted
        to trusted fact/goal/skill, no text is inferred from a source, no
        programs or LLMs are executed. No parallel worker is created.
        """
        settings = core.living8.settings()
        if settings["autonomy_level"] < 2 or not (
            settings["local_permissions"].get("local_practice", False)):
            return {"paused": True, "reason": "local_practice_not_permitted",
                    "cloud_calls": 0, "new_notes": 0}
        with closing(self.store.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                total = 0
                checked = 0
                kinds = tuple(KINDS)
                rotation = db.execute(
                    "SELECT next_kind FROM soul13_turn WHERE id=1"
                ).fetchone()[0] % len(kinds)
                visited = 0
                for offset in range(len(kinds)):
                    kind = kinds[(rotation + offset) % len(kinds)]
                    visited += 1
                    if total >= MAX_NEW_PER_TICK:
                        break
                    db.execute(
                        "INSERT OR IGNORE INTO soul13_source_cursors(kind,last_id)"
                        " VALUES(?,0)", (kind,)
                    )
                    cursor = db.execute(
                        "SELECT last_id FROM soul13_source_cursors WHERE kind=?",
                        (kind,)
                    ).fetchone()[0]
                    scanned_to = cursor
                    for row in db.execute(SOURCE_STREAMS[kind], (cursor,)):
                        checked += 1
                        scanned_to = max(scanned_to, row["id"])
                        cur = db.execute(
                            "INSERT OR IGNORE INTO soul12_notes "
                            "(kind,source_table,source_id) VALUES(?,?,?)",
                            (kind, KINDS[kind][0], row["id"])
                        )
                        total += cur.rowcount
                        if total >= MAX_NEW_PER_TICK:
                            break
                    if scanned_to > cursor:
                        db.execute(
                            "UPDATE soul13_source_cursors SET last_id=? "
                            "WHERE kind=?", (scanned_to, kind)
                        )
                # Rotate the first category next time to prevent sustained
                # high-volume sources starving rarer questions and goals.
                db.execute(
                    "UPDATE soul13_turn SET next_kind=? WHERE id=1",
                    ((rotation + max(1, visited)) % len(kinds),)
                )
                # Already-scanned sources cannot be recreated after pruning,
                # because the durable per-kind cursor moves forward only.
                # Remove only notebook metadata. Never delete original evidence,
                # user messages, existing goals, approvals, or saved facts.
                db.execute(
                    "DELETE FROM soul12_notes WHERE id NOT IN "
                    "(SELECT id FROM soul12_notes ORDER BY id DESC LIMIT ?)",
                    (MAX_NOTES,))
                counts = self._counts(db)
                today = datetime.now(timezone.utc).date().isoformat()
                recorded = db.execute(
                    "SELECT 1 FROM soul8_journal "
                    "WHERE event='inner12_daily' AND source_type='day' "
                    "AND source_ref=? LIMIT 1", (today,)
                ).fetchone()
                if not recorded:
                    core.living8._journal(
                        db, "inner12_daily", "day", today,
                        {**counts, "first_cycle_sources": checked})
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"new_notes": total, "sources_checked": checked,
                "daily_already_recorded": bool(recorded),
                "counts": counts, "cloud_calls": 0,
                "new_workers": 0, "new_skills_activated": 0,
                "facts_approved": 0, "persona_modified": False}

    @staticmethod
    def _counts(db):
        rows = db.execute(
            "SELECT status,COUNT(*) n FROM soul12_notes GROUP BY status"
        ).fetchall()
        statuses = {x["status"]: x["n"] for x in rows}
        return {
            "open_notes": statuses.get("open", 0),
            "dismissed_notes": statuses.get("dismissed", 0),
            "pending_memory_reviews": db.execute(
                "SELECT COUNT(*) FROM memory_suggestions WHERE state='pending'"
            ).fetchone()[0],
            "pending_questions": db.execute(
                "SELECT COUNT(*) FROM cognitive_next_questions WHERE state='pending'"
            ).fetchone()[0],
            "shadow_skills": db.execute(
                "SELECT COUNT(*) FROM forge_skills WHERE state='shadow'"
            ).fetchone()[0],
            "pending_persona_proposals": db.execute(
                "SELECT COUNT(*) FROM soul8_proposals WHERE state='pending'"
            ).fetchone()[0],
            "queue_warning_jobs": db.execute(
                "SELECT COUNT(*) FROM maintenance_queue WHERE failures>0"
            ).fetchone()[0],
        }

    def notes(self, *, limit=20):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Неверное число рабочих заметок.")
        with closing(self.store.connect()) as db:
            rows = [dict(x) for x in db.execute(
                "SELECT id,kind,source_table,source_id,status,created_at,reviewed_at "
                "FROM soul12_notes ORDER BY id DESC LIMIT ?", (min(100, limit*3),)
            )]
            items = []
            for note in rows:
                if note["kind"] not in KINDS:
                    continue
                # If a source was rejected, corrected, deleted or completed,
                # the old entry must not be presented as an actionable thought.
                active = self._source_current(db, note["kind"], note["source_id"])
                if note["status"] != "open" or not active:
                    continue
                template = KINDS[note["kind"]]
                items.append({
                    "id": note["id"], "kind": note["kind"],
                    "source_type": note["source_table"],
                    "source_id": note["source_id"],
                    "label": template[2], "question": template[3],
                    "priority": template[1], "created_at": note["created_at"],
                    "source_current": True, "requires_user_review": True,
                    "not_model_private_reasoning": True,
                })
                if len(items) >= limit:
                    break
        items.sort(key=lambda x: (-x["priority"], -x["id"]))
        return {"items": items, "cloud_calls": 0,
                "source_text_copied": False, "auto_approved": False,
                "note_type": "fixed_source_linked_working_note"}

    def inspect(self, core, note_id):
        """Read-only bounded rule check, not generated private reasoning."""
        if type(note_id) is not int or note_id <= 0:
            raise ValueError("Некорректный ID заметки.")
        with closing(self.store.connect()) as db:
            row = db.execute(
                "SELECT id,kind,source_id,status FROM soul12_notes WHERE id=?",
                (note_id,)
            ).fetchone()
            if row is None or row["kind"] not in KINDS:
                raise ValueError("Заметка не найдена.")
            active = self._source_current(db, row["kind"], row["source_id"])
            evidence = {}
            if row["kind"] == "test_strategy" and active:
                values = db.execute(
                    "SELECT COUNT(*) n,"
                    "COALESCE(SUM(candidate_correct),0) candidate,"
                    "COALESCE(SUM(baseline_correct),0) baseline "
                    "FROM soul_v7_cases WHERE skill_id=?", (row["source_id"],)
                ).fetchone()
                evidence = {"control_cases": values["n"],
                            "candidate_correct": values["candidate"],
                            "baseline_correct": values["baseline"],
                            "reliable_llm_test": False}
            elif row["kind"] == "reflect_feedback" and active:
                evidence = {"negative_rating_saved": True,
                            "cause_not_independently_known": True}
            elif row["kind"] == "continue_goal" and active:
                goal = db.execute(
                    "SELECT progress,state FROM soul8_goals WHERE id=?",
                    (row["source_id"],)
                ).fetchone()
                evidence = {"local_checkpoints": goal["progress"] if goal else 0}
            else:
                evidence = {"source_exists_and_still_requires_attention": active}
        return {"id": note_id, "kind": row["kind"],
                "source_id": row["source_id"], "source_current": active,
                "state": row["status"], "checks": evidence,
                "new_strategy_activated": False, "cloud_calls": 0,
                "not_a_real_model_simulation": True}

    def dismiss(self, note_id, *, confirmed=False):
        if type(note_id) is not int or note_id <= 0 or confirmed is not True:
            raise ValueError("Закрытие заметки требует явного подтверждения.")
        with closing(self.store.connect()) as db, db:
            n = db.execute(
                "UPDATE soul12_notes SET status='dismissed',"
                "reviewed_at=CURRENT_TIMESTAMP WHERE id=? AND status='open'",
                (note_id,)
            ).rowcount
            if n != 1:
                raise ValueError("Заметка не найдена или уже закрыта.")
        return {"id": note_id, "dismissed": True,
                "source_modified": False, "skill_approved": False}

    def chat_hint(self, core, query, *, max_chars=240):
        """Only during a user-initiated chat; no unsolicited autonomous texts."""
        if not isinstance(query, str) or len(query) > 8000:
            raise ValueError("Неверный запрос.")
        if type(max_chars) is not int or not 100 <= max_chars <= 260:
            raise ValueError("Неверный лимит контекста.")
        settings = core.living8.settings()
        if settings["autonomy_level"] < 3 or not (
                settings["local_permissions"].get("proactive_chat", False)):
            return ""
        lowered = query.casefold()
        candidates = self.notes(limit=20)["items"]
        match = next((n for n in candidates
                      if any(w in lowered for w in TOPICS[n["kind"]])), None)
        if match is None:
            return ""
        return (
            "Разрешённая локальная заметка #" + str(match["id"]) +
            " (" + match["source_type"] + " #" + str(match["source_id"]) +
            "): " + match["label"] +
            " Это предложение к проверке, НЕ выполненное действие, "
            "НЕ подтверждённый факт. Не навязывай тему."
        )[:max_chars]

    def status(self, core):
        settings = core.living8.settings()
        with closing(self.store.connect()) as db:
            counts = self._counts(db)
            last = db.execute(
                "SELECT source_ref,counters_json,created_at FROM soul8_journal "
                "WHERE event='inner12_daily' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            due = db.execute(
                "SELECT kind,state,executions,failures,last_status "
                "FROM maintenance_queue WHERE kind='cog12_reflect'"
            ).fetchone()
            latest = db.execute(
                "SELECT COUNT(*) FROM persona_arc"
            ).fetchone()[0]
            approved = db.execute(
                "SELECT COUNT(*) FROM soul8_styles"
            ).fetchone()[0]
        # Built-in V8 policy and existing V7 educational checks are reused,
        # not another "feeling" state and not another approval authority.
        return {
            "version": "12.0", "name": "Living Mind & Inner World",
            "selected_features": [1,2,3,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20],
            "omitted_feature_4": True,
            "runtime": {"existing_sqlite_queue": True, "worker_count": 1,
                        "works_only_while_app_running": True,
                        "chat_has_priority": True,
                        "new_paid_cloud_calls": 0},
            "state": {"active_goals_and_review_items": counts,
                      "autonomy_level": settings["autonomy_level"],
                      "local_practice_enabled": settings["local_permissions"].get(
                          "local_practice", False),
                      "proactive_chat_enabled": settings["local_permissions"].get(
                          "proactive_chat", False),
                      "style_adjustments_approved": approved,
                      "persona_arc_snapshots": latest},
            "last_diary_entry": ({
                "utc_day": last["source_ref"],
                "counters": json.loads(last["counters_json"]),
                "recorded_at": last["created_at"]
            } if last else None),
            "queue_job": dict(due) if due else None,
            "thoughts_are": "transparent_rule_based_source_linked_working_notes",
            "real_consciousness_proven": False,
            "real_emotions_proven": False,
            "automatic_memory_writes": False,
            "automatic_skill_activation": False,
            "protected_persona_unchanged": True,
            "duplicated_background_work": False,
        }
