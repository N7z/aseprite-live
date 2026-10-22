"""HTTP server: the page, the state, the PNGs and an SSE stream."""

from __future__ import annotations

import errno
import json
import queue
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .app import App

INDEX = Path(__file__).with_name("index.html")
HOST = "127.0.0.1"


class Handler(BaseHTTPRequestHandler):
    app: App  # set on the subclass made by make_server
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, status: int, body: bytes, ctype: str, cache: str = "no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            body = json.dumps(self.app.snapshot()).encode()
            self._send(200, body, "application/json")
        elif path == "/events":
            self._events()
        elif path.startswith("/png/"):
            parts = path.split("/")  # ['', 'png', fid, '<step>.png']
            png = None
            if len(parts) == 4 and parts[3].endswith(".png") and parts[3][:-4].isdigit():
                png = self.app.png(parts[2], int(parts[3][:-4]))
            if png is None:
                self._send(404, b"no such step", "text/plain")
            else:  # a step never changes, so the browser may keep it
                self._send(200, png, "image/png", "max-age=31536000, immutable")
        else:
            self._send(404, b"not found", "text/plain")

    def _events(self):
        q = self.app.subscribe()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            hello = json.dumps(self.app.snapshot(), separators=(",", ":"))
            self.wfile.write(f"retry: 1000\nevent: hello\ndata: {hello}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    msg = b": ping\n\n"
                if msg is None:
                    break
                self.wfile.write(msg)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.unsubscribe(q)
            self.close_connection = True


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind a port that is already
    # in use, so a stale viewer and a new one would silently share 7777 and the
    # browser could land on the stale one. Only reuse addresses elsewhere.
    allow_reuse_address = sys.platform != "win32"


def make_server(app: App, port: int, tries: int = 10) -> Server:
    """Bind 127.0.0.1:port, or the next free port after it."""
    handler = type("BoundHandler", (Handler,), {"app": app})
    for p in range(port, port + tries) if port else [0]:
        try:
            return Server((HOST, p), handler)
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
    raise OSError(errno.EADDRINUSE, f"ports {port}-{port + tries - 1} are all in use")


def serve_in_background(server: Server) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    t.start()
    return t


def url(server: Server) -> str:
    return f"http://localhost:{server.server_address[1]}/"
