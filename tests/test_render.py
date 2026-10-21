import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from aseprite_live.render import (IncompleteFile, RenderError, check_header, parse_sheet,
                                  render_file)

from .helpers import find_aseprite, make_png

# Trimmed from real `aseprite -b ... --format json-array --list-layers --list-tags` output.
REAL = {
    "frames": [
        {"filename": "t 0.aseprite", "frame": {"x": 0, "y": 0, "w": 8, "h": 8}, "duration": 100},
        {"filename": "t 1.aseprite", "frame": {"x": 8, "y": 0, "w": 8, "h": 8}, "duration": 250},
    ],
    "meta": {
        "size": {"w": 16, "h": 8},
        "frameTags": [{"name": "walk", "from": 0, "to": 1, "direction": "pingpong", "color": "#000000ff"}],
        "layers": [{"name": "Layer 1", "opacity": 255}, {"name": "top", "opacity": 255}],
    },
}


def ase_header(size: int, frames: int = 1, magic: int = 0xA5E0) -> bytes:
    return struct.pack("<IHH", size, magic, frames) + b"\0" * (128 - 8)


class HeaderTest(unittest.TestCase):
    def test_complete_file(self):
        data = ase_header(200, frames=3) + b"\0" * 72
        self.assertEqual(check_header(data), 3)

    def test_truncated_file_is_incomplete(self):
        data = ase_header(500) + b"\0" * 10
        with self.assertRaises(IncompleteFile):
            check_header(data)
        with self.assertRaises(IncompleteFile):
            check_header(data[:40])  # shorter than the header itself

    def test_not_aseprite(self):
        with self.assertRaises(RenderError) as cm:
            check_header(ase_header(128, magic=0x1234))
        self.assertNotIsInstance(cm.exception, IncompleteFile)


class ParseSheetTest(unittest.TestCase):
    def test_real_output(self):
        sheet = parse_sheet(json.dumps(REAL), make_png(16, 8))
        self.assertEqual((sheet.width, sheet.height), (8, 8))
        self.assertEqual([(f.x, f.duration) for f in sheet.frames], [(0, 100), (8, 250)])
        self.assertEqual(sheet.tags[0].name, "walk")
        self.assertEqual(sheet.tags[0].direction, "pingpong")
        self.assertEqual(sheet.layers, ("Layer 1", "top"))
        self.assertEqual(sheet.meta()["frames"], [[0, 0, 8, 8, 100], [8, 0, 8, 8, 250]])

    def test_bytes_and_hash_format(self):
        hashed = dict(REAL, frames={f["filename"]: f for f in REAL["frames"]})
        sheet = parse_sheet(json.dumps(hashed).encode(), make_png(16, 8))
        self.assertEqual(len(sheet.frames), 2)

    def test_missing_duration_defaults(self):
        data = {"frames": [{"frame": {"x": 0, "y": 0, "w": 4, "h": 4}}]}
        self.assertEqual(parse_sheet(data, make_png(4, 4)).frames[0].duration, 100)

    def test_bad_inputs(self):
        png = make_png(16, 8)
        cases = {
            "not json": ("{oops", png),
            "no frames": ({"frames": []}, png),
            "not an object": ("[1, 2]", png),
            "frame outside the sheet": (
                {"frames": [{"frame": {"x": 12, "y": 0, "w": 8, "h": 8}}]}, png),
            "rect not ints": ({"frames": [{"frame": {"x": "0", "y": 0, "w": 8, "h": 8}}]}, png),
            "bad png": (REAL, b"not a png"),
            "png of half-written sheet": (REAL, png[:20]),
        }
        for name, (data, image) in cases.items():
            with self.subTest(name), self.assertRaises(RenderError):
                parse_sheet(data, image)

    def test_bad_tags_are_skipped(self):
        data = json.loads(json.dumps(REAL))
        data["meta"]["frameTags"] = [{"name": "far", "from": 0, "to": 9}, {"name": "x"},
                                     {"name": "ok", "from": 1, "to": 1}]
        self.assertEqual([t.name for t in parse_sheet(data, make_png(16, 8)).tags], ["ok"])


LUA = """
local s = Sprite(8, 6)
s:newFrame()
s.frames[1].duration = 0.12
s.frames[2].duration = 0.3
s.cels[1].image:drawPixel(1, 1, app.pixelColor.rgba(255, 0, 0, 255))
s:newTag(1, 2).name = "idle"
s:saveAs(app.params.out)
"""


@unittest.skipUnless(find_aseprite(), "Aseprite not installed")
class RealAsepriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.exe = find_aseprite()

    def test_render_and_truncated(self):
        script = self.tmp / "make.lua"
        script.write_text(LUA)
        out = self.tmp / "s.aseprite"
        subprocess.run([self.exe, "-b", "--script-param", f"out={out}", "--script", str(script)],
                       check=True, capture_output=True)
        sheet = render_file(out, self.exe)
        self.assertEqual((sheet.width, sheet.height, len(sheet.frames)), (8, 6, 2))
        self.assertEqual([f.duration for f in sheet.frames], [120, 300])
        self.assertEqual(sheet.tags[0].name, "idle")

        half = self.tmp / "half.aseprite"
        half.write_bytes(out.read_bytes()[:150])
        with self.assertRaises(IncompleteFile):
            render_file(half, self.exe)

    def test_missing_executable(self):
        f = self.tmp / "x.aseprite"
        f.write_bytes(ase_header(128))
        with self.assertRaises(RenderError) as cm:
            render_file(f, str(self.tmp / "no-such-aseprite"))
        self.assertIn("not found", str(cm.exception))
