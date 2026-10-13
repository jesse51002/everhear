"""Shared pieces for the core's tests: an engine on FakeIo with a clock the test moves, and content
written into a temporary folder."""

from __future__ import annotations

import os
import textwrap

import pytest

from vfg_engine.engine import Engine, Settings
from vfg_engine.testing import FakeIo

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(HERE)), "examples")
HEADER = "from vfg_engine import *\n"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Rig:
    """An engine, its FakeIo, its clock and its content folder."""

    def __init__(self, tmp_path, files=None, examples=False, state=True, export=True, **settings):
        self.dir = tmp_path / "content"
        self.dir.mkdir(exist_ok=True)
        self.tmp = tmp_path
        self.io = FakeIo()
        self.clock = Clock()
        folders = [str(self.dir)] + ([EXAMPLES] if examples else [])
        # Content is cut at 50 ms only in the tests about the cut (they pass cut_ms=50): on a loaded
        # box an ordinary call can take longer than that, and must not fail a test about anything else.
        settings.setdefault("cut_ms", 2000.0)
        self.settings = Settings(content=tuple(folders),
                                 state=str(tmp_path / "state.json") if state else None,
                                 export=str(tmp_path / "incantations.json") if export else None,
                                 **settings)
        for name, source in (files or {}).items():
            self.write(name, source)
        self.engine = None

    def write(self, name, source, header=True):
        path = self.dir / (name if name.endswith(".py") else name + ".py")
        path.write_text((HEADER if header else "") + textwrap.dedent(source))
        return str(path)

    def start(self):
        self.engine = Engine(self.io, self.settings, clock=self.clock)
        self.result = self.engine.start()
        return self.result

    def tick(self, n=1, dt=0.1):
        for _ in range(n):
            self.clock.t += dt
            self.engine.tick()

    def run_for(self, seconds, dt=0.1):
        self.tick(int(round(seconds / dt)), dt)

    def op(self, op, **args):
        """Submit an op and run one tick; its reply."""
        fut = self.engine.submit(op, args)
        self.tick()
        return fut.result(timeout=0)

    @property
    def rt(self):
        return self.engine.rt

    def records(self, kind=None, cid=None):
        return [r for r in self.rt.records.values()
                if (kind is None or r.kind == kind) and (cid is None or r.cid == cid)]

    def mods(self, name):
        return dict(self.io.players[name].modifiers)

    def lines(self):
        return [line["msg"] for line in self.engine.log_lines]


@pytest.fixture
def rig(tmp_path):
    rigs = []

    def make(files=None, **kw):
        r = Rig(tmp_path, files, **kw)
        rigs.append(r)
        return r
    yield make
    for r in rigs:
        if r.engine is not None and not r.io.closed:
            try:
                r.engine.close()
            except Exception:
                pass
