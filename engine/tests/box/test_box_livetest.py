"""python -m vfg_engine.livetest without a Minecraft server: it refuses a server with no test-server
marker (and the engine under VFG_TEST=1 does too), a test file it cannot run, an engine that does not
start; and runs a whole test (the real engine on FakeIo, a fake RCON) to one JSON result, PASS, FAIL,
a test that raises, and one stopped by --timeout. On a real server: test_box_livetest_live.py."""

from __future__ import annotations

import io
import json
import os
import re
import socket
import subprocess
import sys

import pytest

from vfg_engine import livetest

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeRcon:
    """The test server, as far as the stage needs: Carpet players come online, mobs are summoned
    with full health, everything else is answered as Minecraft does."""

    def __init__(self):
        self.sent = []
        self.online = []

    def command(self, cmd):
        self.sent.append(cmd)
        m = re.match(r"player (\w+) spawn", cmd)
        if m:
            self.online.append(m.group(1))
            return ""
        if cmd == "list":
            return "There are %d of a max of 20 players online: %s" % (len(self.online), ", ".join(self.online))
        if cmd.startswith("summon"):
            return "Summoned new Husk"
        if cmd.startswith("data get entity") and cmd.endswith(" Health"):
            return "x has the following entity data: 20.0f"
        if cmd.startswith("data get entity") and cmd.endswith(" Pos"):
            return "x has the following entity data: [3000.5d, 296.0d, 3000.5d]"
        if cmd.startswith("data get entity"):
            return "x has the following entity data: 0s"
        if cmd.startswith("execute if entity"):
            return "Test failed"
        return ""

    def close(self):
        pass


def server_dir(tmp, marker=True, port="25576"):
    mc = os.path.join(tmp, "mc-test")
    os.makedirs(mc, exist_ok=True)
    with open(os.path.join(mc, "server.properties"), "w") as f:
        f.write("rcon.port=%s\nrcon.password=x\n" % port)
    if marker:
        with open(os.path.join(mc, livetest.MARKER), "w") as f:
            json.dump({"rcon_port": int(port)} if marker is True else marker, f)
    return os.path.join(mc, "server.properties")


def write(path, text):
    with open(path, "w") as f:
        f.write(text)
    return path


def runner(env, *argv, timeout=60):
    proc = subprocess.run([sys.executable, "-m", "vfg_engine.livetest", *argv], env=env, capture_output=True,
                          text=True, timeout=timeout)
    lines = proc.stdout.strip().splitlines()
    return proc.returncode, json.loads(lines[-1]), proc.stdout


def args(props, state, test, *more):
    return ["--properties", props, "--state-dir", state, "--content", os.path.join(ENGINE_DIR, "examples"),
            "--test", test, *more]


# ---------------------------------------------------------------- refused before anything starts

def test_no_marker_is_refused(sub_env, short_tmp):
    props = server_dir(short_tmp, marker=False)
    test = write(os.path.join(short_tmp, "t.py"), "def test(t):\n    t.check('x', True)\n")
    code, result, out = runner(sub_env, *args(props, os.path.join(short_tmp, "state"), test))
    assert (code, result["code"]) == (3, 3)
    assert "has no .vfg-test-server: it is not a test server" in result["error"]
    assert not os.path.exists(os.path.join(short_tmp, "state"))   # nothing was started


def test_a_marker_for_another_port_is_refused(sub_env, short_tmp):
    props = server_dir(short_tmp, marker={"rcon_port": 25575}, port="25576")
    test = write(os.path.join(short_tmp, "t.py"), "def test(t):\n    pass\n")
    code, result, _ = runner(sub_env, *args(props, os.path.join(short_tmp, "state"), test))
    assert code == 3 and "names RCON port 25575, but server.properties has 25576" in result["error"]


