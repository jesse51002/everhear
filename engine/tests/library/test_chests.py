"""The chests group on FakeIo: weapon_chest (gun_chests merged in) places a chest of library
items -- powers (six named weapons: hellfire_longbow, glacier_bow, stormcaller, thunderbrand,
doomcleaver, gravebreaker, this group's own) or guns (several scattered chests of library items
from another group, two per chest, under a glowing label). Thunderbrand and Doomcleaver's and
Gravebreaker's own abilities (lightning_strike, blast_at_look, self_lift) fire on a right-click;
blast_at_look and self_lift are this group's own, lightning_strike and frozen are another group's
(lightning_staff's and ice_staff's), stubbed here the way the library's own tests stand in for a
dependency they do not own (conftest.py's `extra=`, test_bosses.py's own FROZEN_STUB)."""

import math
import re

from vfg_engine.io import (
    BlockData,
    Damage,
    Message,
    Motion,
    Particle,
    SetBlock,
    Sound,
    Summon,
)

GROUP = ("weapon_chest", "hellfire_longbow", "glacier_bow", "stormcaller", "thunderbrand", "doomcleaver",
         "blast_at_look", "gravebreaker", "self_lift")

FROZEN_STUB = """
class Frozen(Mark):
    class Config(MarkConfig):
        summary = "Stub for testing frost weapons in isolation; the real one is ice_staff's and ice_beam's group."
        duration_s = 4
        damage = 0
"""

LIGHTNING_STRIKE_STUB = """
class LightningStrike(Ability):
    class Config(AbilityConfig):
        summary = "Stub for testing lightning weapons in isolation; the real one is lightning_staff's group."
        damage = 0
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 1
        duration_s = 0

    def on_cast(self, ctx, ev):
        self.sound("minecraft:entity.lightning_bolt.thunder", ctx.user.pos)
"""

GUN_STUB = """
class %s(Item):
    class Config(ItemConfig):
        summary = "Stub for testing weapon_chest's guns mode in isolation; the real one is another group's."
        base = "carrot_on_a_stick"
        name = "%s"
        active_in = "mainhand"
"""

GUN_IDS = ("railgun", "gatling_gun", "super_sniper", "rocket_launcher", "spear_bomb")
EXTRA = {"frozen": FROZEN_STUB, "lightning_strike": LIGHTNING_STRIKE_STUB}
EXTRA.update({gid: GUN_STUB % ("".join(p.title() for p in gid.split("_")), gid) for gid in GUN_IDS})


def health(r, uuid):
    return r.io.by_uuid[uuid].health


def steve(r, yaw=180.0):
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=yaw)
    r.tick()


def test_the_group_loads(rig):
    r = rig(*GROUP, extra=EXTRA)
    assert sorted(r.start()["loaded"]) == sorted(GROUP + tuple(EXTRA))


# ---- hellfire_longbow ---------------------------------------------------------------------

