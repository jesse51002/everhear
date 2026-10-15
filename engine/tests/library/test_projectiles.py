"""The projectiles group on FakeIo: boom_bow and boom_trident (with spear_bomb as its own Config)
explode a breaking blast where a player's shot lands or hits, sparing the thrower, spear_bomb
carving a crater too; wither_wand and summoning_staff (its own Config) summon mobs at safe spots
where a player looks, holding mob_griefing off while any of that cast's live, restoring it once
none do; meteor_staff's cast drops a falling meteor onto the block looked at, up to its range,
then explodes."""

import math

from vfg_engine.io import GiveEffect, Summon

GROUP = ("boom_bow", "boom_trident", "spear_bomb", "wither_wand", "wither_wand_summon",
        "summoning_staff", "summoning_staff_summon", "meteor_staff", "meteor_strike")


def steve(r):
    """Steve at 0 64 0, facing north (-z); FakeIo's ground is stone up to y 63."""
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.tick()


def tnt(r):
    return [s for s in r.sent(Summon) if s.entity_type == "minecraft:tnt"]


def block(r, x, y, z):
    return r.io.block_at("minecraft:overworld", x, y, z)


def test_the_group_loads(rig):
    r = rig(*GROUP)
    assert sorted(r.start()["loaded"]) == sorted(GROUP)


# ---- boom_bow --------------------------------------------------------------------------------

def test_boom_bow_explodes_where_an_arrow_lands_sparing_the_shooter(rig):
    r = rig("boom_bow")
    r.start()
    steve(r)
    assert r.op("give", player="Steve", item="boom_bow")["ok"]
    r.tick()
    r.io.land("Steve", item="boom_bow", pos=(10.0, 64.0, -10.0))
    r.tick(2)
    made = tnt(r)
    assert len(made) == 1
    assert made[0].pos == (10.0, 64.0, -10.0)
    assert "explosion_power:8" in made[0].nbt and "fuse:0s" in made[0].nbt
    # the shooter is shielded from its own blast, never blamed for it
    assert any(e.target == "Steve" and e.effect == "minecraft:resistance" for e in r.sent(GiveEffect))
    assert r.problems() == []


def test_boom_bow_hitting_an_entity_also_explodes(rig):
    r = rig("boom_bow")
    r.start()
    steve(r)
    z = r.io.add_entity("minecraft:zombie", (5.0, 64.0, -5.0), health=100)
    r.op("give", player="Steve", item="boom_bow")
    r.tick()
    r.io.land("Steve", item="boom_bow", pos=(5.0, 64.0, -5.0), target=z, target_type="minecraft:zombie")
    r.tick(2)
    assert len(tnt(r)) == 1
    assert r.problems() == []


# ---- boom_trident / spear_bomb ----------------------------------------------------------------

def test_boom_trident_explodes_where_it_lands_with_no_crater(rig):
    r = rig("boom_trident")
    r.start()
    steve(r)
    r.op("give", player="Steve", item="boom_trident")
    r.tick()
    r.io.land("Steve", item="boom_trident", pos=(10.0, 63.0, -10.0), projectile_type="minecraft:trident")
    r.tick(3)
    made = tnt(r)
    assert len(made) == 1 and "explosion_power:6" in made[0].nbt
    # no crater: the stone at its feet stands, since FakeIo never simulates the TNT's own blast
    assert block(r, 10, 63, -10) == "minecraft:stone"
    assert r.problems() == []


def test_spear_bomb_also_carves_a_crater_where_it_lands(rig):
    r = rig("spear_bomb")
    r.start()
    steve(r)
    r.op("give", player="Steve", item="spear_bomb")
    r.tick()
    r.io.land("Steve", item="spear_bomb", pos=(10.0, 63.0, -10.0), projectile_type="minecraft:trident")
    r.tick(5)
    made = tnt(r)
    assert len(made) == 1 and "explosion_power:6" in made[0].nbt
    assert block(r, 10, 63, -10) == "minecraft:air"           # the crater carved the ground away
    assert r.problems() == []


# ---- wither_wand / summoning_staff -------------------------------------------------------------

