"""Glue: watcher -> debouncer -> render -> history -> browser (SSE)."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .debounce import Debouncer
from .history import DEFAULT_MAX_STEPS, FileHistory
from .render import IncompleteFile, RenderError, render_file
from .watcher import Poller


def file_id(path: str) -> str:
    return hashlib.sha1(path.encode()).hexdigest()[:12]


def log(msg: str) -> None:
    print(f"aseprite-live: {msg}", file=sys.stderr, flush=True)


class App:
    def __init__(self, roots, aseprite: str = "aseprite",
                 max_steps: int = DEFAULT_MAX_STEPS, delay: float = 0.1,
                 interval: float = 0.15,
                 labeler: Callable[[str, float], list[dict]] | None = None):
        self.aseprite = aseprite
        self.max_steps = max_steps
        self.labeler = labeler
        self.files: dict[str, FileHistory] = {}  # id -> history
        self.boot = secrets.token_hex(4)  # step ids restart with the process
        self._lock = threading.Lock()
        self._clients: set[queue.Queue] = set()
        self._detected: dict[str, float] = {}  # path -> when the last change was seen
        self.debouncer = Debouncer(self._render, delay=delay)
        self.poller = Poller(roots, self._on_change, self._on_delete, interval=interval)

    @property
    def roots(self) -> list[Path]:
        return self.poller.roots

    def start(self) -> None:
        self.poller.start()

    def stop(self) -> None:
        self.poller.stop()
        self.debouncer.close()

    # -- watcher callbacks -------------------------------------------------

    def _on_change(self, path: str) -> None:
        self._detected[path] = time.time()
        self.debouncer.trigger(path)

    def _on_delete(self, path: str) -> None:
        hist = self._history(path)
        hist.deleted = True
        hist.changed = time.time()
        self.broadcast("update", {"file": self.file_meta(hist)})

    # -- rendering -----------------------------------------------------------

    def _history(self, path: str) -> FileHistory:
        fid = file_id(path)
        with self._lock:
            hist = self.files.get(fid)
            if hist is None:
                hist = self.files[fid] = FileHistory(path, self.max_steps)
            return hist

    def _render(self, path: str) -> None:
        detected = self._detected.get(path, time.time())
        hist = self._history(path)
        if not os.path.exists(path):
            return
        try:
            sheet = render_file(path, self.aseprite)
        except IncompleteFile as e:
            # Still being written: the finishing write is a new change and
            # triggers another render. Say so without dropping the last image.
            hist.fail(f"file looks half-written, waiting ({e})")
            self.broadcast("update", {"file": self.file_meta(hist)})
            return
        except RenderError as e:
            hist.fail(str(e))
            log(f"{self.rel(path)}: {e}")
            self.broadcast("update", {"file": self.file_meta(hist)})
            return
        labels = self.labeler(path, detected) if self.labeler else []
        with self._lock:
            step = hist.add(sheet, labels)
        self.broadcast("update", {"file": self.file_meta(hist),
                                  "step": step.meta() if step else None})
        if step:
            log(f"{self.rel(path)}: step {step.id}")

    # -- state for the browser ------------------------------------------------

    def rel(self, path: str) -> str:
        for root in self.roots:
            try:
                rel = Path(path).relative_to(root)
            except ValueError:
                continue
            return str(rel) if root.is_dir() else Path(path).name
        return path

    def file_meta(self, hist: FileHistory, with_steps: bool = False) -> dict:
        meta = {
            "id": file_id(hist.path), "path": self.rel(hist.path),
            "name": Path(hist.path).name, "changed": hist.changed,
            "error": hist.error, "deleted": hist.deleted,
        }
        if with_steps:
            with self._lock:
                meta["steps"] = [s.meta() for s in hist.steps]
        return meta

    def snapshot(self) -> dict:
        with self._lock:
            files = list(self.files.values())
        return {"boot": self.boot, "roots": [str(r) for r in self.roots], "maxSteps": self.max_steps,
                "files": [self.file_meta(h, with_steps=True) for h in files]}

    def png(self, fid: str, step_id: int) -> bytes | None:
        with self._lock:
            hist = self.files.get(fid)
            step = hist.get(step_id) if hist else None
            return step.sheet.png if step else None

    # -- server-sent events ---------------------------------------------------

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._clients.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def broadcast(self, event: str, data: dict) -> None:
        msg = f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(msg)
            except queue.Full:  # a stuck tab: drop it, it reconnects
                self.unsubscribe(q)
                q.put(None)
