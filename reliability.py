"""Local-only diagnostics and guarded SQLite snapshots.

No secret values, private chat content, cloud requests, or deceptive 'live'
checks. Restore is OFFLINE and explicit, never performed by the web server.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone, timedelta
import json
import os
import shutil
from pathlib import Path
import sqlite3
import sys
import uuid

from memory_store import MemoryStore


class Reliability:
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
                        return {"created": False, "name": row[1], "last_success": row[0]}
            except (ValueError, TypeError):
                pass
        target = MemoryStore(self.db_path).backup(self.folder)
        saved_at = now.isoformat()
        # Don't report success until metadata has been committed.
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db, db:
            db.execute(
                "UPDATE backup_schedule SET last_success=?,last_filename=? WHERE id=1",
                (saved_at, target.name)
            )
        snapshots = sorted(self.folder.glob("memory-*.sqlite3"), reverse=True)
        for old in snapshots[keep:]:
            old.unlink(missing_ok=True)
        return {"created": True, "name": target.name, "last_success": saved_at}

    def backups(self, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid limit.")
        if not self.folder.is_dir():
            return []
        return [{"filename": x.name, "size": x.stat().st_size}
                for x in sorted(self.folder.glob("memory-*.sqlite3"), reverse=True)[:limit]]

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
