"""Tsukiyora 13.0: ONE read-only view of existing cognition and safe tests.

No new agent, worker, personality authority, queue or Cloud.ru billing.
Uses already approved facts, V7 evaluations, V8 style/permissions, V9
continuity, V10 conversation, V11 retrieval, V12 notebook and V5 reviews.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import re
import statistics
import time

from tsukiyora.memory.memory_store import MemoryStore

SHORT = {"да", "нет", "продолжай", "дальше", "ещё", "еще", "не то",
         "как раньше", "сделай так", "переделай", "повтори"}
PATTERNS = {
    "missing_source": re.compile(r"источник|ссылк|доказател|без фактов", re.I),
    "memory": re.compile(r"памят|не помн|вспом|неправда|устар", re.I),
    "dialogue": re.compile(r"не понял|контекст|не то|повтор|разговор", re.I),
    "style": re.compile(r"тон|груб|нежн|слишком длин|кратк|сухо", re.I),
}


class CognitiveCoordinator13:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cognitive13_eval_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL CHECK(length(question)<=500),
                    expected_memory_id INTEGER NOT NULL REFERENCES memory_items(id),
                    note TEXT NOT NULL CHECK(length(note)<=240),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(question,expected_memory_id)
                );
                CREATE INDEX IF NOT EXISTS idx_cog13_eval_source
                    ON cognitive13_eval_cases(expected_memory_id,id DESC);
            """)

    def review_board(self, *, limit=30):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Неверный размер очереди проверок.")
        # A unified read-only view; no automatic acceptance, no additional
        # authority and no copies of raw private messages.
        specs = [
            ("memory", "memory_suggestions",
             "SELECT id,message_id AS source_id FROM memory_suggestions "
             "WHERE state='pending' ORDER BY id DESC LIMIT 25"),
            ("conflict", "cognitive_v5_reviews",
             "SELECT id,memory_id AS source_id FROM cognitive_v5_reviews "
             "WHERE state='pending' ORDER BY id DESC LIMIT 25"),
            ("skill", "forge_skills",
             "SELECT id,id AS source_id FROM forge_skills WHERE state='shadow' "
             "ORDER BY id DESC LIMIT 25"),
            ("personality", "soul8_proposals",
             "SELECT id,source_id FROM soul8_proposals WHERE state='pending' "
             "ORDER BY id DESC LIMIT 25"),
            ("goal", "soul8_goals",
             "SELECT id,id AS source_id FROM soul8_goals "
             "WHERE state='waiting_review' ORDER BY id DESC LIMIT 25"),
            ("question", "cognitive_next_questions",
             "SELECT id,id AS source_id FROM cognitive_next_questions "
             "WHERE state='pending' ORDER BY id DESC LIMIT 25"),
        ]
        items = []
        seen = set()
        with closing(self.store.connect()) as db:
            for kind, table, query in specs:
                for row in db.execute(query):
                    # If two systems refer to the *same canonical source*
                    # and request the same sort of review, show it once.
                    canon = ("memory_items", row["source_id"]) if kind=="conflict" else (
                        "messages",row["source_id"]) if kind=="memory" else (
                        table, row["id"])
                    key = (kind, canon)
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append({
                        "kind": kind, "table": table, "record_id": row["id"],
                        "source_id": row["source_id"],
                        "action": "manual_review_only", "approved": False,
                    })
        return {"items": items[:limit], "sampled_count": len(items),
                "may_have_more": len(items) > limit,
                "duplicate_keys_hidden": True,
                "has_external_actions": False, "cloud_calls": 0}

    def issue_signals(self):
        """Privacy-preserving lexical hypotheses over existing rated notes."""
        with closing(self.store.connect()) as db:
            notes = [r["note"] for r in db.execute(
                "SELECT note FROM cognitive_feedback "
                "WHERE helpful=0 ORDER BY message_id DESC LIMIT 100"
            )]
        reasons = {kind: 0 for kind in PATTERNS}
        other = 0
        for note in notes:
            categories = [kind for kind, pat in PATTERNS.items()
                          if pat.search(note)]
            if not categories:
                other += 1
            else:
                for kind in categories:
                    reasons[kind] += 1
        return {
            "negative_feedback_scanned": len(notes),
            "possible_categories": reasons,
            "unclassified": other,
            "method": "local_lexical_signals_not_proven_root_causes",
            "raw_feedback_exposed": False, "cloud_calls": 0,
        }

    @staticmethod
    def followup_hint(query, recent_history, *, max_chars=260):
        if not isinstance(query,str) or len(query)>8000:
            raise ValueError("Неверный вопрос.")
        if not isinstance(recent_history,list) or type(max_chars) is not int or (
            not 100 <= max_chars <= 320):
            raise ValueError("Неверный контекст.")
        if query.casefold().strip(" \t\n.!?") not in SHORT:
            return ""
        for row in reversed(recent_history[-16:]):
            if not isinstance(row, dict):
                continue
            if (row.get("role")!="user" or
                type(row.get("id")) is not int or row["id"]<=0 or
                not isinstance(row.get("content"),str)):
                continue
            content = " ".join(row["content"].split())
            if not content or content.casefold().strip(" \t\n.!?")==query.casefold().strip(" \t\n.!?"):
                continue
            return (
                "Короткая реплика пользователя относится, возможно, к "
                "предыдущей теме. Уточни при неоднозначности; не выдумывай "
                "выполненные действия. Ранее [Сообщение #"+str(row["id"])+
                "]: "+content[:115]
            )[:max_chars]
        return ""

    def add_eval_case(self, question, expected_memory_id, *, note="", confirmed=False):
        if (not isinstance(question,str) or not 3 <= len(question.strip())<=500
                or type(expected_memory_id) is not int or expected_memory_id <= 0
                or not isinstance(note,str) or len(note)>240
                or confirmed is not True):
            raise ValueError("Нужно указать вопрос и подтверждённый ID памяти.")
        with closing(self.store.connect()) as db, db:
            active = db.execute(
                "SELECT 1 FROM memory_items WHERE id=? AND status='active'",
                (expected_memory_id,)
            ).fetchone()
            if active is None:
                raise ValueError("Ожидаемое воспоминание должно быть активным.")
            db.execute(
                "INSERT OR IGNORE INTO cognitive13_eval_cases "
                "(question,expected_memory_id,note) VALUES (?,?,?)",
                (question.strip(),expected_memory_id,note.strip())
            )
            row = db.execute(
                "SELECT id FROM cognitive13_eval_cases "
                "WHERE question=? AND expected_memory_id=?",
                (question.strip(),expected_memory_id)
            ).fetchone()
        return {"id": row["id"], "user_attested_expected_answer": True,
                "automatic_model_training": False, "cloud_calls": 0}

    def benchmark(self, core, *, limit=15, k=5):
        """Only explicit user-attested holdout cases, never self-retrieval."""
        if type(limit) is not int or not 1 <= limit <= 50 or (
            type(k) is not int or not 1 <= k <= 10):
            raise ValueError("Некорректные параметры.")
        with closing(self.store.connect()) as db:
            cases = [dict(r) for r in db.execute(
                "SELECT id,question,expected_memory_id "
                "FROM cognitive13_eval_cases ORDER BY id DESC LIMIT ?", (limit,)
            )]
        latencies, hits, attempted = [], 0, 0
        for c in cases:
            # A deleted or corrected source is explicitly excluded, rather
            # than being counted as a correct stale retrieval.
            with closing(self.store.connect()) as db:
                state = db.execute(
                    "SELECT status FROM memory_items WHERE id=?",
                    (c["expected_memory_id"],)
                ).fetchone()
            if state is None or state["status"] != "active":
                continue
            started = time.perf_counter()
            result = core.memory11.search(
                core, c["question"], limit=k, include_history=False
            )
            latencies.append((time.perf_counter()-started)*1000)
            attempted += 1
            hits += int(c["expected_memory_id"] in {
                item["memory_id"] for item in result["items"]})
        return {
            "cases_total": len(cases), "valid_cases": attempted,
            "hits_at_k": hits,
            "recall_at_k": round(hits / attempted, 3) if attempted else None,
            "p50_ms": round(statistics.median(latencies),2) if latencies else None,
            "p95_ms": round(sorted(latencies)[max(
                0,int(len(latencies)*.95)-1)],2) if latencies else None,
            "metric": "user_attested_retrieval_not_llm_answer_accuracy",
            "questions_and_private_sources_logged": False,
            "cloud_calls": 0, "autonomous_eval_creation": False,
        }

    def status(self, core):
        review = self.review_board(limit=30)
        with closing(self.store.connect()) as db:
            queue = db.execute(
                "SELECT COUNT(*) n,SUM(failures>0) warnings "
                "FROM maintenance_queue"
            ).fetchone()
            active = db.execute(
                "SELECT COUNT(*) FROM cognitive13_eval_cases"
            ).fetchone()[0]
        return {
            "version": "13.0", "name": "Coordinated Cognition",
            "selected_improvements": [
                1,2,3,4,6,7,8,9,10,11,12,13,14,15,
                16,17,18,19,20,21,22,23,24,25,26,27,28,29,30],
            "omitted_improvement_5": True,
            "one_worker_one_sqlite": True,
            "queue": {
                "registered_jobs": queue["n"],
                "jobs_with_failures": queue["warnings"] or 0,
                "policy": "static_priority_with_aging; no load_adaptation"},
            "memory": core.memory13.status(),
            "cached_vector_index": core.vector13.status(),
            "working_notebook": core.inner12.status(core)["state"][
                "active_goals_and_review_items"],
            "reviews_sampled": review["sampled_count"],
            "user_attested_holdout_cases": active,
            "source_privacy": "no_private_body_copy_in_board",
            "new_background_model_calls": 0,
            "new_runtime_dependencies": 0,
            "automatic_skill_persona_or_memory_approval": False,
            "measured_intelligence_gain_claimed": False,
        }
