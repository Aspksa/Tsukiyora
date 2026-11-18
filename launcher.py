"""Tsukiyora local bootstrap: no third-party Python dependencies."""
from __future__ import annotations

import argparse
import html
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PROFILE_PATH = ROOT / "soul" / "personality.json"
RUNTIME_DIR = ROOT / "runtime"
LOG = logging.getLogger("tsukiyora")


class ProfileError(ValueError):
    """A missing or invalid personality profile."""


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(_nonempty(item) for item in value)


def validate_profile(data: object) -> dict:
    if not isinstance(data, dict) or not _nonempty(data.get("schema_version")):
        raise ProfileError("Missing schema_version in personality profile")
    identity = data.get("identity")
    communication = data.get("communication")
    development = data.get("development")
    if not isinstance(identity, dict) or not all(
        _nonempty(identity.get(key)) for key in ("name", "name_ru", "persona_summary")
    ):
        raise ProfileError("Missing required identity fields")
    if not isinstance(communication, dict) or not _nonempty(communication.get("address_user_as")) or not _string_list(communication.get("tone")):
        raise ProfileError("Missing required communication fields")
    if not _string_list(data.get("protected_values")):
        raise ProfileError("Missing protected_values")
    if not isinstance(development, dict) or development.get("change_protected_only_with_user_approval") is not True:
        raise ProfileError("Protected values must require user approval")
    return data


def load_profile(path: Path = PROFILE_PATH) -> dict:
    try:
        with path.open("r", encoding="utf-8-sig") as source:
            return validate_profile(json.load(source))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"Cannot load {path}: {exc}") from exc


def safe_profile(profile: dict) -> dict:
    """Only expose an allowlist. Future private fields must not leak to HTTP."""
    identity = profile["identity"]
    communication = profile["communication"]
    return {
        "name": identity["name"],
        "name_ru": identity["name_ru"],
        "summary": identity["persona_summary"],
        "address": communication["address_user_as"],
        "tone": communication["tone"],
        "protected_values": profile["protected_values"],
        "profile_version": profile["schema_version"],
        "cloud_connected": False,
        "chat_available": False,
    }


def render_page(profile: dict) -> str:
    public = safe_profile(profile)
    esc = lambda x: html.escape(str(x), quote=True)
    values = "".join(f"<li>{esc(x)}</li>" for x in public["protected_values"])
    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Цукиёра · SOUL CORE</title><style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
*{box-sizing:border-box}body{margin:0;background:#08090d;color:#eae6df;min-height:100vh;display:grid;place-items:center;padding:24px}
main{width:min(780px,100%);border:1px solid #483723;border-radius:24px;padding:clamp(24px,5vw,52px);background:radial-gradient(ellipse at 95% 0%,#322312 0%,#101116 49%,#0d0e12 100%);box-shadow:0 26px 70px #0008}
.eyebrow{font-size:12px;letter-spacing:.23em;color:#dcb777;text-transform:uppercase}
h1{font-size:clamp(36px,7vw,64px);font-weight:450;line-height:1.1;margin:18px 0 12px;color:#f4d6a5}
p{line-height:1.7;color:#c9c2b8;max-width:60ch}.line{height:1px;background:#55412b;margin:30px 0}
.tag{display:inline-flex;gap:9px;align-items:center;padding:8px 12px;border:1px solid #715634;border-radius:99px;color:#e7c48b;font-size:13px}
.dot{width:7px;height:7px;border-radius:50%;background:#d9b06c;box-shadow:0 0 10px #d9b06c88}
h2{font-size:16px;font-weight:550;color:#f0d7b0}ul{columns:2;padding-left:20px;line-height:1.9;color:#d0c5b5}
footer{color:#9e9486;font-size:13px;margin-top:28px;line-height:1.6}code{color:#e7c48b}
@media(max-width:530px){ul{columns:1}main{border-radius:17px}}
</style></head><body><main>
<div class="eyebrow">Tsukiyora / Soul Core / Prototype 01</div>
<h1>""" + esc(public["name_ru"]) + """</h1>
<p>""" + esc(public["summary"]) + """</p>
<div class="tag"><span class="dot"></span> Профиль личности загружен локальным Python-сервером</div>
<div class="line"></div>
<h2>Постоянные ценности</h2><ul>""" + values + """</ul>
<p>Обращение: <strong>""" + esc(public["address"]) + """</strong>. Это первая техническая основа личности: пока без облачной модели, памяти диалогов и голосового общения.</p>
<footer>Проверка: <code>/api/health</code> · Профиль: <code>/api/personality</code><br>
Характер сохраняется в папке проекта, а не внутри облачной модели. Эта страница пока не чат.</footer>
</main></body></html>"""


def handler_for(profile: dict):
    public = safe_profile(profile)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, kind: str):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, data, "application/json; charset=utf-8")

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                self._send(200, render_page(profile).encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/health":
                self._json(200, {"ok": True, "service": "Tsukiyora", "profile_loaded": True, "chat_available": False})
            elif path == "/api/personality":
                self._json(200, public)
            else:
                self._json(404, {"error": "not_found"})

        def do_POST(self):
            self._json(501, {"error": "chat_not_configured", "message": "Cloud chat is not connected yet"})

        def log_message(self, fmt, *args):
            LOG.info("HTTP %s", fmt % args)

    return Handler


def init_logging():
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG.setLevel(logging.INFO)
    if not LOG.handlers:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        file_handler = RotatingFileHandler(RUNTIME_DIR / "launcher.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        LOG.addHandler(file_handler)
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        LOG.addHandler(console)


def obtain_instance_lock():
    """Keep file handle open; operating system releases the lock on exit."""
    f = (RUNTIME_DIR / "instance.lock").open("a+b")
    try:
        f.seek(0)
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


def open_browser(url: str):
    try:
        webbrowser.open(url)
    except Exception:
        LOG.exception("Browser could not be opened automatically")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run Tsukiyora local Python server")
    parser.add_argument("--check", action="store_true", help="Validate personality profile without starting")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    parser.add_argument("--port", type=int, default=53554, help="First local port to try (0 for random)")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is required", file=sys.stderr)
        return 2
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        profile = load_profile()
    except ProfileError as exc:
        print(f"Profile error: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print(f"OK: {profile['identity']['name_ru']} · profile v{profile['schema_version']}")
        return 0
    try:
        init_logging()
    except OSError as exc:
        print(f"Cannot initialize local runtime directory: {exc}", file=sys.stderr)
        return 2
    lock = obtain_instance_lock()
    if lock is None:
        print("Tsukiyora is already running from this folder. No duplicate started.")
        return 0
    server = None
    try:
        for port in ([0] if args.port == 0 else range(args.port, min(args.port + 40, 65536))):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(profile))
                server.daemon_threads = True
                break
            except OSError:
                continue
        if server is None:
            LOG.error("No available local port in range")
            return 2
        url = f"http://127.0.0.1:{server.server_port}/"
        LOG.info("Tsukiyora profile loaded: %s", profile["identity"]["name_ru"])
        LOG.info("Open: %s (Ctrl+C to stop)", url)
        if not args.no_browser:
            threading.Timer(0.3, open_browser, args=(url,)).start()
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            LOG.info("Stopping Tsukiyora")
        return 0
    finally:
        if server is not None:
            server.server_close()
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
