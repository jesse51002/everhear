"""The teleport and summon_around handlers, run as EC2 runs them (through remote/action.py
on python3 -) against the fake RCON server answering as a FakeWorld."""
from __future__ import annotations

import math
import re

import pytest

from conftest import FakeWorld

OW, NETHER = "minecraft:overworld", "minecraft:the_nether"


def one_player(name: str = "Steve", **more) -> FakeWorld:
    w = FakeWorld()
    w.add(name, (10.5, 64.0, -3.5))
    for name, pos in more.items():
        w.add(name, pos)
    return w


def sent(w: FakeWorld, start: str) -> list[str]:
    return [c for c in w.sent if c.startswith(start) or (" run " + start) in c]


def tp(serve, w, **args):
    r = serve(w).handler("teleport", {"dimension": "overworld", "target": "", **args})
    assert r.code == 0, r.stderr
    return r.out


# ------------------------------------------------------------------ teleport

def test_biome_found_lands_and_reports(serve):
    w = one_player()
    w.located[("biome", "minecraft:snowy_taiga")] = (-368, 64, 912)
    w.landings = [(-368, 70, 912)]
    out = tp(serve, w, player="Steve", to="biome", target="snowy_taiga")
    assert out["ok"] is True
    assert out["summary"] == "teleported Steve to a snowy taiga at -368 70 912"
    assert out["pos"] == [-368, 70, 912] and out["ring"] == 8 and out["dim"] == OW
    assert sent(w, "locate") == ["execute in minecraft:overworld positioned 10 0 -4 run locate biome "
                                 "minecraft:snowy_taiga"]
    assert sent(w, "effect") == ["effect give Steve minecraft:resistance 5 4 true",
                                 "effect give Steve minecraft:slow_falling 5 0 true"]
    assert sent(w, "spreadplayers") == ["execute in minecraft:overworld run spreadplayers -368 912 0 8 under 320 "
                                        "false Steve"]
    # The cushion comes before the move.
    assert w.sent.index("effect give Steve minecraft:slow_falling 5 0 true") < \
        w.sent.index(sent(w, "spreadplayers")[0])
    assert not sent(w, "spawnpoint")  # 990 blocks: under 1,000


def test_a_tagged_structure(serve):
    w = one_player()
    w.located[("structure", "#minecraft:village")] = (-544, 0, 320, "minecraft:village_plains")
    out = tp(serve, w, player="Steve", to="structure", target="#village")
    assert out["summary"] == "teleported Steve to a plains village at -544 64 320"


def test_locate_finding_nothing_fails_before_anything_moves(serve):
    w = one_player()
    out = tp(serve, w, player="Steve", to="biome", target="cherry_grove")
    assert out == {"ok": False, "summary": "no cherry grove within reach of Steve"}
    assert not sent(w, "effect") and not sent(w, "spreadplayers")


def test_an_unknown_id(serve):
    w = one_player()
    w.located[("structure", "minecraft:castle")] = "invalid"
    w.located[("biome", "minecraft:nonsense")] = "invalid"
    assert tp(serve, w, player="Steve", to="structure", target="castle")["summary"] == \
        "Minecraft has no structure called minecraft:castle"
    assert tp(serve, w, player="Steve", to="biome", target="nonsense")["summary"] == \
        "Minecraft has no biome called minecraft:nonsense"


def test_a_landing_in_water_lands_again_at_16(serve):
    w = one_player()
    w.located[("biome", "minecraft:cherry_grove")] = (812, 70, -340)
    w.landings = [(812, 63, -340), (820, 71, -330)]
    w.block(OW, 812, 62, -340, "minecraft:water")
    out = tp(serve, w, player="Steve", to="biome", target="cherry_grove")
    assert out["ok"] is True and out["ring"] == 16
    assert out["summary"] == "teleported Steve to a cherry grove at 820 71 -330"
    assert [re.search(r" 0 (\d+) under", c).group(1) for c in sent(w, "spreadplayers")] == ["8", "16"]
    assert len([c for c in w.sent if " unless block ~ ~-1 ~ minecraft:water" in c]) == 2


