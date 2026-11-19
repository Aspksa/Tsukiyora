"""Cognitive Soul 4.0: bounded evolutionary experiments and evidence-led skills.

All suggested prompts are composed from an audited, immutable vocabulary;
never from untrusted memories or generated free-form executable instructions.
The engine has no independent external requests or source-code modification.
Actual model comparisons are opt-in, rate-limited and backed by local gold labels.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from itertools import combinations
import hashlib
import json
import re
import time
import unicodedata

from tsukiyora.memory.memory_store import MemoryStore

DOMAINS = ("logic", "coding", "legal", "rag", "documents")
METHODS = {
    "evidence": "Отделяй подтвержденные факты от предположений; указывай источник утверждения.",
    "alternatives": "Проверь альтернативные решения и назови проверяемые критерии выбора.",
    "uncertainty": "Явно отмечай неопределенность и недостающие данные, не выдумывай факты.",
    "structure": "Решай задачу по ясным шагам, затем кратко объясни результат.",
    "verification": "Перед выводом проверь возможные противоречия и внутреннюю согласованность.",
    "brevity": "Дай лаконичный ответ без потери существенных ограничений.",
    "edge_cases": "Проверь граничные случаи и укажи, где решение может не сработать.",
}
DOMAIN_HINTS = {
    "legal": ("договор", "юрид", "закон", "контракт", "суд", "стать", "правов", "contract", "legal"),
    "coding": ("код", "python", "typescript", "ошибк", "github", "функци", "програм", "bug", "api"),
    "rag": ("rag", "поиск", "памят", "источник", "вектор", "индекс", "embedding"),
    "documents": ("документ", "pdf", "docx", "файл", "таблиц", "отчёт", "xlsx"),
}
MAX_PER_BATCH = 6
MAX_CALLS_PER_BATCH = 12
DAILY_MAX_CALLS = 24
MIN_PROMOTION_CASES = 30
MIN_GAIN = .10
MIN_FEEDBACK = 8
UNHELPFUL_SUSPEND_RATE = .25


def canonical(text):
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def checked(value, field, upper):
    if not isinstance(value, str) or not 3 <= len(value.strip()) <= upper:
        raise ValueError(field + ": нужно 3–" + str(upper) + " символов.")
    return value.strip()


def detect_domain(query):
    if not isinstance(query, str):
        return "logic"
    lowered = query.casefold()
    for domain in ("legal", "coding", "rag", "documents"):
        if any(hint in lowered for hint in DOMAIN_HINTS[domain]):
            return domain
    return "logic"


class CognitiveEvolution:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS evol4_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    recipe TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    parent_id INTEGER REFERENCES evol4_candidates(id),
                    generation INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'proposed'
                        CHECK(state IN ('proposed','active','paused','retired')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS evol4_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    split TEXT NOT NULL CHECK(split IN ('train','holdout')),
                    question TEXT NOT NULL,
                    expected TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS evol4_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id INTEGER NOT NULL REFERENCES evol4_candidates(id),
                    evaluation_domain TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running','failed','completed')),
                    provider_calls INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS evol4_scores (
                    run_id INTEGER NOT NULL REFERENCES evol4_runs(id),
                    case_id INTEGER NOT NULL REFERENCES evol4_cases(id),
                    baseline_correct INTEGER NOT NULL CHECK(baseline_correct IN (0,1)),
                    candidate_correct INTEGER NOT NULL CHECK(candidate_correct IN (0,1)),
                    baseline_ms INTEGER NOT NULL,
                    candidate_ms INTEGER NOT NULL,
                    PRIMARY KEY(run_id,case_id)
                );
                CREATE TABLE IF NOT EXISTS evol4_active (
                    domain TEXT PRIMARY KEY,
                    candidate_id INTEGER REFERENCES evol4_candidates(id)
                );
                CREATE TABLE IF NOT EXISTS evol4_applied (
                    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                    candidate_id INTEGER NOT NULL REFERENCES evol4_candidates(id),
                    helpful INTEGER CHECK(helpful IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS evol4_budget (
                    day TEXT PRIMARY KEY,
                    calls_reserved INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evol4_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id INTEGER NOT NULL REFERENCES evol4_candidates(id),
                    event TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS evol4_run_candidate
                    ON evol4_runs(candidate_id,evaluation_domain,status);
                CREATE INDEX IF NOT EXISTS evol4_score_case
                    ON evol4_scores(case_id);
            """)
            for domain in DOMAINS:
                db.execute("INSERT OR IGNORE INTO evol4_active(domain) VALUES(?)",
                           (domain,))

    def signals(self):
        """Observations, not a privileged introspection or proof of error."""
        with closing(self.store.connect()) as db:
            ratings = [dict(x) for x in db.execute(
                "SELECT COALESCE(t.category,'general') category,COUNT(*) samples,"
                "SUM(CASE WHEN f.helpful=0 THEN 1 ELSE 0 END) negative "
                "FROM cognitive_feedback f LEFT JOIN experience_feedback_tags t "
                "ON t.message_id=f.message_id GROUP BY category "
                "ORDER BY negative DESC LIMIT 12"
            )]
            audits = [dict(x) for x in db.execute(
                "SELECT state,COUNT(*) n FROM reasoning_audits GROUP BY state"
            )]
        return {"feedback": ratings, "provenance_audits": audits,
                "interpretation": "Сигналы проблем; истинные причины ещё не установлены.",
                "auto_remote_calls": False}

    def observe(self):
        """Propose only drafts when repeated explicit feedback shows a weak skill."""
        signals=self.signals()["feedback"]
        qualified=[x for x in signals if x["negative"]>=4]
        if not qualified:
            return {"proposed":0,"reason":"not_enough_negative_ratings"}
        category=qualified[0]["category"]
        domain=(category if category in DOMAINS else
                "rag" if category in ("sources","accuracy") else "logic")
        with closing(self.store.connect()) as db:
            exists=db.execute(
                "SELECT COUNT(*) FROM evol4_candidates WHERE domain=? AND state='proposed'",
                (domain,)
            ).fetchone()[0]
        if exists>=3:
            return {"proposed":0,"reason":"unreviewed_drafts_exist","domain":domain}
        result=self.generate(domain,count=3,automatic=True)
        return {"proposed":len(result["candidates"]),"domain":domain,
                "automatic_activation":False,"cloud_requests":0}

    def generate(self, domain, *, count=4, confirmed=False, automatic=False):
        """Evolutionary recombination/mutation from a locked library of methods."""
        if domain not in DOMAINS or type(count) is not int or not 3 <= count <= 5:
            raise ValueError("Выбери предметную область и 3–5 вариантов.")
        if confirmed is not True and automatic is not True:
            raise ValueError("Требуется подтверждение генерации гипотез.")
        with closing(self.store.connect()) as db, db:
            parent = db.execute(
                "SELECT c.id,c.recipe FROM evol4_candidates c "
                "JOIN evol4_runs r ON r.candidate_id=c.id AND r.status='completed' "
                "JOIN evol4_scores s ON s.run_id=r.id "
                "WHERE c.domain=? AND r.evaluation_domain=c.domain "
                "GROUP BY c.id "
                "ORDER BY SUM(s.candidate_correct-s.baseline_correct) DESC,c.id DESC LIMIT 1",
                (domain,)
            ).fetchone()
            generation = db.execute(
                "SELECT COALESCE(MAX(generation),0)+1 FROM evol4_candidates WHERE domain=?",
                (domain,)
            ).fetchone()[0]
        parent_id = parent["id"] if parent else None
        ancestors = json.loads(parent["recipe"]) if parent else []
        candidates = list(combinations(METHODS, 2)) + list(combinations(METHODS, 3))
        if ancestors:
            candidates.sort(key=lambda seq:(-len(set(seq)&set(ancestors)),seq))
        # Rotate the candidate search so repeated generations can explore
        # previously untested combinations instead of just the first three.
        offset = (generation-1)*count % len(candidates)
        selected = candidates[offset:]+candidates[:offset]
        result = []
        with closing(self.store.connect()) as db, db:
            for combo in selected:
                recipe = json.dumps(combo, ensure_ascii=False)
                fingerprint = hashlib.sha256((domain+"/"+recipe).encode()).hexdigest()
                cursor = db.execute(
                    "INSERT OR IGNORE INTO evol4_candidates"
                    "(domain,recipe,fingerprint,parent_id,generation)"
                    " VALUES(?,?,?,?,?)",
                    (domain,recipe,fingerprint,parent_id,generation)
                )
                if not cursor.rowcount:
                    continue
                result.append({"id":cursor.lastrowid,"domain":domain,
                               "recipe":list(combo),"generation":generation,
                               "parent_id":parent_id,"state":"proposed"})
                if len(result)>=count:
                    break
        return {"candidates":result,"generated_by":"bounded_recombination",
                "automatically_enabled":False,"cloud_calls":0}

    def add_case(self, domain, question, expected, *, split="holdout", confirmed=False):
        if domain not in DOMAINS or split not in ("train","holdout"):
            raise ValueError("Некорректная область или тип задания.")
        question = checked(question,"Вопрос",500)
        if not isinstance(expected,str) or not 1 <= len(expected.strip()) <= 200:
            raise ValueError("Эталон: требуется короткий проверенный ответ (1–200 символов).")
        expected = expected.strip()
        if confirmed is not True:
            raise ValueError("Нужно подтверждение создания проверенного эталона.")
        fp = hashlib.sha256((domain+"/"+question.casefold()).encode()).hexdigest()
        with closing(self.store.connect()) as db, db:
            if db.execute("SELECT 1 FROM evol4_cases WHERE fingerprint=?", (fp,)).fetchone():
                raise ValueError("Этот вопрос уже есть: не допускаем дубли или утечку в другой набор.")
            cursor = db.execute(
                "INSERT INTO evol4_cases(domain,split,question,expected,fingerprint)"
                " VALUES(?,?,?,?,?)",(domain,split,question,expected,fp)
            )
        return {"id":cursor.lastrowid,"domain":domain,"split":split,
                "sent_to_cloud":False}

    def case_index(self):
        with closing(self.store.connect()) as db:
            return {"cases":[dict(x) for x in db.execute(
                "SELECT id,domain,split,question,expected,created_at "
                "FROM evol4_cases ORDER BY id DESC LIMIT 150"
            )], "gold_answers_local_only":True}

    def curriculum(self, domain):
        if domain not in DOMAINS:
            raise ValueError("Неизвестная предметная область.")
        with closing(self.store.connect()) as db:
            exercises = [dict(x) for x in db.execute(
                "SELECT id,question FROM evol4_cases "
                "WHERE domain=? AND split='train' ORDER BY id LIMIT 10",(domain,)
            )]
            stats = db.execute(
                "SELECT COUNT(*) n,COALESCE(SUM(s.candidate_correct),0) wins "
                "FROM evol4_scores s JOIN evol4_runs r ON r.id=s.run_id "
                "WHERE r.evaluation_domain=? AND r.status='completed'",(domain,)
            ).fetchone()
        rate = stats["wins"]/stats["n"] if stats["n"] else None
        stage = ("foundation" if rate is None or rate<.5 else
                 "practice" if rate<.8 else "challenge")
        return {"domain":domain,"stage":stage,"exercises":exercises,
                "tested_answers":stats["n"],"test_success_rate":round(rate,3) if rate is not None else None,
                "auto_learning_started":False,
                "note":"Уровень выбран по точным совпадениям, не измеряет общий интеллект."}

    def transfer(self, candidate_id):
        if type(candidate_id) is not int or candidate_id<=0:
            raise ValueError("Некорректный кандидат.")
        data = self.report()
        candidate = next((c for c in data["candidates"] if c["id"]==candidate_id),None)
        if not candidate:
            raise ValueError("Кандидат не найден.")
        return {"candidate_id":candidate_id,"original_domain":candidate["domain"],
                "cross_domain_results":candidate["transfer_results"],
                "transfer_verified":bool(candidate["transfer_results"]),
                "no_unmeasured_transfer_claims":True}

    def _summary(self, db, candidate):
        runs=[dict(x) for x in db.execute(
            "SELECT id,evaluation_domain,model,status,provider_calls FROM evol4_runs "
            "WHERE candidate_id=? ORDER BY id", (candidate["id"],)
        )]
        collected={}
        for run in runs:
            if run["status"]!="completed":
                continue
            key=(run["evaluation_domain"],run["model"])
            bucket=collected.setdefault(key, {"cases":set(),"baseline":0,"candidate":0,
                "regressions":0,"baseline_ms":0,"candidate_ms":0,"calls":0})
            for x in db.execute(
                "SELECT case_id,baseline_correct,candidate_correct,baseline_ms,candidate_ms "
                "FROM evol4_scores WHERE run_id=?",(run["id"],)
            ):
                if x["case_id"] in bucket["cases"]:
                    continue
                bucket["cases"].add(x["case_id"])
                bucket["baseline"]+=x["baseline_correct"]
                bucket["candidate"]+=x["candidate_correct"]
                bucket["regressions"]+=int(x["baseline_correct"] and not x["candidate_correct"])
                bucket["baseline_ms"]+=x["baseline_ms"]
                bucket["candidate_ms"]+=x["candidate_ms"]
                bucket["calls"]+=2
        results=[]
        for (domain,model),v in collected.items():
            n=len(v["cases"])
            results.append({
                "domain":domain,"model":model,"cases":n,"baseline_correct":v["baseline"],
                "candidate_correct":v["candidate"],"regressions":v["regressions"],
                "gain":round((v["candidate"]-v["baseline"])/n,3) if n else None,
                "mean_baseline_ms":round(v["baseline_ms"]/n) if n else None,
                "mean_candidate_ms":round(v["candidate_ms"]/n) if n else None,
                "calls":v["calls"],
            })
        own=[v for v in results if v["domain"]==candidate["domain"]]
        candidate["own_results"]=own
        candidate["transfer_results"]=[v for v in results if v["domain"]!=candidate["domain"]]
        candidate["eligible"]=any(v["cases"]>=MIN_PROMOTION_CASES and
                                   v["gain"]>=MIN_GAIN and
                                   v["regressions"]<=max(1,int(v["cases"]*.1))
                                   for v in own)
        candidate["evaluations"]=len(runs)
        candidate["running"]=any(x["status"]=="running" for x in runs)
        candidate["last_status"]=runs[-1]["status"] if runs else None
        candidate["last_run_id"]=runs[-1]["id"] if runs else None
        return candidate

    def report(self):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with closing(self.store.connect()) as db:
            candidates=[self._summary(db,dict(x)) for x in db.execute(
                "SELECT id,domain,recipe,parent_id,generation,state,created_at "
                "FROM evol4_candidates ORDER BY id DESC LIMIT 120"
            )]
            active=[dict(x) for x in db.execute(
                "SELECT domain,candidate_id FROM evol4_active ORDER BY domain")]
            counts=[dict(x) for x in db.execute(
                "SELECT domain,split,COUNT(*) n FROM evol4_cases GROUP BY domain,split")]
            usage=db.execute(
                "SELECT calls_reserved FROM evol4_budget WHERE day=?", (day,)
            ).fetchone()
            history=[dict(x) for x in db.execute(
                "SELECT candidate_id,event,details,created_at "
                "FROM evol4_events ORDER BY id DESC LIMIT 25"
            )]
        for c in candidates:
            c["recipe"]=json.loads(c["recipe"])
        return {"candidates":candidates,"active":active,"cases":counts,
                "events":history,"daily_calls_reserved":usage["calls_reserved"] if usage else 0,
                "daily_call_limit":DAILY_MAX_CALLS,"max_calls_per_batch":MAX_CALLS_PER_BATCH,
                "promotion_requires_cases":MIN_PROMOTION_CASES,
                "auto_activation":False,"estimated_token_billing":False,
                "provider_requests_in_background":False}

    def budget_plan(self):
        """Local prioritisation; not a cost prediction or an API scheduler."""
        report=self.report()
        remaining=max(0,DAILY_MAX_CALLS-report["daily_calls_reserved"])
        priorities=[]
        for c in report["candidates"]:
            if c["state"]!="proposed" or c["running"]:
                continue
            own=max((r["cases"] for r in c["own_results"]),default=0)
            has_regression=any(r["regressions"]>max(1,int(r["cases"]*.1))
                               for r in c["own_results"])
            if has_regression:
                decision="review_regressions_before_more_calls"
            elif own>=MIN_PROMOTION_CASES:
                decision="review_for_manual_activation"
            elif remaining<2:
                decision="wait_for_budget"
            else:
                decision="test_new_holdout_cases"
            priorities.append({
                "candidate_id":c["id"],"domain":c["domain"],
                "assessed_holdout_cases":own,
                "priority":(MIN_PROMOTION_CASES-own) if own<MIN_PROMOTION_CASES else 0,
                "next_step":decision,
            })
        priorities.sort(key=lambda x:(x["next_step"]!="review_for_manual_activation",
                                      -x["assessed_holdout_cases"],x["candidate_id"]))
        return {"today_calls_remaining":remaining,"suggestions":priorities[:12],
                "estimated_billing":False,"automatic_paid_calls":False,
                "max_per_batch":MAX_CALLS_PER_BATCH}

    def _guidance(self, recipe):
        return " ".join(METHODS[name] for name in recipe)[:1400]

    def guidance(self, user_text):
        domain = detect_domain(user_text)
        with closing(self.store.connect()) as db:
            row=db.execute(
                "SELECT c.id,c.recipe FROM evol4_active a "
                "JOIN evol4_candidates c ON c.id=a.candidate_id "
                "WHERE a.domain=? AND c.state='active'",(domain,)
            ).fetchone()
        if not row:
            return None, ""
        return row["id"],self._guidance(json.loads(row["recipe"]))

    def evaluate(self, candidate_id, evaluation_domain, cloud, persona, *,
                 confirmed_send=False, batch_size=MAX_PER_BATCH, baseline_guidance=""):
        if (type(candidate_id) is not int or candidate_id<=0
                or evaluation_domain not in DOMAINS
                or type(batch_size) is not int or not 1<=batch_size<=MAX_PER_BATCH
                or confirmed_send is not True):
            raise ValueError("Для испытания нужна область и явное согласие на 2–12 платных запросов.")
        if not isinstance(baseline_guidance,str) or len(baseline_guidance)>900:
            raise ValueError("Слишком длинная базовая стратегия.")
        with closing(self.store.connect()) as db:
            candidate=db.execute(
                "SELECT id,domain,recipe,state FROM evol4_candidates WHERE id=?",
                (candidate_id,)
            ).fetchone()
            if not candidate or candidate["state"]!="proposed":
                raise ValueError("Проверяются только неактивные гипотезы.")
            tests=[dict(x) for x in db.execute(
                "SELECT id,question,expected FROM evol4_cases "
                "WHERE domain=? AND split='holdout' "
                "AND id NOT IN (SELECT s.case_id FROM evol4_scores s "
                "JOIN evol4_runs r ON r.id=s.run_id "
                "WHERE r.candidate_id=? AND r.status='completed') "
                "ORDER BY id LIMIT ?",(evaluation_domain,candidate_id,batch_size)
            )]
        if len(tests)<batch_size:
            raise ValueError("Недостаточно новых независимых holdout-заданий.")
        strategy=self._guidance(json.loads(candidate["recipe"]))
        baseline_guidance=baseline_guidance[:max(0,1799-len(strategy))]
        proposed=(baseline_guidance+" "+strategy).strip()
        if proposed==baseline_guidance:
            raise ValueError("Гипотеза совпадает с текущим способом ответа.")
        cloud_state=cloud.status()
        model=cloud_state.get("model")
        if not (cloud_state.get("configured") and cloud_state.get("verified") and model):
            raise ValueError("Сначала выбери и проверь модель Cloud.ru.")
        n_calls=2*batch_size
        guard=getattr(cloud,"guard",None)
        if guard:
            limits=guard.status()
            if (limits["limits"]["chat_per_day"]-limits["today"].get("chat",0)<n_calls
                    or limits["limits"]["per_minute"]<n_calls):
                raise ValueError("Недостаточно Cloud.ru лимитов для указанной партии.")
        day=datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with closing(self.store.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute(
                    "SELECT id FROM evol4_runs WHERE status='running' LIMIT 1"
                ).fetchone():
                    raise ValueError("Незавершённый эксперимент. Требуется восстановление.")
                current=db.execute(
                    "SELECT calls_reserved FROM evol4_budget WHERE day=?",(day,)
                ).fetchone()
                used=current["calls_reserved"] if current else 0
                if used+n_calls>DAILY_MAX_CALLS:
                    raise ValueError("Дневной бюджет экспериментов Cognitive Soul исчерпан.")
                db.execute(
                    "INSERT INTO evol4_budget(day,calls_reserved) VALUES(?,?) "
                    "ON CONFLICT(day) DO UPDATE SET calls_reserved=excluded.calls_reserved",
                    (day,used+n_calls)
                )
                cursor=db.execute(
                    "INSERT INTO evol4_runs(candidate_id,evaluation_domain,model,status)"
                    " VALUES(?,?,?,'running')",(candidate_id,evaluation_domain,model)
                )
                run_id=cursor.lastrowid
                db.commit()
            except BaseException:
                db.rollback()
                raise
        actual_calls=0
        try:
            for case in tests:
                grades=[]; latencies=[]
                for guidance in (baseline_guidance, proposed):
                    if cloud.status().get("model")!=model:
                        raise ValueError("Cloud.ru модель изменилась в процессе эксперимента.")
                    start=time.monotonic()
                    answer=cloud.complete(
                        persona,[{"role":"user","content":case["question"]}],
                        [],extra_guidance=guidance)
                    actual_calls+=1
                    if cloud.status().get("model")!=model:
                        raise ValueError("Cloud.ru модель изменилась в процессе эксперимента.")
                    grades.append(int(canonical(answer)==canonical(case["expected"])))
                    latencies.append(int((time.monotonic()-start)*1000))
                with closing(self.store.connect()) as db, db:
                    db.execute(
                        "INSERT INTO evol4_scores(run_id,case_id,baseline_correct,"
                        "candidate_correct,baseline_ms,candidate_ms)"
                        " VALUES(?,?,?,?,?,?)",
                        (run_id,case["id"],grades[0],grades[1],latencies[0],latencies[1])
                    )
                    db.execute(
                        "UPDATE evol4_runs SET provider_calls=? WHERE id=?",
                        (actual_calls,run_id)
                    )
        except Exception:
            with closing(self.store.connect()) as db, db:
                db.execute(
                    "UPDATE evol4_runs SET status='failed',provider_calls=? WHERE id=?",
                    (actual_calls,run_id)
                )
            raise
        with closing(self.store.connect()) as db, db:
            db.execute(
                "UPDATE evol4_runs SET status='completed',provider_calls=? WHERE id=?",
                (actual_calls,run_id)
            )
            db.execute(
                "INSERT INTO evol4_events(candidate_id,event,details) VALUES(?,?,?)",
                (candidate_id,"tested",evaluation_domain)
            )
            candidate_summary=self._summary(db,{"id":candidate_id,"domain":candidate["domain"]})
        return {"run_id":run_id,"provider_calls":actual_calls,
                "evaluation_domain":evaluation_domain,
                "candidate":candidate_summary,"gold_sent":False,
                "automatic_promotion":False,
                "score_type":"normalized_exact_match_not_semantic_truth"}

    def decide(self, candidate_id, *, approve, confirmed=False):
        if type(candidate_id) is not int or candidate_id<=0 or type(approve) is not bool or confirmed is not True:
            raise ValueError("Решение требует подтверждения.")
        with closing(self.store.connect()) as db, db:
            row=db.execute(
                "SELECT id,domain,state FROM evol4_candidates WHERE id=?",(candidate_id,)
            ).fetchone()
            if not row:
                raise ValueError("Неизвестный кандидат.")
            previous=db.execute(
                "SELECT candidate_id FROM evol4_active WHERE domain=?", (row["domain"],)
            ).fetchone()["candidate_id"]
            if approve:
                if row["state"]!="proposed":
                    raise ValueError("Активировать можно только новую гипотезу.")
                info=self._summary(db,dict(row))
                if not info["eligible"]:
                    raise ValueError("Нужны >=30 независимых контрольных заданий,"
                                     " выигрыш >=10 п.п. и отсутствие сильной деградации.")
                if previous:
                    db.execute(
                        "UPDATE evol4_candidates SET state='retired' WHERE id=?",(previous,)
                    )
                db.execute(
                    "UPDATE evol4_candidates SET state='active' WHERE id=?",(candidate_id,)
                )
                db.execute(
                    "UPDATE evol4_active SET candidate_id=? WHERE domain=?",
                    (candidate_id,row["domain"])
                )
                action="activated"
            else:
                if previous != candidate_id:
                    raise ValueError("Откатить можно только активную стратегию.")
                db.execute(
                    "UPDATE evol4_candidates SET state='paused' WHERE id=?",(candidate_id,)
                )
                db.execute(
                    "UPDATE evol4_active SET candidate_id=NULL WHERE domain=?",(row["domain"],)
                )
                action="rolled_back"
            db.execute(
                "INSERT INTO evol4_events(candidate_id,event) VALUES(?,?)",
                (candidate_id,action)
            )
        return {"candidate_id":candidate_id,"state":action,
                "active":candidate_id if approve else None}

    def applied(self, message_id, candidate_id):
        if candidate_id is None:
            return
        if type(message_id) is not int or message_id<=0 or type(candidate_id) is not int:
            raise ValueError("Некорректный ID.")
        with closing(self.store.connect()) as db, db:
            valid=db.execute(
                "SELECT 1 FROM messages WHERE id=? AND role='assistant'",(message_id,)
            ).fetchone()
            if not valid:
                raise ValueError("Можно связывать только сохранённый ответ ассистента.")
            db.execute(
                "INSERT OR IGNORE INTO evol4_applied(message_id,candidate_id)"
                " VALUES(?,?)",(message_id,candidate_id)
            )

    def feedback(self, message_id, helpful):
        if type(message_id) is not int or type(helpful) is not bool:
            raise ValueError("Некорректная оценка.")
        with closing(self.store.connect()) as db, db:
            applied=db.execute(
                "SELECT candidate_id FROM evol4_applied WHERE message_id=?",(message_id,)
            ).fetchone()
            if applied is None:
                return {"measured":False}
            cid=applied["candidate_id"]
            db.execute(
                "UPDATE evol4_applied SET helpful=? WHERE message_id=?",
                (int(helpful),message_id)
            )
            row=db.execute(
                "SELECT COUNT(*) n,SUM(helpful) positive FROM evol4_applied "
                "WHERE candidate_id=? AND helpful IS NOT NULL",(cid,)
            ).fetchone()
            n=row["n"]; rate=row["positive"]/n if n else None
            active=db.execute(
                "SELECT domain FROM evol4_active WHERE candidate_id=?",(cid,)
            ).fetchone()
            suspended=bool(active and n>=MIN_FEEDBACK and rate<=UNHELPFUL_SUSPEND_RATE)
            if suspended:
                db.execute(
                    "UPDATE evol4_active SET candidate_id=NULL WHERE candidate_id=?",(cid,)
                )
                db.execute(
                    "UPDATE evol4_candidates SET state='paused' WHERE id=?",(cid,)
                )
                db.execute(
                    "INSERT INTO evol4_events(candidate_id,event) "
                    "VALUES(?,'automatic_suspension')",(cid,)
                )
        return {"measured":True,"candidate_id":cid,"samples":n,
                "positive_rate":round(rate,3),"suspended":suspended}

    def recover(self, run_id, *, confirmed=False):
        if type(run_id) is not int or run_id<=0 or confirmed is not True:
            raise ValueError("Нужно подтвердить ID зависшего теста.")
        with closing(self.store.connect()) as db, db:
            row=db.execute(
                "SELECT status FROM evol4_runs WHERE id=?",(run_id,)
            ).fetchone()
            if row is None or row["status"]!="running":
                raise ValueError("Закрыть можно только незавершённое испытание.")
            db.execute(
                "UPDATE evol4_runs SET status='failed' WHERE id=?",(run_id,)
            )
        return {"run_id":run_id,"status":"failed","can_activate":False}

    def peer_review(self, candidate_id):
        """Independent layers; never claim factual or semantic verification."""
        candidate=next((c for c in self.report()["candidates"] if c["id"]==candidate_id),None)
        if not candidate:
            raise ValueError("Кандидат не найден.")
        return {
            "candidate_id":candidate_id,
            "layers":{
                "syntax_and_policy":"passed_fixed_allowlisted_methods",
                "objective_testing":"normalized_exact_match_only",
                "factual_grounding":"not_verified",
                "cross_domain":"measured" if candidate["transfer_results"] else "not_tested",
                "regression":"tracked_per_holdout_question",
                "latency":"tracked_wall_clock_ms",
                "cost":"bounded_calls_not_real_billing",
            },
            "eligible_for_human_approval":candidate["eligible"],
            "model_weights_changed":False}
