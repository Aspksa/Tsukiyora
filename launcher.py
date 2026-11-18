"""Portable Tsukiyora local runtime. Python standard library only."""
from __future__ import annotations

from contextlib import closing
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import re

from memory_store import MemoryStore
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
    with closing(sqlite3.connect(db_path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS runtime_events ("
            "id INTEGER PRIMARY KEY, event TEXT NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        db.execute("INSERT INTO runtime_events (event) VALUES (?)", ("startup",))
        db.commit()
    MemoryStore(db_path).initialize()


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

    def __init__(self, address, persona: dict, memory_path: Path = DATABASE):
        self.persona = persona
        self.memory_path = memory_path
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

    def send_json(self, value: dict, status: int = 200):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_bytes(status, payload, "application/json; charset=utf-8")

    def _valid_host(self) -> bool:
        # Reject DNS-rebinding via a non-loopback Host header.
        host = self.headers.get("Host", "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost")

    def _valid_write_origin(self) -> bool:
        origin = self.headers.get("Origin")
        site = self.headers.get("Sec-Fetch-Site")
        if site == "cross-site":
            return False
        if origin is not None and origin != "http://" + self.headers.get("Host", ""):
            return False
        return self.headers.get("Content-Type", "").split(";")[0].strip().lower() == "application/json"

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ValueError("Missing Content-Length.") from None
        if not 1 <= length <= 65536:
            raise ValueError("JSON request size must be between 1 and 65536 bytes.")
        try:
            obj = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Invalid UTF-8 JSON.") from None
        if not isinstance(obj, dict):
            raise ValueError("JSON body must be an object.")
        return obj

    def _memory(self):
        return MemoryStore(self.server.memory_path)

    def _handle_error(self, error):
        if isinstance(error, ValueError):
            return self.send_json({"error": str(error)}, 400)
        LOG.exception("Local memory API failed")
        return self.send_json({"error": "Local memory storage error."}, 500)

    def do_GET(self):
        if not self._valid_host():
            return self.send_json({"error": "Invalid Host."}, 400)
        parsed = urlsplit(self.path)
        path = parsed.path
        args = parse_qs(parsed.query, keep_blank_values=True)
        if path.startswith("/api/memory"):
            try:
                store = self._memory()
                def arg(name, default=""):
                    return args.get(name, [default])[0]
                if path == "/api/memory/status":
                    return self.send_json(store.stats())
                if path == "/api/memory":
                    return self.send_json({"items": store.find_memories(
                        query=arg("query"), kind=arg("kind"),
                        limit=int(arg("limit", "50")))})
                if path == "/api/memory/conversations":
                    return self.send_json({"conversations": store.list_conversations(
                        limit=int(arg("limit", "30")))})
                if path == "/api/memory/messages":
                    before = arg("before_id")
                    return self.send_json({"messages": store.list_messages(
                        conversation_id=arg("conversation_id"),
                        before_id=int(before) if before else None,
                        limit=int(arg("limit", "100")))})
                return self.send_json({"error": "Unknown memory endpoint."}, 404)
            except (ValueError, sqlite3.Error, OSError) as error:
                return self._handle_error(error)
        if path == "/api/health":
            return self.send_json({"app": "tsukiyora", "ok": True})
        if path == "/api/personality":
            return self.send_json(self.server.persona)
        if path == "/api/status":
            try:
                memory = self._memory().stats()
                database = "ready"
            except (sqlite3.Error, OSError):
                memory, database = None, "not_initialized"
            return self.send_json({
                "runtime": "local", "database": database, "memory": memory,
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

    def _write(self, method: str):
        if not self._valid_host():
            return self.send_json({"error": "Invalid Host."}, 400)
        if not self._valid_write_origin():
            return self.send_json({"error": "Same-origin JSON request required."}, 403)
        path = urlsplit(self.path).path
        if not path.startswith("/api/memory"):
            return self.send_json({"error": "Not found."}, 404)
        try:
            store = self._memory()
            obj = self._read_body() if method in ("POST", "PUT") else {}
            if method == "POST" and path == "/api/memory":
                return self.send_json({"item": store.add_memory(
                    kind=obj.get("kind"), content=obj.get("content"))}, 201)
            if method == "POST" and path == "/api/memory/conversations":
                return self.send_json({"conversation": store.create_conversation(
                    title=obj.get("title", "Новый диалог"))}, 201)
            if method == "POST" and path == "/api/memory/messages":
                return self.send_json({"messages": store.append_messages(
                    conversation_id=obj.get("conversation_id"),
                    messages=obj.get("messages"))}, 201)
            if method == "POST" and path == "/api/memory/backup":
                target = store.backup()
                return self.send_json({"saved": True, "filename": target.name}, 201)
            match = re.fullmatch(r"/api/memory/([1-9][0-9]*)", path)
            if match and method == "PUT":
                return self.send_json({"item": store.correct_memory(
                    int(match.group(1)), obj.get("content"),
                    reason=obj.get("reason", "user correction"))})
            if match and method == "DELETE":
                store.delete_memory(int(match.group(1)))
                return self.send_json({"deleted": True})
            return self.send_json({"error": "Unknown memory endpoint."}, 404)
        except (ValueError, sqlite3.Error, OSError) as error:
            return self._handle_error(error)

    def do_POST(self):
        self._write("POST")

    def do_PUT(self):
        self._write("PUT")

    def do_DELETE(self):
        self._write("DELETE")


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
            if os.environ.get("TSUKIYORA_NO_BROWSER") != "1":
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
        if os.environ.get("TSUKIYORA_NO_BROWSER") != "1":
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
