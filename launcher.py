"""Portable Tsukiyora local runtime. Python standard library only."""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler
import webbrowser

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PROFILE = ROOT / "soul" / "personality.json"
DATABASE = DATA / "tsukiyora.sqlite3"
STATE = DATA / "runtime.json"
LOCK = DATA / ".instance.lock"
UI = ROOT / "ui"
LOG = logging.getLogger("tsukiyora")


def load_personality(path: Path = PROFILE) -> dict:
    """Validate the immutable startup snapshot of the approved persona."""
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError("Personality must be a JSON object.")
    identity = value.get("identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("name"), str):
        raise ValueError("Personality identity.name is required.")
    if not isinstance(value.get("protected_values"), list):
        raise ValueError("Personality protected_values must be a list.")
    if not isinstance(value.get("archetypes"), list):
        raise ValueError("Personality archetypes must be a list.")
    return value


def init_database(db_path: Path = DATABASE) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS runtime_events ("
            "id INTEGER PRIMARY KEY, event TEXT NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        db.execute("INSERT INTO runtime_events (event) VALUES (?)", ("startup",))
        db.commit()


def setup_logs() -> None:
    folder = DATA / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    LOG.setLevel(logging.INFO)
    LOG.addHandler(RotatingFileHandler(
        folder / "launcher.log", maxBytes=1_000_000, backupCount=3,
        encoding="utf-8"
    ))


def acquire_lock():
    """Keep a single launcher instance without installing a Windows service."""
    f = LOCK.open("a+b")
    f.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def release_lock(f) -> None:
    try:
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


def previous_url() -> str | None:
    """Only visit a local port if it responds with Tsukiyora's health marker."""
    try:
        info = json.loads(STATE.read_text(encoding="utf-8"))
        port = info.get("port")
        if type(port) is not int or not (1 <= port <= 65535):
            return None
        url = f"http://127.0.0.1:{port}"
        with build_opener(ProxyHandler({})).open(url + "/api/health", timeout=1) as r:
            status = json.load(r)
        if status.get("app") == "tsukiyora" and status.get("ok") is True:
            return url
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


class SoulServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, persona: dict):
        self.persona = persona
        super().__init__(address, SoulHandler)


class SoulHandler(BaseHTTPRequestHandler):
    server_version = "Tsukiyora/0.1"

    def log_message(self, fmt, *args):
        LOG.info("%s %s", self.client_address[0], fmt % args)

    def send_bytes(self, status: int, value: bytes, mime: str):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(value)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        ))
        self.end_headers()
        self.wfile.write(value)

    def send_json(self, value: dict):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_bytes(200, payload, "application/json; charset=utf-8")

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.send_json({"app": "tsukiyora", "ok": True})
        if path == "/api/personality":
            return self.send_json(self.server.persona)
        if path == "/api/status":
            return self.send_json({
                "runtime": "local", "database": "ready",
                "cloud_model": "not_configured", "chat": "not_connected",
            })
        allowed = {
            "/": (UI / "index.html", "text/html; charset=utf-8"),
            "/ui/style.css": (UI / "style.css", "text/css; charset=utf-8"),
            "/ui/app.ts": (UI / "app.ts", "text/javascript; charset=utf-8"),
        }
        match = allowed.get(path)
        if not match:
            return self.send_bytes(404, b"Not found", "text/plain")
        filename, mime = match
        try:
            content = filename.read_bytes()
        except OSError:
            LOG.exception("Static file missing: %s", filename)
            return self.send_bytes(500, b"Local UI error", "text/plain")
        self.send_bytes(200, content, mime)


def run() -> int:
    if sys.version_info < (3, 10):
        print("Python 3.10+ required.", file=sys.stderr)
        return 1
    DATA.mkdir(parents=True, exist_ok=True)
    setup_logs()
    lock = acquire_lock()
    if lock is None:
        url = previous_url()
        if url:
            print("Tsukiyora already running:", url)
            webbrowser.open(url)
            return 0
        print("Tsukiyora is already launching. Check data/logs.")
        return 0

    server = None
    try:
        persona = load_personality()
        init_database()
        server = SoulServer(("127.0.0.1", 0), persona)
        port = server.server_address[1]
        tmp = DATA / "runtime.json.tmp"
        tmp.write_text(json.dumps({"port": port}), encoding="utf-8")
        tmp.replace(STATE)
        url = f"http://127.0.0.1:{port}"
        LOG.info("Tsukiyora started: %s", url)
        print("Tsukiyora running at", url)
        print("Press Ctrl+C or close this console to stop.")
        webbrowser.open(url)
        server.serve_forever(poll_interval=0.25)
        return 0
    except KeyboardInterrupt:
        print("\nTsukiyora stopped.")
        return 0
    except (OSError, ValueError, sqlite3.Error):
        LOG.exception("Launch failed")
        print("Launch failed: see data/logs/launcher.log", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            server.server_close()
        try:
            STATE.unlink(missing_ok=True)
        except OSError:
            LOG.warning("Could not delete state file.")
        release_lock(lock)


if __name__ == "__main__":
    raise SystemExit(run())