@pytest.mark.parametrize("bad", ["minecraft:magma_block", "minecraft:cactus", "minecraft:powder_snow",
                                 "minecraft:soul_campfire", "minecraft:sweet_berry_bush",
                                 "minecraft:pointed_dripstone", "minecraft:lava"])
def test_every_unsafe_ground_lands_again(serve, bad):
    w = one_player()
    w.landings = [(100, 64, 100), (130, 64, 130)]
    w.block(OW, 100, 63, 100, bad)
    out = tp(serve, w, player="Steve", to="coords", target="100 100")
    assert out["ok"] is True and out["ring"] == 16 and out["pos"] == [130, 64, 130]


def test_four_bad_rings_fail_and_put_the_player_back(serve):
    w = one_player()
    w.located[("biome", "minecraft:cherry_grove")] = (812, 70, -340)
    w.landings = [(812 + i, 64, -340) for i in range(4)]
    for i in range(4):
        w.block(OW, 812 + i, 63, -340, "minecraft:magma_block")
    out = tp(serve, w, player="Steve", to="biome", target="cherry_grove")
    assert out == {"ok": False, "summary": "no safe spot within 4 rings of a cherry grove at 812 -340; "
                                           "Steve is back where they were"}
    assert [re.search(r" 0 (\d+) under", c).group(1) for c in sent(w, "spreadplayers")] == ["8", "16", "32", "64"]
    assert sent(w, "tp ") == ["execute in minecraft:overworld run tp Steve 10.500 64.000 -3.500"]
    assert w.players["Steve"].pos == [10.5, 64.0, -3.5]


def test_no_room_at_all_fails_without_moving(serve):
    w = one_player()
    w.landings = ["fail"] * 4
    out = tp(serve, w, player="Steve", to="coords", target="-96 -320")
    assert out == {"ok": False, "summary": "no safe spot within 4 rings of -96 -320"}
    assert not sent(w, "tp ")


def test_a_saved_place_is_reached_exactly_when_the_spot_is_safe(serve):
    w = one_player()
    w.save_place("castle", [197, 68, 251], by="T23")
    out = tp(serve, w, player="Steve", to="place", target="castle")
    assert out["summary"] == "teleported Steve to castle at 197 68 251"
    assert out["target"] == "castle" and out["ring"] == 0
    assert w.players["Steve"].pos == [197.5, 68.0, 251.5]  # the block's centre, not 8 blocks off
    [check] = [c for c in w.sent if " positioned 197 68 251 " in c]
    assert "unless block ~ ~-1 ~ #minecraft:replaceable" in check and "unless block ~ ~-1 ~ minecraft:magma_block" in check
    assert "if block ~ ~ ~ #minecraft:replaceable" in check and "if block ~ ~1 ~ #minecraft:replaceable" in check
    assert "unless block ~ ~1 ~ minecraft:water" in check
    assert sent(w, "tp ") == ["execute in minecraft:overworld run tp Steve 197.5 68.0 251.5"]
    assert not sent(w, "spreadplayers")
    assert tp(serve, w, player="Steve", to="place", target="nowhere") == \
        {"ok": False, "summary": "there is no saved place called nowhere"}


@pytest.mark.parametrize("block,at", [("minecraft:lava", 0), ("minecraft:water", 1), ("minecraft:magma_block", -1),
                                      ("minecraft:air", -1)])
def test_an_unsafe_saved_place_falls_back_to_the_rings(serve, block, at):
    w = one_player()
    w.save_place("castle", [197, 68, 251], by="T23")
    w.block(OW, 197, 68 + at, 251, block)
    w.block(OW, 197, 69 + at, 251, block)  # the spot one block up is no better
    w.landings = [(199, 68, 253)]
    out = tp(serve, w, player="Steve", to="place", target="castle")
    assert out["summary"] == "teleported Steve to castle at 199 68 253" and out["ring"] == 8
    # A known height: land at or below it, not on the roof.
    assert sent(w, "spreadplayers") == ["execute in minecraft:overworld run spreadplayers 197 251 0 8 under 70 "
                                        "false Steve"]
    assert not [c for c in sent(w, "tp ") if " tp Steve 197.5" in c]


