"""The engine as a process (python -m vfg_engine's main), on a fake engine in a subprocess: it serves
the socket, logs JSON lines, stops on SIGTERM and SIGINT with its state saved, and exits 0, 1 or 78
as the unit expects; one engine at a time."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import threading

import pytest

from box_fakes import FakeEngine, wait_for

MAIN = "import sys, box_fakes; from vfg_engine.__main__ import main; sys.exit(main(sys.argv[1:], make=box_fakes.make))"


def start(env, *args):
    proc = subprocess.Popen([sys.executable, "-c", MAIN, *args], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    return proc


def ctl(env, stdin):
    proc = subprocess.run([sys.executable, "-m", "vfg_engine.ctl"], input=stdin, env=env,
                          capture_output=True, text=True, timeout=30)
    return proc.returncode, json.loads(proc.stdout)


def finish(proc, sig=None, timeout=15):
    if sig is not None:
        proc.send_signal(sig)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        pytest.fail("the engine did not stop: %s" % err)
    return proc.returncode, err


def log_lines(env):
    with open(env["VFG_ENGINE_LOG"], encoding="utf-8") as f:
        return [json.loads(line) for line in f]


@pytest.fixture
def state(short_tmp, sub_env):
    path = os.path.join(short_tmp, "state.json")
    sub_env["FAKE_ENGINE_STATE"] = path
    return path


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_serves_then_stops_on_a_signal_with_its_state_saved(sub_env, state, sig):
    proc = start(sub_env)
    sock = sub_env["VFG_ENGINE_SOCKET"]
    assert wait_for(lambda: ctl(sub_env, '{"op": "status"}')[0] == 0 or proc.poll() is not None)
    code, reply = ctl(sub_env, '{"op": "status"}')
    assert code == 0 and reply["pid"] == proc.pid  # answered on the engine's own loop
    assert stat.S_IMODE(os.stat(sock).st_mode) == 0o600
    code, err = finish(proc, sig)
    assert code == 0, err
    assert open(state).read() == "saved\n"
    assert not os.path.exists(sock)
    lines = log_lines(sub_env)
    assert all({"at", "level", "logger", "msg"} <= set(line) for line in lines)
    msgs = [line["msg"] for line in lines]
    assert any(m.startswith("the engine is running") for m in msgs)
    assert any(m.startswith("signal SIG") for m in msgs)
    assert msgs[-1] == "the engine stopped; its state is saved"
    control = [line for line in lines if line.get("op") == "status"]
    assert control and control[0]["ok"] is True and "ms" in control[0]
    assert "the engine is running" in err  # and the journal's plain lines on stderr
    assert ctl(sub_env, '{"op": "status"}')[0] == 3


def test_one_engine_at_a_time(sub_env, state):
    first = start(sub_env)
    assert wait_for(lambda: os.path.exists(sub_env["VFG_ENGINE_SOCKET"]) or first.poll() is not None)
    second = start(sub_env)
    code, err = finish(second)
    assert code == 1
    assert "another engine is running (pid %d holds" % first.pid in err
    assert ctl(sub_env, '{"op": "status"}')[0] == 0  # the first is untouched
    assert finish(first, signal.SIGTERM)[0] == 0


def test_a_bad_setting_is_78(sub_env):
    sub_env["FAKE_ENGINE"] = "config"
    code, err = finish(start(sub_env))
    assert code == 78
    assert "VFG_ENGINE_TICK_HZ is not a number" in err


@pytest.mark.parametrize("key, value, words", [
    ("VFG_ENGINE_LOG", "engine.log", "VFG_ENGINE_LOG must be an absolute path"),
    ("VFG_ENGINE_LOG", "/proc/engine/engine.log", "cannot be opened"),
    ("VFG_ENGINE_SOCKET", "/tmp/" + "s" * 110 + ".sock", "set VFG_ENGINE_SOCKET to a shorter one"),
    ("VFG_ENGINE_SOCKET", "/proc/engine/engine.sock", "the engine's lock /proc/engine/engine.sock.lock cannot be made"),
])
def test_a_place_that_cannot_be_used_is_78(sub_env, key, value, words):
    sub_env[key] = value
    code, err = finish(start(sub_env))
    assert code == 78
    assert words in err


def test_an_engine_that_cannot_start_is_1_and_leaves_its_state_alone(sub_env, state):
    with open(state, "w") as f:
        f.write("the last good state\n")
    sub_env["FAKE_ENGINE"] = "start"
    code, err = finish(start(sub_env))
    assert code == 1
    assert "the engine could not start; its saved state is left as it was" in err and "RCON refused" in err
    assert open(state).read() == "the last good state\n"  # close(), which saves, is not called
    assert not os.path.exists(sub_env["VFG_ENGINE_SOCKET"])


def test_a_failed_start_closes_the_io_only():
    from vfg_engine import __main__ as entry

    class Server:
        path = "x"

        def close(self):
            pass

    engine = FakeEngine(start_error="RCON refused")
    assert entry.run(engine, Server(), threading.Event()) == 1
    assert engine.io.closed and not engine.closed


def test_an_error_out_of_the_loop_is_1_and_still_saves(sub_env, state):
    sub_env["FAKE_ENGINE"] = "run"
    code, err = finish(start(sub_env))
    assert code == 1
    assert "an error escaped the engine's loop" in err and "the loop broke" in err
    assert open(state).read() == "saved\n"
    assert any("exc" in line for line in log_lines(sub_env))  # the traceback, in the JSON line


def test_a_close_that_fails_is_1(sub_env, state):
    sub_env["FAKE_ENGINE"] = "close"
    proc = start(sub_env)
    assert wait_for(lambda: os.path.exists(sub_env["VFG_ENGINE_SOCKET"]) or proc.poll() is not None)
    code, err = finish(proc, signal.SIGTERM)
    assert code == 1 and "its state may not be saved" in err
