# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Token-scoped loopback HTTP transport for the desktop host."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import queue
import secrets
import select
import socket
import threading
import time
from urllib.parse import unquote, urlsplit

from atlas_toolkit.app.rpc_frame import decode_frame, encode_frame
from atlas_toolkit.host import wire


EDIT_METHODS = frozenset({
    "modify", "add", "rename", "remove", "reset_edits", "enter_edit",
    "set_settings", "set_skeleton",
})
_TYPES = {
    ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
    ".css": "text/css", ".json": "application/json", ".png": "image/png",
    ".ico": "image/x-icon", ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
}
_LOG = logging.getLogger(__name__)


def _error(kind, message):
    return json.dumps({"ok": False, "error": {"type": kind, "message": message}},
                      ensure_ascii=True, separators=(",", ":"))


def _static_path(site, path):
    try:
        candidate = (site / (path or "index.html")).resolve()
        if candidate.is_relative_to(site) and candidate.is_file():
            return candidate
    except (OSError, ValueError, RuntimeError):
        pass
    return None


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        _LOG.debug("Desktop request failed for %s", client_address, exc_info=True)


class DesktopServer:
    def __init__(self, site: Path, host, commands, files, events, ping_interval=15.0):
        self.site = site.resolve()
        self.host = host
        self.commands = commands
        self.files = files
        self.events = events
        self.ping_interval = ping_interval
        self._unsaved = False
        self._engine_lock = threading.Lock()
        self._stopped = False
        self._token = secrets.token_urlsafe(32)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                _LOG.debug(format, *args)

            def end_headers(self):
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                super().end_headers()

            def reply(self, status, body=b"", content_type="text/plain"):
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            def handle_route(self):
                if self.headers.get_all("Host", []) != [owner._authority]:
                    self.reply(403)
                    return
                path = urlsplit(self.path).path
                parts = path.split("/", 3)
                if (len(parts) != 4 or parts[1] != "s"
                        or not hmac.compare_digest(parts[2].encode(), owner._token.encode())):
                    self.reply(404)
                    return
                path = unquote(parts[3])
                if path == "api/rpc":
                    if self.command != "POST":
                        self.reply(405)
                    elif self.headers.get_all("Origin", []) != [owner._origin]:
                        self.reply(403)
                    else:
                        self.rpc()
                    return
                if self.command != "GET":
                    self.reply(405)
                    return
                if path == "api/events":
                    self.events()
                    return
                if path.startswith("asset/"):
                    asset = owner.files.lookup(path[len("asset/"):])
                    try:
                        if asset is None:
                            raise FileNotFoundError
                        body = asset.read_bytes()
                    except OSError:
                        self.reply(404)
                    else:
                        self.reply(200, body, "application/octet-stream")
                    return
                candidate = _static_path(owner.site, path)
                if candidate is None:
                    self.reply(404)
                    return
                try:
                    body = candidate.read_bytes()
                except OSError:
                    self.reply(404)
                else:
                    self.reply(200, body, _TYPES.get(candidate.suffix, "application/octet-stream"))

            def rpc(self):
                length = self.headers.get("Content-Length")
                if length is None:
                    self.reply(411)
                    return
                try:
                    length = int(length)
                    if length < 0:
                        raise ValueError
                except ValueError:
                    self.reply(400)
                    return
                if length > 2 * 1024**3:
                    self.reply(413)
                    return
                try:
                    header, buffers = decode_frame(self.rfile.read(length))
                    if not isinstance(header, dict) or not all(
                        isinstance(header.get(key), str) for key in ("target", "method", "argsJson")
                    ):
                        raise ValueError("Invalid RPC header")
                    if header["target"] not in ("engine", "desktop"):
                        raise ValueError("Unknown target")
                except Exception:
                    self.reply(400, b"Invalid RPC frame")
                    return
                method = header["method"]
                if header["target"] == "engine":
                    with owner._engine_lock:
                        if method in EDIT_METHODS:
                            owner._unsaved = True
                        try:
                            text, output = wire.call(owner.host, method, header["argsJson"], buffers)
                        finally:
                            owner._unsaved = bool(owner.host.has_pending())
                elif method not in owner.commands.RPC_METHODS:
                    text, output = _error("ValueError", "Desktop method is not allowed"), []
                else:
                    text, output = wire.call(owner.commands, method, header["argsJson"], buffers)
                try:
                    body = encode_frame({"json": text}, output)
                except Exception:
                    body = encode_frame({"json": _error("EncodeError", "Cannot encode RPC response")}, [])
                self.reply(200, body, "application/octet-stream")

            def events(self):
                subscription = owner.events.subscribe()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    last_write = time.monotonic()
                    while True:
                        try:
                            event = subscription.get(timeout=min(owner.ping_interval, 1.0))
                        except queue.Empty:
                            body = None
                        except StopIteration:
                            break
                        else:
                            body = ("data: " + json.dumps(event, ensure_ascii=True,
                                    separators=(",", ":")) + "\n\n").encode("utf-8")
                        readable, _, _ = select.select([self.connection], [], [], 0)
                        if readable and self.connection.recv(1, socket.MSG_PEEK) == b"":
                            break
                        if body is None:
                            if time.monotonic() - last_write < owner.ping_interval:
                                continue
                            body = b": ping\n\n"
                        self.wfile.write(body)
                        self.wfile.flush()
                        last_write = time.monotonic()
                except OSError:
                    pass
                finally:
                    subscription.close()

            do_GET = handle_route
            do_POST = handle_route
            do_HEAD = handle_route
            do_PUT = handle_route
            do_DELETE = handle_route
            do_PATCH = handle_route
            do_OPTIONS = handle_route
            do_TRACE = handle_route
            do_CONNECT = handle_route

        self._http = _HTTPServer(("127.0.0.1", 0), Handler)
        self._authority = f"127.0.0.1:{self._http.server_port}"
        self._origin = f"http://{self._authority}"
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)

    @property
    def unsaved(self):
        return self._unsaved

    def start(self):
        self._thread.start()
        return f"{self._origin}/s/{self._token}/index.html"

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        if self._thread.is_alive():
            self.events.close()
            self._http.shutdown()
            self._thread.join()
        self._http.server_close()
