"""The hitscan group on FakeIo: super_sniper (item) fires sniper_shot (ability), a long shot that
stops at the first solid block, double damage on the head, and never hits a player (what=mobs);
infinity_gauntlet (item) fires infinity_gauntlet_shot (ability), which kills only the nearest thing
in its beam, with a flash and a thunderclap only when it hit something."""

import pytest

from vfg_engine.io import AddModifier, Damage, Message, Particle, RemoveModifier, Sound

SNIPER = ("super_sniper", "sniper_shot")
GAUNTLET = ("infinity_gauntlet", "infinity_gauntlet_shot")
GROUP = SNIPER + GAUNTLET


def health(r, uuid):
    return r.io.by_uuid[uuid].health


def stage_wall(r):
    """Steve at 0 64 0 facing north (-z); a wall 5 ahead (a few blocks wide, so a straight-down-z
    look lands on it despite yaw 180's own float noise); a zombie before it (level with Steve's
    eyes: a headshot), one behind it, one off to the side of a 0.8-wide line."""
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    for y in (64, 65, 66):
        for x in (-1, 0, 1):
            io.set_block("minecraft:overworld", (x, y, -5), "minecraft:stone")
    return {
        "near": io.add_entity("minecraft:zombie", (0.0, 64.0, -3.0), health=1000),
        "behind_wall": io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0), health=1000),
        "aside": io.add_entity("minecraft:zombie", (3.0, 64.0, -3.0), health=1000),
    }


def stage_gauntlet(r):
    """Steve at 0 64 0 facing north (-z); two zombies in line, one aside, one past its 80 blocks."""
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    return {
        "first": io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0), health=1000),
        "second": io.add_entity("minecraft:zombie", (0.0, 64.0, -20.0), health=1000),
        "aside": io.add_entity("minecraft:zombie", (3.0, 64.0, -10.0), health=1000),
        "beyond": io.add_entity("minecraft:cow", (0.0, 64.0, -100.0), health=1000),
    }


def test_the_group_loads(rig):
    r = rig(*GROUP)
    loaded = r.start()["loaded"]
    assert sorted(loaded) == sorted(GROUP)


# ---- super_sniper / sniper_shot -------------------------------------------------------------

def test_sniper_fires_and_stops_at_the_first_wall(rig):
    r = rig(*SNIPER)
    r.start()
    mobs = stage_wall(r)
    assert r.op("give", player="Steve", item="super_sniper")["ok"]
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert health(r, mobs["near"]) == 1000 - 300          # a headshot, level with Steve's eyes: 150 x 2
    assert health(r, mobs["behind_wall"]) == 1000         # stopped by the wall (through_walls off)
    assert health(r, mobs["aside"]) == 1000               # 3 blocks off a 0.8-wide line
    hits = r.sent(Damage)
    assert {d.damage_type for d in hits} == {"minecraft:player_attack"} and {d.by for d in hits} == {"Steve"}
    assert any(m.where == "actionbar" and m.text == "HEADSHOT" for m in r.sent(Message))
    assert any(s.name == "minecraft:entity.firework_rocket.blast_far" for s in r.sent(Sound))
    assert any(p.name == "minecraft:end_rod" for p in r.sent(Particle))
    assert r.problems() == []


def test_sniper_reaches_far_but_what_is_mobs_so_it_spares_a_player(rig):
    r = rig(*SNIPER)
    r.start()
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    io.join("Alex", pos=(0.0, 64.0, -5.0))                # right on the line
    far = io.add_entity("minecraft:zombie", (0.0, 64.0, -390.0), health=1000)
    beyond = io.add_entity("minecraft:cow", (0.0, 64.0, -450.0), health=1000)
    r.op("give", player="Steve", item="super_sniper")
    r.tick()
    io.use("Steve")
    r.tick(2)
    assert health(r, far) < 1000                          # within its 400 blocks
    assert health(r, beyond) == 1000                       # past its 400 blocks
    assert io.players["Alex"].health == 20                 # what=mobs: never a player, even on the line
    assert r.problems() == []


def test_within_the_cooldown_a_click_only_says_so(rig):
    r = rig(*SNIPER)
    r.start()
    mobs = stage_wall(r)
    r.op("give", player="Steve", item="super_sniper")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    before = health(r, mobs["near"])
    r.io.use("Steve")                                     # 0.2 s later: within the 0.5 s cooldown
    r.tick(2)
    assert health(r, mobs["near"]) == before
    assert any(m.where == "actionbar" and "Super Sniper: recharging" in m.text for m in r.sent(Message))
    r.run_for(1)
    r.io.use("Steve")                                      # a second on: it fires again
    r.tick(2)
    assert health(r, mobs["near"]) < before
    assert r.problems() == []