def test_a_saved_place_on_a_slab_lands_on_top_of_it(serve):
    w = one_player()
    w.save_place("hall", [10, 64, 10])
    w.block(OW, 10, 64, 10, "minecraft:oak_slab")  # where the player stood: half a block up
    out = tp(serve, w, player="Steve", to="place", target="hall")
    assert w.players["Steve"].pos == [10.5, 65.0, 10.5] and out["ring"] == 0
    assert not sent(w, "spreadplayers")


def test_a_saved_place_in_the_nether(serve):
    w = one_player()
    w.save_place("fortress", [-400, 70, -96], dim=NETHER)
    out = tp(serve, w, player="Steve", to="place", target="fortress")
    assert out["summary"] == "teleported Steve to fortress at -400 70 -96 in the nether"
    assert sent(w, "tp ") == ["execute in minecraft:the_nether run tp Steve -399.5 70.0 -95.5"]
    assert out["dim"] == NETHER and w.players["Steve"].dim == NETHER
    assert not sent(w, "spawnpoint")  # another dimension: the spawn point stays


def test_everyone(serve):
    w = one_player("Sam", Steve=(0.5, 70.0, 0.5), Alex=(30.5, 65.0, 30.5))
    w.located[("biome", "minecraft:snowy_taiga")] = (-1408, 64, 1408)
    w.landings = [(-1408, 68, 1410)]
    out = tp(serve, w, player="@a", to="biome", target="snowy_taiga")
    assert out["ok"] is True
    assert out["summary"] == ("teleported everyone (Sam, Steve, Alex) to a snowy taiga at -1408 68 1410; "
                              "spawn point set there")
    assert out["players"] == ["Sam", "Steve", "Alex"]
    assert sent(w, "effect") == ["effect give @a minecraft:resistance 5 4 true",
                                 "effect give @a minecraft:slow_falling 5 0 true"]
    assert len(sent(w, "spreadplayers")) == 1
    assert sent(w, "tp ") == ["tp Steve Sam", "tp Alex Sam"]
    for p in w.players.values():
        assert p.pos == [-1407.5, 68.0, 1410.5]
        assert p.spawnpoint == (-1408, 68, 1410, OW)
    assert sorted(out["spawn_set"]) == ["Alex", "Sam", "Steve"]


def test_everyone_with_nobody_online(serve):
    w = FakeWorld()
    assert tp(serve, w, player="@a", to="far", target="1000") == {"ok": False, "summary": "nobody is online"}


def test_coords(serve):
    w = one_player()
    out = tp(serve, w, player="Steve", to="coords", target="100 -200")
    assert out["summary"] == "teleported Steve to 100 64 -200"
    assert sent(w, "spreadplayers")[0].endswith("spreadplayers 100 -200 0 8 under 320 false Steve")
    w2 = one_player()
    tp(serve, w2, player="Steve", to="coords", target="100 30 -200")
    assert "under 32 " in sent(w2, "spreadplayers")[0]


def test_far_lands_that_far_away_and_sets_the_spawn_point(serve):
    w = one_player()
    out = tp(serve, w, player="Steve", to="far", target="5000")
    assert out["ok"] is True
    m = re.search(r"spreadplayers (-?\d+) (-?\d+) 0 8 under 320", sent(w, "spreadplayers")[0])
    assert abs(math.hypot(int(m.group(1)) - 10.5, int(m.group(2)) + 3.5) - 5000) < 2
    assert out["summary"].startswith("teleported Steve 5000 blocks away, to ")
    assert out["summary"].endswith("; spawn point set there")
    x, y, z = out["pos"]
    assert sent(w, "spawnpoint") == ["execute in minecraft:overworld run spawnpoint Steve %d %d %d" % (x, y, z)]


def test_far_in_the_nether_looks_below_the_roof(serve):
    w = one_player()
    tp(serve, w, player="Steve", to="far", target="200", dimension="the_nether")
    assert re.match(r"^execute in minecraft:the_nether run spreadplayers -?\d+ -?\d+ 0 8 under 120 false Steve$",
                    sent(w, "spreadplayers")[0])


