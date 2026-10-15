"""The real engine as a process: python -m vfg_engine --fake-io, with the core and examples/ on the
testing FakeIo. Through ctl: status, the classes loaded, a binding written to the export the bridge
reads, a cast answered; SIGTERM saves the state and a restart keeps the binding."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

import pytest

from box_fakes import wait_for

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
pytest.importorskip("vfg_engine.engine", reason="the core is not there yet")


def ctl(env, req):
    proc = subprocess.run([sys.executable, "-m", "vfg_engine.ctl"], input=json.dumps(req), env=env,
                          capture_output=True, text=True, timeout=60)
    return proc.returncode, json.loads(proc.stdout)


@pytest.fixture
def engine_env(sub_env, short_tmp):
    sub_env.update(VFG_ENGINE_STATE=os.path.join(short_tmp, "state.json"),
                   VFG_ENGINE_EXPORT=os.path.join(short_tmp, "incantations.json"),
                   VFG_ENGINE_CONTENT=os.path.join(os.path.dirname(ENGINE_DIR), "integration", "engine_e2e_content"))
    return sub_env


def start(env):
    proc = subprocess.Popen([sys.executable, "-m", "vfg_engine", "--fake-io"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if not wait_for(lambda: ctl(env, {"op": "status"})[0] == 0 or proc.poll() is not None, 30):
        proc.kill()
        pytest.fail("the engine did not start: %s" % proc.communicate()[1])
    assert proc.poll() is None, proc.communicate()[1]
    return proc


def stop(proc):
    proc.send_signal(signal.SIGTERM)
    _, err = proc.communicate(timeout=30)
    return proc.returncode, err


def test_the_engine_on_fake_io_through_ctl(engine_env):
    proc = start(engine_env)
    try:
        code, status = ctl(engine_env, {"op": "status"})
        assert code == 0 and status["tick_hz"] == 10.0 and status["classes"] == 4
        code, classes = ctl(engine_env, {"op": "classes"})
        assert {c["id"] for c in classes["classes"]} == {"e2e_fireball", "fire_staff", "giant", "shrunk"}
        code, bound = ctl(engine_env, {"op": "bind", "args": {"player": "Steve", "phrase": "Fire Ball",
                                                              "ability": "e2e_fireball"}})
        assert code == 0 and bound["id"] == "steve.fire_ball"
        with open(engine_env["VFG_ENGINE_EXPORT"]) as f:
            export = json.load(f)
        assert export["incantations"][0]["phrase"] == "fire ball"
        assert export["incantations"][0]["cooldown_s"] == 5.0  # the ability's, from its Config
        code, cast = ctl(engine_env, {"op": "cast", "args": {"player": "Steve", "phrase": "fire ball",
                                                             "cast_id": "c1"}})
        assert code == 0 and cast["status"] in ("not_granted", "fired", "failed")
        assert ctl(engine_env, {"op": "no_such_op"})[0] == 2
    finally:
        code, err = stop(proc)
    assert code == 0, err
    with open(engine_env["VFG_ENGINE_STATE"]) as f:
        assert "steve.fire_ball" in f.read()
    proc = start(engine_env)  # a restart: the binding is kept
    try:
        code, listed = ctl(engine_env, {"op": "incantations", "args": {"player": "Steve"}})
        assert [e["id"] for e in listed["incantations"]] == ["steve.fire_ball"]
    finally:
        assert stop(proc)[0] == 0
