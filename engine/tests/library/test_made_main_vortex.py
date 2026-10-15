"""Unit tests for vortex_charge_krown (made/main/content): a swirling tornado charges for 5 s,
then lightning rings the caster, an explosion bursts at their feet, mobs nearby are hurled away and
a crater tears out of the ground, while the caster is never harmed or moved."""

from __future__ import annotations

import os
import shutil

import pytest

from vfg_engine.engine import Engine, Settings
from vfg_engine.io import Damage, Fill, GiveEffect, Summon, Particle, Sound
from vfg_engine.testing import FakeIo

LIBRARY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "library"
)
MADE_MAIN = "/var/home/jm/Documents/EverHear/External/made/main/content"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Rig:
    """An engine on FakeIo, its clock, and a content folder for vortex_charge_krown."""

    def __init__(self, tmp_path, ids, library_ids=()):
        self.dir = tmp_path / "content"
        self.dir.mkdir(exist_ok=True)
        for cid in ids:
            src = os.path.join(MADE_MAIN, cid + ".py")
            if not os.path.isfile(src):
                raise FileNotFoundError("no made/main class %s: %s does not exist" % (cid, src))
            shutil.copy(src, self.dir / (cid + ".py"))
        for cid in library_ids:
            src = os.path.join(LIBRARY, cid + ".py")
            if not os.path.isfile(src):
                raise FileNotFoundError("no library class %s: %s does not exist" % (cid, src))
            shutil.copy(src, self.dir / (cid + ".py"))
        self.io = FakeIo()
        self.clock = Clock()
        self.settings = Settings(content=(str(self.dir),), state=str(tmp_path / "state.json"), export=None)
        self.engine = None

    def patch(self, cid, old, new):
        """Edit the copied file for `cid` before start()."""
        path = self.dir / (cid + ".py")
        src = path.read_text()
        assert old in src, "patch target not found in %s: %r" % (cid, old)
        path.write_text(src.replace(old, new, 1))

    def start(self, expect_ok=True):
        self.engine = Engine(self.io, self.settings, clock=self.clock)
        result = self.engine.start()
        if expect_ok:
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

    def cast(self, player, ability):
        self.op("grant", who=player, ability=ability)
        phrase = ability.replace("_", " ")
        self.op("bind", player=player, phrase=phrase, ability=ability)
        return self.op("cast", player=player, phrase=phrase, cast_id="c%d" % (self.clock.t * 1000))

    @property
    def rt(self):
        return self.engine.rt

    def records(self, kind=None, cid=None):
        return [
            r
            for r in self.rt.records.values()
            if (kind is None or r.kind == kind) and (cid is None or r.cid == cid)
        ]

    def running(self, iid) -> bool:
        return iid in self.rt.records and not self.rt.records[iid].ending

    def result(self, iid) -> dict:
        if iid in self.rt.results:
            return dict(self.rt.results[iid])
        rec = self.rt.records.get(iid)
        return dict(rec.notes.get("result") or {}) if rec is not None else {}

    def sent(self, op_type):
        return self.io.ops_of(op_type)

    def lines(self):
        return [line["msg"] for line in self.engine.log_lines]

    def problems(self):
        return [m for m in self.lines() if "failed" in m or "slow call" in m or " cut" in m or "damage drift" in m]


@pytest.fixture
def made_rig(tmp_path):
    rigs = []

    def make(*ids, library_ids=()):
        r = Rig(tmp_path, ids, library_ids)
        rigs.append(r)
        return r

    yield make
    for r in rigs:
        if r.engine is not None and not r.io.closed:
            try:
                r.engine.close()
            except Exception:
                pass


# ---- vortex_charge_krown tests -----


def test_vortex_nothing_bursts_during_charge(made_rig):
    """The vortex builds for 5 s; nothing bursts until then."""
    r = made_rig("vortex_charge_krown")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.cast("Steve", "vortex_charge_krown")

    # After 1 second, still charging: no lightning bolts, no crater fills
    r.run_for(1)
    summons = r.sent(Summon)
    lightning_summons = [s for s in summons if "lightning_bolt" in s.entity_type]
    fills = r.sent(Fill)  # crater carves with Fill
    assert len(lightning_summons) == 0, "no lightning should fire during charge"
    assert len(fills) == 0, "no crater should carve during charge"


def test_vortex_fires_after_charge(made_rig):
    """After charge_s, lightning, blast, and crater fire."""
    r = made_rig("vortex_charge_krown")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.cast("Steve", "vortex_charge_krown")

    # Run for 6 seconds: 5 s charge + 1 s for the burst to complete
    r.run_for(6)

    # Check that lightning fired (lightning_bolt summons)
    summons = r.sent(Summon)
    lightning_summons = [s for s in summons if "lightning_bolt" in s.entity_type]
    assert len(lightning_summons) > 0, "lightning should fire after charge"

    # Check that a crater was carved (Fill ops for the crater)
    fills = r.sent(Fill)
    assert len(fills) > 0, "crater should be carved"


def test_vortex_caster_never_hurt(made_rig):
    """The caster is never hurt by their own vortex, lightning, or explosion."""
    r = made_rig("vortex_charge_krown")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.cast("Steve", "vortex_charge_krown")

    # Run the full cast
    r.run_for(6)

    # Check that Steve was never hurt (no damage to Steve)
    damages = r.sent(Damage)
    for d in damages:
        if d.who == "Steve":
            pytest.fail("Steve should never be hurt by their own vortex")
    # Also check the player's health is still 20 (full)
    assert r.io.players["Steve"].health == 20.0, "caster should never be hurt"


def test_vortex_caster_gets_protection(made_rig):
    """The caster gets slow falling and resistance to protect from crater and blast."""
    r = made_rig("vortex_charge_krown")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.cast("Steve", "vortex_charge_krown")

    # Run the full cast
    r.run_for(6)

    # Check that Steve got slow falling and resistance
    effects = r.sent(GiveEffect)
    slow_fall = [e for e in effects if e.effect == "minecraft:slow_falling" and e.target == "Steve"]
    resistance = [e for e in effects if e.effect == "minecraft:resistance" and e.target == "Steve"]
    assert len(slow_fall) > 0, "caster should get slow falling to protect from crater"
    assert len(resistance) > 0, "caster should get resistance to protect from blast"


def test_vortex_cooldown_active(made_rig):
    """A second cast is refused while cooldown is active."""
    r = made_rig("vortex_charge_krown")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)

    # First cast
    r.cast("Steve", "vortex_charge_krown")
    r.run_for(6)

    # Second cast immediately: should be refused (cooldown_s = 20)
    cast2 = r.cast("Steve", "vortex_charge_krown")
    assert cast2["status"] == "cooldown", "second cast should be refused due to cooldown"


def test_vortex_no_performance_issues(made_rig):
    """No call is slow (>50 ms) or cut."""
    r = made_rig("vortex_charge_krown")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.cast("Steve", "vortex_charge_krown")

    # Run the full cast
    r.run_for(6)

    # Check for slow calls and cuts
    problems = r.problems()
    assert len(problems) == 0, f"performance issues found: {problems}"
