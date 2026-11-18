"""Deterministic Academy6 control questions for user-authorized evaluation.

No hidden LLM calls or code edits. Scores are coarse keyword/length probes,
NOT a substitute for expert human rating or model-weight training.
"""
from __future__ import annotations
from contextlib import closing
import re
from memory_store import MemoryStore

QUESTIONS = {
  "sources": (
    "Что показывает ссылка [Память #ID]?",
    ("источник","не"), 550
  ),
  "accuracy": (
    "Что делать, если два подтверждённых воспоминания противоречат друг другу?",
    ("провери","противореч"), 650
  ),
  "clarity": (
    "Как объяснить сложный процесс человеку, который видит его впервые?",
    ("шаг","понят"), 650
  ),
  "brevity": (
    "Как кратко ответить на простой вопрос, не скрывая важных ограничений?",
    ("крат","огранич"), 400
  )
}


class AcademyPractice:
    def __init__(self,path):
        self.store=MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db,db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS academy_practice_runs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill TEXT NOT NULL,
                    passed INTEGER NOT NULL CHECK(passed IN(0,1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def questions(self):
        return {"questions":[{"skill":skill,"question":record[0]}
                             for skill,record in QUESTIONS.items()],
                "method":"explicit_offline_probe_only",
                "real_model_checked":False}

    def evaluate(self,skill,answer,*,confirmed=False):
        if confirmed is not True or skill not in QUESTIONS:
            raise ValueError("Выбери контрольный вопрос и подтверди оценку.")
        if not isinstance(answer,str) or not 1<=len(answer.strip())<=2000:
            raise ValueError("Ответ должен содержать 1–2000 символов.")
        expected=QUESTIONS[skill]
        normalized=answer.casefold()
        passed=all(word in normalized for word in expected[1]) and len(answer)<=expected[2]
        with closing(self.store.connect()) as db,db:
            db.execute("INSERT INTO academy_practice_runs(skill,passed) VALUES(?,?)",
                       (skill,int(passed)))
            db.execute(
                "DELETE FROM academy_practice_runs WHERE id NOT IN "
                "(SELECT id FROM academy_practice_runs ORDER BY id DESC LIMIT 200)"
            )
        return {"skill":skill,"passed":passed,"method":"bounded_keyword_and_length_check",
                "real_world_accuracy_verified":False,
                "model_weight_changes":False}

    def status(self):
        with closing(self.store.connect()) as db:
            scores=[dict(x) for x in db.execute(
                "SELECT skill,COUNT(*) AS runs,SUM(passed) AS passed "
                "FROM academy_practice_runs GROUP BY skill"
            )]
        return {"results":scores,"is_human_quality_rating":False,
                "method":"offline_control_questions",
                "automated_cloud_calls":0}
