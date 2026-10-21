"""Render an .aseprite file to a horizontal sprite sheet with the Aseprite CLI."""

from __future__ import annotations

import json
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ASE_MAGIC = 0xA5E0
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
HEADER_SIZE = 128


class RenderError(Exception):
    """Aseprite could not render the file, or its output made no sense."""


class IncompleteFile(RenderError):
    """The file is still being written (its header disagrees with its size)."""


@dataclass(frozen=True)
class Frame:
    x: int
    y: int
    w: int
    h: int
    duration: int  # milliseconds


@dataclass(frozen=True)
class Tag:
    name: str
    start: int
    end: int
    direction: str = "forward"
    repeat: int = 0  # 0 = loop forever


@dataclass(frozen=True)
class Sheet:
    png: bytes
    width: int  # sprite size, not sheet size
    height: int
    frames: tuple[Frame, ...]
    tags: tuple[Tag, ...] = ()
    layers: tuple[str, ...] = field(default=())

    def meta(self) -> dict:
        """The JSON the browser gets for a step (the PNG is served separately)."""
        return {
            "w": self.width,
            "h": self.height,
            "frames": [[f.x, f.y, f.w, f.h, f.duration] for f in self.frames],
            "tags": [
                {"name": t.name, "from": t.start, "to": t.end,
                 "direction": t.direction, "repeat": t.repeat}
                for t in self.tags
            ],
            "layers": list(self.layers),
        }


def check_header(data: bytes) -> int:
    """Validate the .aseprite header and return the frame count.

    The first DWORD of the header is the file size, so a file that is still
    being written is caught here: Aseprite itself happily renders a truncated
    file as blank frames and exits 0.
    """
    if len(data) >= 6 and struct.unpack_from("<H", data, 4)[0] != ASE_MAGIC:
        raise RenderError("not an Aseprite file (bad magic number)")
    if len(data) < HEADER_SIZE:
        raise IncompleteFile(f"file is {len(data)} bytes, shorter than the header")
    size, magic, frames = struct.unpack_from("<IHH", data, 0)
    if size != len(data):
        raise IncompleteFile(f"header says {size} bytes but the file has {len(data)}")
    if frames == 0:
        raise RenderError("file has no frames")
    return frames


def png_size(png: bytes) -> tuple[int, int]:
    if len(png) < 24 or not png.startswith(PNG_SIGNATURE) or png[12:16] != b"IHDR":
        raise RenderError("sheet is not a valid PNG")
    return struct.unpack(">II", png[16:24])


def _int(obj: dict, key: str, where: str) -> int:
    value = obj.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RenderError(f"{where}: '{key}' is missing or not an integer")
    return value


def parse_sheet(data: str | bytes | dict, png: bytes) -> Sheet:
    """Turn Aseprite's `--format json-array` output plus the sheet PNG into a Sheet."""
    if not isinstance(data, dict):
        try:
            data = json.loads(data)
        except (ValueError, UnicodeDecodeError) as e:
            raise RenderError(f"sheet JSON is invalid: {e}") from None
    if not isinstance(data, dict):
        raise RenderError("sheet JSON is not an object")

    raw_frames = data.get("frames")
    if isinstance(raw_frames, dict):  # --format json-hash, accepted anyway
        raw_frames = list(raw_frames.values())
    if not isinstance(raw_frames, list) or not raw_frames:
        raise RenderError("sheet JSON has no frames")

    sheet_w, sheet_h = png_size(png)
    frames = []
    for i, f in enumerate(raw_frames):
        rect = f.get("frame") if isinstance(f, dict) else None
        if not isinstance(rect, dict):
            raise RenderError(f"frame {i}: missing rectangle")
        x, y, w, h = (_int(rect, k, f"frame {i}") for k in ("x", "y", "w", "h"))
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > sheet_w or y + h > sheet_h:
            raise RenderError(f"frame {i}: rectangle {x},{y} {w}x{h} is outside the "
                              f"{sheet_w}x{sheet_h} sheet")
        duration = f.get("duration", 100)
        if not isinstance(duration, int) or duration <= 0:
            duration = 100
        frames.append(Frame(x, y, w, h, duration))

    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    tags = []
    for t in meta.get("frameTags") or []:
        try:
            start, end = int(t["from"]), int(t["to"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= start <= end < len(frames)):
            continue
        try:
            repeat = int(t.get("repeat", 0) or 0)
        except (TypeError, ValueError):
            repeat = 0
        tags.append(Tag(str(t.get("name", "")), start, end,
                        str(t.get("direction", "forward")), repeat))
    layers = tuple(str(l.get("name", "")) for l in meta.get("layers") or []
                   if isinstance(l, dict))

    return Sheet(png=png, width=frames[0].w, height=frames[0].h,
                 frames=tuple(frames), tags=tuple(tags), layers=layers)


def render_file(path: str | Path, aseprite: str = "aseprite", timeout: float = 30) -> Sheet:
    """Render every frame of `path` in one Aseprite call.

    The file is read once and rendered from a private copy, so a save that
    lands mid-render cannot mix two versions.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise RenderError(f"cannot read file: {e.strerror or e}") from None
    check_header(data)

    with tempfile.TemporaryDirectory(prefix="aseprite-live-") as tmp:
        tmp = Path(tmp)
        src = tmp / ("sprite" + path.suffix.lower())
        src.write_bytes(data)
        out_png, out_json = tmp / "out.png", tmp / "out.json"
        cmd = [aseprite, "-b", str(src),
               "--sheet", str(out_png), "--sheet-type", "horizontal",
               "--data", str(out_json), "--format", "json-array",
               "--list-layers", "--list-tags"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise RenderError(f"Aseprite not found at '{aseprite}' (set ASEPRITE_PATH)") from None
        except subprocess.TimeoutExpired:
            raise RenderError(f"Aseprite timed out after {timeout:g}s") from None
        # Aseprite exits 0 on most failures, so judge it by its output.
        output = (proc.stderr + proc.stdout).decode("utf-8", "replace").strip()
        output = output.replace(str(src), str(path))
        if not out_json.exists() or not out_png.exists():
            raise RenderError(output or f"Aseprite produced no sheet (exit {proc.returncode})")
        return parse_sheet(out_json.read_bytes(), out_png.read_bytes())
