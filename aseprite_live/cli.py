"""Command line: `aseprite-live [folder]` and `aseprite-live proxy -- <cmd>`."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys
from pathlib import Path

from .app import App, log
from .history import DEFAULT_MAX_STEPS
from .server import make_server, serve_in_background, url

DEFAULT_PORT = 7777


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"port on 127.0.0.1 (default {DEFAULT_PORT}; the next free one is used if taken)")
    p.add_argument("--aseprite", default=os.environ.get("ASEPRITE_PATH") or "aseprite",
                   help="Aseprite executable (default: $ASEPRITE_PATH, then 'aseprite')")
    p.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS,
                   help=f"versions kept per file (default {DEFAULT_MAX_STEPS})")


def _check_aseprite(exe: str) -> None:
    if not (shutil.which(exe) or Path(exe).expanduser().is_file()):
        log(f"warning: Aseprite not found at '{exe}'; set ASEPRITE_PATH or pass --aseprite")


def viewer(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="aseprite-live",
                                description="Watch .aseprite files being drawn, live, in the browser.",
                                epilog="Proxy mode: aseprite-live proxy --help")
    p.add_argument("folder", nargs="?", default=".", help="folder to watch (default: current)")
    _common(p)
    a = p.parse_args(argv)
    folder = Path(a.folder).expanduser()
    if not folder.exists():
        p.error(f"{folder} does not exist")
    aseprite = str(Path(a.aseprite).expanduser()) if "/" in a.aseprite else a.aseprite
    _check_aseprite(aseprite)

    app = App([folder], aseprite=aseprite, max_steps=a.max_steps)
    server = make_server(app, a.port)
    app.start()
    print(f"aseprite-live: watching {folder.resolve()}", flush=True)
    print(f"aseprite-live: open {url(server)}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
    return 0


def proxy(argv: list[str]) -> int:
    from .proxy import CallLog, run_proxy

    p = argparse.ArgumentParser(
        prog="aseprite-live proxy",
        description="Run as a stdio MCP server in front of the real one, forward every "
                    "message unchanged, and label each timeline step with its tool call.",
        usage="aseprite-live proxy [options] -- <command that starts the MCP server>")
    p.add_argument("--watch", action="append", default=[],
                   help="folder to watch (repeatable; default: current folder). Folders of "
                        "sprite paths seen in tool calls are added automatically.")
    _common(p)
    if "--" in argv:
        i = argv.index("--")
        own, cmd = argv[:i], argv[i + 1:]
    else:
        own, cmd = argv, []
    a = p.parse_args(own)
    if not cmd:
        p.error("give the MCP server command after --")
    aseprite = str(Path(a.aseprite).expanduser()) if "/" in a.aseprite else a.aseprite

    # stdout belongs to MCP from here on: everything else goes to stderr.
    app_ref: list[App] = []
    calls = CallLog(on_path=lambda path: app_ref and app_ref[0].poller.add_root(Path(path).parent),
                    on_result=lambda: app_ref and app_ref[0].poller.poke())
    app = App(a.watch or ["."], aseprite=aseprite, max_steps=a.max_steps,
              labeler=calls.labels_for)
    app_ref.append(app)
    try:
        server = make_server(app, a.port)
    except OSError as e:
        log(f"viewer disabled, cannot bind a port: {e}")
    else:
        serve_in_background(server)
        app.start()
        log(f"open {url(server)}")
    # Turn SIGTERM from the MCP client into an exception, so the server is stopped too.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        return run_proxy(cmd, calls)
    except KeyboardInterrupt:
        return 130
    except FileNotFoundError:
        log(f"cannot start MCP server: {cmd[0]} not found")
        return 127
    finally:
        app.stop()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["proxy"]:
        return proxy(argv[1:])
    return viewer(argv)
