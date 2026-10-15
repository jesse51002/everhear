"""Unit tests for the made_main group's port (PORT_BRIEF.md): Jesse's server content, kept outside
the worktree and outside git at External/made/main/content/ (that folder is the server's own
content, External/.gitignore's "/made/"). This file loads it by path, the way `tests/library/
conftest.py`'s shared Rig loads named ids from `engine/library/` -- a local Rig below copies named
ids from made/main's own content folder instead, plus (`library_ids=`) any library class a
made/main class names by id (`self.apply(..., "frozen")`, blizzard.py), so a neighbour's
in-progress file in either folder never fails this one.

Covers: blizzard (blizzard_freeze + blizzard_styrka), flame + fire_aura (fire_aura_krown +
krownjm_fire_sphere), speed_dash, fire_crossbow (krownjm_fire_crossbow), lava_wand + lava_sphere
(krownjm_lava_wand), magma_staff + magma_tube (krownjm_magma_tube_dir)."""

from __future__ import annotations

import os
import shutil

import pytest

from vfg_engine.engine import Engine, Settings
from vfg_engine.io import Damage, Fill, GiveEffect, Ignite, Summon
from vfg_engine.testing import FakeIo

LIBRARY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "library")
MADE_MAIN = "/var/home/jm/Documents/EverHear/External/made/main/content"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Rig:
    """An engine on FakeIo, its clock, and a content folder holding named made/main classes (and,
    `library_ids`, named library classes a made/main class depends on by id)."""

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

    # ---- running it ----------------------------------------------------------------------

    def patch(self, cid, old, new):
        """Edit the copied file for `cid` before start(): one `old` substring replaced with `new`
        (to drive a class with a non-default Config number, or to break one on purpose)."""
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

    def start_scenario(self, scenario, **args):
        reply = self.op("start", scenario=scenario, args=args)
        assert reply["ok"], reply
        return reply["instance"]

    def cast(self, player, ability):
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
        if iid in self.rt.results:
            return dict(self.rt.results[iid])
        rec = self.rt.records.get(iid)
        return dict(rec.notes.get("result") or {}) if rec is not None else {}

    def sent(self, op_type):
        return self.io.ops_of(op_type)

    def lines(self):
        return [line["msg"] for line in self.engine.log_lines]

    def problems(self):
        return [m for m in self.lines()
                if "failed" in m or "slow call" in m or " cut" in m or "damage drift" in m]


@pytest.fixture
def made_rig(tmp_path):
    """made_rig("blizzard", library_ids=("frozen",)): a Rig over made/main's own content folder."""
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


# ---------------------------------------------------------------- blizzard (blizzard_freeze + blizzard_styrka)

def test_blizzard_freezes_and_hurts_what_it_catches(made_rig):
    r = made_rig("blizzard", library_ids=("frozen",))
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    caught = r.io.add_entity("minecraft:husk", (0.0, 64.0, -8.0), health=200)
    r.cast("Steve", "blizzard")
    r.tick(3)
    assert r.io.by_uuid[caught].health < 200
    assert any(op.target == caught and op.effect == "minecraft:slowness" for op in r.sent(GiveEffect))
    assert r.problems() == []


def test_blizzard_never_catches_the_caster_or_a_pet(made_rig):
    r = made_rig("blizzard", library_ids=("frozen",))
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    pet = r.io.add_entity("minecraft:wolf", (0.0, 64.0, -5.0), health=20)
    r.io.by_uuid[pet].owner = "Steve"
    r.cast("Steve", "blizzard")
    r.tick(3)
    assert r.io.players["Steve"].health == 20.0
    assert r.io.by_uuid[pet].health == 20.0
    assert not any(op.target == pet for op in r.sent(GiveEffect))
    assert r.problems() == []


def test_blizzard_storm_s_repeats_the_pulse_like_blizzard_styrka(made_rig):
    # storm_s above 0 is blizzard_styrka's own case (inv_G4: "the pulse repeats every pulse_s,
    # which covers styrka"); there is no live op to change a Config number yet (DESIGN2's
    # engine_config), so this patches the file's own default the way engine_config would.
    r = made_rig("blizzard", library_ids=("frozen",))
    r.patch("blizzard", "storm_s: float = Field(0, ge=0, le=60)", "storm_s: float = Field(3, ge=0, le=60)")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    caught = r.io.add_entity("minecraft:husk", (0.0, 64.0, -8.0), health=200)
    r.cast("Steve", "blizzard")
    r.tick(5)           # just past the first pulse
    first = r.io.by_uuid[caught].health
    assert first < 200
    r.tick(15)           # a second pulse, within storm_s
    assert r.io.by_uuid[caught].health < first
    assert r.problems() == []