def test_a_nether_biome_is_looked_for_from_the_scaled_position(serve):
    w = FakeWorld()
    w.add("Steve", (800.5, 64.0, -1600.5))
    w.located[("biome", "minecraft:crimson_forest")] = (288, 1, 288)
    out = tp(serve, w, player="Steve", to="biome", target="crimson_forest", dimension="the_nether")
    assert sent(w, "locate") == ["execute in minecraft:the_nether positioned 100 0 -201 run locate biome "
                                 "minecraft:crimson_forest"]
    assert "under 120" in sent(w, "spreadplayers")[0]
    assert out["summary"] == "teleported Steve to a crimson forest at 288 64 288 in the nether"


def test_spawn_point_with_and_without_a_bed(serve):
    w = one_player()
    w.players["Steve"].respawn = (50, 70, 60, OW)
    out = tp(serve, w, player="Steve", to="spawn_point")
    assert out["summary"] == "teleported Steve to their spawn point at 50 70 60"  # exactly
    assert not sent(w, "spreadplayers")
    w2 = one_player()
    w2.world_spawn = (20, 80, -20)
    out = tp(serve, w2, player="Steve", to="spawn_point")
    assert out["summary"] == "teleported Steve to the world spawn at 20 64 -20"
    # RCON's commands run at the world spawn: spreadplayers centres on `~ ~`.
    assert sent(w2, "spreadplayers") == ["spreadplayers ~ ~ 0 8 under 320 false Steve"]


def test_spawn_point_never_moves_the_spawn_point(serve):
    w = one_player()
    w.players["Steve"].respawn = (5000, 70, 5000, OW)
    out = tp(serve, w, player="Steve", to="spawn_point")
    assert out["ok"] is True and not sent(w, "spawnpoint")


def test_last_death_with_and_without_one(serve):
    w = one_player()
    w.players["Steve"].death = (-20, 30, 40, NETHER)
    w.block(NETHER, -20, 30, 40, "minecraft:lava")  # died in lava: not there, round it
    out = tp(serve, w, player="Steve", to="last_death")
    assert out["summary"] == "teleported Steve to where they last died, at -20 64 40 in the nether"
    assert sent(w, "spreadplayers") == ["execute in minecraft:the_nether run spreadplayers -20 40 0 8 under 32 "
                                        "false Steve"]
    w2 = one_player()
    assert tp(serve, w2, player="Steve", to="last_death") == \
        {"ok": False, "summary": "Steve has no death on record"}
    assert not sent(w2, "spreadplayers")


def test_an_offline_player(serve):
    w = one_player("Alex")  # Steve is not in the world
    out = tp(serve, w, player="Steve", to="coords", target="0 0")
    # `offline` tells the dispatcher to drop the pick at once, with no planner call
    assert out == {"ok": False, "summary": "Steve is not online", "offline": ["Steve"]}
    assert w.sent == ["data get entity Steve Pos"]


# ------------------------------------------------------------------ summon_around

def summon(serve, w, **args):
    full = {"count": 10, "radius": 16, "scale": 1.0, "name": "", "glowing": False, **args}
    r = serve(w).handler("summon_around", full, task="T7")
    assert r.code == 0, r.stderr
    return r.out


def test_summon_in_batches_of_ten(serve):
    w = FakeWorld()
    w.add("Steve", (197.5, 68.0, 251.5))
    out = summon(serve, w, players="Steve", mob="zombie", count=25)
    assert out["ok"] is True
    assert out["summary"] == "summoned 25 zombies round Steve at 197 68 251"
    assert out["summoned"] == 25 and out["standing"] == 25
    summons = sent(w, "summon")
    assert len(summons) == 25
    assert summons[0] == ('execute in minecraft:overworld positioned 197.500 68.000 251.500 run summon '
                          'minecraft:zombie ~ ~ ~ {Tags:["vfg","vfg_summon_T7","vfg_summon_T7_new"]}')
    spreads = sent(w, "spreadplayers")
    assert spreads == ["execute in minecraft:overworld run spreadplayers 197.500 251.500 2 16 under 76 false "
                       "@e[tag=vfg_summon_T7_new]"] * 3
    # Ten summons, then their spread; the batch tag comes off after each.
    i = w.sent.index(spreads[0])
    assert sum(1 for c in w.sent[:i] if " summon " in c) == 10
    assert sent(w, "tag ") == ["tag @e[tag=vfg_summon_T7_new] remove vfg_summon_T7_new"] * 3
    assert all(e["tags"] == {"vfg", "vfg_summon_T7"} for e in w.entities)