def test_the_sniper_shot_cast_by_voice(rig):
    r = rig(*SNIPER)
    r.start()
    mobs = stage_wall(r)
    reply = r.cast("Steve", "sniper_shot")
    assert reply["status"] == "fired", reply
    r.tick(2)
    assert health(r, mobs["near"]) == 1000 - 300
    assert health(r, mobs["behind_wall"]) == 1000


def test_crouching_with_the_sniper_zooms_in_and_standing_unzooms(rig):
    r = rig(*SNIPER)
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.op("give", player="Steve", item="super_sniper")
    r.tick()
    r.io.sneak("Steve", True)
    r.tick()
    added = [m for m in r.sent(AddModifier) if m.target == "Steve" and m.attribute == "minecraft:movement_speed"]
    assert added, r.sent(AddModifier)
    assert added[-1].amount == pytest.approx(-0.6) and added[-1].operation == "add_multiplied_total"
    r.io.sneak("Steve", False)
    r.tick()
    removed = [m for m in r.sent(RemoveModifier) if m.target == "Steve" and m.attribute == "minecraft:movement_speed"]
    assert removed
    assert r.problems() == []


def test_letting_go_of_the_sniper_takes_the_zoom_off_too(rig):
    r = rig(*SNIPER)
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.op("give", player="Steve", item="super_sniper")
    r.tick()
    r.io.sneak("Steve", True)
    r.tick()
    r.io.unhold("Steve")
    r.tick(2)
    removed = [m for m in r.sent(RemoveModifier) if m.target == "Steve" and m.attribute == "minecraft:movement_speed"]
    assert removed
    assert r.problems() == []


# ---- infinity_gauntlet / infinity_gauntlet_shot ---------------------------------------------

def test_gauntlet_kills_only_the_nearest_thing_in_a_beam_of_flame(rig):
    r = rig(*GAUNTLET)
    r.start()
    mobs = stage_gauntlet(r)
    assert r.op("give", player="Steve", item="infinity_gauntlet")["ok"]
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert mobs["first"] not in r.io.by_uuid               # killed outright (generic_kill)
    assert health(r, mobs["second"]) == 1000                # only the first thing in the beam
    assert health(r, mobs["aside"]) == 1000                 # 3 blocks off a 1.5-wide line
    assert health(r, mobs["beyond"]) == 1000                 # past its 80 blocks
    kills = r.sent(Damage)
    assert any(d.damage_type == "minecraft:generic_kill" and d.by == "Steve" for d in kills)
    flame = [p for p in r.sent(Particle) if p.name == "minecraft:flame"]
    assert any(p.count == 150 for p in flame)                # the burst where it hit
    assert any(p.name == "minecraft:explosion_emitter" for p in r.sent(Particle))
    assert any(s.name == "minecraft:entity.lightning_bolt.thunder" for s in r.sent(Sound))
    assert r.problems() == []


def test_gauntlet_shows_no_burst_when_nothing_is_hit(rig):
    r = rig(*GAUNTLET)
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)     # nothing stands in its way
    r.op("give", player="Steve", item="infinity_gauntlet")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert not any(p.name == "minecraft:explosion_emitter" for p in r.sent(Particle))
    assert not any(s.name == "minecraft:entity.lightning_bolt.thunder" for s in r.sent(Sound))
    assert any(p.name == "minecraft:flame" for p in r.sent(Particle))   # the beam itself still shows
    assert any(s.name == "minecraft:entity.blaze.shoot" for s in r.sent(Sound))
    assert r.problems() == []


def test_gauntlet_has_no_cooldown_by_default(rig):
    r = rig(*GAUNTLET)
    r.start()
    mobs = stage_gauntlet(r)
    r.op("give", player="Steve", item="infinity_gauntlet")
    r.tick()
    r.io.use("Steve")
    r.tick(1)
    assert mobs["first"] not in r.io.by_uuid
    r.io.use("Steve")                                       # right away: no cooldown
    r.tick(1)
    assert mobs["second"] not in r.io.by_uuid               # the next nearest is gone too
    assert not any("recharging" in m.text for m in r.sent(Message))
    assert r.problems() == []


def test_gauntlet_what_is_everything_so_it_can_hit_a_player(rig):
    r = rig(*GAUNTLET)
    r.start()
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    io.join("Alex", pos=(0.0, 64.0, -10.0))
    r.op("give", player="Steve", item="infinity_gauntlet")
    r.tick()
    io.use("Steve")
    r.tick(2)
    assert not io.players["Alex"].alive
    assert r.problems() == []


def test_the_gauntlet_shot_cast_by_voice(rig):
    r = rig(*GAUNTLET)
    r.start()
    mobs = stage_gauntlet(r)
    reply = r.cast("Steve", "infinity_gauntlet_shot")
    assert reply["status"] == "fired", reply
    r.tick(2)
    assert mobs["first"] not in r.io.by_uuid
