"""GREY MATTER 14.1 — measurable quality of *persisted real answers*.

Does not ask another LLM to grade itself. Locally audits source references
in actually saved assistant replies, records user-attested quality dimensions,
and gives a short, fixed, same-conversation correction hint only after an
explicit negative user review. Existing Cloud.ru calls, persona permissions,
academy trials, and one SQLite worker remain untouched.

Metrics are observations, not independently verified answer truth or causal
proof of improvement.
"""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import re

from tsukiyora.memory.memory_store import MemoryStore

AXES = ("accuracy", "clarity", "grounding", "continuity")
FIXED_HINTS = {
    "accuracy": "В этом разговоре была отмечена фактическая ошибка. Перепроверь доступные источники, не угадывай и отделяй факты от предположений.",
    "clarity": "В этом разговоре отмечали неясный ответ. Сначала дай понятный прямой вывод, затем краткие необходимые пояснения.",
    "grounding": "В этом разговоре не хватало подтверждений. Указывай только действительно доступные источники; если доказательств нет, прямо сообщи об этом.",
    "continuity": "В этом разговоре была потеряна нить беседы. Сверь текущую задачу с последними сообщениями пользователя и не утверждай, что что-либо уже сделано без результата.",
}
ASKED_CITATION = re.compile(
    r"источник|ссылк|доказательств|цитат|source|citation|reference", re.I)


def _digest(value):
    return sha256(value.encode("utf-8")).hexdigest()


