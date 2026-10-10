"""Every test runs with HOME a temp dir and the fake vfg first on the PATH, and gets the
environment back as it was: no test can reach ~/.vfg, ~/.claude or the real vfg."""
import os
import shutil
import tempfile

import pytest

import helpers

_HOME = tempfile.mkdtemp(prefix="vfg-orch-home-")
os.environ["HOME"] = _HOME
os.environ["PATH"] = helpers.FAKEBIN + ":" + os.environ.get("PATH", "")
os.environ.pop("VFG_FAKE_FAIL_REPLIES", None)
os.environ["VFG_FAKE_LOG"] = os.path.join(_HOME, "vfg.log")


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    saved = dict(os.environ)
    helpers.TMP_ROOT = str(tmp_path)
    yield
    os.environ.clear()
    os.environ.update(saved)


def pytest_unconfigure(config):
    shutil.rmtree(_HOME, ignore_errors=True)
