"""The control socket (server.py) on a fake engine: one JSON line in, one out, a reply whatever goes
wrong, the timeouts and the queue, the stop, the socket file."""

from __future__ import annotations

import json
import logging
import os
import socket
import stat
import threading
import time

import pytest

from vfg_engine import server as srv

from box_fakes import FakeEngine, wait_for


def ask(path, line, timeout=10.0):
    """Send raw bytes, read the one reply line; and whether the server closed after it."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall(line)
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        closed = s.recv(1) == b""
    return json.loads(data), closed


def request(path, doc, timeout=10.0):
    return ask(path, (json.dumps(doc) + "\n").encode(), timeout)[0]


@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture
def ticking(engine):
    """The fake engine's loop on a thread (the real one runs on the main thread)."""
    stop = threading.Event()
    t = threading.Thread(target=engine.run, args=(stop,), daemon=True)
    t.start()
    yield engine
    stop.set()
    t.join(5)


@pytest.fixture
def server(short_tmp, engine):
    s = srv.ControlServer(os.path.join(short_tmp, "engine.sock"), engine)
    s.start()
    yield s
    s.close()


def test_a_request_and_its_reply(server, ticking):
    reply, closed = ask(server.path, b'{"op": "status", "args": {}}\n')
    assert reply["ok"] is True and reply["tick"] >= 1
    assert closed  # one request a connection
    assert request(server.path, {"op": "echo", "args": {"player": "Steve"}}) == {
        "ok": True, "args": {"player": "Steve"}}
    assert request(server.path, {"op": "echo"}) == {"ok": True, "args": {}}  # no args is {}
    assert ticking.handled == ["status", "echo", "echo"]


def test_the_socket_is_mode_600(server):
    assert stat.S_IMODE(os.stat(server.path).st_mode) == 0o600
    assert stat.S_ISSOCK(os.stat(server.path).st_mode)


@pytest.mark.parametrize("line, words", [
    (b"not json\n", "not JSON"),
    (b"[1, 2]\n", "must be a JSON object"),
    (b'{"args": {}}\n', "op: "),
    (b'{"op": ""}\n', "op: "),
    (b'{"op": "status", "args": [1]}\n', "args: "),
    (b'{"op": "status", "timeout_s": "soon"}\n', "timeout_s: "),
    (b"\n", "no request"),
    (b"\xff\xfe\n", "not JSON"),
])
def test_a_bad_request_still_gets_a_reply(server, engine, line, words):
    reply, _ = ask(server.path, line)
    assert reply["ok"] is False and reply["reason"] == "bad_request"
    assert words in reply["error"]
    assert engine.inbox.empty()  # never reached the engine


def test_a_request_over_1_mib(server):
    reply, _ = ask(server.path, b'{"op": "echo", "args": {"x": "' + b"a" * (1 << 20) + b'"}}\n')
    assert reply["reason"] == "bad_request" and "longer than" in reply["error"]


def test_no_line_in_time(server, monkeypatch):
    monkeypatch.setattr(srv, "READ_TIMEOUT_S", 0.2)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(server.path)
        reply = json.loads(s.makefile().readline())
    assert reply["reason"] == "bad_request" and "no request line came within 0.2 s" in reply["error"]


def test_the_engines_refusal_is_passed_on(server, ticking):
    assert request(server.path, {"op": "refuse"}) == {"ok": False, "error": "refused"}


@pytest.mark.parametrize("op, words", [
    ("boom", "the engine failed on boom: the handler broke"),
    ("shape", "the engine answered shape with no reply (list)"),
    ("raise_submit", "the engine would not take raise_submit: the engine is closed"),
])
def test_an_engine_failure_is_a_reply(server, ticking, op, words, caplog):
    with caplog.at_level(logging.ERROR, logger="vfg_engine.server"):
        reply = request(server.path, {"op": op})
    assert reply == {"ok": False, "error": words, "reason": "failed"}
    assert caplog.records  # logged, with the traceback where there is one


def test_a_value_json_cannot_hold_goes_as_text(server, ticking):
    reply = request(server.path, {"op": "odd"})
    assert reply["ok"] is True and reply["when"] in ("{1, 2}", "{2, 1}")


