"""Local-only diagnostics and guarded SQLite snapshots.

No secret values, private chat content, cloud requests, or deceptive 'live'
checks. Restore is OFFLINE and explicit, never performed by the web server.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone, timedelta
import json
import hashlib
import os
import shutil
from pathlib import Path
import sqlite3
import threading
import sys
import uuid

from tsukiyora.memory.memory_store import MemoryStore


class Reliability:
    # Serialize concurrent manual and scheduled snapshots in this process.
    # SQLite's backup API handles concurrent WAL writes.
    _backup_mutex = threading.Lock()
    def __init__(self, db_path, project_root=None):
        self.db_path = Path(db_path)
        self.root = Path(project_root) if project_root else self.db_path.parent.parent
        self.folder = self.db_path.parent / "backups"
        self.last_error = ""

    def initialize(self):
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS backup_schedule (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    last_success TEXT,
                    last_filename TEXT
                )
            """)
            db.execute("INSERT OR IGNORE INTO backup_schedule(id) VALUES(1)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS backup_digests(
                    filename TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest=hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):
                digest.update(chunk)
        return digest.hexdigest()

    def wal_health(self) -> dict:
        """Local SQLite journal and passive checkpoint, no delete/truncate."""
        with closing(sqlite3.connect(self.db_path,timeout=5)) as db:
            mode=db.execute("PRAGMA journal_mode").fetchone()[0]
            result=db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            foreign=db.execute("PRAGMA foreign_key_check").fetchmany(10)
            integrity=db.execute("PRAGMA quick_check").fetchone()[0]
        return {"journal_mode":mode,"passive_checkpoint":{
                    "busy":result[0],"wal_frames":result[1],"checkpointed":result[2]},
                "foreign_key_errors":len(foreign),
                "sqlite_ok":integrity=="ok",
                "no_live_restore_performed":True}

    def local_checks(self) -> dict:
        checks = {}
        for name, file in (
            ("launcher", self.root / "Tsukiyora.bat"),
            ("personality", self.root / "soul" / "personality.json"),
            ("html", self.root / "ui" / "index.html"),
            ("styles", self.root / "ui" / "style.css"),
            ("script", self.root / "ui" / "app.ts"),
        ):
            checks[name] = {"status": "passed" if file.is_file() else "failed"}
        checks["python"] = {"status": "passed" if sys.version_info >= (3, 10) else "failed",
                            "version": ".".join(map(str, sys.version_info[:3]))}
        try:
            with closing(sqlite3.connect(self.db_path, timeout=2)) as db:
                db.execute("PRAGMA busy_timeout=2000")
                consistency = db.execute("PRAGMA quick_check").fetchone()[0]
            checks["sqlite"] = {"status": "passed" if consistency == "ok" else "failed",
                                "integrity": consistency[:100]}
        except (sqlite3.Error, OSError):
            checks["sqlite"] = {"status": "failed", "integrity": "could not check"}
        try:
            with closing(sqlite3.connect(self.db_path)) as db:
                row = db.execute(
                    "SELECT last_success,last_filename FROM backup_schedule WHERE id=1"
                ).fetchone()
            last = {"last_success": row[0] if row else None,
                    "filename": row[1] if row else None}
        except sqlite3.Error:
            last = {"last_success": None, "filename": None}
        checks["backup"] = {"status": "passed" if last["last_success"] else "pending",
                            **last}
        return {"checks": checks, "all_local_passed": all(
            x["status"] == "passed" for key, x in checks.items() if key != "backup"),
                "cloud_live_tested": False,
                "note": "Проверка кнопок браузера требует запуска в браузере; Cloud.ru отдельно проверяется с ключом."}

    def ensure_snapshot(self, interval_hours: int = 5, keep: int = 12, force: bool = False) -> dict:
        with self._backup_mutex:
            return self._ensure_snapshot_unlocked(interval_hours,keep,force)

    def _ensure_snapshot_unlocked(self, interval_hours: int, keep: int, force: bool) -> dict:
        """Online SQLite backup is safe with concurrent WAL writes."""
        if type(interval_hours) is not int or not 1 <= interval_hours <= 168:
            raise ValueError("Invalid backup interval.")
        if type(keep) is not int or not 2 <= keep <= 100:
            raise ValueError("Invalid retention.")
        now = datetime.now(timezone.utc)
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db:
            row = db.execute("SELECT last_success,last_filename FROM backup_schedule WHERE id=1").fetchone()
        if row and row[0] and not force:
            try:
                previous = datetime.fromisoformat(row[0])
                if now - previous < timedelta(hours=interval_hours):
                    if row[1] and (self.folder / row[1]).is_file():
                        # A present but corrupt snapshot MUST NOT postpone a new copy.
                        previous_check = self.verify_snapshots(limit=3)
                        if any(x["name"] == row[1] and x["status"]=="healthy"
                               for x in previous_check["snapshots"]):
                            return {"created": False, "name": row[1], "last_success": row[0]}
            except (ValueError, TypeError):
                pass
        target = MemoryStore(self.db_path).backup(self.folder)
        digest=self._sha256(target)
        saved_at = now.isoformat()
        # Don't report success until metadata has been committed.
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
            db.execute(
                "UPDATE backup_schedule SET last_success=?,last_filename=? WHERE id=1",
                (saved_at, target.name)
            )
            db.execute(
                "INSERT OR REPLACE INTO backup_digests(filename,sha256,size_bytes) "
                "VALUES(?,?,?)",(target.name,digest,target.stat().st_size)
            )
        snapshots = sorted(self.folder.glob("memory-*.sqlite3"), reverse=True)
        for old in snapshots[keep:]:
            old.unlink(missing_ok=True)
            with closing(sqlite3.connect(self.db_path)) as db,db:
                db.execute("DELETE FROM backup_digests WHERE filename=?",(old.name,))
        return {"created": True, "name": target.name, "last_success": saved_at,
                "checksum_algorithm":"sha256"}

    def verify_snapshots(self, limit: int = 3) -> dict:
        """Read-only SQLite check of a bounded set of local snapshots.

        This reports their actual structural integrity, not whether the
        original facts are correct. Never auto-deletes the only good backup.
        """
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Invalid verification limit.")
        snapshots = sorted(self.folder.glob("memory-*.sqlite3"), reverse=True)[:limit] \
            if self.folder.is_dir() else []
        checks = []
        with closing(sqlite3.connect(self.db_path,timeout=5)) as db:
            digests={r[0]:(r[1],r[2]) for r in db.execute(
                "SELECT filename,sha256,size_bytes FROM backup_digests"
            )}
        for snapshot in snapshots:
            if snapshot.is_symlink() or not snapshot.is_file():
                checks.append({"name": snapshot.name, "status": "invalid",
                               "detail": "not_regular_file"})
                continue
            try:
                with closing(sqlite3.connect(
                    "file:" + snapshot.resolve().as_posix() + "?mode=ro",
                    uri=True, timeout=5
                )) as connection:
                    result = connection.execute("PRAGMA quick_check").fetchone()
                    tables = {row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )}
                healthy = bool(result and result[0]=="ok"
                               and {"memory_items","messages","conversations"} <= tables)
                expected=digests.get(snapshot.name)
                matched=(expected is None or
                         (snapshot.stat().st_size==expected[1] and
                          self._sha256(snapshot)==expected[0]))
                healthy=healthy and matched
                checks.append({"name":snapshot.name,
                               "status":"healthy" if healthy else "invalid",
                               "checksum":"verified" if expected and matched else
                                          "mismatch" if expected else "legacy_unrecorded",
                               "detail":"quick_check+schema+sha256" if healthy and expected
                               else "quick_check+schema" if healthy else "failed_integrity_schema_or_checksum"})
            except (sqlite3.Error, OSError, ValueError):
                checks.append({"name":snapshot.name,"status":"invalid",
                               "detail":"unable_to_read"})
        return {"checked":len(checks),
                "healthy":sum(x["status"]=="healthy" for x in checks),
                "invalid":sum(x["status"]=="invalid" for x in checks),
                "snapshots":checks, "restoration_performed":False}

    def local_selftest(self) -> dict:
        """Pure local checks and bounded backup validation, no user secrets."""
        baseline = self.local_checks()
        verify = self.verify_snapshots(limit=3)
        return {
            "local":baseline,
            "backups":verify,
            "browser_interaction_verified":False,
            "cloud_live_verified":False,
            "can_claim_real_pc_test":False,
            "note":"DOM и кнопки нужно проверить в локальном браузере; Cloud.ru — отдельно с ключом."
        }

    def backups(self, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid limit.")
        if not self.folder.is_dir():
            return []
        return [{"filename": x.name, "size": x.stat().st_size}
                for x in sorted(self.folder.glob("memory-*.sqlite3"), reverse=True)[:limit]]

    def dry_run_restore(self, backup_filename: str) -> dict:
        """Read-only restore into an isolated temporary SQLite DB, never live DB."""
        if (not isinstance(backup_filename,str) or
            not backup_filename.startswith("memory-") or
            not backup_filename.endswith(".sqlite3") or
            Path(backup_filename).name != backup_filename or len(backup_filename)>150):
            raise ValueError("Choose a local memory snapshot by filename.")
        source=self.folder/backup_filename
        if not source.is_file() or source.is_symlink():
            raise ValueError("Snapshot missing or not a regular file.")
        if source.stat().st_size > 1024*1024*1024:
            raise ValueError("Snapshot too large for automated recovery rehearsal.")
        with closing(sqlite3.connect(self.db_path,timeout=5)) as current:
            recorded=current.execute(
                "SELECT sha256,size_bytes FROM backup_digests WHERE filename=?",
                (backup_filename,)
            ).fetchone()
        if recorded is not None and (recorded[1]!=source.stat().st_size
                                      or recorded[0]!=self._sha256(source)):
            raise ValueError("Backup checksum mismatch; recovery rehearsal refused.")
        import tempfile
        with tempfile.TemporaryDirectory(prefix="tsukiyora-restore-") as temp:
            target=Path(temp)/"rehearsal.sqlite3"
            with closing(sqlite3.connect(
                "file:"+source.resolve().as_posix()+"?mode=ro",uri=True,timeout=10
            )) as original:
                if original.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                    raise ValueError("Selected snapshot is damaged.")
                with closing(sqlite3.connect(target,timeout=15)) as copy:
                    original.backup(copy,pages=128,sleep=.02)
                    if copy.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                        raise ValueError("Rehearsal restore failed SQLite integrity check.")
                    tables={r[0] for r in copy.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )}
                    if not {"memory_items","messages","conversations"}<=tables:
                        raise ValueError("Backup is missing the required history tables.")
                    counts={
                        table:copy.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
                        for table in ("memory_items","messages","conversations")
                    }
        return {"name":backup_filename,"restorable":True,
                "checked":"isolated_sqlite_restore_and_integrity",
                "history_counts":counts,"live_database_modified":False,
                "offline_manual_restore_still_required":True}

    def restore_offline(self, backup_filename: str, confirm: bool = False) -> dict:
        """Manually restore only while server is stopped, preserving safety copy.

        No HTML/API restore endpoint. Caller must hold the same instance lock
        as launcher; see restore_memory.py.
        """
        if confirm is not True:
            raise ValueError("Explicit confirmation required.")
        if (not isinstance(backup_filename, str)
            or not backup_filename.startswith("memory-")
            or not backup_filename.endswith(".sqlite3")
            or Path(backup_filename).name != backup_filename):
            raise ValueError("Backup filename is not permitted.")
        source = self.folder / backup_filename
        if not source.is_file() or source.is_symlink():
            raise ValueError("Selected backup not found.")
        with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Selected backup failed SQLite integrity check.")
            for name in ("messages", "memory_items", "conversations"):
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
                ).fetchone() is None:
                    raise ValueError("Backup is missing required tables: " + name)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Force WAL checkpoint before taking a safety backup. This is safe only
        # because the CLI holds the launcher lock and checks the health URL.
        if self.db_path.exists():
            try:
                with closing(sqlite3.connect(self.db_path, timeout=5)) as db:
                    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                safety = MemoryStore(self.db_path).backup(self.folder)
            except sqlite3.DatabaseError:
                # A damaged database still deserves a byte-for-byte safety copy.
                # The copy is NOT a valid SQLite backup and is named accordingly.
                damaged_name = "damaged-" + datetime.now(timezone.utc).strftime(
                    "%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + ".sqlite3"
                destination = self.folder / damaged_name
                shutil.copy2(self.db_path, destination)
                safety = destination
        else:
            safety = None
        staged = self.db_path.with_name(self.db_path.name + ".restore-" + uuid.uuid4().hex)
        try:
            with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(staged)) as dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Restored snapshot failed integrity check.")
            # SQLite sidecar files from the old database must be absent before
            # atomic replacement; opening and closing after checkpoint cleans
            # most. Refuse otherwise rather than risk mixing journals.
            for suffix in ("-wal", "-shm"):
                if Path(str(self.db_path) + suffix).exists():
                    raise ValueError("SQLite WAL sidecar is still present; stop all processes.")
            os.replace(staged, self.db_path)
        finally:
            staged.unlink(missing_ok=True)
        return {"restored": backup_filename, "safety_copy": safety.name if safety else None}
