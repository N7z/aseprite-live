"""A small polling watcher for .aseprite/.ase files (stdlib only)."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Callable

EXTENSIONS = (".aseprite", ".ase")
SKIP_DIRS = {"node_modules", "__pycache__", "venv"}


def is_sprite(name: str) -> bool:
    return name.lower().endswith(EXTENSIONS)


class Poller:
    """Scan folders recursively every `interval` seconds.

    Calls `on_change(path)` for new or modified files and `on_delete(path)`
    for removed ones. Polling (mtime + size) needs no dependencies and works
    the same on WSL, network drives and /mnt/c, where inotify is unreliable.
    """

    def __init__(self, roots, on_change: Callable[[str], None],
                 on_delete: Callable[[str], None] = lambda p: None,
                 interval: float = 0.25):
        self.roots: list[Path] = []
        self.on_change = on_change
        self.on_delete = on_delete
        self.interval = interval
        self._seen: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        for root in roots:
            self.add_root(root)

    def add_root(self, root) -> bool:
        root = Path(root).resolve()
        with self._lock:
            if any(root == r or root.is_relative_to(r) for r in self.roots):
                return False
            # A new root that contains old ones replaces them.
            self.roots = [r for r in self.roots if not r.is_relative_to(root)] + [root]
        self.poke()
        return True

    def poke(self) -> None:
        """Scan now instead of waiting for the next tick."""
        self._wake.set()

    def _walk(self, root: Path):
        if root.is_file():
            if is_sprite(root.name):
                yield str(root)
            return
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
            for name in filenames:
                if is_sprite(name):
                    yield os.path.join(dirpath, name)

    def scan(self) -> None:
        with self._lock:
            roots = list(self.roots)
        current: dict[str, tuple[int, int]] = {}
        for root in roots:
            for path in self._walk(root):
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                current[path] = (st.st_mtime_ns, st.st_size)
        for path, sig in current.items():
            if self._seen.get(path) != sig:
                self.on_change(path)
        for path in self._seen.keys() - current.keys():
            self.on_delete(path)
        self._seen = current

    def run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self.scan()
            except Exception as e:  # keep watching whatever happens
                print(f"aseprite-live: scan failed: {e}", file=sys.stderr)
            self._wake.wait(self.interval)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="poller", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