def test_wither_wand_summons_withers_where_steve_looks_holding_griefing_off(rig):
    r = rig("wither_wand", "wither_wand_summon")
    r.start()
    steve(r)
    assert r.io.gamerules["mob_griefing"] == "true"
    r.op("give", player="Steve", item="wither_wand")
    r.tick()
    r.io.use("Steve")
    r.tick(3)
    made = [s for s in r.sent(Summon) if s.entity_type == "minecraft:wither"]
    assert len(made) == 5
    for s in made:
        assert "PersistenceRequired:1b" in s.nbt
        assert abs(s.pos[0]) <= 3 + 1 and abs(s.pos[2] - (-100.0)) <= 3 + 1    # near 100 blocks ahead
    assert r.io.gamerules["mob_griefing"] == "false"
    rec = r.records(cid="wither_wand_summon")[0]
    assert rec.runs                                          # the cast's run is still going
    for s in made:
        r.io.kill_entity(s.uuid)
    r.run_for(2)
    assert not r.records(cid="wither_wand_summon")[0].runs    # the run ended once its withers were gone
    assert r.io.gamerules["mob_griefing"] == "true"           # back once none is left
    assert r.problems() == []


def test_summoning_staff_summons_zombies_instead(rig):
    r = rig("summoning_staff", "summoning_staff_summon")
    r.start()
    steve(r)
    r.op("give", player="Steve", item="summoning_staff")
    r.tick()
    r.io.use("Steve")
    r.tick(3)
    made = r.sent(Summon)
    assert len(made) == 5 and {s.entity_type for s in made} == {"minecraft:zombie"}
    assert r.io.gamerules["mob_griefing"] == "false"
    assert r.problems() == []


def test_wither_wand_casts_by_voice_too(rig):
    r = rig("wither_wand", "wither_wand_summon")
    r.start()
    steve(r)
    reply = r.cast("Steve", "wither_wand_summon")
    assert reply["status"] == "fired", reply
    r.tick(2)
    assert len([s for s in r.sent(Summon) if s.entity_type == "minecraft:wither"]) == 5
    assert r.problems() == []


# ---- meteor_staff -------------------------------------------------------------------------------

def test_meteor_staff_drops_a_meteor_where_steve_looks_then_blasts(rig):
    r = rig("meteor_staff", "meteor_strike")
    r.start()
    steve(r)
    for x in (-1, 0):                                     # yaw 180's look drifts a hair off x=0
        r.io.set_block("minecraft:overworld", (x, 65, -20), "minecraft:stone")
    r.op("give", player="Steve", item="meteor_staff")
    r.tick()
    r.io.use("Steve")
    r.tick(1)
    displays = [s for s in r.sent(Summon) if s.entity_type == "minecraft:block_display"]
    assert len(displays) == 19                              # a 3x3x3 cluster without its corners
    r.run_for(4)                                             # fall_s 2.5 s, then its blast
    made = tnt(r)
    assert len(made) == 5                                    # "plus": the point and four more
    assert all("explosion_power:4" in s.nbt for s in made)
    centre = next(s for s in made if math.isclose(s.pos[2], -19.5))
    assert math.isclose(centre.pos[1], 66.0)                 # on top of the block it struck
    assert r.problems() == []


def test_meteor_staffs_no_target_in_range_refuses_in_words(rig):
    r = rig("meteor_staff", "meteor_strike")
    r.start()
    steve(r)                                                # open sky ahead: nothing within 120 blocks
    r.op("give", player="Steve", item="meteor_staff")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert not tnt(r)
    from vfg_engine.io import Message
    assert any("No target in range" in m.text for m in r.sent(Message))
    assert r.problems() == []


def test_meteor_strike_cast_by_voice_too(rig):
    r = rig("meteor_staff", "meteor_strike")
    r.start()
    steve(r)
    for x in (-1, 0):
        r.io.set_block("minecraft:overworld", (x, 65, -20), "minecraft:stone")
    reply = r.cast("Steve", "meteor_strike")
    assert reply["status"] == "fired", reply
    r.run_for(4)
    assert len(tnt(r)) == 5
    assert r.problems() == []