def test_a_timeout_before_the_engine_took_it_is_cancelled(server, engine):
    # the engine is not ticking: the request waits in its queue
    start = time.monotonic()
    reply = request(server.path, {"op": "echo", "timeout_s": 0.2})
    assert 0.2 <= time.monotonic() - start < 2
    assert reply["reason"] == "timeout" and reply["cancelled"] is True
    assert "taken off the queue and will not be carried out" in reply["error"]
    engine.tick()
    assert engine.handled == []  # never carried out


def test_a_timeout_after_the_engine_took_it(server, ticking):
    reply = request(server.path, {"op": "never", "timeout_s": 0.2})
    assert reply["reason"] == "timeout" and reply["cancelled"] is False
    assert "may still be carried out" in reply["error"]


def test_the_default_timeouts(monkeypatch):
    assert srv.parse(b'{"op": "status"}')[2] == 10.0
    assert srv.parse(b'{"op": "reload"}')[2] == 120.0
    assert srv.parse(b'{"op": "start", "args": {"scenario": "meteor", "wait_s": 20}}')[2] == 25.0
    assert srv.parse(b'{"op": "start", "args": {"scenario": "meteor", "wait_s": 2}}')[2] == 10.0
    assert srv.parse(b'{"op": "start", "args": {"wait_s": "soon"}}')[2] == 10.0
    assert srv.parse(b'{"op": "start", "args": {"wait_s": 20}, "timeout_s": 3}')[2] == 3.0   # the request's own wins
    assert srv.parse(b'{"op": "status", "timeout_s": 9999}')[2] == 600.0
    assert srv.parse(b'{"op": "status", "timeout_s": 0}')[2] == srv.MIN_TIMEOUT_S


def test_close_answers_what_waits_and_removes_the_socket(short_tmp, engine):
    s = srv.ControlServer(os.path.join(short_tmp, "engine.sock"), engine)
    s.start()
    got = []
    t = threading.Thread(target=lambda: got.append(request(s.path, {"op": "echo", "timeout_s": 30})))
    t.start()
    assert wait_for(lambda: not engine.inbox.empty())
    start = time.monotonic()
    s.close()
    t.join(5)
    assert time.monotonic() - start < 2
    assert got[0]["reason"] == "stopping" and got[0]["cancelled"] is True
    assert not os.path.exists(s.path)
    with pytest.raises(OSError):
        request(s.path, {"op": "status"})


def test_a_stale_socket_is_replaced_and_anything_else_refused(short_tmp, engine):
    path = os.path.join(short_tmp, "engine.sock")
    old = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old.bind(path)  # a crashed engine's
    old.close()
    s = srv.ControlServer(path, engine)
    s.close()
    with open(path, "w") as f:
        f.write("not a socket")
    with pytest.raises(srv.SetupError, match="is there and is not a socket"):
        srv.ControlServer(path, engine)
    with pytest.raises(srv.SetupError, match="at most 107"):
        srv.ControlServer("/tmp/" + "x" * 120 + ".sock", engine)


def test_close_leaves_a_socket_another_server_made(short_tmp, engine):
    path = os.path.join(short_tmp, "engine.sock")
    first = srv.ControlServer(path, engine)
    second = srv.ControlServer(path, engine)  # replaced the first one's file
    first.close()
    assert os.path.exists(path)
    second.close()
    assert not os.path.exists(path)


def test_each_request_is_logged_with_its_op_and_time(server, ticking, caplog):
    with caplog.at_level(logging.INFO, logger="vfg_engine.server"):
        request(server.path, {"op": "status"})
        request(server.path, {"op": "refuse"})
    lines = [r for r in caplog.records if getattr(r, "op", None)]
    assert [(r.op, r.ok) for r in lines] == [("status", True), ("refuse", False)]
    assert lines[1].error == "refused" and isinstance(lines[0].ms, float)


def test_requests_wait_in_parallel(server, ticking):
    replies = []
    threads = [threading.Thread(target=lambda: replies.append(
        request(server.path, {"op": "sleep", "args": {"s": 0.3}}))) for _ in range(5)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert time.monotonic() - start < 1.0
    assert replies == [{"ok": True}] * 5
