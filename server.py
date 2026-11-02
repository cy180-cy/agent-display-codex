"""Local, read-only Agent Display bridge for the selected knowledge chat."""
from __future__ import annotations

import argparse
import hmac
import html
import re
import json
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

BASE = Path(__file__).resolve().parent

from observer import process_memory
from live_observer import LiveObserver

VERSION = "2.1.0"
WEB = BASE / "web"
ASSETS = {"/forge-live.js": ("forge-live.js", "text/javascript; charset=utf-8")}
CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "connect-src 'self'; img-src 'self' data: blob:; "
    "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


def load_config(path):
    selected_path = Path(path)
    if not selected_path.is_file():
        raise SystemExit("缺少本地配置。先运行 python configure.py --rollout <日志路径> --title <显示名称>")
    config = json.loads(selected_path.read_text("utf-8-sig"))
    thread = config.get("thread_id", "")
    if not isinstance(thread, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", thread):
        raise ValueError("thread_id 格式无效")
    rollout = Path(config.get("rollout_path", "")).expanduser().resolve()
    if not rollout.is_file() or rollout.suffix != ".jsonl":
        raise ValueError("请明确选择一个存在的 JSONL 执行记录文件")
    config["rollout_path"] = str(rollout)
    config["title"] = str(config.get("title", "我的任务"))[:80]
    return config


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, observer):
        if address[0] != "127.0.0.1":
            raise ValueError("此服务只允许在本机回环地址启动")
        super().__init__(address, Handler)
        self.observer = observer
        self.key = secrets.token_urlsafe(32)
        self.started = time.monotonic()

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentDisplayLocal/1"

    def log_message(self, *_):
        pass

    def send(self, code, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def permitted(self, api=False):
        port = self.server.server_port
        if self.headers.get("Host", "").lower() not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self.send(403, {"error": "host_not_allowed"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in (self.server.origin, f"http://localhost:{port}"):
            self.send(403, {"error": "origin_not_allowed"})
            return False
        if api and (self.headers.get("Sec-Fetch-Site") == "cross-site"
                    or not hmac.compare_digest(self.headers.get("X-Display-Key", ""), self.server.key)):
            self.send(403, {"error": "local_page_required"})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.permitted(api=path.startswith("/api/")):
            return
        if path == "/health":
            self.send(200, {"service": "agent-display-local", "version": VERSION,
                            "readonly": True, "thread_id": self.server.observer.thread_id, "liveThemes": ["forge", "works"]})
        elif path == "/api/snapshot":
            result = self.server.observer.snapshot()
            result["metrics"] = {**process_memory(),
                                 "uptimeSeconds": round(time.monotonic() - self.server.started),
                                 "version": VERSION, "canClearCache": True}
            result["lastCleanup"] = getattr(self.server.observer, "last_cleanup", None)
            self.send(200, result)
        elif path in ("/", "/index.html"):
            document_path = WEB / "index.html"
            if not document_path.is_file():
                self.send(503, {"error": "page_not_prepared"})
                return
            document = document_path.read_text("utf-8").replace("DISPLAY_LOCAL_SESSION_KEY", self.server.key)
            document = document.replace("DISPLAY_SELECTED_THREAD_ID", html.escape(self.server.observer.thread_id, quote=True))
            document = document.replace("DISPLAY_SELECTED_TITLE", html.escape(self.server.observer.config["title"], quote=True))
            self.send(200, document, "text/html; charset=utf-8")
        elif path in ASSETS:
            file_name, content_type = ASSETS[path]
            asset_path = WEB / file_name
            if not asset_path.is_file():
                self.send(404, {"error": "asset_not_found"})
                return
            self.send(200, asset_path.read_bytes(), content_type)
        elif path == "/favicon.ico":
            self.send(204, b"", "image/x-icon")
        else:
            self.send(404, {"error": "not_found"})

    def do_POST(self):
        if not self.permitted(api=True):
            return
        path = urlsplit(self.path).path
        if path == "/api/cache/clear":
            self.send(200, {"cleanup": self.server.observer.clear_history()})
        elif path == "/api/shutdown":
            self.send(200, {"stopping": True, "scope": "viewer-only"})
            self.server.observer.closed.set()
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self.send(405, {"error": "read_only"})

    def do_PUT(self):
        self.send(405, {"error": "read_only"})

    do_DELETE = do_PUT
    do_PATCH = do_PUT
    do_OPTIONS = do_PUT


def main():
    parser = argparse.ArgumentParser(description="Agent Display Codex：本机只读看板")
    parser.add_argument("--config", type=Path, default=BASE / "config.local.json")
    parser.add_argument("--port", type=int, default=8773)
    parser.add_argument("--open", action="store_true", help="启动后在默认浏览器打开看板")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("端口必须在 0 到 65535 之间")
    config = load_config(args.config)
    observer = LiveObserver(config)
    observer.poll()
    try:
        server = Server(("127.0.0.1", args.port), observer)
    except OSError as exc:
        raise SystemExit(f"无法启动本地看板，端口 {args.port} 可能已被占用：{exc}") from exc
    watcher = threading.Thread(target=observer.run, daemon=True, name="display-readonly-observer")
    watcher.start()
    print(json.dumps({"url": server.origin + "/#forge", "title": config["title"],
                      "readonly": True, "liveThemes": ["forge", "works"], "memory": process_memory()},
                     ensure_ascii=False), flush=True)
    if args.open:
        webbrowser.open(server.origin + "/#forge")
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        observer.closed.set()
        watcher.join(timeout=2)
        server.server_close()


if __name__ == "__main__":
    main()
