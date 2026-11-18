"""Non-destructive SQLite schema migrations and pre-upgrade safety checks.

Runs offline/local, never migrates secrets or uploads a database. A pre-upgrade
snapshot is created before updating existing user databases.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import sqlite3

from memory_store import MemoryStore

SCHEMA_VERSION=11


class MigrationGuard:
    def __init__(self,path):
        self.path=Path(path)
        self.backups=self.path.parent/"backups"

    def prepare(self)->dict:
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size==0:
            return {"existing":False,"backup":None}
        with closing(sqlite3.connect(self.path,timeout=15)) as db:
            result=db.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0]!="ok":
                raise sqlite3.DatabaseError("Existing SQLite database failed pre-migration integrity check.")
            version=db.execute("PRAGMA user_version").fetchone()[0]
            if version>SCHEMA_VERSION:
                raise ValueError("SQLite schema is newer than this version of Tsukiyora.")
            existing=db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_items'"
            ).fetchone() is not None
        if version==SCHEMA_VERSION or not existing:
            return {"existing":existing,"version":version,"backup":None}
        # Reuse the online-consistent snapshot mechanism, retaining a local
        # recovery point before any v0.9 DDL executes.
        snapshot=MemoryStore(self.path).backup(self.backups)
        return {"existing":True,"version":version,"backup":snapshot.name}

    def finish(self):
        with closing(sqlite3.connect(self.path,timeout=10)) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0]!="ok":
                raise sqlite3.DatabaseError("Post-migration integrity check failed.")
            with db:
                db.execute("PRAGMA user_version="+str(SCHEMA_VERSION))

    def audit(self)->dict:
        with closing(sqlite3.connect(self.path,timeout=10)) as db:
            version=db.execute("PRAGMA user_version").fetchone()[0]
            check=db.execute("PRAGMA quick_check").fetchone()[0]
            fk=db.execute("PRAGMA foreign_key_check").fetchmany(10)
            tables={r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            triggers={r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )}
            fts="memory_fts" in tables
            duplicates=0
            missing=0
            if fts:
                duplicates=db.execute(
                    "SELECT COUNT(*) FROM (SELECT rowid,COUNT(*) AS c "
                    "FROM memory_fts GROUP BY rowid HAVING c>1)"
                ).fetchone()[0]
                missing=db.execute(
                    "SELECT COUNT(*) FROM memory_items m WHERE m.status='active' "
                    "AND NOT EXISTS(SELECT 1 FROM memory_fts f WHERE f.rowid=m.id)"
                ).fetchone()[0]
        return {"version":version,"target_version":SCHEMA_VERSION,
                "sqlite_integrity":check=="ok","foreign_key_errors":len(fk),
                "fts_index_available":fts,
                "fts_missing_rows":missing,"fts_duplicate_rows":duplicates,
                "orphaned_old_update_triggers":any(
                    name in triggers for name in ("memory_fts_au_del","memory_fts_au_ins")
                ),
                "no_data_sent_to_cloud":True}
