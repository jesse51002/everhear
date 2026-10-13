"""The kit on a real 26.3 server: each case in tests/kit/live/ (case_*.py, a livetest test file) run
by the livetest runner against the kit's live content (tests/kit/live/content/), one engine a case.
LIVETEST_PROPERTIES names a test server's server.properties (a realmc server's, its folder given the
.vfg-test-server marker, the datapack installed: engine/README.md, "Tests of content: livetest");
skipped without it. About 15 s a case."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "live")
ENGINE = os.path.dirname(os.path.dirname(HERE))
PROPS = os.environ.get("LIVETEST_PROPERTIES")
CASES = sorted(f[len("case_"):-3] for f in os.listdir(LIVE) if f.startswith("case_") and f.endswith(".py"))

pytestmark = pytest.mark.skipif(not PROPS, reason="LIVETEST_PROPERTIES names no test server")


@pytest.mark.parametrize("case", CASES)
def test_the_kit_live(case):
    state = tempfile.mkdtemp(prefix="gk-")
    try:
        env = dict(os.environ, PYTHONPATH=ENGINE)
        proc = subprocess.run([sys.executable, "-m", "vfg_engine.livetest", "--properties", PROPS,
                               "--state-dir", os.path.join(state, "state"),
                               "--content", os.path.join(LIVE, "content"),
                               "--test", os.path.join(LIVE, "case_%s.py" % case)],
                              env=env, capture_output=True, text=True, timeout=300)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        report = "\n".join(line for line in proc.stdout.splitlines() if not line.startswith("{"))
        assert result["code"] == 0 and result["failed"] == 0, report + proc.stderr[-2000:]
        assert result["cant_tell"] == 0, report
        assert result["engine"]["errors"] == [] and result["engine"]["exit"] == 0, report
    finally:
        shutil.rmtree(state, True)
