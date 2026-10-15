"""The livetest runner on a real 26.3 server with Carpet: LIVETEST_PROPERTIES names a test server's
server.properties (a realmc server's, its folder given the .vfg-test-server marker; the datapack
installed). Skipped without it. One tiny ability (livetest_case/content/test_zap.py) and its test
(livetest_case/zap_case.py), run as vfg test runs one: every check passes, the engine stops cleanly,
the stage is cleared. And a one-shot build (test_pillar.py, pillar_case.py): what it did counts as
fired, its result is said. Each within the 20 s budget."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

CASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "livetest_case")
PROPS = os.environ.get("LIVETEST_PROPERTIES")

pytestmark = pytest.mark.skipif(not PROPS, reason="LIVETEST_PROPERTIES names no test server")


def run_case(sub_env, short_tmp, case):
    proc = subprocess.run([sys.executable, "-m", "vfg_engine.livetest", "--properties", PROPS,
                           "--state-dir", os.path.join(short_tmp, "state"),
                           "--content", os.path.join(CASE, "content"),
                           "--test", os.path.join(CASE, case)],
                          env=sub_env, capture_output=True, text=True, timeout=240)
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert sorted(result["engine"]["loaded"]) == ["test_pillar", "test_zap"] and result["engine"]["exit"] == 0
    assert result["slow"] is False, proc.stdout   # within the budget, setup included
    return result, proc.stdout


def test_a_tiny_ability_passes_on_a_real_server(sub_env, short_tmp):
    result, out = run_case(sub_env, short_tmp, "zap_case.py")
    assert (result["code"], result["passed"], result["failed"]) == (0, 6, 0)
    assert "CAST       Steve says 'zap it': fired" in out


def test_a_one_shot_build_counts_as_fired_and_says_its_result(sub_env, short_tmp):
    result, out = run_case(sub_env, short_tmp, "pillar_case.py")
    assert (result["code"], result["passed"], result["failed"], result["cant_tell"]) == (0, 4, 0, 1)
    assert [c["ok"] for c in result["checks"]] == [True, True, True, True, None]
    assert "nothing fired since the crouch" in result["checks"][-1]["why"]