def test_blizzard_refuses_a_bad_radius_at_reload(made_rig):
    r = made_rig("blizzard", library_ids=("frozen",))
    r.patch("blizzard", "radius: float = Field(16, ge=1, le=64)", "radius: float = Field(1000, ge=1, le=64)")
    result = r.start(expect_ok=False)
    assert any(e.get("id") == "blizzard" for e in result["errors"])


# ---------------------------------------------------------------- flame + fire_aura (fire_aura_krown + krownjm_fire_sphere)

def test_flame_applies_the_fire_aura_mark(made_rig):
    r = made_rig("flame", "fire_aura")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.cast("Steve", "flame")
    r.tick(1)
    marks = r.records(kind="mark", cid="fire_aura")
    assert len(marks) == 1
    assert r.problems() == []


def test_fire_aura_burns_what_steps_in_but_never_the_bearer(made_rig):
    r = made_rig("flame", "fire_aura")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    caught = r.io.add_entity("minecraft:husk", (0.0, 64.0, -3.0), health=200)
    far = r.io.add_entity("minecraft:cow", (0.0, 64.0, -10.0), health=10)
    r.op("apply", who="Steve", mark="fire_aura", seconds=6, args={"radius": 4, "burn_s": 10, "damage_per_s": 2,
                                                                  "pulse_s": 1})
    r.tick(12)
    assert r.io.by_uuid[caught].health < 200
    assert any(op.target == caught for op in r.sent(Ignite))
    assert r.io.by_uuid[far].health == 10
    assert r.io.players["Steve"].health == 20.0
    assert r.problems() == []


def test_fire_aura_damage_per_s_zero_never_burns(made_rig):
    r = made_rig("flame", "fire_aura")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    caught = r.io.add_entity("minecraft:husk", (0.0, 64.0, -3.0), health=200)
    r.op("apply", who="Steve", mark="fire_aura", seconds=4, args={"radius": 4, "burn_s": 0, "damage_per_s": 0,
                                                                  "pulse_s": 1})
    r.tick(12)
    assert r.io.by_uuid[caught].health == 200
    assert not any(op.target == caught for op in r.sent(Ignite))
    assert r.problems() == []


# ---------------------------------------------------------------- speed_dash

def test_speed_dash_pushes_the_caster_forward_never_a_teleport(made_rig):
    r = made_rig("speed_dash")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)   # facing north, -z
    r.cast("Steve", "speed_dash")
    r.tick(1)
    # a grounded player is thrown by wind charges, never a raw Motion (KIT.md, movement.py):
    assert any(s.entity_type == "minecraft:wind_charge" for s in r.sent(Summon))
    assert r.problems() == []


def test_speed_dash_never_damages_the_caster(made_rig):
    r = made_rig("speed_dash")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.cast("Steve", "speed_dash")
    r.tick(1)
    assert r.io.players["Steve"].health == 20.0
    assert not r.sent(Damage)
    assert r.problems() == []


# ---------------------------------------------------------------- fire_crossbow (krownjm_fire_crossbow)

def test_fire_crossbow_ignites_mobs_near_any_landed_arrow(made_rig):
    r = made_rig("fire_crossbow")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.op("grant", who="Steve", ability="fire_crossbow")
    near = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -2.0), health=20)
    far = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -40.0), health=20)
    r.io.land("Steve", item=None, pos=(0.0, 64.0, -1.0), projectile_type="minecraft:arrow")  # any bow, any arrow
    r.tick(1)
    assert any(op.target == near for op in r.sent(Ignite))
    assert not any(op.target == far for op in r.sent(Ignite))
    assert r.problems() == []


