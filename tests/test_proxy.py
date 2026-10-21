import io
import json
import sys
import unittest
from pathlib import Path

from aseprite_live.proxy import CallLog, run_proxy


def line(obj) -> bytes:
    return json.dumps(obj).encode() + b"\n"


def call(i, name, **args):
    return line({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                 "params": {"name": name, "arguments": args}})


def result(i, error=False):
    return line({"jsonrpc": "2.0", "id": i, "result": {"content": [], "isError": error}})


class CallLogTest(unittest.TestCase):
    def test_labels_match_file_and_time(self):
        log = CallLog(cwd="/work")
        log.client_line(call(1, "draw_line_at", filename="a.aseprite", x1=1), now=10)
        log.client_line(call(2, "draw_pixels", filename="/other/b.aseprite"), now=11)
        log.server_line(result(1), now=12)
        log.server_line(result(2, error=True), now=13)
        log.client_line(call(3, "fill_area_at", filename="a.aseprite"), now=20)

        a = str(Path("/work/a.aseprite").resolve())
        labels = log.labels_for(a, detected=15)
        self.assertEqual([l["tool"] for l in labels], ["draw_line_at"])  # not 3: started later
        self.assertEqual(log.labels_for(a, detected=15), [])  # each call labels one step
        self.assertEqual([l["tool"] for l in log.labels_for(a, detected=21)], ["fill_area_at"])
        b = log.labels_for(str(Path("/other/b.aseprite").resolve()), detected=15)
        self.assertTrue(b[0]["error"])

    def test_lua_script_mentions_file_by_name(self):
        log = CallLog(cwd="/work")
        log.client_line(call(1, "run_lua_script", script='app.open("hero.aseprite")'), now=1)
        self.assertEqual(len(log.labels_for("/somewhere/hero.aseprite", detected=2)), 1)

    def test_garbage_and_notifications_are_ignored(self):
        log = CallLog()
        log.client_line(b"not json\n")
        log.client_line(line({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        log.client_line(line([{"jsonrpc": "2.0", "id": 9, "method": "tools/list"}]))
        log.server_line(line({"jsonrpc": "2.0", "id": 9, "result": {}}))
        self.assertEqual(len(log.calls), 0)

    def test_hooks(self):
        seen, results = [], []
        log = CallLog(cwd="/work", on_path=seen.append, on_result=lambda: results.append(1))
        log.client_line(call("x", "draw", filename="/art/c.ase"))
        log.server_line(result("x"))
        self.assertEqual(seen, [str(Path("/art/c.ase").resolve())])
        self.assertEqual(results, [1])


# A fake MCP server: answers every request with a result echoing its id.
ECHO = r"""
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if "id" in msg:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": msg["id"]}}) + "\n")
        sys.stdout.flush()
"""


class RunProxyTest(unittest.TestCase):
    def test_forwards_unchanged(self):
        requests = (call(1, "draw_line_at", filename="a.aseprite")
                    + b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                    + '{"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {"t": "ação"}}\n'.encode())
        out = io.BytesIO()
        log = CallLog(cwd="/work")
        code = run_proxy([sys.executable, "-c", ECHO], log, stdin=io.BytesIO(requests), stdout=out)
        self.assertEqual(code, 0)
        replies = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertEqual(len(log.calls), 1)
        self.assertIsNotNone(log.calls[0].finished)

    def test_server_that_ignores_eof_is_stopped(self):
        import time
        start = time.monotonic()
        stubborn = "import sys, time\nsys.stdin.read()\ntime.sleep(60)"
        code = run_proxy([sys.executable, "-c", stubborn], CallLog(), stdin=io.BytesIO(b""),
                  stdout=io.BytesIO(), grace=0.2)
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(code, 0)
