"""Read-only source-backed indicators for conversational and skill quality.

All results are queries over existing SQLite tables. A self-reported V7 pair
is NOT independently verified; positive live ratings are observations, not
causal evidence of improved model quality. Nothing activates a skill.
"""
from __future__ import annotations

from contextlib import closing

from tsukiyora.memory.memory_store import MemoryStore


class GrowthQuality:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def status(self, core, limit=8):
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError("Invalid quality report limit.")
        with closing(self.store.connect()) as db:
            conversations = db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            messages = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            memories = db.execute(
                "SELECT COUNT(*) FROM memory_items WHERE status='active'"
            ).fetchone()[0]
            feedback = db.execute(
                "SELECT COUNT(*) AS total,COALESCE(SUM(helpful),0) AS positive "
                "FROM cognitive_feedback"
            ).fetchone()
            goals = db.execute(
                "SELECT COUNT(*) AS total,"
                "COALESCE(SUM(state='waiting_review'),0) AS review "
                "FROM soul8_goals"
            ).fetchone()
            open_proposals = db.execute(
                "SELECT COUNT(*) FROM soul8_proposals WHERE state='pending'"
            ).fetchone()[0]
            queue_warnings = db.execute(
                "SELECT COUNT(*) FROM maintenance_queue WHERE failures>0"
            ).fetchone()[0]
            ids = [r["id"] for r in db.execute(
                "SELECT id FROM forge_skills ORDER BY id DESC LIMIT ?", (limit,)
            )]
            rated = {}
            for row in db.execute(
                "SELECT skill_id,COUNT(*) AS total,COALESCE(SUM(helpful),0) AS positive "
                "FROM forge_applied WHERE helpful IS NOT NULL "
                "GROUP BY skill_id"
            ):
                rated[row["skill_id"]] = {"total": row["total"], "positive": row["positive"]}
        skills = []
        for skill_id in ids:
            verdict = core.supervisor7.verdict(skill_id)
            observed = rated.get(skill_id, {"total": 0, "positive": 0})
            n = observed["total"]
            approval = verdict["state"] == "approved"
            skills.append({
                "skill_id": skill_id, "domain": verdict["domain"],
                "state": verdict["state"], "version": verdict["version"],
                "attested_cases": verdict["cases"],
                "attested_gain": verdict["net_gain"],
                "attested_regressions": verdict["regressions"],
                "one_model": verdict["one_model"],
                "v7_evidence_ready": verdict["evidence_ready"],
                "real_answer_ratings": n,
                "real_answer_positive": observed["positive"],
                "real_answer_negative": n - observed["positive"],
                "observed_positive_share": round(observed["positive"] / n, 3) if n else None,
                "observed_signal": "insufficient" if n < 5 else "rated_use",
                "manual_approval_recorded": approval,
                "causal_improvement_proven": False,
                "independent_llm_benchmark": False,
                "source_tables": ["soul_v7_cases", "forge_applied", "cognitive_feedback"]
            })
        return {
            "conversation": {"dialogues": conversations, "messages": messages,
                             "source": "conversations/messages"},
            "memory": {"active": memories, "source": "memory_items"},
            "ratings": {"total": feedback["total"], "positive": feedback["positive"],
                        "negative": feedback["total"]-feedback["positive"],
                        "source": "cognitive_feedback"},
            "growth": {"goals": goals["total"], "pending_review": goals["review"],
                       "proposals": open_proposals, "queue_warnings": queue_warnings,
                       "source": "soul8_goals/soul8_proposals/maintenance_queue"},
            "skills": skills,
            "data_origin": "local_sqlite_existing_evidence",
            "no_claim_of_general_intelligence_gain": True,
            "offline_attested_cases_not_independently_verified": True,
            "live_feedback_is_not_controlled_ab_test": True,
            "extra_cloud_calls": 0,
            "changes_or_approvals_performed": 0,
        }
