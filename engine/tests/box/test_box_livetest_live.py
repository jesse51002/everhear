"""The livetest runner on a real 26.3 server with Carpet: LIVETEST_PROPERTIES names a test server's
server.properties (a realmc server's, its folder given the .vfg-test-server marker; the datapack
installed). Skipped without it. One tiny ability (livetest_case/content/test_zap.py) and its test
(livetest_case/zap_case.py), run as vfg test runs one: every check passes, the engine stops cleanly,
the stage is cleared."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

CASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "livetest_case")
PROPS = os.environ.get("LIVETEST_PROPERTIES")

pytestmark = pytest.mark.skipif(not PROPS, reason="LIVETEST_PROPERTIES names no test server")


def test_a_tiny_ability_passes_on_a_real_server(sub_env, short_tmp):
    proc = subprocess.run([sys.executable, "-m", "vfg_engine.livetest", "--properties", PROPS,
                           "--state-dir", os.path.join(short_tmp, "state"),
                           "--content", os.path.join(CASE, "content"),
                           "--test", os.path.join(CASE, "zap_case.py")],
                          env=sub_env, capture_output=True, text=True, timeout=240)
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (result["code"], result["passed"], result["failed"]) == (0, 6, 0)
    assert result["engine"]["loaded"] == ["test_zap"] and result["engine"]["exit"] == 0
    assert "CAST       Steve says 'zap it': fired" in proc.stdout
