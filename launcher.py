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
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import re

from memory_store import MemoryStore
from soul_growth import SoulGrowth
from cognitive_core import CognitiveCore, CognitiveWorker
from migration_guard import MigrationGuard
from resource_guard import ResourceGuard
from reliability import Reliability
from memory_quality import MemoryQuality
from vector_memory import VectorMemory
from stream_runtime import handle_stream, stop_stream
from cloudru import CloudSession, CloudError
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
    MigrationGuard(db_path).prepare()
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
    SoulGrowth(db_path).initialize()
    CognitiveCore(db_path).initialize()
    MigrationGuard(db_path).finish()


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
        self.cloud = CloudSession(Path(memory_path).parent / "cloudru_preferences.json")
        self.guard = ResourceGuard(memory_path)
        self.guard.initialize()
        self.cloud.guard = self.guard
        self.chat_lock = threading.Lock()
        self.cognition = CognitiveCore(memory_path)
        self.cognition.chat_priority = self.chat_lock
        self.active_streams = {}
        super().__init__(address, SoulHandler)


class SoulHandler(BaseHTTPRequestHandler):
    server_version = "Tsukiyora/0.1"

    def log_message(self, fmt, *args):
        # Never log user search terms, chat text or API keys in URL query strings.
        LOG.info("%s %s %s", self.client_address[0], self.command,
                 urlsplit(self.path).path)

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
        if path == "/api/system/cloud-budget":
            return self.send_json(self.server.guard.status())
        if path == "/api/system/diagnostics":
            return self.send_json(self.server.cognition.reliability.local_checks())
        if path == "/api/system/selftest":
            return self.send_json(self.server.cognition.reliability.local_selftest())
        if path == "/api/system/migrations":
            return self.send_json(self.server.cognition.migrations.audit())
        if path == "/api/system/recovery-check":
            try:
                name = args.get("name", [""])[0]
                return self.send_json(self.server.cognition.reliability.dry_run_restore(name))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if path == "/api/system/backups":
            return self.send_json({"items": self.server.cognition.reliability.backups()})
        if path == "/api/system/workload":
            return self.send_json(self.server.cognition.scheduler.pending())
        if path == "/api/soul/layers":
            try:
                args_layer = args.get("layer", ["all"])[0]
                args_limit = int(args.get("limit", ["50"])[0])
                conv = args.get("conversation_id", [None])[0]
                return self.send_json(self.server.cognition.layers.inspect(
                    layer=args_layer,limit=args_limit,conversation_id=conv))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if path == "/api/soul/audits":
            return self.send_json({"items": self.server.cognition.audit.history()})
        if path == "/api/soul/atlas":
            try:
                level = args.get("level", ["overview"])[0]
                atlas = self.server.cognition.atlas
                if level == "overview":
                    return self.send_json(atlas.overview())
                if level == "locate":
                    return self.send_json(atlas.locate(int(args.get("id",["0"])[0])))
                sector = int(args.get("sector", ["-1"])[0])
                if level == "regions":
                    cursor = args.get("cursor", [None])[0]
                    return self.send_json(atlas.regions(
                        sector,cursor=int(cursor) if cursor is not None else None))
                if level == "clusters":
                    cursor = args.get("cursor", [None])[0]
                    region = args.get("region", [None])[0]
                    return self.send_json(atlas.clusters(
                        sector, cursor=int(cursor) if cursor is not None else None,
                        region=int(region) if region is not None else None))
                if level == "nodes":
                    bucket = int(args.get("bucket", ["-1"])[0])
                    return self.send_json(atlas.nodes(sector,bucket))
                raise ValueError("Unknown atlas level.")
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if path == "/api/soul/academy":
            return self.send_json(self.server.cognition.academy.status())
        if path == "/api/soul/academy/metrics":
            return self.send_json(self.server.cognition.metrics.status())
        if path == "/api/soul/academy/experiments":
            return self.send_json(self.server.cognition.experiments.report())
        if path == "/api/soul/character/timeline":
            return self.send_json(self.server.cognition.continuity.timeline())
        if path == "/api/soul/character":
            return self.send_json(self.server.cognition.continuity.state())
        if path == "/api/soul/precision/status":
            return self.send_json({"fts5":self.server.cognition.precision.has_fts,
                                   "cloud_calls":0,"index":"SQLite local"})
        if path == "/api/soul/graph":
            return self.send_json(self.server.cognition.graph())
        if path == "/api/soul/cognition":
            return self.send_json(self.server.cognition.status())
        if path == "/api/soul/quality":
            try:
                return self.send_json(MemoryQuality(self.server.memory_path).review())
            except (ValueError, sqlite3.Error, OSError) as error:
                return self._handle_error(error)
        if path == "/api/soul/experience":
            return self.send_json({
                "state": self.server.cognition.experience.state(),
                "timeline": self.server.cognition.experience.timeline(),
            })
        if path == "/api/soul/biography":
            return self.send_json(self.server.cognition.biography())
        if path == "/api/soul/suggestions":
            return self.send_json({"items": SoulGrowth(self.server.memory_path).suggestions()})
        if path == "/api/soul/journal":
            return self.send_json({"items": SoulGrowth(self.server.memory_path).journal_entries()})
        if path == "/api/soul/links":
            return self.send_json({"items": SoulGrowth(self.server.memory_path).links()})
        if path.startswith("/api/cloud/"):
            try:
                if path == "/api/cloud/status":
                    return self.send_json(self.server.cloud.status())
                if path == "/api/cloud/models":
                    return self.send_json({"models": self.server.cloud.models()})
                return self.send_json({"error": "Unknown cloud endpoint."}, 404)
            except CloudError as error:
                return self.send_json({"error": str(error)}, error.status)
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
            cloud = self.server.cloud.status()
            return self.send_json({
                "runtime": "local", "database": database, "memory": memory,
                "cloud_model": cloud["model"] if cloud["configured"] and cloud["verified"] else "not_configured",
                "chat": "available" if cloud["configured"] and cloud["verified"] and cloud["model"] else "not_connected",
            })
        allowed = {
            "/": (UI / "index.html", "text/html; charset=utf-8"),
            "/ui/style.css": (UI / "style.css", "text/css; charset=utf-8"),
            "/ui/app.ts": (UI / "app.ts", "text/javascript; charset=utf-8"),
            "/ui/atlas_view.ts": (UI / "atlas_view.ts", "text/javascript; charset=utf-8"),
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
        if method == "POST" and path == "/api/system/cloud-budget":
            try:
                obj=self._read_body()
                return self.send_json(self.server.guard.configure(
                    chat_per_day=obj.get("chat_per_day"),
                    embeddings_per_day=obj.get("embeddings_per_day"),
                    per_minute=obj.get("per_minute")))
            except (ValueError,sqlite3.Error) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/rag4":
            try:
                obj=self._read_body()
                cloud_state=self.server.cloud.status()
                model=(cloud_state.get("embedding_model")
                       if cloud_state.get("vector_chat_enabled") else None)
                return self.send_json(self.server.cognition.fusion.search(
                    obj.get("query"),limit=obj.get("limit",12),
                    model=model,use_cached_vectors=bool(model)))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/rag":
            try:
                obj=self._read_body()
                return self.send_json(self.server.cognition.rag.search(
                    obj.get("query"),limit=obj.get("limit",12),
                    max_age_days=obj.get("max_age_days",365)))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/precision":
            try:
                obj = self._read_body()
                return self.send_json(self.server.cognition.precision.search(
                    obj.get("query"),limit=obj.get("limit",12),
                    include_links=obj.get("include_links",True)))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/academy/enabled":
            try:
                obj = self._read_body()
                return self.send_json(self.server.cognition.academy.set_enabled(
                    obj.get("enabled")))
            except ValueError as error:
                return self.send_json({"error":str(error)},400)
        if method == "POST" and path == "/api/system/backup":
            try:
                self._read_body()
                return self.send_json(self.server.cognition.reliability.ensure_snapshot(
                    interval_hours=5,keep=12,force=True))
            except (ValueError,OSError,sqlite3.Error) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/chat/stream":
            try:
                return handle_stream(self, self._read_body())
            except CloudError as error:
                return self.send_json({"error": str(error)}, error.status)
            except (ValueError, sqlite3.Error, OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/chat/stop":
            try:
                return self.send_json({"stopping": stop_stream(self.server, self._read_body().get("stream_id"))})
            except ValueError as error:
                return self.send_json({"error": str(error)}, 400)
        if method == "POST" and path == "/api/cloud/embedding/model":
            try:
                return self.send_json(self.server.cloud.select_embedding(
                    self._read_body().get("model")))
            except (ValueError, OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/cloud/embedding/auto":
            try:
                return self.send_json(self.server.cloud.enable_vector_chat(
                    self._read_body().get("allowed")))
            except ValueError as error:
                return self.send_json({"error": str(error)}, 400)
        if method == "POST" and path == "/api/soul/vectors/index":
            try:
                obj = self._read_body()
                if obj.get("confirm_send_to_cloud") is not True:
                    raise ValueError("Нужно подтверждение отправки воспоминаний в Cloud.ru.")
                return self.send_json(self.server.cognition.vectors.sync(
                    self.server.cloud, obj.get("limit", 40)))
            except CloudError as error:
                return self.send_json({"error": str(error)}, error.status)
            except (ValueError, OSError, sqlite3.Error) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/vectors/search":
            try:
                obj = self._read_body()
                if obj.get("confirm_send_to_cloud") is not True:
                    raise ValueError("Нужно согласие на отправку поискового запроса.")
                return self.send_json(self.server.cognition.vectors.query(
                    obj.get("query"), self.server.cloud))
            except CloudError as error:
                return self.send_json({"error": str(error)}, error.status)
            except (ValueError, OSError, sqlite3.Error) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/feedback":
            try:
                obj = self._read_body()
                return self.send_json(self.server.cognition.feedback(
                    obj.get("message_id"), obj.get("helpful"), obj.get("note", "")))
            except (ValueError, OSError, sqlite3.Error) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/cloud/checkup":
            try:
                obj = self._read_body()
                return self.send_json(self.server.cloud.checkup(
                    include_stream=obj.get("include_stream", True),
                    include_embeddings=obj.get("include_embeddings", True)))
            except CloudError as error:
                return self.send_json({"error": str(error)}, error.status)
            except ValueError as error:
                return self.send_json({"error": str(error)}, 400)
        if method == "POST" and path == "/api/cloud/diagnose":
            try:
                return self.send_json(self.server.cloud.diagnostic())
            except CloudError as error:
                return self.send_json({"error": str(error)}, error.status)
        if method == "POST" and path == "/api/soul/search":
            try:
                obj = self._read_body()
                query = obj.get("query")
                if not isinstance(query, str) or not query.strip() or len(query) > 500:
                    raise ValueError("Укажи поисковый запрос длиной до 500 символов.")
                rows = SoulGrowth(self.server.memory_path).retrieve(query, limit=18)
                semantic = False
                if obj.get("semantic") is True and len(rows) > 1:
                    ids = self.server.cloud.rerank(query, rows)
                    ranked = {r["id"]: r for r in rows}
                    rows = [ranked[i] for i in ids if i in ranked]
                    semantic = True
                return self.send_json({"items": rows[:10], "semantic": semantic})
            except CloudError as error:
                return self.send_json({"error": str(error)}, error.status)
            except (ValueError, sqlite3.Error, OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/review":
            try:
                obj = self._read_body()
                return self.send_json(SoulGrowth(self.server.memory_path).review(
                    obj.get("id"), obj.get("approve")))
            except (ValueError, sqlite3.Error, OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path in ("/api/cloud/key", "/api/cloud/model", "/api/chat"):
            try:
                return self._cloud_write(path, self._read_body())
            except CloudError as error:
                return self.send_json({"error": str(error)}, error.status)
            except (ValueError, sqlite3.Error, OSError) as error:
                return self._handle_error(error)
        if not path.startswith("/api/memory"):
            return self.send_json({"error": "Not found."}, 404)
        try:
            store = self._memory()
            obj = self._read_body() if method in ("POST", "PUT") else {}
            if method == "POST" and path == "/api/memory":
                item = store.add_memory(kind=obj.get("kind"), content=obj.get("content"))
                SoulGrowth(self.server.memory_path).build_links(item["id"])
                return self.send_json({"item": item}, 201)
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
                item = store.correct_memory(
                    int(match.group(1)), obj.get("content"),
                    reason=obj.get("reason", "user correction"))
                SoulGrowth(self.server.memory_path).build_links(item["id"])
                return self.send_json({"item": item})
            if match and method == "DELETE":
                store.delete_memory(int(match.group(1)))
                return self.send_json({"deleted": True})
            return self.send_json({"error": "Unknown memory endpoint."}, 404)
        except (ValueError, sqlite3.Error, OSError) as error:
            return self._handle_error(error)

    def _cloud_write(self, path: str, obj: dict):
        cloud = self.server.cloud
        if path == "/api/cloud/key":
            return self.send_json(cloud.set_key(obj.get("api_key")))
        if path == "/api/cloud/model":
            return self.send_json(cloud.select(obj.get("model")))
        if path != "/api/chat":
            return self.send_json({"error": "Unknown chat endpoint."}, 404)

        content = obj.get("content")
        if not isinstance(content, str) or not (1 <= len(content.strip()) <= 8000):
            raise ValueError("Сообщение должно содержать от 1 до 8000 символов.")
        content = content.strip()
        client_id = obj.get("client_message_id")
        if client_id is None:
            client_id = uuid.uuid4().hex
        if not isinstance(client_id, str) or not (1 <= len(client_id) <= 100):
            raise ValueError("Некорректный идентификатор сообщения.")
        conversation_id = obj.get("conversation_id")
        if conversation_id is not None and (not isinstance(conversation_id, str) or len(conversation_id) > 64):
            raise ValueError("Некорректный идентификатор диалога.")

        with self.server.chat_lock:
            state = cloud.status()
            if not (state["configured"] and state["verified"] and state["model"]):
                raise CloudError("Сначала укажи ключ, обнови модели и выбери модель Cloud.ru.", 400)
            store = self._memory()
            if not conversation_id:
                conversation_id = store.create_conversation(content[:72])["id"]
            key = "assistant:" + client_id
            # Validate the idempotency key against the stored user text
            # before returning any cached assistant answer.
            user = store.append_messages(conversation_id, [{
                "role": "user", "content": content, "client_message_id": client_id
            }])[0]
            existing = store.message_by_client_id(conversation_id, key)
            if existing:
                return self.send_json({"conversation_id": conversation_id,
                                       "answer": existing["content"], "cached": True})
            history = store.list_messages(conversation_id, limit=24)
            if not history or history[-1]["id"] != user["id"]:
                raise ValueError("Нельзя повторить старое сообщение после новых. Начни новый диалог.")
            memories = store.find_memories(limit=20)
            answer = cloud.complete(self.server.persona, history, memories)
            store.append_messages(conversation_id, [{
                "role": "assistant", "content": answer, "client_message_id": key
            }])
            return self.send_json({"conversation_id": conversation_id,
                                   "answer": answer, "cached": False})

    def do_POST(self):
        self._write("POST")

    def do_PUT(self):
        self._write("PUT")

    def do_DELETE(self):
        self._write("DELETE")


def run() -> int:
    if sys.argv[1:] == ["--self-test"]:
        # Real portable launcher smoke test. Uses TEMP SQLite, no browser,
        # secrets, system services, network or the user's persistent database.
        import tempfile
        with tempfile.TemporaryDirectory(prefix="tsukiyora-smoke-") as folder:
            candidate=Path(folder)/"data"/"memory.sqlite3"
            persona=load_personality()
            init_database(candidate)
            probe=SoulServer(("127.0.0.1",0),persona,candidate)
            try:
                report=Reliability(candidate,ROOT).local_checks()
                fts=probe.cognition.migrations.audit()
                html=(UI/"index.html").read_text(encoding="utf-8")
                script=(UI/"app.ts").read_text(encoding="utf-8")
                buttons=("cloud-diagnose","send-message","stop-message",
                         "run-system-check","precision-search","academy-enabled")
                interface=all('id="'+name+'"' in html for name in buttons)
                ok=bool(report["all_local_passed"] and
                        fts["sqlite_integrity"] and interface and
                        "/api/chat/stream" in script)
                print(json.dumps({
                    "portable_launch_smoke":"passed" if ok else "failed",
                    "sqlite":fts["sqlite_integrity"],
                    "ui_markup":interface,"cloud_live_tested":False,
                    "browser_buttons_clicked":False,
                }))
                return 0 if ok else 1
            finally:
                probe.server_close()
    if len(sys.argv)>1:
        print("Usage: launcher.py [--self-test]", file=sys.stderr)
        return 2
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
    worker = None
    try:
        persona = load_personality()
        init_database()
        server = SoulServer(("127.0.0.1", 0), persona)
        worker = CognitiveWorker(server.cognition)
        worker.start()
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
        if worker is not None:
            worker.stop()
        if server is not None:
            server.server_close()
        try:
            STATE.unlink(missing_ok=True)
        except OSError:
            LOG.warning("Could not delete state file.")
        release_lock(lock)


if __name__ == "__main__":
    raise SystemExit(run())