def test_summon_a_hundred(serve):
    w = FakeWorld()
    w.add("Steve", (197.5, 68.0, 251.5))
    out = summon(serve, w, players="Steve", mob="minecraft:zombie", count=100)
    assert out["summary"] == "summoned 100 zombies round Steve at 197 68 251"
    assert len(sent(w, "spreadplayers")) == 10 and len(w.entities) == 100


def test_summon_round_everyone(serve):
    w = FakeWorld()
    w.add("Alex", (197.5, 68.0, 251.5))
    w.add("Steve", (10.2, 64.0, -3.7), dim=NETHER)
    out = summon(serve, w, players="@a", mob="sheep", count=3)
    assert out["summary"] == "summoned 3 sheep round each of Alex at 197 68 251 and Steve at 10 64 -4"
    assert any(c.startswith("execute in minecraft:the_nether positioned 10.200 64.000 -3.700 run summon")
               for c in w.sent)
    assert out["standing"] == 6


def test_summon_options(serve):
    w = FakeWorld()
    w.add("Steve", (0.5, 64.0, 0.5))
    out = summon(serve, w, players="Steve", mob="wolf", count=2, radius=4, scale=2.0, name='Bob "Fang"',
                 glowing=True)
    assert out["summary"] == 'summoned 2 wolves round Steve at 0 64 0 (named Bob "Fang", glowing, 2x size)'
    nbt = w.entities[0]["nbt"]
    assert 'CustomName:"Bob \\"Fang\\"",CustomNameVisible:1b' in nbt
    assert "Glowing:1b" in nbt and 'attributes:[{id:"minecraft:scale",base:2.0d}]' in nbt
    assert w.entities[0]["name"] == 'Bob "Fang"'
    assert " 1 4 under " in sent(w, "spreadplayers")[0]  # a small radius packs them closer


def test_summon_spread_that_fails_is_tried_closer(serve):
    w = FakeWorld()
    w.add("Steve", (0.5, 64.0, 0.5))
    w.spread_fail = 1
    out = summon(serve, w, players="Steve", mob="cow", count=5)
    assert out["ok"] is True
    assert [c.split()[7] for c in sent(w, "spreadplayers")] == ["2", "0"]


def test_summon_refusals(serve):
    w = FakeWorld(peaceful=True)
    w.add("Alex", (0.5, 64.0, 0.5))
    assert summon(serve, w, players="Alex", mob="zombie") == \
        {"ok": False, "summary": "could not summon zombie: Monsters cannot be summoned in Peaceful difficulty"}
    out = summon(serve, w, players="Alex", mob="nonsense")
    assert out["ok"] is False and out["summary"].startswith("could not summon nonsense: Can't find element")
    assert summon(serve, w, players="Alex", mob="player") == \
        {"ok": False, "summary": "could not summon player: Can't summon entity of type minecraft:player"}
    assert summon(serve, w, players="Steve", mob="cow") == \
        {"ok": False, "summary": "Steve is not online", "offline": ["Steve"]}
    assert summon(serve, FakeWorld(), players="@a", mob="cow") == {"ok": False, "summary": "nobody is online"}
    assert not sent(w, "spreadplayers")


def test_a_tampered_place_is_not_used(serve):
    w = one_player()
    w.storage["evil"] = {"pos": [1, 2, 3], "by": "", "dim": "minecraft:overworld run fill ~ ~ ~ ~9 ~9 ~9 tnt"}
    out = tp(serve, w, player="Steve", to="place", target="evil")
    assert out["ok"] is False
    assert not sent(w, "spreadplayers")
