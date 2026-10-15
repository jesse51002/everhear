"""The box tests' fixtures: a short temporary folder (a Unix socket's path is at most 107 bytes,
which pytest's own tmp_path can pass) and the environment a subprocess of the engine runs in."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

import pytest

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BOX_TESTS = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def short_tmp():
    path = tempfile.mkdtemp(prefix="ge-")
    yield path
    shutil.rmtree(path, True)


@pytest.fixture
def sub_env(short_tmp):
    """For a subprocess: the engine package and these tests importable, its files in short_tmp."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([ENGINE_DIR, BOX_TESTS])
    env["VFG_ENGINE_SOCKET"] = os.path.join(short_tmp, "engine.sock")
    env["VFG_ENGINE_LOG"] = os.path.join(short_tmp, "engine.log")
    env.pop("FAKE_ENGINE", None)
    return env


@pytest.fixture
def python():
    return sys.executable
