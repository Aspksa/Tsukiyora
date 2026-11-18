"""Cognitive Soul 3.0: bounded, testable improvement of response strategies.

A proposal is only a hypothesis; a benchmark measures STRICT normalized exact
matches on user-authored, held-out answers. The model never sees gold answers.
Provider calls require per-run explicit consent, quotas and a fixed call limit.
No self-editing code, network research, model fine-tuning or automatic promotion.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
import re
import time
import unicodedata

from memory_store import MemoryStore
from reflective_optimization import STRATEGIES

REQUIRED_CASES = 4
MAX_CALLS = 8
MIN_GAIN = 0.25
SUSPEND_SAMPLES = 8
SUSPEND_RATE = 0.25


def _text(value, name, limit):
    if not isinstance(value, str) or not 3 <= len(value.strip()) <= limit:
        raise ValueError(name + ": требуется от 3 до " + str(limit) + " символов.")
    return value.strip()


def _normalized(value):
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    # Exact-match grading accepts whitespace/punctuation variation, but never
    # keyword overlap or a verbose answer containing the gold label.
    return re.sub(r"[\W_]+", " ", value, flags=re.UNICODE).strip()


class SelfImprovement:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS improvement_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL,
                    expected TEXT NOT NULL,
                    split TEXT NOT NULL CHECK(split IN ('train','holdout')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS improvement_proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL UNIQUE,
                    rationale TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'proposed'
                        CHECK(state IN ('proposed','approved','suspended','retired')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS improvement_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id INTEGER NOT NULL REFERENCES improvement_proposals(id),
                    baseline_strategy TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
                    provider_calls INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS improvement_scores (
                    run_id INTEGER NOT NULL REFERENCES improvement_runs(id),
                    case_id INTEGER NOT NULL REFERENCES improvement_cases(id),
                    baseline_correct INTEGER NOT NULL CHECK(baseline_correct IN (0,1)),
                    candidate_correct INTEGER NOT NULL CHECK(candidate_correct IN (0,1)),
                    baseline_ms INTEGER NOT NULL,
                    candidate_ms INTEGER NOT NULL,
                    PRIMARY KEY(run_id,case_id)
                );
                CREATE TABLE IF NOT EXISTS improvement_active (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    proposal_id INTEGER REFERENCES improvement_proposals(id)
                );
                INSERT OR IGNORE INTO improvement_active(id,proposal_id)
                    VALUES(1,NULL);
                CREATE TABLE IF NOT EXISTS improvement_applied (
                    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                    proposal_id INTEGER NOT NULL REFERENCES improvement_proposals(id),
                    helpful INTEGER CHECK(helpful IN(0,1))
                );
                CREATE TABLE IF NOT EXISTS improvement_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id INTEGER NOT NULL REFERENCES improvement_proposals(id),
                    event TEXT NOT NULL,
                    previous_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def diagnose(self):
        with closing(self.store.connect()) as db:
            negatives = [dict(x) for x in db.execute(
                "SELECT COALESCE(t.category,'general') category,COUNT(*) n "
                "FROM cognitive_feedback f "
                "LEFT JOIN experience_feedback_tags t ON t.message_id=f.message_id "
                "WHERE f.helpful=0 GROUP BY category ORDER BY n DESC LIMIT 8"
            )]
            audits = [dict(x) for x in db.execute(
                "SELECT state,COUNT(*) n FROM reasoning_audits "
                "GROUP BY state"
            )]
            practice = [dict(x) for x in db.execute(
                "SELECT skill,COUNT(*) n,SUM(passed) wins "
                "FROM academy_practice_runs GROUP BY skill"
            )]
        return {"negative_feedback_by_category": negatives,
                "source_audits": audits, "control_question_checks": practice,
                "fact_checked": False,
                "interpretation": "Сигналы ошибок, а не доказанные причины ошибок."}

    def observe(self):
        """Form a bounded unapproved hypothesis from repeated negative ratings."""
        with closing(self.store.connect()) as db:
            rows = [dict(x) for x in db.execute(
                "SELECT COALESCE(t.category,'general') category,COUNT(*) n "
                "FROM cognitive_feedback f "
                "LEFT JOIN experience_feedback_tags t ON t.message_id=f.message_id "
                "WHERE f.helpful=0 GROUP BY category ORDER BY n DESC,category LIMIT 4"
            )]
        if not rows or rows[0]["n"] < 3:
            return {"created":False,"reason":"insufficient_negative_feedback"}
        category=rows[0]["category"]
        strategy = ("uncertainty" if category in ("sources","accuracy") else
                    "causal_steps" if category in ("clarity","general") else
                    "alternatives")
        reason = ("Автоматическая гипотеза: " + str(rows[0]["n"]) +
                  " отрицательных оценок категории " + category[:40] +
                  ". Применение требует реального эталонного теста и ручного решения.")
        with closing(self.store.connect()) as db, db:
            cursor=db.execute(
                "INSERT OR IGNORE INTO improvement_proposals(strategy,rationale)"
                " VALUES(?,?)",(strategy,reason)
            )
            row=db.execute(
                "SELECT id,state FROM improvement_proposals WHERE strategy=?",
                (strategy,)
            ).fetchone()
        return {"created":bool(cursor.rowcount),"proposal_id":row["id"],
                "state":row["state"],"automatic_activation":False}

    def propose(self, strategy, *, confirmed=False):
        if not isinstance(strategy, str) or strategy not in STRATEGIES:
            raise ValueError("Разрешены только проверенные стратегии.")
        if confirmed is not True:
            raise ValueError("Нужно подтвердить создание гипотезы.")
        diagnostics = self.diagnose()
        negative = sum(x["n"] for x in diagnostics["negative_feedback_by_category"])
        rationale = ("Гипотеза по накопленной обратной связи: отрицательных оценок "
                     + str(negative) + ". Не доказано, что стратегия улучшит качество.")
        with closing(self.store.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO improvement_proposals(strategy,rationale)"
                " VALUES(?,?)", (strategy,rationale)
            )
            row = db.execute(
                "SELECT id,strategy,state,rationale FROM improvement_proposals "
                "WHERE strategy=?", (strategy,)
            ).fetchone()
        return {**dict(row), "approved_automatically": False}

    def add_case(self, question, expected, split="holdout", *, confirmed=False):
        question = _text(question, "Контрольный вопрос", 500)
        expected = _text(expected, "Эталонный ответ", 200)
        if split not in ("train","holdout") or confirmed is not True:
            raise ValueError("Укажи набор и подтверди сохранение эталона.")
        with closing(self.store.connect()) as db, db:
            cursor = db.execute(
                "INSERT INTO improvement_cases(question,expected,split)"
                " VALUES(?,?,?)",(question,expected,split)
            )
        return {"id": cursor.lastrowid, "split":split, "local_only":True}

    def remove_case(self, case_id, *, confirmed=False):
        if type(case_id) is not int or case_id<=0 or confirmed is not True:
            raise ValueError("Удаление контрольного задания требует подтверждения.")
        with closing(self.store.connect()) as db, db:
            in_use = db.execute(
                "SELECT 1 FROM improvement_scores WHERE case_id=? LIMIT 1",
                (case_id,)
            ).fetchone()
            if in_use:
                raise ValueError("Использованный эталон нельзя удалить: история оценки защищена.")
            cursor = db.execute(
                "DELETE FROM improvement_cases WHERE id=?", (case_id,)
            )
        if cursor.rowcount!=1:
            raise ValueError("Эталон не найден.")
        return {"id":case_id,"deleted":True}

    def cases(self):
        with closing(self.store.connect()) as db:
            rows = [dict(x) for x in db.execute(
                "SELECT id,question,expected,split,created_at "
                "FROM improvement_cases ORDER BY id DESC LIMIT 100"
            )]
        return {"items": rows, "only_local_reference_answers": True}

    def _last_run(self, db, proposal_id):
        run = db.execute(
            "SELECT id,status,model,baseline_strategy,provider_calls,created_at "
            "FROM improvement_runs WHERE proposal_id=? ORDER BY id DESC LIMIT 1",
            (proposal_id,)
        ).fetchone()
        if not run:
            return None
        run = dict(run)
        rows = [dict(x) for x in db.execute(
            "SELECT case_id,baseline_correct,candidate_correct,baseline_ms,candidate_ms "
            "FROM improvement_scores WHERE run_id=? ORDER BY case_id", (run["id"],)
        )]
        n = len(rows)
        baseline = sum(x["baseline_correct"] for x in rows)
        candidate = sum(x["candidate_correct"] for x in rows)
        regressions = sum(x["baseline_correct"] and not x["candidate_correct"] for x in rows)
        run.update({"cases":n,"baseline_correct":baseline,
                    "candidate_correct":candidate,"regressions":regressions,
                    "gain":round((candidate-baseline)/n,3) if n else None,
                    "baseline_mean_ms":round(sum(x["baseline_ms"] for x in rows)/n) if n else None,
                    "candidate_mean_ms":round(sum(x["candidate_ms"] for x in rows)/n) if n else None,
                    "ready_for_review":bool(run["status"]=="completed"
                       and n >= REQUIRED_CASES and regressions==0
                       and candidate-baseline>=max(1,int(n*MIN_GAIN+0.9999)))})
        return run

    def report(self):
        with closing(self.store.connect()) as db:
            proposal_rows = [dict(x) for x in db.execute(
                "SELECT id,strategy,state,rationale,created_at "
                "FROM improvement_proposals ORDER BY id DESC"
            )]
            active = db.execute(
                "SELECT proposal_id FROM improvement_active WHERE id=1"
            ).fetchone()["proposal_id"]
            cases = [dict(x) for x in db.execute(
                "SELECT split,COUNT(*) n FROM improvement_cases GROUP BY split"
            )]
            for proposal in proposal_rows:
                proposal["last_run"] = self._last_run(db,proposal["id"])
            recent_events = [dict(x) for x in db.execute(
                "SELECT proposal_id,event,previous_id,created_at "
                "FROM improvement_events ORDER BY id DESC LIMIT 15"
            )]
        return {"proposals":proposal_rows, "active_id":active,
                "case_counts":{x["split"]:x["n"] for x in cases},
                "minimum_holdout_cases":REQUIRED_CASES, "max_cloud_calls_per_run":MAX_CALLS,
                "recent_events":recent_events,
                "scores_are_exact_match_not_truth":True,
                "auto_code_changes":False,"auto_activation":False}

    def guidance(self):
        with closing(self.store.connect()) as db:
            row = db.execute(
                "SELECT p.id,p.strategy FROM improvement_active a "
                "JOIN improvement_proposals p ON p.id=a.proposal_id "
                "WHERE a.id=1 AND p.state='approved'"
            ).fetchone()
        if row is None:
            return None, ""
        return row["id"], STRATEGIES[row["strategy"]]

    def evaluate(self, proposal_id, cloud, persona, *,
                 confirmed_send=False, max_pairs=REQUIRED_CASES):
        if (type(proposal_id) is not int or proposal_id<=0
                or confirmed_send is not True
                or type(max_pairs) is not int or max_pairs != REQUIRED_CASES):
            raise ValueError("Нужно явно разрешить 8 запросов Cloud.ru и 4 независимых эталона.")
        with closing(self.store.connect()) as db:
            proposal = db.execute(
                "SELECT id,strategy,state FROM improvement_proposals WHERE id=?",
                (proposal_id,)
            ).fetchone()
            if not proposal or proposal["state"]!="proposed":
                raise ValueError("Проверять можно только гипотезу до активации.")
            cases = [dict(x) for x in db.execute(
                "SELECT id,question,expected FROM improvement_cases "
                "WHERE split='holdout' ORDER BY id DESC LIMIT ?",
                (max_pairs,)
            )]
            active = db.execute(
                "SELECT p.strategy FROM improvement_active a JOIN improvement_proposals p "
                "ON p.id=a.proposal_id WHERE a.id=1 AND p.state='approved'"
            ).fetchone()
            prior_evolution = db.execute(
                "SELECT strategy FROM evolution_trials WHERE state='active' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if len(cases)<REQUIRED_CASES:
            raise ValueError("Сначала добавь 4 независимых эталонных задания holdout.")
        baseline = " ".join(STRATEGIES[x] for x in
                            (prior_evolution["strategy"] if prior_evolution else None,
                             active["strategy"] if active else None)
                            if x in STRATEGIES)
        proposed = (baseline+" "+STRATEGIES[proposal["strategy"]]).strip()
        if STRATEGIES[proposal["strategy"]] in baseline:
            raise ValueError("Стратегия уже применяется: сравнение было бы бессмысленным.")
        status=cloud.status()
        if not status.get("verified") or not status.get("configured") or not status.get("model"):
            raise ValueError("Сначала проверь подключение Cloud.ru и выбери модель.")
        if getattr(cloud,"guard",None) is not None:
            usage=cloud.guard.status()
            limits=usage["limits"]; daily=usage["today"].get("chat",0)
            if limits["chat_per_day"]-daily < MAX_CALLS or limits["per_minute"] < MAX_CALLS:
                raise ValueError("Недостаточно лимитов Cloud.ru на 8 запросов. "
                                 "Проверь дневной и минутный предел.")
        with closing(self.store.connect()) as db, db:
            busy = db.execute(
                "SELECT id FROM improvement_runs WHERE status='running' LIMIT 1"
            ).fetchone()
            if busy:
                # Crashed older attempts cannot prove readiness. Fail closed
                # until operator manually inspects the SQLite backup.
                raise ValueError("Есть незавершённый тест. Проверь и восстанови состояние.")
            cursor = db.execute(
                "INSERT INTO improvement_runs(proposal_id,baseline_strategy,model,status)"
                " VALUES(?,?,?,'running')",(proposal_id,baseline,status["model"])
            )
            run_id=cursor.lastrowid
        count=0
        try:
            for case in cases:
                answers=[]
                elapsed=[]
                for guide in (baseline,proposed):
                    if cloud.status().get("model") != status["model"]:
                        raise ValueError("Модель Cloud.ru изменилась во время проверки.")
                    start=time.monotonic()
                    answer=cloud.complete(persona,[
                        {"role":"user","content":case["question"]}
                    ],[],extra_guidance=guide)
                    count+=1
                    if cloud.status().get("model") != status["model"]:
                        raise ValueError("Модель Cloud.ru изменилась во время проверки.")
                    answers.append(int(_normalized(answer)==_normalized(case["expected"])))
                    elapsed.append(int((time.monotonic()-start)*1000))
                with closing(self.store.connect()) as db, db:
                    db.execute(
                        "INSERT INTO improvement_scores"
                        "(run_id,case_id,baseline_correct,candidate_correct,"
                        "baseline_ms,candidate_ms) VALUES(?,?,?,?,?,?)",
                        (run_id,case["id"],answers[0],answers[1],elapsed[0],elapsed[1])
                    )
                    db.execute(
                        "UPDATE improvement_runs SET provider_calls=? WHERE id=?",
                        (count,run_id)
                    )
        except Exception:
            with closing(self.store.connect()) as db, db:
                db.execute(
                    "UPDATE improvement_runs SET status='failed',provider_calls=? "
                    "WHERE id=?", (count,run_id)
                )
            raise
        with closing(self.store.connect()) as db, db:
            db.execute(
                "UPDATE improvement_runs SET status='completed',provider_calls=? "
                "WHERE id=?", (count,run_id)
            )
            result=self._last_run(db,proposal_id)
        return {"proposal_id":proposal_id,"run":result,
                "gold_answers_sent_to_cloud":False,
                "candidate_automatically_activated":False,
                "billed_tokens_unknown":True}

    def recover_interrupted(self, run_id, *, confirmed=False):
        """Manual fail-closed recovery after server restart during an evaluation."""
        if type(run_id) is not int or run_id <= 0 or confirmed is not True:
            raise ValueError("Требуется подтверждённый номер прерванного теста.")
        with closing(self.store.connect()) as db, db:
            run=db.execute(
                "SELECT status FROM improvement_runs WHERE id=?",(run_id,)
            ).fetchone()
            if run is None or run["status"]!="running":
                raise ValueError("Только незавершённый тест может быть закрыт.")
            db.execute(
                "UPDATE improvement_runs SET status='failed' WHERE id=?",(run_id,)
            )
        return {"run_id":run_id,"status":"failed",
                "activation_allowed":False}

    def decide(self, proposal_id, *, approve, confirmed=False):
        if (type(proposal_id) is not int or proposal_id<=0
                or type(approve) is not bool or confirmed is not True):
            raise ValueError("Требуется подтверждение решения.")
        with closing(self.store.connect()) as db, db:
            proposal=db.execute(
                "SELECT id,state FROM improvement_proposals WHERE id=?",
                (proposal_id,)
            ).fetchone()
            if proposal is None:
                raise ValueError("Гипотеза не найдена.")
            old=db.execute(
                "SELECT proposal_id FROM improvement_active WHERE id=1"
            ).fetchone()["proposal_id"]
            run=self._last_run(db,proposal_id)
            if approve:
                if proposal["state"]!="proposed" or not run or not run["ready_for_review"]:
                    raise ValueError("Контрольная проверка не показала достаточного улучшения.")
                db.execute(
                    "UPDATE improvement_proposals SET state='approved' WHERE id=?",
                    (proposal_id,)
                )
                if old is not None and old!=proposal_id:
                    db.execute(
                        "UPDATE improvement_proposals SET state='retired' WHERE id=?",(old,)
                    )
                db.execute(
                    "UPDATE improvement_active SET proposal_id=? WHERE id=1",
                    (proposal_id,)
                )
                kind="approved"
            else:
                if old!=proposal_id:
                    raise ValueError("Отключить можно только активную стратегию.")
                prior=db.execute(
                    "SELECT previous_id FROM improvement_events "
                    "WHERE proposal_id=? AND event='approved' "
                    "ORDER BY id DESC LIMIT 1",(proposal_id,)
                ).fetchone()
                restored = prior["previous_id"] if prior else None
                if restored is not None:
                    old_proposal = db.execute(
                        "SELECT state FROM improvement_proposals WHERE id=?",
                        (restored,)
                    ).fetchone()
                    if old_proposal is None or old_proposal["state"] not in ("retired","approved"):
                        restored=None
                db.execute(
                    "UPDATE improvement_proposals SET state='suspended' WHERE id=?",
                    (proposal_id,)
                )
                if restored is not None:
                    db.execute(
                        "UPDATE improvement_proposals SET state='approved' WHERE id=?",
                        (restored,)
                    )
                db.execute(
                    "UPDATE improvement_active SET proposal_id=? WHERE id=1",
                    (restored,)
                )
                kind="rolled_back"
            db.execute(
                "INSERT INTO improvement_events(proposal_id,event,previous_id)"
                " VALUES(?,?,?)",
                (proposal_id,kind,old if approve else restored)
            )
        return {"proposal_id":proposal_id,"state":kind,
                "active_id": proposal_id if approve else restored}

    def applied(self, message_id, proposal_id):
        if proposal_id is None:
            return
        if type(message_id) is not int or message_id<=0 or type(proposal_id) is not int:
            raise ValueError("Некорректный ID ответа.")
        with closing(self.store.connect()) as db, db:
            row=db.execute(
                "SELECT role FROM messages WHERE id=?", (message_id,)
            ).fetchone()
            if row is None or row["role"]!="assistant":
                raise ValueError("Можно отмечать только ответы ассистента.")
            db.execute(
                "INSERT OR IGNORE INTO improvement_applied(message_id,proposal_id)"
                " VALUES(?,?)",(message_id,proposal_id)
            )

    def feedback(self, message_id, helpful):
        if type(message_id) is not int or type(helpful) is not bool:
            raise ValueError("Некорректная оценка.")
        with closing(self.store.connect()) as db, db:
            link=db.execute(
                "SELECT proposal_id FROM improvement_applied WHERE message_id=?",
                (message_id,)
            ).fetchone()
            if not link:
                return {"measured":False}
            db.execute(
                "UPDATE improvement_applied SET helpful=? WHERE message_id=?",
                (int(helpful),message_id)
            )
            trial=link["proposal_id"]
            active=db.execute(
                "SELECT proposal_id FROM improvement_active WHERE id=1"
            ).fetchone()["proposal_id"]
            count=db.execute(
                "SELECT COUNT(*) n,SUM(helpful) wins FROM improvement_applied "
                "WHERE proposal_id=? AND helpful IS NOT NULL",(trial,)
            ).fetchone()
            rate=count["wins"]/count["n"] if count["n"] else None
            suspended=bool(active==trial and count["n"]>=SUSPEND_SAMPLES
                           and rate<=SUSPEND_RATE)
            if suspended:
                db.execute(
                    "UPDATE improvement_active SET proposal_id=NULL WHERE id=1"
                )
                db.execute(
                    "UPDATE improvement_proposals SET state='suspended' WHERE id=?",
                    (trial,)
                )
                db.execute(
                    "INSERT INTO improvement_events(proposal_id,event,previous_id)"
                    " VALUES(?,'automatic_suspension',NULL)",(trial,)
                )
        return {"measured":True,"proposal_id":trial,"samples":count["n"],
                "helpful_rate":round(rate,3),"automatically_suspended":suspended}