class RealAnswerQuality:
    def __init__(self, path):
        self.store = MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS answer15_audits(
                    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                    answer_sha256 TEXT NOT NULL,
                    source_state TEXT NOT NULL,
                    valid_count INTEGER NOT NULL CHECK(valid_count>=0),
                    missing_count INTEGER NOT NULL CHECK(missing_count>=0),
                    conflict_count INTEGER NOT NULL CHECK(conflict_count>=0),
                    asked_for_citations INTEGER NOT NULL CHECK(
                        asked_for_citations IN (0,1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS answer15_reviews(
                    message_id INTEGER PRIMARY KEY REFERENCES messages(id),
                    answer_sha256 TEXT NOT NULL,
                    accuracy INTEGER CHECK(accuracy IN (0,1)),
                    clarity INTEGER CHECK(clarity IN (0,1)),
                    grounding INTEGER CHECK(grounding IN (0,1)),
                    continuity INTEGER CHECK(continuity IN (0,1)),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CHECK(accuracy IS NOT NULL OR clarity IS NOT NULL
                        OR grounding IS NOT NULL OR continuity IS NOT NULL)
                );
                CREATE INDEX IF NOT EXISTS idx_answer15_audit_bad
                    ON answer15_audits(source_state,message_id DESC);
            """)

    def _assistant(self, db, message_id):
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("Нужен настоящий ID ответа Цукиёры.")
        row = db.execute(
            "SELECT id,conversation_id,content,created_at "
            "FROM messages WHERE id=? AND role='assistant'",
            (message_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Указанный ответ не найден.")
        return row

    def observe(self, core, message_id, answer):
        """Audit the actual saved model response after completion, not a draft.

        The existing ReasoningAudit only checks citations to existing sources.
        This method preserves its non-truth guarantee and stores counts, not
        the model's text or a second copy of private conversation history.
        """
        if not isinstance(answer, str) or not answer or len(answer)>32000:
            raise ValueError("Некорректный сохранённый ответ.")
        with closing(self.store.connect()) as db:
            row = self._assistant(db, message_id)
            if row["content"] != answer:
                raise ValueError("Текст ответа отличается от сохранённого.")
            prompt=db.execute(
                "SELECT content FROM messages WHERE conversation_id=? "
                "AND role='user' AND id<? ORDER BY id DESC LIMIT 1",
                (row["conversation_id"],message_id)
            ).fetchone()
        existing=core.audit.verify(message_id, answer)
        asked=bool(prompt and ASKED_CITATION.search(prompt["content"][:8000]))
        with closing(self.store.connect()) as db, db:
            db.execute(
                "INSERT INTO answer15_audits "
                "(message_id,answer_sha256,source_state,valid_count,missing_count,"
                "conflict_count,asked_for_citations) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(message_id) DO UPDATE SET "
                "answer_sha256=excluded.answer_sha256,"
                "source_state=excluded.source_state,"
                "valid_count=excluded.valid_count,"
                "missing_count=excluded.missing_count,"
                "conflict_count=excluded.conflict_count,"
                "asked_for_citations=excluded.asked_for_citations",
                (message_id,_digest(answer),existing["state"],
                 len(existing["valid_citations"]),
                 len(existing["missing_citations"]),
                 len(existing["possible_conflicts"]),int(asked))
            )
        return {"message_id":message_id,"source_state":existing["state"],
                "valid_citations":len(existing["valid_citations"]),
                "missing_citations":len(existing["missing_citations"]),
                "possible_conflicts":len(existing["possible_conflicts"]),
                "asked_for_citations":asked,
                "citation_gap_signal":asked and not existing["valid_citations"],
                "truth_verified":False,"model_called":False}

    def review(self, message_id, *, confirmed=False, **dimensions):
        if confirmed is not True or set(dimensions)-set(AXES):
            raise ValueError("Требуется явное подтверждение известных критериев.")
        values=[dimensions.get(k) for k in AXES]
        if all(x is None for x in values) or any(
                type(x) not in (bool,type(None)) for x in values):
            raise ValueError("Оцени хотя бы один критерий как да или нет.")
        normalized=[int(v) if v is not None else None for v in values]
        with closing(self.store.connect()) as db, db:
            row=self._assistant(db,message_id)
            db.execute(
                "INSERT INTO answer15_reviews "
                "(message_id,answer_sha256,accuracy,clarity,grounding,continuity)"
                " VALUES(?,?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET "
                "answer_sha256=excluded.answer_sha256,"
                "accuracy=excluded.accuracy,clarity=excluded.clarity,"
                "grounding=excluded.grounding,continuity=excluded.continuity,"
                "updated_at=CURRENT_TIMESTAMP",
                (message_id,_digest(row["content"]),*normalized)
            )
        return {"message_id":message_id,"recorded":True,
                "judgement":"user_attested_not_independent_verification",
                "changed_persona":False,"activated_strategy":False,
                "extra_cloud_calls":0}

    def recent(self, conversation_id, *, limit=20):
        if (not isinstance(conversation_id,str) or not 1<=len(conversation_id)<=64
                or type(limit) is not int or not 1<=limit<=50):
            raise ValueError("Некорректный диалог или размер списка.")
        with closing(self.store.connect()) as db:
            rows=[dict(r) for r in db.execute(
                "SELECT m.id,m.created_at,r.accuracy,r.clarity,r.grounding,"
                "r.continuity,a.source_state,a.missing_count,a.conflict_count,"
                "a.asked_for_citations,a.valid_count "
                "FROM messages m LEFT JOIN answer15_reviews r "
                "ON r.message_id=m.id LEFT JOIN answer15_audits a "
                "ON a.message_id=m.id "
                "WHERE m.conversation_id=? AND m.role='assistant' "
                "ORDER BY m.id DESC LIMIT ?",(conversation_id,limit)
            )]
        return {"items":rows,"message_text_copied":False,
                "automatic_model_judging":False,"cloud_calls":0}

    def report(self, *, limit=400):
        if type(limit) is not int or not 1<=limit<=1000:
            raise ValueError("Некорректный размер отчёта.")
        with closing(self.store.connect()) as db:
            reviewed=[dict(x) for x in db.execute(
                "SELECT r.*,COALESCE(u.model,'unrecorded') model "
                "FROM answer15_reviews r LEFT JOIN usage_estimates u "
                "ON u.message_id=r.message_id "
                "ORDER BY r.message_id DESC LIMIT ?",(limit,)
            )]
            audits=db.execute(
                "SELECT COUNT(*) total,"
                "COALESCE(SUM(missing_count>0),0) missing,"
                "COALESCE(SUM(conflict_count>0),0) conflicts,"
                "COALESCE(SUM(asked_for_citations=1 AND valid_count=0),0) "
                "citation_gaps FROM answer15_audits"
            ).fetchone()
        summary={}
        for model in sorted({r["model"] for r in reviewed}):
            group=[row for row in reviewed if row["model"]==model]
            columns={}
            for k in AXES:
                scored=[x[k] for x in group if x[k] is not None]
                columns[k]={"samples":len(scored),
                            "positive":sum(scored),
                            "positive_rate":round(sum(scored)/len(scored),3)
                                if scored else None}
            summary[model]={"reviewed_answers":len(group),"dimensions":columns,
                            "reliable_comparison":False}
        return {"reviewed_real_answers":len(reviewed),"by_model":summary,
                "source_audits":{"total":audits["total"],
                                 "missing_source_refs":audits["missing"],
                                 "possible_conflicts":audits["conflicts"],
                                 "requested_citation_without_valid_ref":
                                    audits["citation_gaps"]},
                "metric":"user_attested_real_reply_observations",
                "source_check_not_truth_verification":True,
                "no_claimed_causal_improvement":True,
                "no_extra_paid_cloud_calls":True}

    def guidance(self, conversation_id):
        """A small fixed correction to the NEXT reply in this conversation.

        Only based on a user-reviewed failure. Does not train the model or
        change approval of any strategy, fact, or personality.
        """
        if not isinstance(conversation_id,str) or not 1<=len(conversation_id)<=64:
            raise ValueError("Некорректный ID диалога.")
        with closing(self.store.connect()) as db:
            row=db.execute(
                "SELECT r.message_id,r.accuracy,r.clarity,r.grounding,r.continuity "
                "FROM answer15_reviews r JOIN messages m ON m.id=r.message_id "
                "WHERE m.conversation_id=? ORDER BY r.message_id DESC LIMIT 1",
                (conversation_id,)
            ).fetchone()
        if not row:
            return ""
        for key in AXES:
            if row[key]==0:
                return ("[Пользовательская оценка ответа #"+str(row["message_id"])+
                        "] " + FIXED_HINTS[key])[:220]
        return ""

    def status(self, core):
        info=self.report(limit=400)
        return {"version":"14.1","name":"GREY MATTER — Real Answer Quality",
                **info,
                "existing_v14_logic":core.grey14.status(core)["evaluated_cases"],
                "manual_review_required":True,
                "same_cloud_request_for_next_turn":True,
                "extra_background_cloud_calls":0,
                "no_new_queue_or_worker":True,
                "protected_persona_unchanged":True}
