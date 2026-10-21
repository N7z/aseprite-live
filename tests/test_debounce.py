import threading
import time
import unittest

from aseprite_live.debounce import Debouncer


def wait_idle(d, timeout=3.0):
    end = time.monotonic() + timeout
    while d.pending() and time.monotonic() < end:
        time.sleep(0.01)


class DebounceTest(unittest.TestCase):
    def test_burst_becomes_one_call(self):
        calls = []
        d = Debouncer(calls.append, delay=0.08, max_wait=5)
        for _ in range(20):
            d.trigger("a")
            time.sleep(0.005)
        wait_idle(d)
        self.assertEqual(calls, ["a"])

    def test_keys_are_independent(self):
        calls = []
        d = Debouncer(calls.append, delay=0.05)
        d.trigger("a"); d.trigger("b"); d.trigger("a")
        wait_idle(d)
        self.assertEqual(sorted(calls), ["a", "b"])

    def test_never_two_runs_of_one_key_at_once(self):
        lock = threading.Lock()
        running, peak, runs = [0], [0], [0]

        def slow(key):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.15)
            with lock:
                running[0] -= 1
                runs[0] += 1

        d = Debouncer(slow, delay=0.01, max_wait=0.02)
        for _ in range(30):  # keeps triggering while the first run is busy
            d.trigger("a")
            time.sleep(0.01)
        wait_idle(d)
        self.assertEqual(peak[0], 1)
        # the first run, then the triggers queued meanwhile collapse into few reruns
        self.assertGreaterEqual(runs[0], 2)
        self.assertLessEqual(runs[0], 4)

    def test_max_wait_renders_during_a_long_burst(self):
        times = []
        d = Debouncer(lambda k: times.append(time.monotonic()), delay=0.1, max_wait=0.2)
        start = time.monotonic()
        while time.monotonic() - start < 0.7:  # never quiet for 100 ms
            d.trigger("a")
            time.sleep(0.02)
        self.assertGreaterEqual(len(times), 2)  # renders happened mid-burst
        self.assertLess(times[0] - start, 0.35)
        wait_idle(d)

    def test_error_does_not_stop_later_runs(self):
        calls = []

        def flaky(key):
            calls.append(key)
            if len(calls) == 1:
                raise RuntimeError("boom")

        d = Debouncer(flaky, delay=0.02)
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()):
            d.trigger("a"); wait_idle(d)
            d.trigger("a"); wait_idle(d)
        self.assertEqual(calls, ["a", "a"])

    def test_close_cancels_pending(self):
        calls = []
        d = Debouncer(calls.append, delay=0.05)
        d.trigger("a")
        d.close()
        d.trigger("b")
        time.sleep(0.15)
        self.assertEqual(calls, [])
