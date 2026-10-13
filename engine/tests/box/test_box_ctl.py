"""python -m vfg_engine.ctl as the box runs it: one request on stdin, the reply on stdout, and its
exit codes (0 ok, 2 refused, 3 not running, 4 timeout), against a control socket on a fake engine."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading

import pytest

from vfg_engine import server as srv

from box_fakes import FakeEngine


def ctl(env, stdin, *args, timeout=30):
    proc = subprocess.run([os.sys.executable, "-m", "vfg_engine.ctl", *args], input=stdin, env=env,
                          capture_output=True, text=True, timeout=timeout)
    lines = proc.stdout.splitlines()
    assert len(lines) == 1, (proc.stdout, proc.stderr)
    return proc.returncode, json.loads(lines[0])


@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture
def running(sub_env, engine):
    """A control socket at the env's VFG_ENGINE_SOCKET, on a ticking fake engine."""
    s = srv.ControlServer(sub_env["VFG_ENGINE_SOCKET"], engine)
    s.start()
    stop = threading.Event()
    t = threading.Thread(target=engine.run, args=(stop,), daemon=True)
    t.start()
    yield s
    stop.set()
    t.join(5)
    s.close()


def test_ok_is_0(sub_env, running):
    code, reply = ctl(sub_env, '{"op": "status"}')
    assert code == 0 and reply["ok"] is True and "tick" in reply


def test_a_pretty_request_on_stdin(sub_env, running):
    code, reply = ctl(sub_env, json.dumps({"op": "echo", "args": {"player": "Steve", "item": "fire_staff"}}, indent=2))
    assert (code, reply) == (0, {"ok": True, "args": {"player": "Steve", "item": "fire_staff"}})


def test_refused_is_2(sub_env, running):
    assert ctl(sub_env, '{"op": "refuse"}') == (2, {"ok": False, "error": "refused"})
    code, reply = ctl(sub_env, '{"op": "boom"}')
    assert (code, reply["reason"]) == (2, "failed")


@pytest.mark.parametrize("stdin, words", [("", "stdin is not JSON"), ("status", "stdin is not JSON"),
                                          ("[]", "one JSON object"), ('{"args": {}}', "op: "),
                                          ('{"op": "x", "args": 1}', "args: ")])
def test_a_bad_request_on_stdin_is_2_and_never_sent(sub_env, running, engine, stdin, words):
    code, reply = ctl(sub_env, stdin)
    assert (code, reply["reason"]) == (2, "bad_request")
    assert words in reply["error"]
    assert engine.handled == []


def test_not_running_is_3(sub_env, short_tmp):
    code, reply = ctl(sub_env, '{"op": "status"}')
    assert (code, reply["reason"]) == (3, "not_running")
    assert "there is no socket at %s" % sub_env["VFG_ENGINE_SOCKET"] in reply["error"]
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(sub_env["VFG_ENGINE_SOCKET"])  # left by an engine that died: nobody listens
    try:
        code, reply = ctl(sub_env, '{"op": "status"}')
    finally:
        dead.close()
    assert code == 3 and "nothing listens on" in reply["error"]


def test_the_engine_stopping_is_3(sub_env, engine):
    s = srv.ControlServer(sub_env["VFG_ENGINE_SOCKET"], engine)
    s.start()
    s.closing.set()  # as close() begins
    try:
        code, reply = ctl(sub_env, '{"op": "status"}')
    finally:
        s.close()
    assert (code, reply["reason"]) == (3, "stopping")


def test_the_engines_timeout_is_4(sub_env, running):
    code, reply = ctl(sub_env, '{"op": "never"}', "--timeout", "0.3")
    assert (code, reply["reason"]) == (4, "timeout")
    code, reply = ctl(sub_env, '{"op": "never", "timeout_s": 0.3}')
    assert code == 4


def test_ctls_own_timeout_is_4(sub_env, monkeypatch):
    """A socket that takes the request and never answers: ctl gives up at the engine's time plus 2 s."""
    hung = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    hung.bind(sub_env["VFG_ENGINE_SOCKET"])
    hung.listen(1)
    try:
        code, reply = ctl(sub_env, '{"op": "status"}', "--timeout", "0.2")
    finally:
        hung.close()
    assert (code, reply["reason"]) == (4, "timeout")
    assert "did not answer status within 2.2 s" in reply["error"]


def test_the_socket_flag_and_a_bad_timeout(sub_env, running):
    env = dict(sub_env, VFG_ENGINE_SOCKET="/nowhere/engine.sock")
    assert ctl(env, '{"op": "status"}')[0] == 3
    assert ctl(env, '{"op": "status"}', "--socket", sub_env["VFG_ENGINE_SOCKET"])[0] == 0
    code, reply = ctl(sub_env, '{"op": "status"}', "--timeout", "0")
    assert (code, reply["reason"]) == (2, "bad_request")
