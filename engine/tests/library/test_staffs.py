"""The staffs group on FakeIo: gravity_staff (crush, lift), ice_staff, lightning_staff and zeus,
and the mark `frozen` they (and ice_beam and weapon_chest) share. A wall stops the raycast at a
known spot; mobs near it are hurt and frozen, flyers pinned to the ground, pushed up, or struck by
lightning; what is too far, or too far off the look, is spared."""

from vfg_engine.io import Damage, EntityData, Message, Motion, Sound, Summon

GROUP = ("frozen", "gravity_crush_staff", "gravity_crush", "gravity_lift_staff", "gravity_lift",
         "ice_staff", "ice_staff_freeze", "lightning_staff", "lightning_strike", "zeus")


def health(r, uuid):
    return r.io.by_uuid[uuid].health


def stage(r, wall_z=-20):
    """Steve at 0 64 0 facing north (-z); a wall at wall_z stops a raycast there. Three blocks
    wide in x: a yaw of exactly 180 gives a facing with a vanishingly small negative x (float
    trig, not quite 0), which floors to -1, not 0, so a one-wide wall at x 0 is missed."""
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    for x in (-1, 0, 1):
        for y in (64, 65, 66):
            io.set_block("minecraft:overworld", (x, y, wall_z), "minecraft:stone")


def test_the_group_loads(rig):
    r = rig(*GROUP)
    assert sorted(r.start()["loaded"]) == sorted(GROUP)


# ---- gravity_crush --------------------------------------------------------------------------

def test_gravity_crush_freezes_and_pins_what_is_round_the_spot_looked_at(rig):
    r = rig(*GROUP)
    r.start()
    stage(r)
    near = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -20.0), health=1000)
    flier = r.io.add_entity("minecraft:phantom", (3.0, 90.0, -24.0), health=1000)
    far = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -70.0), health=1000)       # 50: past 40
    r.op("give", player="Steve", item="gravity_crush_staff")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert {rec.user for rec in r.records("mark", "frozen")} == {near, flier}
    assert any(op.target == near and "TicksFrozen" in op.snbt for op in r.sent(EntityData))
    assert not any(op.target == far for op in r.sent(EntityData))
    assert any(s.name == "minecraft:entity.warden.sonic_boom" for s in r.sent(Sound))
    r.run_for(0.5)                                       # the pin: ticked down toward the ground
    assert r.io.by_uuid[flier].pos[1] < 90.0
    r.run_for(1.0)
    assert round(r.io.by_uuid[flier].pos[1]) == 64        # settled on the ground (ground=63)
    assert r.io.by_uuid[far].pos == (0.0, 64.0, -70.0)    # never touched
    assert r.problems() == []


def test_gravity_crush_spares_what_is_too_far(rig):
    r = rig(*GROUP)
    r.start()
    stage(r)
    far = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -70.0), health=1000)       # 50: past 40
    r.op("give", player="Steve", item="gravity_crush_staff")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert not r.records("mark", "frozen")
    assert health(r, far) == 1000
    assert r.problems() == []


# ---- gravity_lift ----------------------------------------------------------------------------

def test_gravity_lift_pushes_up_what_is_round_the_spot_and_spares_what_is_too_far(rig):
    r = rig(*GROUP)
    r.start()
    stage(r)
    near = r.io.add_entity("minecraft:zombie", (2.0, 64.0, -22.0), health=1000)
    far = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -70.0), health=1000)
    r.op("give", player="Steve", item="gravity_lift_staff")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    moved = [op for op in r.sent(Motion) if op.target == near]
    assert moved and moved[-1].vec[1] > 0                 # thrown up
    assert not [op for op in r.sent(Motion) if op.target == far]
    assert any(s.name == "minecraft:entity.generic.explode" for s in r.sent(Sound))
    assert r.problems() == []


# ---- ice_staff / ice_staff_freeze ----------------------------------------------------------

def test_ice_staff_locks_onto_a_mob_in_the_cone_and_freezes_and_hurts_it(rig):
    r = rig(*GROUP)
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    mob = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0), health=1000)       # straight ahead
    aside = r.io.add_entity("minecraft:zombie", (40.0, 64.0, -10.0), health=1000)    # well off the cone
    r.op("give", player="Steve", item="ice_staff")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert health(r, mob) == 800                           # 200 magic
    assert health(r, aside) == 1000
    assert {rec.user for rec in r.records("mark", "frozen")} == {mob}
    assert r.problems() == []


