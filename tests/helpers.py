import os
import shutil
import struct
import zlib
from pathlib import Path


def make_png(w: int, h: int) -> bytes:
    """A tiny transparent RGBA PNG."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\0" + b"\0" * 4 * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def find_aseprite():
    for exe in (os.environ.get("ASEPRITE_PATH"), str(Path.home() / "aseprite/build/bin/aseprite"),
                shutil.which("aseprite")):
        if exe and Path(exe).is_file():
            return exe
    return None
