"""GREY MATTER 14.0: deterministic, source-grounded logic practice.

The logic evaluator operates ONLY on user-attested symbolic propositions,
not on unrestricted natural-language truth, model private reasoning, or
autonomous neural-network retraining. It reuses V7/V8/Forge/Evolution and
one existing SQLite worker; no new paid LLM, services or dependencies.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import re
from time import perf_counter

from tsukiyora.memory.memory_store import MemoryStore

ATOM = re.compile(r"^!?[a-z][a-z0-9_]{0,31}$")
LABELS = frozenset(("entailed", "refuted", "unknown", "conflict"))
MAX_STEPS = 32
MAX_CASES_PER_CYCLE = 6
ENGINE_VERSION = "symbolic-v1"


def _literal(value):
    if not isinstance(value, str) or not ATOM.fullmatch(value):
        raise ValueError("Use symbolic names like decision_a, !decision_a.")
    return value


def _case_payload(facts, rules, target):
    """Canonical, small propositional rule graph. No text is executed."""
    if not isinstance(facts, list) or not 1 <= len(facts) <= 32:
        raise ValueError("Supply 1–32 user-attested symbolic premises.")
    if not isinstance(rules, list) or len(rules) > 32:
        raise ValueError("Too many symbolic implication rules.")
    if not isinstance(target, str) or not ATOM.fullmatch(target) or target.startswith("!"):
        raise ValueError("Target must be a positive symbolic name.")
    facts = [_literal(x) for x in facts]
    parsed = []
    for rule in rules:
        if (not isinstance(rule, dict) or set(rule) != {"when", "then"}
                or not isinstance(rule["when"], list)
                or not 1 <= len(rule["when"]) <= 4):
            raise ValueError("A rule requires when:[premises] and then:conclusion.")
        premises = [_literal(x) for x in rule["when"]]
        consequence = _literal(rule["then"])
        parsed.append({"when": list(dict.fromkeys(premises)),
                       "then": consequence})
    if len(set(facts)) > 32 or len(set(facts + [x["then"] for x in parsed])) > 64:
        raise ValueError("Symbolic graph is too large.")
    return list(dict.fromkeys(facts)), parsed, target


def solve(facts, rules, target):
    """Explainable forward chaining; paths are symbolic, NOT LLM thoughts."""
    facts, rules, target = _case_payload(facts, rules, target)
    known = set(facts)
    evidence = {item: "premise" for item in facts}
    iterations = 0
    for iteration in range(MAX_STEPS):
        iterations = iteration + 1
        changed = False
        for number, rule in enumerate(rules, 1):
            if rule["then"] in known:
                continue
            if all(antecedent in known for antecedent in rule["when"]):
                known.add(rule["then"])
                evidence[rule["then"]] = "rule #" + str(number)
                changed = True
        if not changed:
            break
    pos = target in known
    neg = "!" + target in known
    conclusion = ("conflict" if pos and neg else
                  "entailed" if pos else "refuted" if neg else "unknown")
    return {"conclusion": conclusion,
            "support": {"positive": evidence.get(target),
                        "negative": evidence.get("!" + target)},
            "derived_literals": len(known)-len(set(facts)),
            "iterations": iterations,
            "method": "bounded_forward_chaining_not_llm_reasoning",
            "truth_in_real_world_verified": False}


class GreyMatter14:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS grey14_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    facts_json TEXT NOT NULL,
                    rules_json TEXT NOT NULL,
                    target TEXT NOT NULL,
                    expected TEXT NOT NULL CHECK(expected IN
                        ('entailed','refuted','unknown','conflict')),
                    source_memory_id INTEGER REFERENCES memory_items(id),
                    source_sha256 TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(facts_json,rules_json,target,expected)
                );
                CREATE TABLE IF NOT EXISTS grey14_results (
                    case_id INTEGER PRIMARY KEY REFERENCES grey14_cases(id),
                    conclusion TEXT NOT NULL,
                    passed INTEGER NOT NULL CHECK(passed IN (0,1)),
                    engine_version TEXT NOT NULL,
                    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS grey14_cursor (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    last_case_id INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO grey14_cursor(id,last_case_id)
                    VALUES(1,0);
                CREATE INDEX IF NOT EXISTS idx_grey14_cases_created
                    ON grey14_cases(id DESC);
            """)

    @staticmethod
    def _current_source(db, row):
        if row["source_memory_id"] is None:
            return True
        from hashlib import sha256
        item = db.execute(
            "SELECT content FROM memory_items WHERE id=? AND status='active'",
            (row["source_memory_id"],)
        ).fetchone()
        return bool(item and sha256(item["content"].encode()).hexdigest()
                    == row["source_sha256"])

    def create_case(self, facts, rules, target, expected, *,
                    source_memory_id=None, confirmed=False):
        facts, rules, target = _case_payload(facts, rules, target)
        if expected not in LABELS or confirmed is not True:
            raise ValueError("User confirmation and an expected result are required.")
        if source_memory_id is not None and (
                type(source_memory_id) is not int or source_memory_id <= 0):
            raise ValueError("Invalid source-memory ID.")
        facts_json = json.dumps(facts, separators=(",", ":"))
        rules_json = json.dumps(rules, separators=(",", ":"), sort_keys=True)
        with closing(self.store.connect()) as db, db:
            source_sha = None
            if source_memory_id is not None:
                from hashlib import sha256
                record = db.execute(
                    "SELECT content FROM memory_items WHERE id=? AND status='active'",
                    (source_memory_id,)
                ).fetchone()
                if record is None:
                    raise ValueError("User-attested evidence must still be active.")
                source_sha = sha256(record["content"].encode()).hexdigest()
            db.execute(
                "INSERT OR IGNORE INTO grey14_cases "
                "(facts_json,rules_json,target,expected,"
                "source_memory_id,source_sha256) VALUES(?,?,?,?,?,?)",
                (facts_json, rules_json, target, expected,
                 source_memory_id,source_sha)
            )
            found = db.execute(
                "SELECT id FROM grey14_cases WHERE facts_json=? "
                "AND rules_json=? AND target=? AND expected=?",
                (facts_json,rules_json,target,expected)
            ).fetchone()
            if found is None:
                raise ValueError("Case could not be persisted.")
            case_id = found["id"]
        return {"case_id":case_id, "expected_user_attested": True,
                "facts_not_independently_verified": True,
                "cloud_calls":0, "auto_activated_strategies":0}

    def cases(self, *, limit=20):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Invalid case limit.")
        with closing(self.store.connect()) as db:
            rows = [dict(x) for x in db.execute(
                "SELECT c.id,c.target,c.expected,c.source_memory_id,"
                "c.source_sha256,c.created_at,r.conclusion,r.passed,r.checked_at "
                "FROM grey14_cases c LEFT JOIN grey14_results r ON c.id=r.case_id "
                "ORDER BY c.id DESC LIMIT ?",(limit,)
            )]
            for row in rows:
                row["source_current"] = self._current_source(db,row)
                row.pop("source_sha256")
        return {"items": rows, "case_kind":"user_attested_symbolic",
                "model_accuracy_claimed":False,"cloud_calls":0}

    def _run_one(self, db, row):
        if not self._current_source(db,row):
            db.execute("DELETE FROM grey14_results WHERE case_id=?",
                       (row["id"],))
            return {"case_id":row["id"],"skipped":"source_stale"}
        result=solve(json.loads(row["facts_json"]),
                     json.loads(row["rules_json"]),row["target"])
        passed=int(result["conclusion"]==row["expected"])
        db.execute(
            "INSERT INTO grey14_results(case_id,conclusion,passed,engine_version,"
            "checked_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(case_id) "
            "DO UPDATE SET conclusion=excluded.conclusion,passed=excluded.passed,"
            "engine_version=excluded.engine_version,checked_at=CURRENT_TIMESTAMP",
            (row["id"],result["conclusion"],passed,ENGINE_VERSION))
        return {"case_id":row["id"],"passed":bool(passed),
                "actual":result["conclusion"],"expected":row["expected"],
                "symbolic_proof":result["support"],
                "truth_in_real_world_verified":False}

    def benchmark(self, core, *, limit=20):
        """Explicit local test run: results are stored, never the raw dialog."""
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Invalid benchmark size.")
        began=perf_counter()
        with closing(self.store.connect()) as db, db:
            rows = db.execute(
                "SELECT * FROM grey14_cases ORDER BY id DESC LIMIT ?",(limit,)
            ).fetchall()
            items=[self._run_one(db,row) for row in rows]
        tested=[x for x in items if "passed" in x]
        return {"tested":len(tested),"passed":sum(x["passed"] for x in tested),
                "skipped_stale":len(items)-len(tested),
                "accuracy_on_user_attested_cases":(
                    round(sum(x["passed"] for x in tested)/len(tested),3)
                    if tested else None),
                "milliseconds":round((perf_counter()-began)*1000,2),
                "items":items[:20], "cloud_calls":0,
                "not_llm_or_world_truth_benchmark":True}

    def cycle(self, core, *, limit=MAX_CASES_PER_CYCLE):
        """Share existing V12 job. Resume after restart via a persisted cursor."""
        if type(limit) is not int or not 1 <= limit <= MAX_CASES_PER_CYCLE:
            raise ValueError("Invalid background batch.")
        settings=core.living8.settings()
        if (settings["autonomy_level"]<2 or
                not settings["local_permissions"].get("local_practice",False)):
            return {"paused":True,"reason":"local_practice_off",
                    "tested":0,"cloud_calls":0}
        with closing(self.store.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                cursor = db.execute(
                    "SELECT last_case_id FROM grey14_cursor WHERE id=1"
                ).fetchone()["last_case_id"]
                rows = db.execute(
                    "SELECT * FROM grey14_cases WHERE id>? ORDER BY id LIMIT ?",
                    (cursor,limit)
                ).fetchall()
                if not rows and cursor:
                    rows = db.execute(
                        "SELECT * FROM grey14_cases ORDER BY id LIMIT ?",
                        (limit,)
                    ).fetchall()
                reports=[self._run_one(db,row) for row in rows]
                if rows:
                    db.execute(
                        "UPDATE grey14_cursor SET last_case_id=? WHERE id=1",
                        (rows[-1]["id"],)
                    )
                today=datetime.now(timezone.utc).date().isoformat()
                logged=db.execute(
                    "SELECT 1 FROM soul8_journal WHERE event='grey14_daily' "
                    "AND source_type='day' AND source_ref=? LIMIT 1",(today,)
                ).fetchone()
                if not logged and rows:
                    core.living8._journal(
                        db,"grey14_daily","day",today,{
                            "reviewed":len(reports),
                            "passes":sum(x.get("passed",False) for x in reports),
                            "stale":sum("skipped" in x for x in reports)})
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"tested":sum("passed" in x for x in reports),
                "passed":sum(x.get("passed",False) for x in reports),
                "skipped_stale":sum("skipped" in x for x in reports),
                "daily_already_recorded":bool(logged),"cloud_calls":0,
                "new_workers":0,"auto_promoted":0,
                "persona_modified":False,"memory_modified":False}

    def status(self, core):
        with closing(self.store.connect()) as db:
            rows=db.execute(
                "SELECT COUNT(*) n FROM grey14_cases"
            ).fetchone()["n"]
            checks=db.execute(
                "SELECT COUNT(*) n,COALESCE(SUM(passed),0) correct "
                "FROM grey14_results"
            ).fetchone()
            last=db.execute(
                "SELECT source_ref,created_at FROM soul8_journal "
                "WHERE event='grey14_daily' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        trials=core.evolution.report()["trials"]
        candidates=core.evolution4.report()["candidates"]
        return {"version":"14.0", "name":"GREY MATTER",
                "symbolic_cases":rows,"evaluated_cases":checks["n"],
                "passing_cases":checks["correct"],
                "last_background_day":dict(last) if last else None,
                "active_prompt_trials":sum(x["state"]=="testing" for x in trials),
                "prompt_trials_waiting_review":sum(
                    bool(x["ready_for_review"]) for x in trials),
                "evolution4_candidates_proposed":sum(
                    x["state"]=="proposed" for x in candidates),
                "v7_guard_active":True,
                "same_sqlite_queue":True,"additional_paid_calls":0,
                "automatic_strategy_approval":False,
                "automatic_persona_changes":False,
                "real_consciousness_proven":False,
                "symbolic_logic_not_LLM_reasoning":True,
                "benchmark_is_not_general_intelligence":True}