def test_hellfire_longbow_explodes_where_an_arrow_lands_breaking_nothing(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    steve(r)
    mob = r.io.add_entity("minecraft:zombie", (10.0, 64.0, 10.0), health=1000)
    r.op("give", player="Steve", item="hellfire_longbow")
    r.tick()
    r.io.land("Steve", item="hellfire_longbow", pos=(10.0, 64.0, 10.0))
    r.tick(2)
    assert 900 < health(r, mob) < 1000                      # hurt by the (non-breaking) blast, power 3
    assert not r.sent(Summon)                                # breaks=False: no TNT, nothing summoned
    assert any(d.target == mob and d.damage_type == "minecraft:explosion" for d in r.sent(Damage))
    assert r.problems() == []


# ---- glacier_bow ----------------------------------------------------------------------------

def test_glacier_bow_freezes_what_is_near_where_an_arrow_lands(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    steve(r)
    near = r.io.add_entity("minecraft:zombie", (10.0, 64.0, 10.0), health=1000)
    far = r.io.add_entity("minecraft:zombie", (10.0, 64.0, 30.0), health=1000)      # 20 away: past radius 4
    r.op("give", player="Steve", item="glacier_bow")
    r.tick()
    r.io.land("Steve", item="glacier_bow", pos=(10.0, 64.0, 10.0))
    r.tick(2)
    assert {rec.user for rec in r.records("mark", "frozen")} == {near}
    assert health(r, near) == 1000 and health(r, far) == 1000     # the bow itself deals no damage
    assert r.problems() == []


# ---- stormcaller ----------------------------------------------------------------------------

def test_stormcaller_calls_lightning_and_hurts_what_is_near_where_a_trident_lands(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    steve(r)
    near = r.io.add_entity("minecraft:zombie", (10.0, 64.0, 10.0), health=1000)
    r.op("give", player="Steve", item="stormcaller")
    r.tick()
    r.io.land("Steve", item="stormcaller", pos=(10.0, 64.0, 10.0), projectile_type="minecraft:trident")
    r.tick(2)
    bolts = [s for s in r.sent(Summon) if s.entity_type == "minecraft:lightning_bolt"]
    assert len(bolts) == 1
    assert health(r, near) == 990.0                          # 10 lightning_bolt, round the strike
    hits = [d for d in r.sent(Damage) if d.target == near]
    assert hits and hits[0].damage_type == "minecraft:lightning_bolt"
    assert r.problems() == []


def test_stormcaller_ignores_arrows(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    steve(r)
    r.op("give", player="Steve", item="stormcaller")
    r.tick()
    r.io.land("Steve", item="stormcaller", pos=(10.0, 64.0, 10.0), projectile_type="minecraft:arrow")
    r.tick(2)
    assert not [s for s in r.sent(Summon) if s.entity_type == "minecraft:lightning_bolt"]
    assert r.problems() == []


# ---- thunderbrand (lightning_strike, stubbed) ----------------------------------------------

def test_thunderbrand_casts_lightning_strike_on_a_click(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    steve(r)
    r.op("give", player="Steve", item="thunderbrand")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert any(s.name == "minecraft:entity.lightning_bolt.thunder" for s in r.sent(Sound))
    r.io.use("Steve")                                        # straight away: recharging (cooldown_s 1)
    r.tick(2)
    assert any(m.where == "actionbar" and re.search(r"Thunderbrand: \d+\.\d s", m.text) for m in r.sent(Message))
    assert r.problems() == []


# ---- doomcleaver / blast_at_look ------------------------------------------------------------

def wall(r, z=-10):
    for x in (-1, 0, 1):
        for y in (64, 65, 66):
            r.io.set_block("minecraft:overworld", (x, y, z), "minecraft:stone")


def test_doomcleaver_blasts_the_block_looked_at_hurting_what_is_near(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    steve(r)
    wall(r, z=-10)
    near = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -9.0), health=1000)
    r.op("give", player="Steve", item="doomcleaver")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert health(r, near) < 1000                            # hurt by the blast
    assert any(p.name == "minecraft:explosion_emitter" for p in r.sent(Particle))
    assert any(s.name == "minecraft:entity.generic.explode" for s in r.sent(Sound))
    assert not r.sent(Summon)                                 # breaks=False: never real TNT
    assert r.problems() == []


def test_doomcleaver_says_so_with_no_target_in_range(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=0.0, pitch=-89.0)     # straight up: no block near
    r.tick()
    r.op("give", player="Steve", item="doomcleaver")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert any("No target in range" in m.text for m in r.sent(Message))
    assert r.problems() == []


# ---- gravebreaker / self_lift ----------------------------------------------------------------

def test_gravebreaker_lifts_what_is_near_the_caster_sparing_what_is_too_far(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    steve(r)
    near = r.io.add_entity("minecraft:zombie", (2.0, 64.0, 0.0), health=1000)     # within radius 4
    far = r.io.add_entity("minecraft:zombie", (20.0, 64.0, 0.0), health=1000)     # past it
    r.op("give", player="Steve", item="gravebreaker")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    moved = [op for op in r.sent(Motion) if op.target == near]
    assert moved and moved[-1].vec[1] > 0                     # thrown up
    assert not [op for op in r.sent(Motion) if op.target == far]
    assert r.problems() == []


# ---- weapon_chest: contents="powers" ---------------------------------------------------------

def power_chest_ops(r):
    return [op for op in r.sent(SetBlock) if "chest" in op.block]


def chest_contents(r, op):
    """Everything put in the chest `op` placed: its own Items and the stacks queued after it."""
    return op.block + " ".join(d.snbt for d in r.sent(BlockData) if d.pos == op.pos)


def test_powers_places_one_chest_beside_the_first_player_and_never_more(rig):
    # six power weapons plus 64 arrows are more than one command (1446 bytes): the kit's chest()
    # places the one chest with what fits and appends the rest the tick after
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    steve(r)
    iid = r.start_scenario("weapon_chest")
    r.tick(3)
    chests = power_chest_ops(r)
    assert len(chests) == 1
    first = chests[0]
    assert first.pos[1] == 64 and first.pos[2] == -2 and first.pos[0] in (-1, 0)  # 2 ahead of Steve, facing north
    whole = chest_contents(r, first)
    assert whole != first.block                                # some stacks came in a later command
    for g in ("hellfire_longbow", "glacier_bow", "stormcaller", "thunderbrand", "doomcleaver", "gravebreaker"):
        assert g in whole
    assert "id:\"minecraft:arrow\",count:64" in whole
    assert r.running(iid)                                      # until_stopped: it keeps going, watching joiners
    r.run_for(5)                                               # several re-check intervals (every 2 s)
    assert len(power_chest_ops(r)) == 1                         # still only the one, shared chest
    assert r.op("stop", instance=iid)["ok"]
    assert r.problems() == []


def test_powers_per_player_gives_each_player_their_own_chest(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.io.join("Alex", pos=(50.0, 64.0, 0.0), yaw=180.0)
    r.tick()
    r.start_scenario("weapon_chest", per_player=True)
    r.tick()
    chests = power_chest_ops(r)
    near_steve = [c for c in chests if abs(c.pos[0]) < 10]
    near_alex = [c for c in chests if abs(c.pos[0] - 50) < 10]
    assert len(near_steve) == len(near_alex) == 1 and len(chests) == 2      # one each
    assert r.problems() == []


def test_powers_at_a_place_needs_no_player_online(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    iid = r.start_scenario("weapon_chest", at="10 64 10")
    r.tick()
    chests = power_chest_ops(r)
    assert len(chests) == 1 and chests[0].pos == (10, 64, 10)   # placed right at the given spot
    got = r.result(iid)
    assert got["summary"] == "a chest of powers is ready at 10 64 10"
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))                   # a player joining later changes nothing: fixed place
    r.run_for(5)
    assert len(power_chest_ops(r)) == len(chests)
    assert r.problems() == []


def test_powers_args_refused_in_words(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    reply = r.op("start", scenario="weapon_chest", args={"contents": "mystery"})
    assert not reply["ok"] and "contents" in reply["error"]
    reply = r.op("start", scenario="weapon_chest", args={"nearest": 5000})
    assert not reply["ok"] and "nearest" in reply["error"]


# ---- weapon_chest: contents="guns" ------------------------------------------------------------

def test_guns_scatters_chests_at_a_fixed_distance_with_a_glowing_label(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    steve(r)
    iid = r.start_scenario("weapon_chest", contents="guns", chests_per_player=1, nearest=0, farthest=0)
    for _ in range(20):
        if not r.running(iid):
            break
        r.tick()
    assert not r.running(iid)                                  # a one-shot: it ends itself
    chests = power_chest_ops(r)
    assert len(chests) == 1
    c = chests[0]
    assert c.pos == (0, 64, 0)                                 # nearest=farthest=0: right beside Steve
    assert sum(1 for g in GUN_IDS if g in c.block) == 2        # two of the gun table, each an EverHear item
    labels = [s for s in r.sent(Summon) if s.entity_type == "minecraft:armor_stand"]
    assert labels and "GUN CHEST" in labels[0].nbt and "Glowing:1b" in labels[0].nbt
    got = r.result(iid)
    assert got["facts"]["chests"] == 1 and got["facts"]["players"] == ["Steve"]
    assert r.problems() == []


def test_guns_scatters_within_the_given_range(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    steve(r)
    iid = r.start_scenario("weapon_chest", contents="guns", chests_per_player=3, nearest=20, farthest=20)
    for _ in range(60):
        if not r.running(iid):
            break
        r.tick()
    chests = power_chest_ops(r)
    assert len(chests) == 3
    for c in chests:
        assert abs(math.dist((c.pos[0], c.pos[2]), (0.0, 0.0)) - 20) < 2       # int() truncation per axis
    assert r.problems() == []


def test_guns_with_nobody_online_refuses_in_words(rig):
    r = rig(*GROUP, extra=EXTRA)
    r.start()
    iid = r.start_scenario("weapon_chest", contents="guns")
    r.tick()
    assert not r.running(iid)
    got = r.result(iid)
    assert got["facts"]["chests"] == 0 and "nobody" in got["summary"]
    assert r.problems() == []