def test_ice_staff_falls_back_to_the_block_looked_at(rig):
    r = rig(*GROUP)
    r.start()
    stage(r, wall_z=-15)
    off_cone = r.io.add_entity("minecraft:zombie", (3.0, 64.0, -15.0), health=1000)  # beside the wall, off the cone
    r.op("give", player="Steve", item="ice_staff")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert health(r, off_cone) == 800                      # found by around(), not lock-on
    assert {rec.user for rec in r.records("mark", "frozen")} == {off_cone}
    assert r.problems() == []


# ---- lightning_staff / lightning_strike -----------------------------------------------------

def test_lightning_staff_strikes_the_block_looked_at_and_hurts_round_it(rig):
    r = rig(*GROUP)
    r.start()
    stage(r, wall_z=-10)
    near = r.io.add_entity("minecraft:zombie", (2.0, 64.0, -10.0), health=1000)
    far = r.io.add_entity("minecraft:cow", (0.0, 64.0, -40.0), health=1000)
    r.op("give", player="Steve", item="lightning_staff")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert health(r, near) == 960                          # 40 lightning_bolt
    assert health(r, far) == 1000
    bolts = [s for s in r.sent(Summon) if s.entity_type == "minecraft:lightning_bolt"]
    assert len(bolts) == 4
    hits = [d for d in r.sent(Damage) if d.target == near]
    assert hits and hits[0].damage_type == "minecraft:lightning_bolt"
    assert r.problems() == []


def test_lightning_staff_says_so_with_no_target_in_range(rig):
    r = rig(*GROUP)
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=0.0, pitch=-89.0)    # straight up: no block near
    r.op("give", player="Steve", item="lightning_staff")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert any("No target in range" in m.text for m in r.sent(Message))
    assert not r.sent(Summon)
    assert r.problems() == []


# ---- zeus --------------------------------------------------------------------------------

def test_zeus_calls_lightning_where_a_thrown_trident_lands(rig):
    r = rig(*GROUP)
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.op("grant", who="Steve", ability="zeus")
    r.tick()
    r.io.land("Steve", pos=(10.0, 64.0, 10.0), projectile_type="minecraft:trident")
    r.tick(2)
    bolts = [s for s in r.sent(Summon) if s.entity_type == "minecraft:lightning_bolt"]
    assert len(bolts) == 1 and all(abs(s.pos[0] - 10.0) < 0.1 for s in bolts)
    assert r.problems() == []


def test_zeus_never_strikes_within_4_blocks_of_the_thrower(rig):
    r = rig(*GROUP)
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.op("grant", who="Steve", ability="zeus")
    r.tick()
    r.io.land("Steve", pos=(1.0, 64.0, 0.0), projectile_type="minecraft:trident")     # 1 block off: too close
    r.tick(2)
    assert not [s for s in r.sent(Summon) if s.entity_type == "minecraft:lightning_bolt"]
    assert r.problems() == []


def test_zeus_ignores_arrows(rig):
    r = rig(*GROUP)
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.op("grant", who="Steve", ability="zeus")
    r.tick()
    r.io.land("Steve", pos=(10.0, 64.0, 10.0), projectile_type="minecraft:arrow")
    r.tick(2)
    assert not [s for s in r.sent(Summon) if s.entity_type == "minecraft:lightning_bolt"]
    assert r.problems() == []


# ---- frozen --------------------------------------------------------------------------------

def test_frozen_keeps_a_player_slowed_weak_and_blind_when_asked(rig):
    r = rig(*GROUP)
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.op("apply", who="Steve", mark="frozen", args={"blind": True})
    r.run_for(0.5)
    effects = r.io.players["Steve"].effects
    assert "minecraft:slowness" in effects and effects["minecraft:slowness"].amplifier == 9
    assert "minecraft:weakness" in effects
    assert "minecraft:blindness" in effects
    assert r.problems() == []


def test_frozen_chills_a_mob_with_ticks_frozen_and_ends_when_it_is_gone(rig):
    r = rig(*GROUP)
    r.start()
    mob = r.io.add_entity("minecraft:zombie", (0.0, 64.0, 0.0))
    r.op("apply", who=mob, mark="frozen")
    r.tick(2)
    assert any(op.target == mob and "TicksFrozen" in op.snbt for op in r.sent(EntityData))
    r.io.kill_entity(mob)
    r.tick(2)
    assert not r.records("mark", "frozen")
    assert r.problems() == []


def test_frozen_args_are_refused_in_words(rig):
    r = rig(*GROUP)
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    reply = r.op("apply", who="Steve", mark="frozen", args={"chilly": True})
    assert not reply["ok"] and "not an arg of this" in reply["error"]
