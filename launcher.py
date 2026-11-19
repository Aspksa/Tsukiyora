"""Portable Tsukiyora local runtime. Python standard library only."""
from __future__ import annotations

from contextlib import closing
import json
import base64
import binascii
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

    def _read_body(self, max_bytes: int = 65536) -> dict:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ValueError("Missing Content-Length.") from None
        if not 1 <= length <= max_bytes:
            raise ValueError("JSON request exceeds the allowed size.")
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
        if path == "/api/system/wal":
            try:
                return self.send_json(self.server.cognition.reliability.wal_health())
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
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
        if path == "/api/soul/documents":
            return self.send_json({"items":self.server.cognition.evidence.documents.list()})
        if path == "/api/soul/contracts/insights":
            try:
                doc_id=int(args.get("id",["0"])[0])
                return self.send_json(self.server.cognition.contracts.inspect(doc_id))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if path == "/api/soul/academy/rollback":
            return self.send_json(self.server.cognition.rollback.status())
        if path == "/api/soul/atlas/cache":
            return self.send_json(self.server.cognition.materializer.verify())
        if path == "/api/soul/evidence/audits":
            return self.send_json(self.server.cognition.evidence.status())
        if path == "/api/soul/academy/curriculum":
            return self.send_json(self.server.cognition.curriculum.status())
        if path == "/api/soul/persona/arc":
            return self.send_json(self.server.cognition.persona_arc.history())
        if path == "/api/soul/persona/links":
            return self.send_json(self.server.cognition.persona_arc.links())
        if path == "/api/soul/academy/practice":
            return self.send_json({
                **self.server.cognition.practice.questions(),
                **self.server.cognition.practice.status()
            })
        if path.startswith("/api/soul/next/"):
            try:
                next_core=self.server.cognition.next
                readers={
                    "/api/soul/next/state": next_core.working_state,
                    "/api/soul/next/understanding": next_core.understanding,
                    "/api/soul/next/skills": next_core.skill_synthesis,
                    "/api/soul/next/personality": next_core.personality_growth,
                    "/api/soul/next/questions": next_core.questions,
                    "/api/soul/next/simulations": next_core.simulations,
                }
                if path in readers:
                    return self.send_json(readers[path]())
                return self.send_json({"error":"Unknown Cognitive Soul endpoint."},404)
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if path.startswith("/api/soul/forge"):
            try:
                engine=self.server.cognition.forge
                if path=="/api/soul/forge":
                    return self.send_json(engine.status())
                if path=="/api/soul/forge/skills":
                    return self.send_json(engine.skills())
                if path=="/api/soul/forge/history":
                    return self.send_json(engine.history())
                if path=="/api/soul/forge/budget":
                    return self.send_json(engine.budget_status())
                if path=="/api/soul/forge/curriculum":
                    return self.send_json(engine.curriculum(
                        args.get("domain",["logic"])[0]))
                if path=="/api/soul/forge/compete":
                    return self.send_json(engine.compete(
                        args.get("domain",["logic"])[0]))
                return self.send_json({"error":"Unknown Skill Forge resource."},404)
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if path == "/api/soul/skills":
            try:
                return self.send_json(self.server.cognition.skills.list(
                    limit=int(args.get("limit",["30"])[0])))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if path == "/api/soul/evolution4":
            return self.send_json(self.server.cognition.evolution4.report())
        if path == "/api/soul/evolution4/cases":
            return self.send_json(self.server.cognition.evolution4.case_index())
        if path == "/api/soul/evolution4/budget":
            return self.send_json(self.server.cognition.evolution4.budget_plan())
        if path == "/api/soul/evolution4/signals":
            return self.send_json(self.server.cognition.evolution4.signals())
        if path == "/api/soul/evolution4/curriculum":
            try:
                return self.send_json(self.server.cognition.evolution4.curriculum(
                    args.get("domain",["logic"])[0]))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if path == "/api/soul/improvement":
            return self.send_json(self.server.cognition.improvement.report())
        if path == "/api/soul/improvement/cases":
            return self.send_json(self.server.cognition.improvement.cases())
        if path == "/api/soul/improvement/diagnostics":
            return self.send_json(self.server.cognition.improvement.diagnose())
        if path == "/api/soul/evolution":
            return self.send_json(self.server.cognition.evolution.report())
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
        if path == "/api/soul/unified":
            return self.send_json(self.server.cognition.orchestrator.status(
                self.server.cognition.scheduler))
        if path == "/api/soul/unified/reviews":
            return self.send_json(self.server.cognition.orchestrator.reviews())
        if path == "/api/soul/cognition":
            return self.send_json(self.server.cognition.status())
        if path == "/api/soul/semantic/groups":
            try:
                return self.send_json(self.server.cognition.semantic_groups.list(
                    limit=int(args.get("limit",["60"])[0])))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
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
                if path == "/api/memory/history/search":
                    before=arg("before_id")
                    return self.send_json(self.server.cognition.dialogues.search(
                        arg("query"),limit=int(arg("limit","20")),
                        before_id=int(before) if before else None))
                if path == "/api/memory/history/recap":
                    return self.send_json(self.server.cognition.dialogues.summary(
                        arg("conversation_id")))
                if path == "/api/memory/history/context":
                    return self.send_json(self.server.cognition.dialogues.context(
                        int(arg("message_id","0"))))
                if path == "/api/memory/history/health":
                    return self.send_json(self.server.cognition.dialogues.health())
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
        if method == "POST" and path.startswith("/api/soul/forge/"):
            try:
                data=self._read_body()
                engine=self.server.cognition.forge
                if path=="/api/soul/forge/propose":
                    result=engine.propose(
                        data.get("domain"),confirmed=data.get("confirmed",False))
                elif path=="/api/soul/forge/variant":
                    result=engine.variant(
                        data.get("skill_id"),confirmed=data.get("confirmed",False))
                elif path=="/api/soul/forge/compose":
                    result=engine.compose(
                        data.get("left_id"),data.get("right_id"),
                        confirmed=data.get("confirmed",False))
                elif path=="/api/soul/forge/transfer":
                    result=engine.transfer(
                        data.get("skill_id"),data.get("target"),
                        confirmed=data.get("confirmed",False))
                elif path=="/api/soul/forge/shadow":
                    if data.get("confirmed") is not True:
                        raise ValueError("Подтверди локальную проверку.")
                    result=engine.shadow(limit=data.get("limit",8))
                elif path=="/api/soul/forge/decide":
                    result=engine.decide(
                        data.get("skill_id"),approve=data.get("approve"),
                        confirmed=data.get("confirmed",False))
                elif path=="/api/soul/forge/archive":
                    result=engine.archive(
                        data.get("skill_id"),confirmed=data.get("confirmed",False))
                elif path=="/api/soul/forge/budget/request":
                    result=engine.request_budget(
                        data.get("calls"),confirmed=data.get("confirmed",False))
                elif path=="/api/soul/forge/budget/decide":
                    result=engine.decide_budget(
                        data.get("approve"),confirmed=data.get("confirmed",False),
                        ack_unpriced=data.get("ack_unpriced",False))
                elif path=="/api/soul/forge/cloud/draft":
                    if not self.server.chat_lock.acquire(blocking=False):
                        raise CloudError("Дождись окончания чата или другого теста.",409)
                    try:
                        result=engine.cloud_draft(
                            data.get("skill_id"),self.server.cloud,
                            self.server.persona,
                            confirmed_send=data.get("confirm_cloud_calls",False))
                    finally:
                        self.server.chat_lock.release()
                else:
                    return self.send_json({"error":"Unknown Skill Forge operation."},404)
                return self.send_json(result)
            except CloudError as error:
                return self.send_json({"error":str(error)},error.status)
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path.startswith("/api/soul/unified/"):
            try:
                obj=self._read_body()
                engine=self.server.cognition.orchestrator
                if path=="/api/soul/unified/control":
                    return self.send_json(engine.configure(
                        obj.get("enabled"),confirmed=obj.get("confirmed",False)))
                if path=="/api/soul/unified/review":
                    return self.send_json(engine.mark_reviewed(
                        obj.get("id"),confirmed=obj.get("confirmed",False)))
                if path=="/api/soul/unified/tick":
                    if obj.get("confirmed") is not True:
                        raise ValueError("Локальная проверка требует подтверждения.")
                    return self.send_json(engine.tick(
                        self.server.cognition,chat_lock=self.server.chat_lock,
                        max_millis=850))
                return self.send_json({"error":"Unknown unified cognitive route."},404)
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/system/cloud-budget":
            try:
                obj=self._read_body()
                return self.send_json(self.server.guard.configure(
                    chat_per_day=obj.get("chat_per_day"),
                    embeddings_per_day=obj.get("embeddings_per_day"),
                    per_minute=obj.get("per_minute")))
            except (ValueError,sqlite3.Error) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/cloud/documents/consent":
            try:
                obj=self._read_body()
                return self.send_json(self.server.cloud.enable_document_context(
                    obj.get("allowed")))
            except (ValueError,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path.startswith("/api/soul/next/"):
            try:
                obj=self._read_body()
                next_core=self.server.cognition.next
                if path=="/api/soul/next/causal":
                    result=next_core.causal_map(obj.get("query"),limit=obj.get("limit",8))
                elif path=="/api/soul/next/selfcheck":
                    result=next_core.self_check(obj.get("message_id"))
                elif path=="/api/soul/next/gap":
                    result=next_core.knowledge_gap(obj.get("query"))
                elif path=="/api/soul/next/question/save":
                    result=next_core.save_question(
                        obj.get("question"),confirmed=obj.get("confirmed",False))
                elif path=="/api/soul/next/question/decide":
                    result=next_core.question_decide(
                        obj.get("id"),obj.get("state"),
                        confirmed=obj.get("confirmed",False))
                elif path=="/api/soul/next/goal/save":
                    result=next_core.add_goal(
                        obj.get("title"),confirmed=obj.get("confirmed",False))
                elif path=="/api/soul/next/goal/state":
                    result=next_core.goal_state(
                        obj.get("id"),obj.get("state"),
                        confirmed=obj.get("confirmed",False))
                elif path=="/api/soul/next/scenario":
                    result=next_core.scenario(
                        obj.get("decision"),obj.get("option_a"),obj.get("option_b"))
                elif path=="/api/soul/next/memory/review":
                    result=next_core.memory_review(limit=obj.get("limit",100))
                elif path=="/api/soul/next/simulate":
                    result=next_core.simulate(
                        obj.get("strategy"),obj.get("skill"),obj.get("answer"),
                        confirmed=obj.get("confirmed",False))
                else:
                    return self.send_json({"error":"Unknown Cognitive Soul action."},404)
                return self.send_json(result)
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/reflect":
            try:
                obj=self._read_body()
                return self.send_json(self.server.cognition.reflection.inspect(
                    obj.get("query"),limit=obj.get("limit",8)))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/skills/decide":
            try:
                obj=self._read_body()
                return self.send_json(self.server.cognition.skills.decide(
                    obj.get("candidate_id"),approve=obj.get("approve"),
                    confirmed=obj.get("confirmed",False)))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path.startswith("/api/soul/evolution4/"):
            try:
                obj=self._read_body()
                engine=self.server.cognition.evolution4
                if path=="/api/soul/evolution4/generate":
                    result=engine.generate(
                        obj.get("domain"),count=obj.get("count",4),
                        confirmed=obj.get("confirmed",False))
                elif path=="/api/soul/evolution4/case":
                    result=engine.add_case(
                        obj.get("domain"),obj.get("question"),obj.get("expected"),
                        split=obj.get("split","holdout"),
                        confirmed=obj.get("confirmed",False))
                elif path=="/api/soul/evolution4/decision":
                    result=engine.decide(
                        obj.get("candidate_id"),approve=obj.get("approve"),
                        confirmed=obj.get("confirmed",False))
                elif path=="/api/soul/evolution4/recover":
                    if not self.server.chat_lock.acquire(blocking=False):
                        raise CloudError("Сначала дождись окончания текущего теста.",409)
                    try:
                        result=engine.recover(
                            obj.get("run_id"),confirmed=obj.get("confirmed",False))
                    finally:
                        self.server.chat_lock.release()
                elif path=="/api/soul/evolution4/peer-review":
                    result=engine.peer_review(obj.get("candidate_id"))
                elif path=="/api/soul/evolution4/transfer":
                    result=engine.transfer(obj.get("candidate_id"))
                elif path=="/api/soul/evolution4/evaluate":
                    if not self.server.chat_lock.acquire(blocking=False):
                        raise CloudError("Дождись окончания чата или другого теста.",409)
                    try:
                        prior=self.server.cognition.improvement.guidance()[1]
                        result=engine.evaluate(
                            obj.get("candidate_id"),obj.get("evaluation_domain"),
                            self.server.cloud,self.server.persona,
                            confirmed_send=obj.get("confirm_cloud_calls",False),
                            batch_size=obj.get("batch_size",6),
                            baseline_guidance=prior[:900])
                    finally:
                        self.server.chat_lock.release()
                else:
                    return self.send_json({"error":"Unknown evolution4 action."},404)
                return self.send_json(result)
            except CloudError as error:
                return self.send_json({"error":str(error)},error.status)
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path.startswith("/api/soul/improvement/"):
            try:
                obj=self._read_body()
                engine=self.server.cognition.improvement
                if path=="/api/soul/improvement/propose":
                    result=engine.propose(
                        obj.get("strategy"),confirmed=obj.get("confirmed",False))
                elif path=="/api/soul/improvement/case":
                    result=engine.add_case(
                        obj.get("question"),obj.get("expected"),
                        split=obj.get("split","holdout"),
                        confirmed=obj.get("confirmed",False))
                elif path=="/api/soul/improvement/case/delete":
                    result=engine.remove_case(
                        obj.get("id"),confirmed=obj.get("confirmed",False))
                elif path=="/api/soul/improvement/decide":
                    result=engine.decide(
                        obj.get("proposal_id"),approve=obj.get("approve"),
                        confirmed=obj.get("confirmed",False))
                elif path=="/api/soul/improvement/recover":
                    if not self.server.chat_lock.acquire(blocking=False):
                        raise CloudError("Нельзя закрыть тест во время активного чата или проверки.",409)
                    try:
                        result=engine.recover_interrupted(
                            obj.get("run_id"),confirmed=obj.get("confirmed",False))
                    finally:
                        self.server.chat_lock.release()
                elif path=="/api/soul/improvement/run":
                    if not self.server.chat_lock.acquire(blocking=False):
                        raise CloudError("Дождись окончания чата или другого теста.",409)
                    try:
                        result=engine.evaluate(
                            obj.get("proposal_id"),self.server.cloud,
                            self.server.persona,
                            confirmed_send=obj.get("confirm_cloud_calls",False),
                            max_pairs=obj.get("max_pairs",4))
                    finally:
                        self.server.chat_lock.release()
                else:
                    return self.send_json({"error":"Unknown improvement action."},404)
                return self.send_json(result)
            except CloudError as error:
                return self.send_json({"error":str(error)},error.status)
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/evolution/propose":
            try:
                obj=self._read_body()
                return self.send_json(self.server.cognition.evolution.propose(
                    obj.get("strategy")))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/evolution/start":
            try:
                obj=self._read_body()
                return self.send_json(self.server.cognition.evolution.start(
                    obj.get("trial_id"),confirmed=obj.get("confirmed",False)))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/evolution/decide":
            try:
                obj=self._read_body()
                return self.send_json(self.server.cognition.evolution.decide(
                    obj.get("trial_id"),activate=obj.get("activate"),
                    confirmed=obj.get("confirmed",False)))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/academy/practice/evaluate":
            try:
                obj=self._read_body()
                return self.send_json(self.server.cognition.practice.evaluate(
                    obj.get("skill"),obj.get("answer"),
                    confirmed=obj.get("confirmed",False)))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/academy/rollback/resume":
            try:
                obj=self._read_body()
                return self.send_json(self.server.cognition.rollback.resume(
                    obj.get("strategy"),confirmed=obj.get("confirmed",False)))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/documents/import-binary":
            try:
                obj=self._read_body(max_bytes=1500000)
                if obj.get("confirmed") is not True:
                    raise ValueError("Требуется подтверждение импорта.")
                encoded=obj.get("base64")
                if not isinstance(encoded,str) or len(encoded)>1400000:
                    raise ValueError("Файл превышает допустимый размер.")
                from document_parser import extract_document
                try:
                    binary=base64.b64decode(encoded,validate=True)
                except (ValueError,binascii.Error):
                    raise ValueError("Повреждённый Base64.") from None
                parsed=extract_document(obj.get("name"),binary)
                record=self.server.cognition.evidence.documents.import_text(
                    obj.get("name"),parsed["text"],confirmed=True)
                record["extraction_method"]=parsed["method"]
                record["complete_extraction_guaranteed"]=parsed["complete_extraction_guaranteed"]
                return self.send_json(record)
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/documents/import":
            try:
                obj=self._read_body()
                return self.send_json(self.server.cognition.evidence.documents.import_text(
                    obj.get("name"),obj.get("text"),confirmed=obj.get("confirmed",False)))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/documents/delete":
            try:
                obj=self._read_body()
                return self.send_json(self.server.cognition.evidence.documents.remove(
                    obj.get("id"),confirmed=obj.get("confirmed",False)))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path == "/api/soul/evidence":
            try:
                obj=self._read_body()
                cloud_state=self.server.cloud.status()
                model=(cloud_state.get("embedding_model")
                       if cloud_state.get("vector_chat_enabled") else None)
                return self.send_json(self.server.cognition.evidence.search(
                    obj.get("query"),limit=obj.get("limit",12),
                    model=model,use_cached_vectors=bool(model)))
            except (ValueError,sqlite3.Error,OSError) as error:
                return self._handle_error(error)
        if method == "POST" and path in (
            "/api/soul/semantic/link","/api/soul/semantic/unlink"):
            try:
                obj=self._read_body()
                groups=self.server.cognition.semantic_groups
                operation=groups.confirm if path.endswith("/link") else groups.revoke
                return self.send_json(operation(
                    obj.get("left_id"),obj.get("right_id"),
                    confirmed=obj.get("confirmed",False)))
            except (ValueError,sqlite3.Error,OSError) as error:
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
            guidance = self.server.cognition.guidance()
            evolving = self.server.cognition.evolution.guidance_for_conversation(conversation_id)
            improvement_id, approved = self.server.cognition.improvement.guidance()
            evol4_id, evol4_style = self.server.cognition.evolution4.guidance(content)
            forge_id, forge_style = self.server.cognition.forge.guidance(content)
            earlier = (guidance + " " + evolving + " " + approved).strip()
            combined = (evol4_style + " " + forge_style).strip()
            extra = (earlier[:max(0,1799-len(combined))] +
                     " " + combined).strip()[:1800]
            answer = cloud.complete(self.server.persona, history, memories,
                                    extra_guidance=extra)
            assistant = store.append_messages(conversation_id, [{
                "role": "assistant", "content": answer, "client_message_id": key
            }])[0]
            if improvement_id is not None:
                try:
                    self.server.cognition.improvement.applied(
                        assistant["id"],improvement_id)
                except (ValueError,sqlite3.Error):
                    pass
            if evol4_id is not None:
                try:
                    self.server.cognition.evolution4.applied(assistant["id"],evol4_id)
                except (ValueError,sqlite3.Error):
                    pass
            if forge_id is not None:
                try:
                    self.server.cognition.forge.applied(assistant["id"],forge_id)
                except (ValueError,sqlite3.Error):
                    pass
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
