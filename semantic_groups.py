"""Non-destructive user-confirmed semantic grouping of local memory facts.

No automatic truth verification or deletion. Original rows, sources and
revisions remain intact. Corrections invalidate a group's content signatures
until re-confirmed, preventing outdated equivalences from being trusted.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from memory_quality import compare
from memory_store import MemoryStore


class SemanticGroups:
    def __init__(self,path:Path):
        self.store=MemoryStore(path)

    def initialize(self)->None:
        with closing(self.store.connect()) as db:
            with db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS semantic_groups(
                        left_id INTEGER NOT NULL REFERENCES memory_items(id),
                        right_id INTEGER NOT NULL REFERENCES memory_items(id),
                        kind TEXT NOT NULL,
                        left_fingerprint TEXT NOT NULL,
                        right_fingerprint TEXT NOT NULL,
                        confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(left_id,right_id),
                        CHECK(left_id<right_id)
                    );
                    CREATE TABLE IF NOT EXISTS semantic_group_events(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        left_id INTEGER NOT NULL,
                        right_id INTEGER NOT NULL,
                        action TEXT NOT NULL CHECK(action IN ('confirm','revoke')),
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """)

    @staticmethod
    def _pair(left_id:int,right_id:int)->tuple[int,int]:
        if any(type(n) is not int or n<=0 for n in (left_id,right_id)) or left_id==right_id:
            raise ValueError("Choose two distinct approved memory IDs.")
        return min(left_id,right_id),max(left_id,right_id)

    def confirm(self,left_id:int,right_id:int,confirmed:bool=False)->dict:
        if confirmed is not True:
            raise ValueError("Explicit confirmation is required.")
        left_id,right_id=self._pair(left_id,right_id)
        with closing(self.store.connect()) as db:
            with db:
                pair=db.execute(
                    "SELECT id,kind,content,fingerprint,source_type,source_ref "
                    "FROM memory_items WHERE id IN (?,?) AND status='active' "
                    "ORDER BY id",(left_id,right_id)
                ).fetchall()
                if len(pair)!=2 or pair[0]["id"]!=left_id or pair[1]["id"]!=right_id:
                    raise ValueError("Both memories must exist and be active.")
                if pair[0]["kind"]!=pair[1]["kind"]:
                    raise ValueError("Memory kinds must match.")
                quality=compare(pair[0]["content"],pair[1]["content"])
                if quality["conflict"]:
                    raise ValueError("Potentially contradicting memories cannot be grouped.")
                if not quality["duplicate"] and quality["similarity"]<.35:
                    raise ValueError("Too little evidence of similarity; review the text first.")
                db.execute(
                    "INSERT INTO semantic_groups("
                    "left_id,right_id,kind,left_fingerprint,right_fingerprint)"
                    " VALUES(?,?,?,?,?) ON CONFLICT(left_id,right_id) DO UPDATE SET "
                    "kind=excluded.kind,left_fingerprint=excluded.left_fingerprint,"
                    "right_fingerprint=excluded.right_fingerprint,"
                    "confirmed_at=CURRENT_TIMESTAMP",
                    (left_id,right_id,pair[0]["kind"],
                     pair[0]["fingerprint"],pair[1]["fingerprint"])
                )
                db.execute(
                    "INSERT INTO semantic_group_events(left_id,right_id,action) "
                    "VALUES(?,?,'confirm')",(left_id,right_id)
                )
        return {"confirmed":True,"left_id":left_id,"right_id":right_id,
                "similarity":quality["similarity"],"originals_kept":True,
                "source_records_untouched":True,"cloud_requests":0}

    def revoke(self,left_id:int,right_id:int,confirmed:bool=False)->dict:
        if confirmed is not True:
            raise ValueError("Explicit confirmation is required.")
        left_id,right_id=self._pair(left_id,right_id)
        with closing(self.store.connect()) as db, db:
            deleted=db.execute(
                "DELETE FROM semantic_groups WHERE left_id=? AND right_id=?",
                (left_id,right_id)
            ).rowcount
            if deleted:
                db.execute(
                    "INSERT INTO semantic_group_events(left_id,right_id,action) "
                    "VALUES(?,?,'revoke')",(left_id,right_id)
                )
        return {"revoked":bool(deleted),"originals_kept":True}

    def list(self,limit:int=60)->dict:
        if type(limit) is not int or not 1<=limit<=100:
            raise ValueError("Invalid group limit.")
        with closing(self.store.connect()) as db:
            rows=db.execute(
                "SELECT g.left_id,g.right_id,g.kind,g.confirmed_at,"
                "a.content AS left_text,b.content AS right_text,"
                "a.source_type AS left_source_type,a.source_ref AS left_source_ref,"
                "b.source_type AS right_source_type,b.source_ref AS right_source_ref,"
                "CASE WHEN a.status='active' AND b.status='active'"
                " AND a.fingerprint=g.left_fingerprint "
                "AND b.fingerprint=g.right_fingerprint THEN 1 ELSE 0 END AS current "
                "FROM semantic_groups g "
                "JOIN memory_items a ON a.id=g.left_id "
                "JOIN memory_items b ON b.id=g.right_id "
                "ORDER BY g.confirmed_at DESC,g.left_id DESC LIMIT ?",(limit,)
            ).fetchall()
        return {"items":[dict(x, current=bool(x["current"])) for x in rows],
                "confirmed_by_user_only":True,
                "unverified_meaning":True,"automatic_deletions":0,"cloud_requests":0}

    def health(self)->dict:
        with closing(self.store.connect()) as db:
            rows=db.execute(
                "SELECT COUNT(*) AS n FROM semantic_groups"
            ).fetchone()
        return {"groups":rows["n"],"auto_merge":False,
                "all_original_rows_preserved":True}
