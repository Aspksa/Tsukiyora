"""Local Cloud.ru request quotas and privacy-preserving operational events.

SQLite transaction serializes requests across HTTP worker threads. Limits are
request counts, not provider billing amounts; no keys, prompts, chat text,
stack traces or raw exception messages are persisted.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time

DEFAULT_CHAT_DAILY=100
DEFAULT_EMBED_DAILY=120
MINUTE_WINDOW=60
ALLOWED_EVENTS=frozenset(("api_error","cloud_failure","stream_cancel","backup_failure",
                         "db_integrity_warning","maintenance_failure"))
ALLOWED_KINDS=frozenset(("chat","embeddings"))
ALLOWED_REASONS=frozenset(("network","quota","validation","provider","sqlite","cancelled","unknown"))


class LimitReached(ValueError):
    pass


class ResourceGuard:
    def __init__(self,db_path:Path):
        self.path=Path(db_path)

    def connect(self):
        db=sqlite3.connect(self.path, timeout=12)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA busy_timeout=12000")
        return db

    def initialize(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as db:
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS cloud_limits (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        chat_per_day INTEGER NOT NULL DEFAULT 100 CHECK(chat_per_day BETWEEN 1 AND 1000),
                        embeddings_per_day INTEGER NOT NULL DEFAULT 120 CHECK(embeddings_per_day BETWEEN 1 AND 1000),
                        per_minute INTEGER NOT NULL DEFAULT 10 CHECK(per_minute BETWEEN 1 AND 60)
                    );
                    INSERT OR IGNORE INTO cloud_limits(id) VALUES(1);
                    CREATE TABLE IF NOT EXISTS cloud_attempts(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        kind TEXT NOT NULL CHECK(kind IN ('chat','embeddings')),
                        minute_bucket INTEGER NOT NULL,
                        day_bucket TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS ix_cloud_attempts_day
                        ON cloud_attempts(day_bucket,kind);
                    CREATE INDEX IF NOT EXISTS ix_cloud_attempts_minute
                        ON cloud_attempts(minute_bucket);
                    CREATE TABLE IF NOT EXISTS system_events(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)

    def limits(self)->dict:
        with closing(self.connect()) as db:
            row=db.execute("SELECT chat_per_day,embeddings_per_day,per_minute "
                           "FROM cloud_limits WHERE id=1").fetchone()
            return dict(row)

    def configure(self,*,chat_per_day:int,embeddings_per_day:int,per_minute:int)->dict:
        for name,val,maxval in (("chat_per_day",chat_per_day,1000),
                                ("embeddings_per_day",embeddings_per_day,1000),
                                ("per_minute",per_minute,60)):
            if type(val) is not int or not 1 <= val <= maxval:
                raise ValueError("Invalid quota for "+name)
        with closing(self.connect()) as db,db:
            db.execute("UPDATE cloud_limits SET chat_per_day=?,embeddings_per_day=?,"
                       "per_minute=? WHERE id=1",
                       (chat_per_day,embeddings_per_day,per_minute))
        return self.status()

    def reserve(self,kind:str)->dict:
        """Atomically reserve capacity BEFORE a possible paid provider call."""
        if kind not in ALLOWED_KINDS:
            raise ValueError("Unknown cloud operation")
        now=datetime.now(timezone.utc)
        day=now.strftime("%Y-%m-%d")
        minute=int(now.timestamp()//60)
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row=db.execute("SELECT chat_per_day,embeddings_per_day,per_minute "
                               "FROM cloud_limits WHERE id=1").fetchone()
                day_limit=row["chat_per_day" if kind=="chat" else "embeddings_per_day"]
                day_count=db.execute("SELECT COUNT(*) FROM cloud_attempts "
                                     "WHERE day_bucket=? AND kind=?",(day,kind)).fetchone()[0]
                minute_count=db.execute("SELECT COUNT(*) FROM cloud_attempts "
                                        "WHERE minute_bucket>=?",(minute,)).fetchone()[0]
                if day_count>=day_limit or minute_count>=row["per_minute"]:
                    db.rollback()
                    raise LimitReached("Лимит запросов Cloud.ru достигнут. Измени его в настройках или подожди.")
                db.execute("INSERT INTO cloud_attempts(kind,minute_bucket,day_bucket) "
                           "VALUES(?,?,?)",(kind,minute,day))
                # Retain only 8 days of coarse request counts.
                from datetime import timedelta
                old=(now-timedelta(days=8)).strftime("%Y-%m-%d")
                db.execute("DELETE FROM cloud_attempts WHERE day_bucket<?",(old,))
                db.commit()
            except Exception:
                if db.in_transaction: db.rollback()
                raise
        return {"kind":kind,"day_used":day_count+1,"day_limit":day_limit,
                "minute_used":minute_count+1}

    def status(self)->dict:
        now=datetime.now(timezone.utc)
        day=now.strftime("%Y-%m-%d")
        minute=int(now.timestamp()//60)
        with closing(self.connect()) as db:
            quotas=dict(db.execute("SELECT chat_per_day,embeddings_per_day,per_minute "
                                   "FROM cloud_limits WHERE id=1").fetchone())
            daily=[dict(x) for x in db.execute(
                "SELECT kind,COUNT(*) AS used FROM cloud_attempts "
                "WHERE day_bucket=? GROUP BY kind",(day,))]
            minute_count=db.execute("SELECT COUNT(*) FROM cloud_attempts "
                                    "WHERE minute_bucket>=?",(minute,)).fetchone()[0]
            recent=[dict(x) for x in db.execute(
                "SELECT event,reason,created_at FROM system_events "
                "ORDER BY id DESC LIMIT 15")]
        return {"limits":quotas,"today":{x["kind"]:x["used"] for x in daily},
                "minute_used":minute_count,"recent_events":recent,
                "estimated_billing":False,
                "notice":"Считаются попытки запросов, а не оплаченные токены Cloud.ru."}

    def event(self,event:str,reason:str)->None:
        if event not in ALLOWED_EVENTS or reason not in ALLOWED_REASONS:
            raise ValueError("Invalid operational event")
        with closing(self.connect()) as db,db:
            db.execute("INSERT INTO system_events(event,reason) VALUES(?,?)",
                       (event,reason))
            db.execute("DELETE FROM system_events WHERE id NOT IN "
                       "(SELECT id FROM system_events ORDER BY id DESC LIMIT 250)")
