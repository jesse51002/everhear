"""python -m vfg_engine.ctl [--socket PATH] [--timeout S] -- one control request to the running
engine, on the box (the game engine spec, THE CONTROL OPS).

It reads one request as JSON on stdin, ``{"op": "status", "args": {}}``, sends it to the engine's
control socket (``VFG_ENGINE_SOCKET``, by default /opt/vfg/engine/engine.sock; server.py has the
protocol), and prints the reply as one line of JSON on stdout. Exit codes:

    0  the engine did it (``ok`` true)
    2  refused (``ok`` false): by the engine, or the request on stdin was not one
    3  the engine is not running: no socket, nobody listening, or it stopped before it answered
    4  timeout: the engine did not answer in time

Whatever happens, stdout is one JSON reply with ``ok``, and ``error`` when it is false; a reply
ctl makes itself says ``reason`` (``bad_request``, ``not_running``, ``timeout``), as the socket's
own do. Run it with the engine venv's Python:

    echo '{"op": "status"}' | PYTHONPATH=/opt/vfg/engine /opt/vfg/engine/.venv/bin/python -m vfg_engine.ctl

Standard library only; it imports nothing of the engine but the socket's constants.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys

from .server import DEFAULT_SOCKET, MAX_TIMEOUT_S, default_timeout, one_line

EXIT_OK, EXIT_REFUSED, EXIT_NOT_RUNNING, EXIT_TIMEOUT = 0, 2, 3, 4
# ctl's own wait runs this much past the engine's, so the engine's answer that it did not answer in
# time is what comes back, not ctl's.
MARGIN_S = 2.0
MAX_REPLY = 64 << 20


class NotRunning(Exception):
    pass


class Timeout(Exception):
    pass


def socket_path(given: str | None = None) -> str:
    return given or os.environ.get("VFG_ENGINE_SOCKET") or DEFAULT_SOCKET


def request(path: str, req: dict, wait: float) -> dict:
    """Send one request and read its reply. NotRunning when there is no engine to ask, Timeout
    when no reply came within ``wait`` s."""
    line = (json.dumps(req, separators=(",", ":")) + "\n").encode("utf-8")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(wait)
        try:
            sock.connect(path)
        except FileNotFoundError:
            raise NotRunning("the engine is not running: there is no socket at %s" % path) from None
        except ConnectionRefusedError:
            raise NotRunning("the engine is not running: nothing listens on %s" % path) from None
        except PermissionError:
            raise NotRunning("cannot reach the engine: %s is not this user's (run ctl as the engine's"
                             " user, ec2-user on the box)" % path) from None
        except socket.timeout:
            raise Timeout("the engine's socket %s did not take the connection in %g s" % (path, wait)) from None
        except OSError as exc:
            raise NotRunning("cannot reach the engine at %s: %s" % (path, one_line(exc))) from None
        chunks, size = [], 0
        try:
            sock.sendall(line)
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if chunk.endswith(b"\n") or size > MAX_REPLY:
                    break
        except socket.timeout:
            raise Timeout("the engine did not answer %s within %g s" % (req.get("op"), wait)) from None
        except OSError as exc:
            raise NotRunning("the engine's socket failed: %s" % one_line(exc)) from None
    finally:
        sock.close()
    raw = b"".join(chunks).split(b"\n", 1)[0]
    if not raw.strip():
        raise NotRunning("the engine closed the connection without a reply (it may have stopped)")
    try:
        reply = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        return {"ok": False, "error": "the engine's reply is not JSON: %s" % one_line(exc), "reason": "failed"}
    if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
        return {"ok": False, "error": "the engine's reply has no ok", "reason": "failed"}
    return reply


def exit_code(reply: dict) -> int:
    if reply.get("ok") is True:
        return EXIT_OK
    reason = reply.get("reason")
    if reason == "timeout":
        return EXIT_TIMEOUT
    if reason in ("stopping", "not_running"):
        return EXIT_NOT_RUNNING
    return EXIT_REFUSED


def read_request(text: str) -> dict:
    """The request on stdin; ValueError says what is wrong with it."""
    try:
        req = json.loads(text)
    except ValueError as exc:
        raise ValueError("stdin is not JSON: %s" % one_line(exc)) from None
    if not isinstance(req, dict):
        raise ValueError('stdin must be one JSON object, {"op": ..., "args": {...}}')
    if not isinstance(req.get("op"), str) or not req["op"].strip():
        raise ValueError('op: must be the name of a control op, such as "status"')
    if req.get("args") is not None and not isinstance(req["args"], dict):
        raise ValueError("args: must be a JSON object")
    return req


def main(argv=None, stdin=None, stdout=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vfg_engine.ctl", description=__doc__.split("\n")[0])
    parser.add_argument("--socket", help="the engine's control socket (default VFG_ENGINE_SOCKET, else %s)"
                                         % DEFAULT_SOCKET)
    parser.add_argument("--timeout", type=float,
                        help="seconds the engine may take (default 10, reload 120; at most %g)" % MAX_TIMEOUT_S)
    args = parser.parse_args(argv)
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout

    def out(reply):
        stdout.write(json.dumps(reply) + "\n")
        stdout.flush()
        return exit_code(reply)

    try:
        req = read_request(stdin.read())
    except ValueError as exc:
        return out({"ok": False, "error": str(exc), "reason": "bad_request"})
    if args.timeout is not None:
        if not 0 < args.timeout <= MAX_TIMEOUT_S:
            return out({"ok": False, "error": "--timeout: must be over 0 and at most %g s" % MAX_TIMEOUT_S,
                        "reason": "bad_request"})
        req["timeout_s"] = args.timeout
    engine_wait = req.get("timeout_s")
    if not isinstance(engine_wait, (int, float)) or isinstance(engine_wait, bool):
        engine_wait = default_timeout(req["op"].strip())
    try:
        reply = request(socket_path(args.socket), req, min(float(engine_wait), MAX_TIMEOUT_S) + MARGIN_S)
    except NotRunning as exc:
        return out({"ok": False, "error": str(exc), "reason": "not_running"})
    except Timeout as exc:
        return out({"ok": False, "error": str(exc), "reason": "timeout"})
    return out(reply)


if __name__ == "__main__":
    sys.exit(main())
