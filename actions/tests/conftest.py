"""Tests for actions/, toolkit/remote/action.py and toolkit/remote/places.py.

    uv run --no-project --with pytest --with pydantic pytest actions/tests

No network and no real server. The handlers and action.py run the way EC2 runs them:
common.py + ARGS + action.py as one program on `python3 -`'s stdin, against
scripts/_test's fake RCON server answering as FakeWorld, which speaks Minecraft 26.3's
recorded replies (scripts/_test/mc_26_3.py). That program runs under python3.9 when it
is installed, EC2's Python, else under this one.
"""
from __future__ import annotations

import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
REMOTE = REPO / "toolkit" / "remote"
TEST_LIB = REPO / "scripts" / "_test"
for p in (str(REPO), str(TEST_LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fake_rcon import FakeRcon  # noqa: E402
from fake_places_world import FakeWorld  # noqa: E402

PY39 = shutil.which("python3.9") or sys.executable


def load_places():
    """scripts/_lib/places.py as a module, the way a handler sees it."""
    spec = importlib.util.spec_from_file_location("places", REPO / "scripts" / "_lib" / "places.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def toolkit():
    """toolkit/vfg as a module, for its program(): what `vfg action run` sends."""
    import importlib.machinery
    loader = importlib.machinery.SourceFileLoader("vfg_toolkit_for_actions", str(REPO / "toolkit" / "vfg"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


def action_namespace() -> dict:
    """common.py + remote/commands.py + action.py's functions, loaded without running it."""
    ns = {"__name__": "action_under_test"}
    source = program({}).decode("utf-8")
    exec(compile(source, "action.py", "exec"), ns)
    return ns


def program(args: dict) -> bytes:
    """Exactly what toolkit/vfg's `vfg action run` sends: common.py, ARGS, the libraries, action.py."""
    vfg = toolkit()
    return vfg.program("action.py", args, vfg.ACTION_LIBS)


@dataclass
class Ran:
    code: int
    out: dict | None    # the one JSON line, when there was one
    stdout: str
    stderr: str


class Server:
    """A fake RCON server answering as a FakeWorld, and a temp mc folder pointing at it."""

    def __init__(self, tmp: Path, world: FakeWorld):
        self.world = world
        self.password = secrets.token_hex(8)
        self.rcon = FakeRcon(self.password, responder=world.respond).start()
        self.mc = tmp / "mc"
        self.mc.mkdir(exist_ok=True)
        self.write_props(self.rcon.port)
        self.tmp = tmp

    def write_props(self, port: int, enabled: bool = True) -> None:
        (self.mc / "server.properties").write_text(
            "#Minecraft server properties\nenable-rcon=%s\nrcon.port=%d\nrcon.password=%s\n"
            % ("true" if enabled else "false", port, self.password))

    def run(self, args: dict, vfg: Path = REPO, timeout: float = 60) -> Ran:
        full = dict(args)
        full.setdefault("mc", str(self.mc))
        full.setdefault("vfg", str(vfg))
        env = {k: v for k, v in os.environ.items() if not k.startswith("VFG_")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        p = subprocess.run([PY39, "-u", "-"], input=program(full), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout, env=env, cwd=str(self.tmp))
        stdout, stderr = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
        lines = stdout.splitlines()
        assert len(lines) <= 1, "more than one line on stdout:\n" + stdout
        out = json.loads(lines[0]) if lines else None
        return Ran(p.returncode, out, stdout, stderr)

    def handler(self, name: str, args: dict, task: str = "T7", **kw) -> Ran:
        source = (REPO / "actions" / name / "handler.py").read_text()
        ran = self.run({"mode": "handler", "name": name, "task": task, "args": args,
                        "handler": source, "places": (REPO / "scripts" / "_lib" / "places.py").read_text()}, **kw)
        # A handler never places or removes a block, whatever it was asked.
        from actions._types import command_problem
        for c in self.world.sent:
            why = command_problem(c) or ""
            assert "blocks" not in why and "owner" not in why, c
        return ran

    def commands(self, commands: list, task: str = "T3", **kw) -> Ran:
        return self.run({"mode": "commands", "task": task, "commands": commands}, **kw)

    def stop(self) -> None:
        self.rcon.stop()


@pytest.fixture
def serve(tmp_path):
    """serve(world) -> a Server; every one is stopped after the test."""
    servers = []

    def make(world: FakeWorld) -> Server:
        s = Server(tmp_path, world)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.stop()
