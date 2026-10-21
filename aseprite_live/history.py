"""Per-file history of rendered versions ("steps")."""

from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass, field

from .render import Sheet

DEFAULT_MAX_STEPS = 200


@dataclass(frozen=True)
class Step:
    id: int
    time: float
    sheet: Sheet
    labels: tuple[dict, ...] = ()  # tool calls that caused this step (proxy mode)

    def meta(self) -> dict:
        return {"id": self.id, "time": self.time, "labels": list(self.labels),
                **self.sheet.meta()}


def sheet_digest(sheet: Sheet) -> str:
    h = hashlib.sha1(sheet.png)
    h.update(json.dumps(sheet.meta(), sort_keys=True).encode())
    return h.hexdigest()


@dataclass
class FileHistory:
    path: str
    max_steps: int = DEFAULT_MAX_STEPS
    steps: deque[Step] = field(init=False)
    error: str | None = None
    error_time: float | None = None
    deleted: bool = False
    changed: float = 0.0  # last time anything happened to this file
    _next_id: int = 1
    _last_digest: str | None = None

    def __post_init__(self):
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.steps = deque(maxlen=self.max_steps)

    def add(self, sheet: Sheet, labels=(), now: float | None = None) -> Step | None:
        """Record a new version. Returns None when it looks exactly like the last one."""
        now = time.time() if now is None else now
        self.error = self.error_time = None
        self.deleted = False
        self.changed = now
        digest = sheet_digest(sheet)
        if digest == self._last_digest and self.steps:
            return None
        self._last_digest = digest
        step = Step(self._next_id, now, sheet, tuple(labels))
        self._next_id += 1
        self.steps.append(step)  # deque(maxlen) drops the oldest
        return step

    def fail(self, message: str, now: float | None = None) -> None:
        """Remember a failed render; the last good step stays."""
        self.error = message
        self.error_time = self.changed = time.time() if now is None else now

    def get(self, step_id: int) -> Step | None:
        for step in reversed(self.steps):
            if step.id == step_id:
                return step
        return None

    @property
    def latest(self) -> Step | None:
        return self.steps[-1] if self.steps else None
