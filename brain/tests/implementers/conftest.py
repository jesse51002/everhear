"""Every implementer test runs with HOME a temp dir and no VFG_* setting from outside, and the log
quiet: none can reach ~/.vfg, ~/.claude or the real vfg. Each test's actions catalogs are dropped
after it. It leans on nothing in tests/conftest.py, which is the brain's."""
import os

import pytest

from brain.implementers import actions
from brain.implementers.config import set_quiet

set_quiet(True)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in list(os.environ):
        if key.startswith("VFG_"):
            monkeypatch.delenv(key)
    yield
    actions.forget()
