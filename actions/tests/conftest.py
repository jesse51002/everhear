"""Tests for actions/ and scripts/_lib/places.py:

    uv run --no-project --with pytest --with pydantic pytest actions/tests

No network and no real server. places.py is pure, so it runs here against scripts/_test's
FakeWorld, which speaks Minecraft 26.3's recorded replies (scripts/_test/mc_26_3.py). The
scripts under the core actions are tested where every script is, in scripts/_test
(test_core.py, run by run_tests.py).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
TEST_LIB = REPO / "scripts" / "_test"
for p in (str(REPO), str(TEST_LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fake_places_world import FakeWorld  # noqa: E402,F401


def load_places():
    """scripts/_lib/places.py as a module, the way a script sees it."""
    spec = importlib.util.spec_from_file_location("places", REPO / "scripts" / "_lib" / "places.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
