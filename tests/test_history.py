import unittest

from aseprite_live.history import FileHistory
from aseprite_live.render import Frame, Sheet

from .helpers import make_png


def sheet(n: int) -> Sheet:
    # a distinct sheet per n (the durations differ)
    return Sheet(png=make_png(4, 4), width=4, height=4, frames=(Frame(0, 0, 4, 4, 100 + n),))


class HistoryTest(unittest.TestCase):
    def test_cap_drops_oldest(self):
        h = FileHistory("a.aseprite", max_steps=200)
        for n in range(250):
            h.add(sheet(n), now=n)
        self.assertEqual(len(h.steps), 200)
        self.assertEqual(h.steps[0].id, 51)  # 1..50 dropped
        self.assertEqual(h.latest.id, 250)
        self.assertIsNone(h.get(50))
        self.assertEqual(h.get(51).sheet.frames[0].duration, 150)

    def test_small_cap(self):
        h = FileHistory("a.aseprite", max_steps=1)
        h.add(sheet(1)); h.add(sheet(2))
        self.assertEqual([s.id for s in h.steps], [2])
        with self.assertRaises(ValueError):
            FileHistory("a.aseprite", max_steps=0)

    def test_identical_save_is_not_a_new_step(self):
        h = FileHistory("a.aseprite")
        self.assertIsNotNone(h.add(sheet(1)))
        self.assertIsNone(h.add(sheet(1)))
        self.assertIsNotNone(h.add(sheet(2)))
        self.assertIsNotNone(h.add(sheet(1)))  # going back is a change too
        self.assertEqual(len(h.steps), 3)

    def test_error_keeps_last_good_step(self):
        h = FileHistory("a.aseprite")
        h.add(sheet(1))
        h.fail("bad file")
        self.assertEqual(h.error, "bad file")
        self.assertEqual(h.latest.id, 1)
        h.add(sheet(2))
        self.assertIsNone(h.error)

    def test_labels_are_kept(self):
        h = FileHistory("a.aseprite")
        step = h.add(sheet(1), labels=[{"tool": "draw_line_at", "arguments": {}}])
        self.assertEqual(step.meta()["labels"][0]["tool"], "draw_line_at")