def test_the_engine_under_vfg_test_refuses_a_server_without_the_marker(sub_env, short_tmp):
    props = server_dir(short_tmp, marker=False)
    env = dict(sub_env, VFG_TEST="1", VFG_MC_PROPERTIES=props, VFG_ENGINE_STATE=os.path.join(short_tmp, "s.json"))
    proc = subprocess.run([sys.executable, "-m", "vfg_engine"], env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 78
    assert "VFG_TEST=1: " in proc.stderr and "it is not a test server" in proc.stderr
    with open(os.path.join(short_tmp, livetest.MARKER), "w"):   # not beside server.properties: still refused
        pass
    assert subprocess.run([sys.executable, "-m", "vfg_engine"], env=env, capture_output=True,
                          timeout=60).returncode == 78


@pytest.mark.parametrize("text, words", [
    ("def tset(t):\n    pass\n", "has no test(t)"),
    ("def test(t)\n    pass\n", "does not load: SyntaxError"),
    ("import no_such_module_here\ndef test(t):\n    pass\n", "does not load: ModuleNotFoundError"),
])
def test_a_test_file_that_cannot_run_is_2_and_starts_nothing(sub_env, short_tmp, text, words):
    props = server_dir(short_tmp)
    test = write(os.path.join(short_tmp, "t.py"), text)
    code, result, _ = runner(sub_env, *args(props, os.path.join(short_tmp, "state"), test))
    assert (code, result["code"]) == (2, 2)
    assert words in result["error"]
    assert result["engine"]["exit"] is None   # no engine was started


def test_an_engine_that_does_not_start_is_4_with_its_last_words(sub_env, short_tmp):
    with socket.socket() as s:   # a port nothing listens on: RCON refused
        s.bind(("127.0.0.1", 0))
        port = str(s.getsockname()[1])
    props = server_dir(short_tmp, port=port)
    test = write(os.path.join(short_tmp, "t.py"), "def test(t):\n    t.check('x', True)\n")
    code, result, out = runner(sub_env, *args(props, os.path.join(short_tmp, "state"), test, "--boot", "20"))
    assert (code, result["code"]) == (4, 4)
    assert "the engine exited with 1 before it answered" in result["error"]
    assert "ENGINE" in out and "could not start" in out


# ---------------------------------------------------------------- a whole run, the engine on FakeIo

def run_inproc(short_tmp, text, *more):
    props = server_dir(short_tmp)
    test = write(os.path.join(short_tmp, "t.py"), text)
    out = io.StringIO()
    code = livetest.main(args(props, os.path.join(short_tmp, "state"), test, *more), out=out,
                         rcon_factory=FakeRcon, fake_io=True)
    lines = out.getvalue().strip().splitlines()
    return code, json.loads(lines[-1]), out.getvalue()


def test_a_passing_test(short_tmp, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", ENGINE_DIR)
    code, result, out = run_inproc(short_tmp, (
        "def test(t):\n"
        "    t.check('the engine has the examples', 'fireball' in [c['id'] for c in t.op('classes')['classes']])\n"
        "    t.check('Steve is not hurt', t.hurt('Steve', at_least=1).__class__(not t.hurt('Steve')[0], 'fine'))\n"))
    assert (code, result["code"], result["passed"], result["failed"]) == (0, 0, 2, 0)
    assert sorted(result["engine"]["loaded"]) == ["fire_staff", "fireball", "giant", "shrunk"]
    assert result["engine"]["exit"] == 0   # stopped cleanly
    assert "AT         Steve: the caster" in out and "RESULT: 2 passed, 0 failed, 0 can't tell" in out
    assert not os.path.exists(os.path.join(short_tmp, "state", "engine.sock"))


def test_a_failing_check_is_1_and_a_test_with_no_check_is_2(short_tmp, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", ENGINE_DIR)
    code, result, out = run_inproc(short_tmp, "def test(t):\n    t.check('a', True)\n    t.check('b', False)\n")
    assert (code, result["passed"], result["failed"]) == (1, 1, 1)
    assert "FAIL       b — the test's own False" in out
    code, result, _ = run_inproc(short_tmp, "def test(t):\n    pass\n")
    assert (code, result["error"]) == (2, "the test made no check")


def test_a_refused_op_and_a_raising_test_are_2(short_tmp, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", ENGINE_DIR)
    code, result, out = run_inproc(short_tmp, "def test(t):\n    t.check('a', True)\n    t.give('no_such_item')\n")
    assert code == 2 and "give no_such_item to Steve: the engine refused it" in result["error"]
    assert result["passed"] == 1   # what it checked before is still reported
    code, result, out = run_inproc(short_tmp, "def test(t):\n    t.check('a', True)\n    1 / 0\n")
    assert code == 2 and result["error"].startswith("ZeroDivisionError")
    assert "Traceback" in out
    code, result, _ = run_inproc(short_tmp, "def test(t):\n    t.cast('fire ball')\n")
    assert code == 2 and "no incantation 'fire ball' in the engine: t.bind" in result["error"]
    code, result, _ = run_inproc(short_tmp, "def test(t):\n    t.hurt()\n")
    assert code == 2 and result["error"].startswith("t.hurt(who, at_least=1): missing a required argument")


def test_timeout_stops_it_with_5_and_the_engine_is_stopped(short_tmp, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", ENGINE_DIR)
    code, result, out = run_inproc(short_tmp, "def test(t):\n    t.check('a', True)\n    t.wait(60)\n",
                                   "--timeout", "3")
    assert (code, result["stopped"]) == (5, True)
    assert result["error"] == "timed out after 3 s (--timeout)"
    assert result["checks"] == [{"item": "a", "ok": True, "why": "the test's own True (verdict(ok, why) would say why)"}]
    assert result["engine"]["exit"] == 0


def test_the_stage_is_cleared_after(short_tmp, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", ENGINE_DIR)
    seen = []

    def rcon():
        r = FakeRcon()
        seen.append(r)
        return r
    props = server_dir(short_tmp)
    test = write(os.path.join(short_tmp, "t.py"), "def test(t):\n    t.mob('husk', 0, 0, 5)\n    t.check('a', True)\n")
    livetest.main(args(props, os.path.join(short_tmp, "state"), test), out=io.StringIO(), rcon_factory=rcon,
                  fake_io=True)
    sent = seen[0].sent
    assert sent[0].startswith("forceload add")
    assert "player Steve kill" in sent and "player Alex kill" in sent and "kill @e[tag=vfg_test]" in sent
    assert sent[-1].startswith("forceload remove")
    assert any(c.startswith("fill") and c.endswith(" air") for c in sent)


def test_verdicts_and_the_stage_geometry():
    t = livetest.T(FakeRcon(), None, see_players=False, out=io.StringIO())
    assert t.xyz(0, 0, 10) == (3000.5, 296, 2990.5)            # forward is north, -z
    assert t.at(2, 1, 3) == "3002.5 297 2997.5"
    with pytest.raises(livetest.TestError, match="forward is the way he faces"):
        t.mob("husk", 0, 0, -3)
    with pytest.raises(livetest.TestError, match="refused: a test only watches"):
        t.cmd("effect give Steve speed")
    with pytest.raises(livetest.TestError, match="t has no hurl"):
        t.hurl("x")
    v = livetest.verdict(None, "can't say")
    assert not v and v[0] is None
    t.check("x", v)
    t.check("y", True)
    assert t._report() == (1, 0, 1)
