"""Per-key debouncing with at most one run in flight per key."""

from __future__ import annotations

import sys
import threading
import time
import traceback
from typing import Callable, Hashable


class Debouncer:
    """Call `fn(key)` once a burst of `trigger(key)` calls goes quiet.

    - A burst is held for `delay` seconds after its last trigger, but never
      longer than `max_wait` after its first one, so a file that is saved
      continuously still gets rendered.
    - Runs of the same key never overlap: a trigger that fires while `fn` is
      running for that key queues exactly one more run after it.
    """

    def __init__(self, fn: Callable[[Hashable], None], delay: float = 0.15,
                 max_wait: float = 1.0):
        self.fn = fn
        self.delay = delay
        self.max_wait = max_wait
        self._lock = threading.Lock()
        self._timers: dict[Hashable, threading.Timer] = {}
        self._first: dict[Hashable, float] = {}
        self._running: set[Hashable] = set()
        self._again: set[Hashable] = set()
        self._closed = False

    def trigger(self, key: Hashable) -> None:
        with self._lock:
            if self._closed:
                return
            now = time.monotonic()
            first = self._first.setdefault(key, now)
            wait = max(0.0, min(self.delay, first + self.max_wait - now))
            old = self._timers.pop(key, None)
            if old:
                old.cancel()
            timer = threading.Timer(wait, self._fire, (key,))
            timer.daemon = True
            self._timers[key] = timer
            timer.start()

    def _fire(self, key: Hashable) -> None:
        with self._lock:
            if self._timers.get(key) is not threading.current_thread():
                return  # superseded by a later trigger
            del self._timers[key]
            self._first.pop(key, None)
            if key in self._running:
                self._again.add(key)
                return
            self._running.add(key)
        while True:
            try:
                self.fn(key)
            except Exception:  # never let one bad run kill the worker
                traceback.print_exc(file=sys.stderr)
            with self._lock:
                if key in self._again and not self._closed:
                    self._again.discard(key)
                    continue
                self._running.discard(key)
                return

    def pending(self) -> bool:
        with self._lock:
            return bool(self._timers or self._running)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for t in self._timers.values():
                t.cancel()
            self._timers.clear()
