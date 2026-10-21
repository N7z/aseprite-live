"""stdio MCP proxy: forwards bytes unchanged and remembers each tool call.

MCP over stdio is newline-delimited JSON-RPC. Every line is written through
untouched; a copy is parsed on the side to learn which `tools/call` caused
which save, so the timeline can label its steps.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable

from .watcher import is_sprite


@dataclass
class Call:
    id: object
    name: str
    arguments: dict
    started: float
    paths: frozenset[str]  # sprite paths named in the arguments, resolved
    text: str  # all string arguments, for loose matching (Lua scripts)
    finished: float | None = None
    error: bool = False
    used_by: set[str] = field(default_factory=set)

    def label(self) -> dict:
        return {"tool": self.name, "arguments": self.arguments, "error": self.error}

    def mentions(self, path: str) -> bool:
        return path in self.paths or Path(path).name in self.text


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


class CallLog:
    """Recent tool calls, matched to the file changes they caused."""

    def __init__(self, cwd: str | None = None, keep: int = 500,
                 on_path: Callable[[str], None] = lambda p: None,
                 on_result: Callable[[], None] = lambda: None):
        self.cwd = Path(cwd or os.getcwd())
        self.calls: deque[Call] = deque(maxlen=keep)
        self._pending: dict[str, Call] = {}
        self._lock = threading.Lock()
        self.on_path = on_path  # a sprite path was named (maybe outside the roots)
        self.on_result = on_result  # a tool call finished: scan now

    @staticmethod
    def _key(msg_id) -> str:
        return json.dumps(msg_id)

    def _messages(self, line: bytes):
        try:
            msg = json.loads(line)
        except ValueError:
            return []
        return msg if isinstance(msg, list) else [msg]

    def client_line(self, line: bytes, now: float | None = None) -> None:
        for msg in self._messages(line):
            if not isinstance(msg, dict) or msg.get("method") != "tools/call" or "id" not in msg:
                continue
            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
            args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            strings = list(_strings(args))
            paths = frozenset(str((self.cwd / s).resolve()) for s in strings
                              if is_sprite(s) and "\n" not in s)
            call = Call(msg["id"], str(params.get("name", "?")), args,
                        time.time() if now is None else now, paths, "\n".join(strings))
            with self._lock:
                self.calls.append(call)
                self._pending[self._key(msg["id"])] = call
            for p in paths:
                self.on_path(p)

    def server_line(self, line: bytes, now: float | None = None) -> None:
        done = False
        for msg in self._messages(line):
            if not isinstance(msg, dict) or "id" not in msg or "method" in msg:
                continue
            with self._lock:
                call = self._pending.pop(self._key(msg["id"]), None)
            if call is None:
                continue
            call.finished = time.time() if now is None else now
            result = msg.get("result")
            call.error = "error" in msg or bool(isinstance(result, dict) and result.get("isError"))
            done = True
        if done:
            self.on_result()

    def labels_for(self, path: str, detected: float) -> list[dict]:
        """Calls that touched `path`, started before the change was seen, and
        have not labelled an earlier step of that file."""
        with self._lock:
            calls = [c for c in self.calls if c.started <= detected
                     and path not in c.used_by and c.mentions(path)]
            for c in calls:
                c.used_by.add(path)
        return [c.label() for c in calls]


def _pump(src: BinaryIO, dst: BinaryIO, inspect: Callable[[bytes], None]) -> None:
    try:
        for line in iter(src.readline, b""):
            dst.write(line)
            dst.flush()
            try:
                inspect(line)
            except Exception as e:  # inspecting must never break forwarding
                print(f"aseprite-live: could not inspect a message: {e}", file=sys.stderr)
    except (BrokenPipeError, ValueError, OSError):
        pass


def _stop(child: subprocess.Popen) -> None:
    child.terminate()
    try:
        child.wait(3)
    except subprocess.TimeoutExpired:
        child.kill()


def run_proxy(cmd: list[str], log: CallLog, stdin: BinaryIO | None = None,
              stdout: BinaryIO | None = None, grace: float = 2.0) -> int:
    """Run `cmd` as the real MCP server and sit between it and our client."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    child = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
    stopped = threading.Event()  # we ended a server the client had already left

    def client_to_server():
        _pump(stdin, child.stdin, log.client_line)
        try:
            child.stdin.close()  # client went away: let the server exit
        except OSError:
            pass
        # Some servers (the Python MCP SDK among them) keep running after
        # stdin closes, which would leave the proxy hanging around too.
        try:
            child.wait(grace)
        except subprocess.TimeoutExpired:
            stopped.set()
            _stop(child)

    threading.Thread(target=client_to_server, name="c2s", daemon=True).start()
    try:
        _pump(child.stdout, stdout, log.server_line)  # until the server exits
    finally:  # also on SIGTERM / Ctrl-C: never leave the server orphaned
        child.stdout.close()
        if child.poll() is None:
            _stop(child)
    code = child.wait()
    return 0 if stopped.is_set() else code
