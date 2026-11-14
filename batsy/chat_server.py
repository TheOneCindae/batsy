"""Authenticated loopback web chat. The page can drive Batsy's tools, so every request is origin-checked."""
import hmac
import json
import logging
import secrets
import threading
import time
import webbrowser
import base64
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from batsy.config import CONFIG_DIR, DATA_DIR, SOURCE_DIR
from batsy.conversations import ConversationService
from batsy.events import EventBus
from batsy.modes import ModeManager

log = logging.getLogger("batsy.chat")


def _token():
    path = CONFIG_DIR / "chat_token"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_text().strip()
    value = secrets.token_urlsafe(32)
    path.write_text(value)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


class ChatServer:
    def __init__(self, app):
        self.app = app
        self.bus = EventBus()
        self.conversations = ConversationService(app.brain.memory)
        self.modes = ModeManager(app.cfg, self.bus)
        self.token = _token()
        cfg = app.cfg.get("chat")
        self.host, self.port = cfg["host"], cfg["port"]
        self.server = None
        self.opened = False
        self.pending = {}

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "BatsyChat/1"

            def log_message(self, fmt, *args):
                log.info("chat %s - %s", self.address_string(), fmt % args)

            def _auth(self):
                cookie = self.headers.get("Cookie", "")
                bearer = self.headers.get("Authorization", "")
                supplied = self.headers.get("X-Batsy-Token", "")
                if not supplied:
                    supplied = next((x.partition("=")[2] for x in cookie.split("; ") if x.startswith("batsy=")), "")
                if not supplied:
                    supplied = bearer.removeprefix("Bearer ")
                return hmac.compare_digest(supplied, owner.token)

            def _host_ok(self):
                return self.headers.get("Host", "").lower() in {f"127.0.0.1:{owner.port}", f"localhost:{owner.port}"}

            def _origin_ok(self):
                origin = self.headers.get("Origin")
                return origin in {f"http://127.0.0.1:{owner.port}", f"http://localhost:{owner.port}"}

            def _json(self, obj, status=200):
                data = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                n = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                if not self._host_ok():
                    return self._json({"error": "invalid host"}, 403)
                u = urlparse(self.path)
                if u.path == "/" and "token" in parse_qs(u.query):
                    if not hmac.compare_digest(parse_qs(u.query)["token"][0], owner.token):
                        return self._json({"error": "unauthorized"}, 401)
                    self.send_response(302)
                    self.send_header("Set-Cookie", f"batsy={owner.token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000")
                    self.send_header("Location", "/")
                    self.end_headers()
                    return
                if not self._auth():
                    return self._json({"error": "unauthorized"}, 401)
                if u.path == "/":
                    p = SOURCE_DIR / "chat_ui" / "index.html"
                    content = p.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                elif u.path in ("/chat.js", "/chat.css"):
                    p = SOURCE_DIR / "chat_ui" / u.path[1:]
                    content = p.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/javascript" if u.path.endswith(".js") else "text/css")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                elif u.path == "/api/conversations":
                    self._json({"items": owner.conversations.list(parse_qs(u.query).get("q", [""])[0]),
                                "active": owner.conversations.active_id})
                elif u.path == "/api/messages":
                    cid = parse_qs(u.query).get("id", [None])[0]
                    self._json({"items": owner.conversations.messages(int(cid) if cid else None)})
                elif u.path == "/api/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    q = owner.bus.subscribe(int(self.headers.get("Last-Event-ID", "0")))
                    try:
                        while True:
                            try:
                                e = q.get(timeout=15)
                                self.wfile.write(f"id: {e['seq']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n".encode())
                            except Exception:
                                self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        owner.bus.unsubscribe(q)
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):
                if not self._host_ok() or not self._origin_ok():
                    return self._json({"error": "forbidden origin or host"}, 403)
                if not self._auth():
                    return self._json({"error": "unauthorized"}, 401)
                try:
                    if int(self.headers.get("Content-Length", "0")) > 12_000_000:
                        return self._json({"error": "request too large"}, 413)
                    data = self._body()
                    if self.path == "/api/send":
                        text = str(data.get("text", "")).strip()
                        if not text:
                            return self._json({"error": "empty message"}, 400)
                        cid = int(data.get("conversation") or owner.conversations.active_id)
                        owner.conversations.activate(cid)
                        tid, abort = owner.conversations.begin_turn()
                        def work():
                            raw = []
                            delta = lambda s: (raw.append(s), owner.bus.publish("text_delta", id=tid, text=s))
                            def confirm(question):
                                aid = secrets.token_hex(12)
                                done = threading.Event()
                                owner.pending[aid] = {"done": done, "answer": False}
                                owner.bus.publish("confirm_request", id=aid, question=question)
                                done.wait(timeout=60)
                                req = owner.pending.pop(aid, None)
                                return bool(req and req["answer"])
                            try:
                                if not owner.app.local_command(text):
                                    owner.app.run_turn(text, "chat", lambda s: owner.bus.publish("sentence", id=tid, text=s), abort, confirm,
                                                       conversation_id=cid, turn_id=tid, delta_callback=delta,
                                                       brain_preference=data.get("brain", "auto"), chat_style=True)
                                memory = owner.app.brain.memory
                                with memory.lock, memory.db:
                                    row = memory.db.execute("SELECT id FROM exchanges WHERE user=? AND conversation_id IS NULL ORDER BY id DESC LIMIT 1", (text,)).fetchone()
                                    if row:
                                        memory.db.execute("UPDATE exchanges SET conversation_id=?,reply_raw=?,style='chat',brain=? WHERE id=?",
                                                          (cid, "".join(raw), getattr(owner.app.brain, "route", "local"), row["id"]))
                                        memory.db.execute("UPDATE conversations SET updated=? WHERE id=?", (time.time(), cid))
                                    c = memory.db.execute("SELECT title FROM conversations WHERE id=?", (cid,)).fetchone()
                                    if c and c["title"] == "New chat":
                                        memory.db.execute("UPDATE conversations SET title=? WHERE id=?", (text[:72], cid))
                                owner.bus.publish("turn_done", id=tid, conversation=cid, reply="".join(raw), brain=getattr(owner.app.brain, "route", "local"))
                            except Exception as exc:
                                log.exception("chat turn failed")
                                owner.bus.publish("turn_error", id=tid, error=str(exc))
                            finally:
                                owner.conversations.finish_turn(tid)
                        threading.Thread(target=work, daemon=True, name=f"chat-{tid}").start()
                        self._json({"id": tid, "conversation": cid}, 202)
                    elif self.path == "/api/stop":
                        owner.conversations.stop(data.get("id"))
                        from batsy import claude_code
                        claude_code.cancel(thread="turn")
                        self._json({"ok": True})
                    elif self.path == "/api/conversation/new":
                        cid = owner.conversations.create()
                        owner.app.brain.memory.new_session()
                        self._json({"id": cid})
                    elif self.path == "/api/conversation/activate":
                        cid = int(data["id"])
                        owner.conversations.activate(cid)
                        owner.app.brain.memory.new_session()
                        self._json({"id": cid})
                    elif self.path == "/api/conversation/rename":
                        with owner.app.brain.memory.lock, owner.app.brain.memory.db:
                            owner.app.brain.memory.db.execute("UPDATE conversations SET title=?,updated=? WHERE id=?",
                                                              (str(data["title"])[:100], time.time(), int(data["id"])))
                        self._json({"ok": True})
                    elif self.path == "/api/conversation/delete":
                        cid = int(data["id"])
                        with owner.app.brain.memory.lock, owner.app.brain.memory.db:
                            owner.app.brain.memory.db.execute("UPDATE conversations SET archived=1 WHERE id=?", (cid,))
                        if cid == owner.conversations.active_id:
                            owner.conversations.create()
                        self._json({"ok": True})
                    elif self.path == "/api/confirm":
                        req = owner.pending.get(data.get("id"))
                        if req:
                            req["answer"] = bool(data.get("yes"))
                            req["done"].set()
                        self._json({"ok": bool(req)})
                    elif self.path == "/api/upload":
                        name = Path(str(data.get("name", "upload"))).name
                        if not re.fullmatch(r"[\w .()-]{1,160}\.(pdf|png|jpe?g|gif|webp|bmp|tiff?)", name, re.I):
                            return self._json({"error": "Upload a PDF or image"}, 400)
                        content = base64.b64decode(data.get("data", ""), validate=True)
                        if len(content) > 8_000_000:
                            return self._json({"error": "file exceeds 8 MB"}, 413)
                        dest = Path.home() / "Batsy" / "inbox" / "chat"
                        dest.mkdir(parents=True, exist_ok=True)
                        path = dest / name
                        if path.exists():
                            path = dest / f"{path.stem}-{int(time.time())}{path.suffix}"
                        path.write_bytes(content)
                        from batsy import docs
                        note = docs.learn(path)
                        self._json({"name": path.name, "note": note})
                    elif self.path == "/api/mode":
                        owner.modes.set_mode(data["mode"])
                        owner.app.switch_mode(data["mode"])
                        self._json({"state": owner.modes.state})
                    elif self.path == "/api/brain":
                        preference = data.get("brain", "auto")
                        if preference not in ("auto", "local", "claude"):
                            return self._json({"error": "invalid brain"}, 400)
                        owner.app.brain.brain_preference = preference
                        self._json({"brain": preference})
                    else:
                        self._json({"error": "not found"}, 404)
                except (ValueError, KeyError, json.JSONDecodeError) as exc:
                    self._json({"error": str(exc)}, 400)

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True, name="chat-server").start()
        log.info("chat UI listening on http://%s:%s/", self.host, self.port)

    def url(self):
        return f"http://{self.host}:{self.port}/?token={self.token}"

    def open(self):
        if not self.opened and self.app.cfg.get("chat.open_browser"):
            self.opened = True
            webbrowser.open(self.url())
