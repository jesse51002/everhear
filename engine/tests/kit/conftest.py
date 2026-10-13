"""Shared pieces for the kit's tests: an engine on FakeIo with a clock the test moves, and content
written into a temporary folder that imports the kit, so each helper is tried where it lives:
inside a content call, in the engine's loop, under its limits."""

from __future__ import annotations

import textwrap

import pytest

from vfg_engine.engine import Engine, Settings
from vfg_engine.testing import FakeIo

HEADER = "from vfg_engine import *\nfrom vfg_engine.kit import *\n"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Rig:
    """An engine, its FakeIo, its clock and its content folder."""

    def __init__(self, tmp_path, files=None, **settings):
        self.dir = tmp_path / "content"
        self.dir.mkdir(exist_ok=True)
        self.io = FakeIo()
        self.clock = Clock()
        # the call limits are real time, so a loaded PC could trip them: these tests run on the fake
        # clock alone, with limits no load reaches (test_limits keeps its own and checks the cost)
        settings.setdefault("slow_ms", 10_000.0)
        settings.setdefault("cut_ms", 20_000.0)
        self.settings = Settings(content=(str(self.dir),), state=None, export=None, **settings)
        for name, source in (files or {}).items():
            self.write(name, source)
        self.engine = None

    def write(self, name, source):
        path = self.dir / (name if name.endswith(".py") else name + ".py")
        path.write_text(HEADER + textwrap.dedent(source))
        return str(path)

    def start(self):
        self.engine = Engine(self.io, self.settings, clock=self.clock)
        result = self.engine.start()
        assert result["errors"] == [], result["errors"]
        return result

    def tick(self, n=1, dt=0.1):
        for _ in range(n):
            self.clock.t += dt
            self.engine.tick()

    def run_for(self, seconds, dt=0.1):
        self.tick(int(round(seconds / dt)), dt)

    def op(self, op, **args):
        fut = self.engine.submit(op, args)
        self.tick()
        return fut.result(timeout=0)

    def start_scenario(self, scenario, **args):
        reply = self.op("start", scenario=scenario, args=args)
        assert reply["ok"], reply
        return reply["instance"]

    def cast(self, player, ability):
        self.op("grant", who=player, ability=ability)
        self.op("bind", player=player, phrase=ability.replace("_", " "), ability=ability)
        reply = self.op("cast", player=player, phrase=ability.replace("_", " "), cast_id="c%d" % self.clock.t)
        assert reply["ok"], reply
        return reply

    @property
    def rt(self):
        return self.engine.rt

    def records(self, kind=None, cid=None):
        return [r for r in self.rt.records.values()
                if (kind is None or r.kind == kind) and (cid is None or r.cid == cid)]

    def state(self, cid):
        recs = self.records(cid=cid)
        assert recs, "no instance of %s" % cid
        return recs[0].state

    def lines(self):
        return [line["msg"] for line in self.engine.log_lines]

    def problems(self):
        """Log lines that say a content call failed or was cut."""
        return [m for m in self.lines() if "failed" in m or "cut:" in m]

    def sent(self, op_type):
        return self.io.ops_of(op_type)


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
