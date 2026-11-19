"""Materialized SQLite atlas counts, refreshed once and maintained by triggers.

The cache is derived data, never a source of truth. Build is atomic with
BEGIN IMMEDIATE; future INSERT/UPDATE/DELETE events update count summaries.
Invalid/missing summaries fall back to the source tables.
"""
from __future__ import annotations
from contextlib import closing
import sqlite3

from tsukiyora.memory.memory_store import MemoryStore

REGION_SIZE=8192
KINDS={"fact":0,"preference":2,"event":4,"biography":6}


class AtlasMaterializer:
    def __init__(self,path):
        self.store=MemoryStore(path)

    def initialize(self):
        with closing(self.store.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS atlas_region_totals(
                        sector INTEGER NOT NULL,
                        region INTEGER NOT NULL,
                        n INTEGER NOT NULL CHECK(n>=0),
                        PRIMARY KEY(sector,region)
                    ) WITHOUT ROWID;
                    CREATE TABLE IF NOT EXISTS atlas_cache_meta(
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        initialized INTEGER NOT NULL DEFAULT 0
                    );
                    INSERT OR IGNORE INTO atlas_cache_meta(id,initialized) VALUES(1,0);
                """)
                # Existing databases are indexed only at startup or if invalid.
                state=db.execute(
                    "SELECT initialized FROM atlas_cache_meta WHERE id=1"
                ).fetchone()[0]
                if not state:
                    self._rebuild_locked(db)
                db.executescript("""
                    CREATE TRIGGER IF NOT EXISTS atlas_insert
                    AFTER INSERT ON memory_items WHEN NEW.status='active'
                    BEGIN
                        INSERT INTO atlas_region_totals(sector,region,n)
                        VALUES(
                            CASE NEW.kind WHEN 'fact' THEN 0 WHEN 'preference' THEN 2
                            WHEN 'event' THEN 4 ELSE 6 END + (NEW.id%2),
                            CAST((NEW.id-1)/8192 AS INTEGER),1
                        ) ON CONFLICT(sector,region) DO UPDATE SET n=n+1;
                    END;
                    CREATE TRIGGER IF NOT EXISTS atlas_delete
                    AFTER DELETE ON memory_items WHEN OLD.status='active'
                    BEGIN
                        UPDATE atlas_region_totals SET n=n-1
                        WHERE sector=(CASE OLD.kind WHEN 'fact' THEN 0
                            WHEN 'preference' THEN 2 WHEN 'event' THEN 4 ELSE 6 END)+(OLD.id%2)
                        AND region=CAST((OLD.id-1)/8192 AS INTEGER);
                        DELETE FROM atlas_region_totals WHERE n=0;
                    END;
                    CREATE TRIGGER IF NOT EXISTS atlas_update
                    AFTER UPDATE OF kind,status ON memory_items
                    WHEN OLD.status<>NEW.status OR OLD.kind<>NEW.kind
                    BEGIN
                        UPDATE atlas_region_totals SET n=n-1
                        WHERE OLD.status='active'
                          AND sector=(CASE OLD.kind WHEN 'fact' THEN 0
                            WHEN 'preference' THEN 2 WHEN 'event' THEN 4 ELSE 6 END)+(OLD.id%2)
                          AND region=CAST((OLD.id-1)/8192 AS INTEGER);
                        DELETE FROM atlas_region_totals WHERE n=0;
                        INSERT INTO atlas_region_totals(sector,region,n)
                        SELECT (CASE NEW.kind WHEN 'fact' THEN 0
                            WHEN 'preference' THEN 2 WHEN 'event' THEN 4 ELSE 6 END)+(NEW.id%2),
                               CAST((NEW.id-1)/8192 AS INTEGER),1
                        WHERE NEW.status='active'
                        ON CONFLICT(sector,region) DO UPDATE SET n=n+1;
                    END;
                """)
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _rebuild_locked(self,db):
        db.execute("DELETE FROM atlas_region_totals")
        db.execute(
            "INSERT INTO atlas_region_totals(sector,region,n) "
            "SELECT CASE kind WHEN 'fact' THEN 0 WHEN 'preference' THEN 2 "
            "WHEN 'event' THEN 4 ELSE 6 END+(id%2), "
            "CAST((id-1)/8192 AS INTEGER),COUNT(*) "
            "FROM memory_items WHERE status='active' "
            "GROUP BY 1,2"
        )
        db.execute("UPDATE atlas_cache_meta SET initialized=1 WHERE id=1")

    def rebuild(self):
        with closing(self.store.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._rebuild_locked(db)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return self.status()

    def status(self):
        with closing(self.store.connect()) as db:
            tables={x[0] for x in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if not {"atlas_region_totals","atlas_cache_meta"}<=tables:
                return {"ready":False,"regions":0,"total":0}
            ready=bool(db.execute("SELECT initialized FROM atlas_cache_meta WHERE id=1").fetchone()[0])
            count,total=db.execute(
                "SELECT COUNT(*),COALESCE(SUM(n),0) FROM atlas_region_totals"
            ).fetchone()
        return {"ready":ready,"regions":count,"total":total,"materialized":True}

    def verify(self):
        with closing(self.store.connect()) as db:
            truth=db.execute(
                "SELECT COUNT(*) FROM memory_items WHERE status='active'"
            ).fetchone()[0]
        cached=self.status()
        return {"verified":cached["ready"] and cached["total"]==truth,
                "source_count":truth,"cached_count":cached["total"],
                "action":"rebuild_derived_cache_only" if cached["total"]!=truth else "none"}
