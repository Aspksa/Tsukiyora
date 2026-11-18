"""XSkill-inspired, reviewable experience-to-skill proposals.

No XSkill dependency, neural fine-tuning or automatic promotion. Only explicit
positive feedback with a useful note can create a candidate. User must confirm
activation; revisions and disabled entries remain locally auditable.
"""
from __future__ import annotations

from contextlib import closing
from memory_store import MemoryStore


class ExperienceSkills:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS experience_skill_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id),
                    guidance TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending'
                        CHECK(state IN ('pending','approved','disabled')),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_experience_skill_state
                    ON experience_skill_candidates(state,id DESC);
            """)

    def capture(self, message_id: int) -> dict:
        """Idempotent candidate creation after a real, rated assistant answer."""
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("Invalid message ID.")
        with closing(self.store.connect()) as db, db:
            row = db.execute(
                "SELECT f.helpful,f.note,m.role FROM cognitive_feedback f "
                "JOIN messages m ON m.id=f.message_id WHERE f.message_id=?",
                (message_id,)
            ).fetchone()
            if row is None or row["role"] != "assistant":
                raise ValueError("Only explicit assistant feedback is eligible.")
            guidance = " ".join(row["note"].split()).strip()[:280]
            existing = db.execute(
                "SELECT id,guidance,state FROM experience_skill_candidates WHERE message_id=?",
                (message_id,)
            ).fetchone()
            if row["helpful"] != 1 or len(guidance) < 8:
                if existing is not None:
                    db.execute(
                        "UPDATE experience_skill_candidates SET state='disabled',"
                        "updated_at=CURRENT_TIMESTAMP WHERE id=?", (existing["id"],)
                    )
                return {"candidate_id": existing["id"] if existing else None,
                        "state": "disabled" if existing else "not_eligible"}
            if existing is None:
                cursor = db.execute(
                    "INSERT INTO experience_skill_candidates(message_id,guidance) "
                    "VALUES(?,?)", (message_id,guidance)
                )
                return {"candidate_id": cursor.lastrowid, "state": "pending"}
            if existing["guidance"] != guidance:
                # A changed note is never silently promoted into a live instruction.
                db.execute(
                    "UPDATE experience_skill_candidates SET guidance=?,state='pending',"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (guidance, existing["id"])
                )
                return {"candidate_id": existing["id"], "state": "pending"}
            return {"candidate_id": existing["id"], "state": existing["state"]}

    def decide(self, candidate_id: int, *, approve: bool, confirmed: bool = False) -> dict:
        if type(candidate_id) is not int or candidate_id <= 0:
            raise ValueError("Invalid candidate ID.")
        if type(approve) is not bool or confirmed is not True:
            raise ValueError("Требуется явное подтверждение решения о навыке.")
        with closing(self.store.connect()) as db, db:
            row = db.execute(
                "SELECT s.id,s.message_id,s.guidance,s.state,f.helpful,f.note "
                "FROM experience_skill_candidates s "
                "JOIN cognitive_feedback f ON f.message_id=s.message_id WHERE s.id=?",
                (candidate_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Candidate not found.")
            if approve:
                # Check consent remains valid; a rescinded positive rating cannot be reused.
                if row["helpful"] != 1 or " ".join(row["note"].split())[:280] != row["guidance"]:
                    raise ValueError("Source feedback changed; review the candidate again.")
                if row["state"] == "disabled":
                    raise ValueError("Disabled candidate needs a new positive review.")
            state = "approved" if approve else "disabled"
            db.execute(
                "UPDATE experience_skill_candidates SET state=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (state,candidate_id)
            )
            return {"candidate_id": candidate_id, "state": state, "confirmed": True}

    def list(self, limit: int = 30) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid listing limit.")
        with closing(self.store.connect()) as db:
            items = [dict(x) for x in db.execute(
                "SELECT id,message_id,guidance,state,updated_at "
                "FROM experience_skill_candidates ORDER BY id DESC LIMIT ?", (limit,)
            )]
        return {"items": items, "automatic_approval": False, "cloud_requests": 0}

    def guidance(self) -> str:
        with closing(self.store.connect()) as db:
            rows = db.execute(
                "SELECT s.guidance FROM experience_skill_candidates s "
                "JOIN cognitive_feedback f ON f.message_id=s.message_id "
                "WHERE s.state='approved' AND f.helpful=1 AND "
                "s.guidance=trim(f.note) ORDER BY s.id DESC LIMIT 3"
            ).fetchall()
        # Only explicitly reviewed, positive notes; subordinate to persona and rules.
        return " ".join(x["guidance"] for x in rows)[:500]
