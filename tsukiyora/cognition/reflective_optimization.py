"""GEPA-inspired controlled reflection on response strategies.

No GEPA package, LLM self-judging, weight training or unreviewed prompt rewrites.
Known-safe candidates are evaluated through explicit user ratings in
conversation-stable A/B cohorts and require consent to test and activate.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
from tsukiyora.memory.memory_store import MemoryStore


STRATEGIES = {
    "causal_steps": "При объяснении причин отделяй подтверждённые факты от гипотез и показывай цепочку доводов.",
    "alternatives": "Для сложной задачи кратко проверь минимум две альтернативы и укажи критерий выбора.",
    "uncertainty": "Если доказательств недостаточно, прямо обозначь границы знания и способ проверки.",
}
MIN_PER_ARM = 8
MIN_GAIN = 0.15


class ReflectiveOptimization:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS evolution_trials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL DEFAULT 'draft'
                        CHECK(state IN ('draft','testing','active','retired')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS evolution_assignments (
                    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
                    trial_id INTEGER NOT NULL REFERENCES evolution_trials(id),
                    arm TEXT NOT NULL CHECK(arm IN ('baseline','candidate'))
                );
                CREATE TABLE IF NOT EXISTS evolution_outcomes (
                    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                    trial_id INTEGER NOT NULL REFERENCES evolution_trials(id),
                    arm TEXT NOT NULL CHECK(arm IN ('baseline','candidate')),
                    helpful INTEGER NOT NULL CHECK(helpful IN (0,1))
                );
            """)

    def propose(self, strategy: str) -> dict:
        if not isinstance(strategy, str) or strategy not in STRATEGIES:
            raise ValueError("Unknown safe strategy.")
        with closing(self.store.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO evolution_trials(strategy) VALUES(?)",
                       (strategy,))
            row = db.execute(
                "SELECT id,state FROM evolution_trials WHERE strategy=?", (strategy,)
            ).fetchone()
        return {"trial_id": row["id"], "strategy": strategy, "state": row["state"],
                "guidance": STRATEGIES[strategy], "automatic_activation": False}

    def start(self, trial_id: int, *, confirmed: bool = False) -> dict:
        if type(trial_id) is not int or trial_id <= 0 or confirmed is not True:
            raise ValueError("Starting a trial requires explicit confirmation.")
        with closing(self.store.connect()) as db, db:
            row = db.execute(
                "SELECT state FROM evolution_trials WHERE id=?", (trial_id,)
            ).fetchone()
            if row is None or row["state"] != "draft":
                raise ValueError("Only a draft trial may be started.")
            busy = db.execute(
                "SELECT 1 FROM evolution_trials WHERE state='testing' LIMIT 1"
            ).fetchone()
            if busy:
                raise ValueError("Another trial is already in progress.")
            db.execute("UPDATE evolution_trials SET state='testing' WHERE id=?",
                       (trial_id,))
        return {"trial_id": trial_id, "state": "testing"}

    def guidance_for_conversation(self, conversation_id: str) -> str:
        if not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 64:
            raise ValueError("Invalid conversation ID.")
        with closing(self.store.connect()) as db, db:
            active = db.execute(
                "SELECT strategy FROM evolution_trials WHERE state='active' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            # An explicitly active candidate is the established baseline
            # outside an A/B trial.
            testing = db.execute(
                "SELECT id,strategy FROM evolution_trials WHERE state='testing' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if testing is None:
                return STRATEGIES[active["strategy"]] if active else ""
            if not db.execute("SELECT 1 FROM conversations WHERE id=?",
                              (conversation_id,)).fetchone():
                raise ValueError("Unknown conversation.")
            # Deterministic assignment: one conversation cannot drift arms.
            digest = hashlib.sha256((conversation_id + "/" + str(testing["id"])).encode()).digest()
            arm = "candidate" if digest[0] & 1 else "baseline"
            db.execute(
                "INSERT OR IGNORE INTO evolution_assignments"
                "(conversation_id,trial_id,arm) VALUES(?,?,?)",
                (conversation_id,testing["id"],arm)
            )
            assigned = db.execute(
                "SELECT trial_id,arm FROM evolution_assignments WHERE conversation_id=?",
                (conversation_id,)
            ).fetchone()
            if assigned["trial_id"] != testing["id"]:
                # Older chats keep their original assignment and do not
                # contaminate a newer experiment.
                return STRATEGIES[active["strategy"]] if active else ""
            if assigned["arm"] == "candidate":
                return STRATEGIES[testing["strategy"]]
            return STRATEGIES[active["strategy"]] if active else ""

    def record(self, message_id: int, helpful: bool) -> dict:
        if type(message_id) is not int or message_id <= 0 or type(helpful) is not bool:
            raise ValueError("Invalid rating.")
        with closing(self.store.connect()) as db, db:
            row = db.execute(
                "SELECT a.trial_id,a.arm,t.state FROM messages m "
                "JOIN cognitive_feedback f ON f.message_id=m.id "
                "JOIN evolution_assignments a ON a.conversation_id=m.conversation_id "
                "JOIN evolution_trials t ON t.id=a.trial_id "
                "WHERE m.id=? AND m.role='assistant'", (message_id,)
            ).fetchone()
            if row is None or row["state"] != "testing":
                return {"measured": False}
            # Retired experiments keep historical ratings but are never applied.
            db.execute(
                "INSERT INTO evolution_outcomes(message_id,trial_id,arm,helpful)"
                " VALUES(?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET"
                " helpful=excluded.helpful",
                (message_id,row["trial_id"],row["arm"],int(helpful))
            )
        return {"measured": True, "trial_id": row["trial_id"], "arm": row["arm"]}

    def report(self) -> dict:
        with closing(self.store.connect()) as db:
            trials = [dict(row) for row in db.execute(
                "SELECT id,strategy,state,created_at FROM evolution_trials ORDER BY id DESC"
            )]
            measurements = [dict(row) for row in db.execute(
                "SELECT trial_id,arm,COUNT(*) n,SUM(helpful) successes "
                "FROM evolution_outcomes GROUP BY trial_id,arm"
            )]
        for trial in trials:
            groups = {}
            for arm in ("baseline","candidate"):
                source = next((r for r in measurements if r["trial_id"] == trial["id"]
                               and r["arm"] == arm), None)
                n = int(source["n"]) if source else 0
                wins = int(source["successes"]) if source else 0
                groups[arm] = {"samples": n, "helpful": wins,
                               "rate": round(wins/n, 3) if n else None}
            comparable = all(x["samples"] >= MIN_PER_ARM for x in groups.values())
            gain = round(groups["candidate"]["rate"]-groups["baseline"]["rate"],3) if comparable else None
            trial["arms"] = groups
            trial["gain"] = gain
            trial["ready_for_review"] = bool(comparable and gain >= MIN_GAIN)
            trial["guidance"] = STRATEGIES[trial["strategy"]]
        return {"trials": trials, "min_samples_per_arm": MIN_PER_ARM,
                "required_gain": MIN_GAIN, "auto_activation": False,
                "interpretation": "Наблюдаемые оценки, не доказательство причинного эффекта.",
                "cloud_requests": 0}

    def decide(self, trial_id: int, *, activate: bool, confirmed: bool = False) -> dict:
        if type(trial_id) is not int or trial_id <= 0 or type(activate) is not bool or confirmed is not True:
            raise ValueError("Trial decision requires explicit confirmation.")
        trial = next((t for t in self.report()["trials"] if t["id"] == trial_id), None)
        if not trial:
            raise ValueError("Unknown trial.")
        if activate and (trial["state"] != "testing" or not trial["ready_for_review"]):
            raise ValueError("Insufficient evidence to activate this strategy.")
        with closing(self.store.connect()) as db, db:
            if activate:
                db.execute("UPDATE evolution_trials SET state='retired' WHERE state='active'")
            db.execute("UPDATE evolution_trials SET state=? WHERE id=?",
                       ("active" if activate else "retired",trial_id))
        return {"trial_id":trial_id,"state":"active" if activate else "retired"}
