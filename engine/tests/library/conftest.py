"""The library's unit tests share this: an engine on FakeIo, with a clock the test moves, loading the
library classes a test names (engine/library/<id>.py) and nothing else.

    def test_it_fires(rig):
        r = rig("railgun", "railgun_shot")          # those files, copied into a fresh content folder
        r.start()                                   # loads them; fails the test on any load error
        r.io.join("Steve", yaw=180)                 # FakeIo: players, entities, blocks (vfg_engine.testing)
        r.op("give", player="Steve", item="railgun")
        ...

Only the named files are loaded, so a neighbour's file that does not load never fails your test.
`extra={"name": source}` adds content written in the test itself (an actor your scenario spawns, a
scenario that starts yours), with `from vfg_engine import *` and `from vfg_engine.kit import *` on
top. The library's README says what each test covers."""

from __future__ import annotations

import os
import shutil
import textwrap

import pytest

from vfg_engine.engine import Engine, Settings
from vfg_engine.testing import FakeIo

LIBRARY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "library")
HEADER = "from vfg_engine import *\nfrom vfg_engine.kit import *\n"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Rig:
    """An engine on FakeIo, its clock, and a content folder holding the named library classes."""

    def __init__(self, tmp_path, ids, extra=None):
        self.dir = tmp_path / "content"
        self.dir.mkdir(exist_ok=True)
        for cid in ids:
            src = os.path.join(LIBRARY, cid + ".py")
            if not os.path.isfile(src):
                raise FileNotFoundError("no library class %s: %s does not exist" % (cid, src))
            shutil.copy(src, self.dir / (cid + ".py"))
        for name, source in (extra or {}).items():
            (self.dir / (name + ".py")).write_text(HEADER + textwrap.dedent(source))
        self.io = FakeIo()
        self.clock = Clock()
        # the call limits are real time, so a loaded PC could trip them: these tests run on the fake
        # clock alone, with limits no load reaches (the kit's test_limits checks what calls cost)
        self.settings = Settings(content=(str(self.dir),), state=str(tmp_path / "state.json"), export=None,
                                 slow_ms=10_000.0, cut_ms=20_000.0)
        self.engine = None

    # ---- running it ----------------------------------------------------------------------

    def start(self):
        """Start the engine; every named class must load."""
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
        """A control op, as the brain or `vfg engine` sends it, and one tick: its reply."""
        fut = self.engine.submit(op, args)
        self.tick()
        return fut.result(timeout=0)

    # ---- the engine's ops, the short way -------------------------------------------------

    def start_scenario(self, scenario, **args):
        """Start a scenario with its args (refused args fail the test); its instance id."""
        reply = self.op("start", scenario=scenario, args=args)
        assert reply["ok"], reply
        return reply["instance"]

    def cast(self, player, ability):
        """Grant `ability` to `player`, bind a phrase to it and cast it as the bridge does: the reply."""
        self.op("grant", who=player, ability=ability)
        phrase = ability.replace("_", " ")
        self.op("bind", player=player, phrase=phrase, ability=ability)
        return self.op("cast", player=player, phrase=phrase, cast_id="c%d" % (self.clock.t * 1000))

    # ---- reading what happened -----------------------------------------------------------

    @property
    def rt(self):
        return self.engine.rt

    def records(self, kind=None, cid=None):
        return [r for r in self.rt.records.values()
                if (kind is None or r.kind == kind) and (cid is None or r.cid == cid)]

    def running(self, iid) -> bool:
        return iid in self.rt.records and not self.rt.records[iid].ending

    def result(self, iid) -> dict:
        """A scenario's result, {"summary", "facts"} (and "reason" once it ended); {} when none yet."""
        if iid in self.rt.results:
            return dict(self.rt.results[iid])
        rec = self.rt.records.get(iid)
        return dict(rec.notes.get("result") or {}) if rec is not None else {}

    def sent(self, op_type):
        """Every op of that type the engine sent to Minecraft (vfg_engine.io: Damage, Summon, Fill...)."""
        return self.io.ops_of(op_type)

    def lines(self):
        return [line["msg"] for line in self.engine.log_lines]

    def problems(self):
        """Log lines that say a content call failed or was cut, or that damage drifted from what the
        class declares."""
        return [m for m in self.lines()
                if "failed" in m or "cut:" in m or "damage drift" in m]


@pytest.fixture
def rig(tmp_path):
    """rig("horde"), rig("railgun", "railgun_shot", extra={...}): a Rig; its engine is closed after."""
    rigs = []

    def make(*ids, extra=None):
        r = Rig(tmp_path, ids, extra)
        rigs.append(r)
        return r
    yield make
    for r in rigs:
        if r.engine is not None and not r.io.closed:
            try:
                r.engine.close()
            except Exception:
                pass