def test_fire_crossbow_never_ignites_a_player(made_rig):
    r = made_rig("fire_crossbow")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.io.join("Alex", pos=(0.0, 64.0, -2.0), yaw=0.0)
    r.op("grant", who="Steve", ability="fire_crossbow")
    r.io.land("Steve", item=None, pos=(0.0, 64.0, -1.0), projectile_type="minecraft:arrow")
    r.tick(1)
    assert not any(op.target == "Alex" for op in r.sent(Ignite))
    assert r.problems() == []


# ---------------------------------------------------------------- lava_wand + lava_sphere (krownjm_lava_wand)

def test_lava_wand_use_casts_lava_sphere(made_rig):
    r = made_rig("lava_wand", "lava_sphere")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.io.add_entity("minecraft:zombie", (0.0, 64.0, -20.0), health=20)   # something for the look to find
    r.op("give", player="Steve", item="lava_wand")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    fills = r.sent(Fill)
    assert fills and any(f.block == "minecraft:lava" for f in fills)
    assert r.problems() == []


def test_lava_sphere_fills_lava_at_the_first_mob_found(made_rig):
    r = made_rig("lava_sphere")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.io.add_entity("minecraft:zombie", (0.0, 64.0, -20.0), health=20)
    r.cast("Steve", "lava_sphere")
    r.tick(3)
    fills = r.sent(Fill)
    assert fills and all(f.block == "minecraft:lava" and f.replace == "#minecraft:replaceable" for f in fills)
    assert r.problems() == []


def test_lava_sphere_refuses_too_close_to_the_caster(made_rig):
    r = made_rig("lava_sphere")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.io.add_entity("minecraft:zombie", (0.0, 64.0, -4.0), health=20)   # well within safe(5)+radius(3)
    r.cast("Steve", "lava_sphere")
    r.tick(3)
    assert r.sent(Fill) == []
    assert r.problems() == []


# ---------------------------------------------------------------- magma_staff + magma_tube (krownjm_magma_tube_dir)

def _fill_natural(r, dim, lo, hi, block="minecraft:grass_block"):
    """A solid block (one of magma_tube's own `from_blocks`, by literal id -- FakeIo's own block
    tags are not seeded past "minecraft:logs", testing.py, so a tag never matches here) filling
    the tube's own path, for it to carve through and turn a shell of into magma."""
    for x in range(lo[0], hi[0] + 1):
        for y in range(lo[1], hi[1] + 1):
            for z in range(lo[2], hi[2] + 1):
                r.io.set_block(dim, (x, y, z), block)


def test_magma_staff_use_casts_magma_tube(made_rig):
    r = made_rig("magma_staff", "magma_tube")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    dim = "minecraft:overworld"
    _fill_natural(r, dim, (-4, 62, -40), (4, 70, 2))
    r.op("give", player="Steve", item="magma_staff")
    r.tick()
    r.io.use("Steve")
    r.run_for(30)
    assert r.io.block_at(dim, 0, 66, -10) == "minecraft:air"
    assert r.problems() == []


def test_magma_tube_carves_a_tube_and_turns_its_shell_to_magma(made_rig):
    r = made_rig("magma_tube")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    dim = "minecraft:overworld"
    _fill_natural(r, dim, (-4, 62, -40), (4, 70, 2))
    r.cast("Steve", "magma_tube")
    r.run_for(30)
    assert r.io.block_at(dim, 0, 66, -10) == "minecraft:air"           # the inside, carved open
    assert r.io.block_at(dim, 3, 66, -10) == "minecraft:magma_block"   # its shell
    assert r.io.block_at(dim, -3, 66, -10) == "minecraft:magma_block"
    assert r.problems() == []


def test_magma_tube_never_touches_a_block_outside_its_own_from_list(made_rig):
    r = made_rig("magma_tube")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    dim = "minecraft:overworld"
    _fill_natural(r, dim, (-4, 62, -40), (4, 70, 2), block="minecraft:bricks")   # not in SHELL_FROM
    r.cast("Steve", "magma_tube")
    r.run_for(30)
    # Fill jobs are still queued (and sent) for every from-list entry at every step; none of them
    # match bricks, so nothing actually changes -- the world is the proof, not the op list.
    assert r.io.block_at(dim, 0, 66, -10) == "minecraft:bricks"
    assert r.io.block_at(dim, 3, 66, -10) == "minecraft:bricks"
    assert r.problems() == []
