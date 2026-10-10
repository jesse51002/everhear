"""Shared bits for the unit tests: temp dirs, the fake vfg on the PATH, a quiet log."""
import asyncio
import json
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Callable

from orchestrator.config import Config, load_config, set_quiet
from orchestrator.tasks import Store

set_quiet(True)

HERE = os.path.dirname(os.path.abspath(__file__))
FAKEBIN = os.path.join(HERE, "fakebin")
ORCH = os.path.dirname(HERE)
# Each test's own tmp_path (conftest sets it); temp dirs go inside it.
TMP_ROOT: str | None = None


def temp_dir(prefix: str = "vfg-orch-test-") -> str:
    return tempfile.mkdtemp(prefix=prefix, dir=TMP_ROOT)


@dataclass
class FakeVfg:
    log: str
    fail_file: str

    def calls(self) -> list[list[str]]:
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(line) for line in f.read().split("\n") if line]


def use_fake_vfg(d: str) -> FakeVfg:
    """Puts the fake vfg first on the PATH and logs its calls to a fresh file."""
    log = os.path.join(d, "vfg.log")
    fail_file = os.path.join(d, "fail-replies")
    with open(log, "w"):
        pass
    rest = [p for p in os.environ.get("PATH", "").split(":") if p != FAKEBIN]
    os.environ["PATH"] = ":".join([FAKEBIN] + rest)
    os.environ["VFG_FAKE_LOG"] = log
    os.environ["VFG_FAKE_FAIL_REPLIES"] = fail_file
    return FakeVfg(log, fail_file)


def config_in(d: str, extra: dict[str, str] | None = None) -> Config:
    """The config of a runner whose repo is `d` and whose state is in d/orch."""
    return load_config({"VFG_ORCH_DIR": os.path.join(d, "orch"), "VFG_REPO": d, **(extra or {})})


def new_store(d: str) -> Store:
    s = Store(os.path.join(d, "state.json"))
    s.load()
    return s


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def append(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


async def until(cond: Callable[[], bool], ms: float = 3000, step: float = 10) -> None:
    end = time.monotonic() + ms / 1000
    while not cond():
        if time.monotonic() > end:
            raise TimeoutError("timed out waiting")
        await asyncio.sleep(step / 1000)


async def tick() -> None:
    await asyncio.sleep(0.005)
