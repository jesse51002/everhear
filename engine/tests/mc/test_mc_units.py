"""vfg_engine.mc's parsing and translation, without a server. The replies are 26.3's own, recorded on
a realmc server on 2026-10-12 (engine/MINECRAFT.md)."""

import math
import os
import re

import pytest

from vfg_engine import bases
from vfg_engine import io as I
from vfg_engine.mc import commands as C
from vfg_engine.mc import reading as R
from vfg_engine.mc import snbt

STEVE = [-2040022415, -1202044924, -1353431686, -1758138665]
STEVE_UUID = "8667ba71-b85a-4004-af54-457a9734eed7"
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ---- snbt ----------------------------------------------------------------------------------------

def test_snbt_reads_what_data_get_prints():
    reply = ('Storage everhear:io has the following contents: {over: 0, q: [{d: {item: "fire_staff", '
             'count: 1, slot: "mainhand"}, t: 17795, u: [I; -2040022415, -1202044924, -1353431686, '
             '-1758138665], k: "use", n: 1}], len: 1, last: 1}')
    doc = snbt.data_reply(reply)
    assert doc == {"over": 0, "len": 1, "last": 1, "q": [
        {"d": {"item": "fire_staff", "count": 1, "slot": "mainhand"}, "t": 17795, "u": STEVE, "k": "use", "n": 1}]}


def test_snbt_types_and_quoting():
    doc = snbt.parse('{a: 1b, b: 2s, c: 3L, d: 1.5f, e: -2.0d, f: 6.0E7d, g: [B; 1b, 0b], '
                     '"minecraft:custom_data": {x: \'say "hi"\'}, h: "a\\"b\\\\c\\n", i: [], j: {}, k: 0.5}')
    assert doc == {"a": 1, "b": 2, "c": 3, "d": 1.5, "e": -2.0, "f": 6.0e7, "g": [1, 0],
                   "minecraft:custom_data": {"x": 'say "hi"'}, "h": 'a"b\\c\n', "i": [], "j": {}, "k": 0.5}


def test_snbt_entity_reply_and_no_data():
    assert snbt.data_reply("Zed has the following entity data: 19.06f") == pytest.approx(19.06)
    assert snbt.data_reply('Named Husk has the following entity data: ["a", "b"]') == ["a", "b"]
    with pytest.raises(snbt.NoData):
        snbt.data_reply("Found no elements matching version")
    with pytest.raises(snbt.SnbtError):
        snbt.data_reply("No entity was found")


# ---- uuids and numbers ---------------------------------------------------------------------------

def test_uuid_both_ways():
    assert C.uuid_str(STEVE) == STEVE_UUID
    assert C.uuid_ints(STEVE_UUID) == STEVE
    assert C.uuid_str([1, 2, 3, 5]) == "00000001-0000-0002-0000-000300000005"


def test_numbers_without_exponents():
    assert C.num(1.0) == "1" and C.num(0.000001) == "0.000001" and C.num(-0.0) == "0"
    assert C.num(1e7) == "10000000" and C.num(2.5) == "2.5"
    with pytest.raises(C.OpRefused):
        C.num(float("nan"))


# ---- ops into 26.3 commands ----------------------------------------------------------------------

@pytest.mark.parametrize("op, cmds", [
    (I.AddModifier("Steve", "minecraft:scale", "everhear:mark/giant", 1.0, "add_multiplied_total"),
     ["attribute Steve minecraft:scale modifier add everhear:mark/giant 1 add_multiplied_total"]),
    (I.RemoveModifier("Steve", "scale", "everhear:mark/giant"),
     ["attribute Steve minecraft:scale modifier remove everhear:mark/giant"]),
    (I.GiveEffect("Steve", "minecraft:poison", 30, 2), ["effect give Steve minecraft:poison 30 2 false"]),
    (I.GiveEffect("Steve", "speed", float("inf"), 0, particles=False),
     ["effect give Steve minecraft:speed infinite 0 true"]),
    (I.GiveEffect("Steve", "speed", 0.3), ["effect give Steve minecraft:speed 1 0 false"]),
    (I.GiveItem("Steve", "minecraft:carrot_on_a_stick", "fire_staff", "Fire Staff",
                {"enchantment_glint_override": "true"}),
     ['give Steve minecraft:carrot_on_a_stick[custom_data={everhear:{item:"fire_staff"}},'
      'item_name="Fire Staff",enchantment_glint_override=true] 1']),
    (I.TakeItem("Steve", "fire_staff"), ['clear Steve *[custom_data~{everhear:{item:"fire_staff"}}]']),
    (I.Summon("minecraft:husk", "minecraft:overworld", (2.5, 85, 2.5), "00000001-0000-0002-0000-000300000014",
              nbt="{NoAI:1b}", tags=("everhear.e",)),
     ['summon minecraft:husk 2.5 85 2.5 {NoAI:1b,Tags:["everhear.e"],UUID:[I;1,2,3,20]}']),
    (I.Summon("husk", "minecraft:the_nether", (0, 70, 0), "00000001-0000-0002-0000-000300000016"),
     ["execute in minecraft:the_nether run summon minecraft:husk 0 70 0 {UUID:[I;1,2,3,22]}"]),
    (I.Remove("00000001-0000-0002-0000-00030000000C"),
     ['function everhear:remove {u:"00000001-0000-0002-0000-00030000000c"}']),
    (I.Damage("00000001-0000-0002-0000-000300000014", 3, by="Steve"),
     ["damage 00000001-0000-0002-0000-000300000014 3 minecraft:generic by Steve"]),
    (I.Particle("flame", "minecraft:overworld", (0, 86, 0), (0.1, 0.1, 0.1), 0.01, 5),
     ["particle minecraft:flame 0 86 0 0.1 0.1 0.1 0.01 5"]),
    (I.Sound("minecraft:entity.blaze.shoot", "minecraft:overworld", (0, 86, 0)),
     ["playsound minecraft:entity.blaze.shoot master @a 0 86 0 1 1"]),
    (I.Message((), 'hello "q" é'), ['tellraw @a "hello \\"q\\" é"']),
    (I.Message(("Steve", "Alex"), "Cooldown: 3 s", "actionbar"),
     ['title Steve actionbar "Cooldown: 3 s"', 'title Alex actionbar "Cooldown: 3 s"']),
    (I.Teleport("Steve", "minecraft:the_nether", (0.5, 70, 0.5)),
     ["execute in minecraft:the_nether run tp Steve 0.5 70 0.5"]),
    (I.Teleport("Steve", "minecraft:overworld", (0.5, 85, 0.5), pitch=20),
     ["tp Steve 0.5 85 0.5 ~ 20"]),
    (I.Gamerule("mob_griefing", "false"), ["gamerule mob_griefing false"]),
    (I.Command("/time query gametime"), ["time query gametime"]),
    # part 2
    (I.Fill("minecraft:overworld", (0, 84, 0), (2, 86, 2), "stone"), ["fill 0 84 0 2 86 2 stone strict"]),
    (I.Fill("minecraft:overworld", (0, 84, 0), (2, 86, 2), "stone", strict=False), ["fill 0 84 0 2 86 2 stone"]),
    (I.Fill("minecraft:the_nether", (0, 84, 0), (2, 86, 2), "air", replace="#minecraft:logs", mode="destroy"),
     ["execute in minecraft:the_nether run fill 0 84 0 2 86 2 air replace #minecraft:logs destroy"]),
    (I.Fill("minecraft:overworld", (0, 84, 0), (2, 86, 2), "dirt", replace="minecraft:stone"),
     ["fill 0 84 0 2 86 2 dirt replace minecraft:stone strict"]),
    (I.Fill("minecraft:overworld", (0, 84, 0), (4, 88, 4), "glass", mode="hollow"), ["fill 0 84 0 4 88 4 glass hollow"]),
    (I.SetBlock("minecraft:overworld", (1.7, 85, -0.2), "oak_stairs[facing=north]"),
     ["setblock 1 85 -1 oak_stairs[facing=north] strict"]),
    (I.SetBlock("minecraft:overworld", (1, 85, 1), "chest", mode="keep"), ["setblock 1 85 1 chest keep"]),
    (I.Forceload("minecraft:overworld", (64, 64), (79, 79)), ["forceload add 64 64 79 79"]),
    (I.Forceload("minecraft:overworld", (64, 64), (79, 79), add=False), ["forceload remove 64 64 79 79"]),
    (I.Motion("0000ee00-0000-4000-8000-000000000001", (0, 1.5, -0.25)),
     ["data merge entity 0000ee00-0000-4000-8000-000000000001 {Motion:[0d,1.5d,-0.25d]}"]),
    (I.EntityData("0000ee00-0000-4000-8000-000000000001", "{Glowing:1b,Fire:100s}"),
     ["data merge entity 0000ee00-0000-4000-8000-000000000001 {Glowing:1b,Fire:100s}"]),
    (I.Bossbar("queen", "Ice Queen", "blue", 150, 300, ("Steve",)),
     ['bossbar add everhear:queen "Ice Queen"', 'bossbar set everhear:queen name "Ice Queen"',
      "bossbar set everhear:queen color blue", "bossbar set everhear:queen max 300",
      "bossbar set everhear:queen value 150", "bossbar set everhear:queen players Steve"]),
    (I.Bossbar("everhear:queen", players=("Steve", "Alex")),
     ["tag Steve add everhear.bb", "tag Alex add everhear.bb", "bossbar set everhear:queen players @a[tag=everhear.bb]",
      "tag @a[tag=everhear.bb] remove everhear.bb"]),
    (I.Bossbar("queen", players=()), ["bossbar set everhear:queen players"]),
    (I.Bossbar("queen", remove=True), ["bossbar remove everhear:queen"]),
    (I.StopSound(), ["stopsound @a"]),
    (I.StopSound(("Steve",), "minecraft:music.game"), ["stopsound Steve * minecraft:music.game"]),
    (I.Spawnpoint("Steve", "minecraft:the_nether", (0.5, 70, -0.5)),
     ["execute in minecraft:the_nether run spawnpoint Steve 0 70 -1"]),
    (I.Ignite("Steve", 3), ['function everhear:ignite {t:"Steve",f:-0.8,k:60}']),
    (I.Ignite("0000ee00-0000-4000-8000-000000000001", 10),
     ['function everhear:ignite {t:"0000ee00-0000-4000-8000-000000000001",f:-0.333333,k:200}']),
    (I.Sound("minecraft:entity.blaze.shoot", "minecraft:overworld", (0, 86, 0), to=("Steve", "Alex")),
     ["playsound minecraft:entity.blaze.shoot master Steve 0 86 0 1 1",
      "playsound minecraft:entity.blaze.shoot master Alex 0 86 0 1 1"]),
    (I.Particle("flame", "minecraft:overworld", (0, 86, 0), force=True),
     ["particle minecraft:flame 0 86 0 0 0 0 0 1 force @a"]),
    (I.Particle("flame", "minecraft:overworld", (0, 86, 0), to=("Steve",)),
     ["particle minecraft:flame 0 86 0 0 0 0 0 1 normal Steve"]),
])
def test_each_op_is_its_command(op, cmds):
    assert C.command_for(op) == cmds


@pytest.mark.parametrize("op, why", [
    (I.AddModifier("Steve", "scale", "minecraft:effect.speed", 1, "add_value"), "only everhear"),
    (I.RemoveModifier("Steve", "scale", "minecraft:effect.speed"), "never minecraft:effect.speed"),
    (I.AddModifier("Steve", "scale", "everhear:a", 1, "multiply"), "operation"),
    (I.GiveEffect("Steve", "speed", 0), "more than 0"),
    (I.GiveItem("Steve", "stick", "x", components={"custom_data": "{}"}), "custom_data is the engine's"),
    (I.GiveItem("@a", "stick", "x"), "not a player's name"),
    (I.Summon("husk", "minecraft:overworld", (0, 0, 0), "00000001-0000-0002-0000-000300000014",
              nbt="{Tags:[\"x\"]}"), "UUID and Tags are the engine's"),
    (I.Damage("@e", 1), "neither a player's name"),
    (I.Command("stop"), "server owner's"),
    (I.Command("execute as @a run op Steve"), "server owner's"),
    (I.Message((), "x", "popup"), "where is one of"),
    (I.Gamerule("Mob Griefing", "x"), "snake_case"),
    (I.Fill("minecraft:overworld", (0, 0, 0), (40, 40, 40), "stone"), "over the 32768"),
    (I.Fill("minecraft:overworld", (0, 0, 0), (1, 1, 1), "stone", replace="dirt", mode="keep"), "not keep"),
    (I.Fill("minecraft:overworld", (0, 0, 0), (1, 1, 1), "stone; say hi"), "not a block"),
    (I.Forceload("minecraft:overworld", (0, 0), (512, 512)), "over the 256"),
    (I.Motion("Steve", (0, 1, 0)), "wind charges"),
    (I.EntityData("Steve", "Glowing:1b"), "compound"),
    (I.Bossbar("queen", color="orange"), "color is one of"),
    (I.Bossbar("queen"), "changes nothing"),
    (I.Ignite("Steve", 0), "more than 0"),
])
def test_ops_that_cannot_be_commands_are_refused(op, why):
    with pytest.raises(C.OpRefused, match=why):
        C.command_for(op)


def test_summon_nbt_keys_are_read_at_the_top_only():
    nbt = ('{Health:60f,PersistenceRequired:1b,attributes:[{id:"minecraft:max_health",base:60}],'
           'CustomName:"Brute",equipment:{mainhand:{id:"minecraft:iron_axe",count:1,components:{"minecraft:custom_data":{Tags:1b}}}}}')
    assert C.top_level_keys(nbt[1:-1]) == {"Health", "PersistenceRequired", "attributes", "CustomName", "equipment"}
    cmd = C.command_for(I.Summon("minecraft:vindicator", "minecraft:overworld", (1, 85, 1),
                                 "00000001-0000-0002-0000-000300000030", nbt=nbt))[0]
    assert cmd == "summon minecraft:vindicator 1 85 1 " + nbt[:-1] + ",UUID:[I;1,2,3,48]}"
    assert C.top_level_keys('"Tags": [], x: 1') == {"Tags", "x"}


def test_the_refused_list_is_the_kits():
    """commands.REFUSED copies scripts/_lib/commands.py's, the one list."""
    path = os.path.join(REPO, "scripts", "_lib", "commands.py")
    if not os.path.exists(path):
        pytest.skip("scripts/_lib/commands.py is not beside this engine")
    ns = {}
    exec(compile(open(path).read(), path, "exec"), ns)
    assert set(ns["REFUSED"]) == set(C.REFUSED)


# ---- replies into OpResults ----------------------------------------------------------------------

ADD = I.AddModifier("Steve", "scale", "everhear:mark/giant", 1, "add_multiplied_total")


@pytest.mark.parametrize("op, reply, ok, error", [
    (ADD, "Added modifier everhear:mark/giant to attribute Scale for entity Steve", True, None),
    (ADD, "Modifier everhear:mark/giant is already present on attribute Scale for entity Steve", False, "already present"),
    (I.RemoveModifier("Steve", "scale", "everhear:x"), "Attribute Scale for entity Steve has no modifier everhear:mark/giant",
     False, "no modifier with that id (not present)"),
    (ADD, "No entity was found", False, "no entity found"),
    (ADD, "Can't find element 'minecraft:nope' of type 'minecraft:attribute'...craft:nope modifier add everhear:x 1 add_value<--[HERE]",
     False, "no attribute minecraft:nope"),
    (I.GiveEffect("Steve", "poison", 5), "Applied effect Poison to Steve", True, None),
    (I.GiveEffect("Steve", "poison", 5),
     "Unable to apply this effect (target is either immune to effects, or has something stronger)", False,
     "has something stronger"),
    (I.GiveItem("Nobody", "stick", "x"), "No player was found", False, "no player found"),
    (I.GiveItem("Steve", "nope", "x"), "Unknown item 'minecraft:nope'...ive Steve minecraft:nope 1<--[HERE]", False, "no such item"),
    (I.GiveItem("Steve", "carrot_on_a_stick", "fire_staff"), "Gave 1 [Fire Staff] to Steve", True, None),
    (I.TakeItem("Steve", "fire_staff"), "Removed 1 item(s) from player Steve", True, None),
    (I.TakeItem("Steve", "fire_staff"), "No items were found on player Steve", True, None),
    (I.Summon("husk", "minecraft:overworld", (0, 0, 0), STEVE_UUID), "Summoned new Husk", True, None),
    (I.Summon("husk", "minecraft:overworld", (0, 0, 0), STEVE_UUID), "Unable to summon entity due to duplicate UUIDs",
     False, "an entity with that uuid already exists"),
    (I.Remove(STEVE_UUID), "Running function everhear:removeFunction everhear:remove returned 1", True, None),
    (I.Remove(STEVE_UUID), "Running function everhear:removeFunction everhear:remove returned 0", False,
     "no such entity (gone already, dying, or a player: players are never removed)"),
    (I.Damage("Steve", 3), "Applied 3.0 damage to Husk", True, None),
    (I.Damage("Steve", 3), "Target is invulnerable to the given damage type", False,
     "invulnerable to it (or still unhurtable after a hit)"),
    (I.Particle("flame", "minecraft:overworld", (0, 0, 0)), "Displaying particle minecraft:flame", True, None),
    (I.Particle("flame", "minecraft:overworld", (0, 0, 0)), "The particle was not visible for anybody", True, None),
    (I.Particle("nope", "minecraft:overworld", (0, 0, 0)), "Unknown particle: minecraft:nope...craft:nope 0 86 0<--[HERE]",
     False, "no such particle"),
    (I.Sound("x", "minecraft:overworld", (0, 0, 0)), "The sound is too far away to be heard", True, None),
    (I.Message((), "x"), "", True, None),
    (I.Message(("Steve", "Nobody"), "x", "title"), "Showing new title for Steve | No player was found", False,
     "no player found"),
    (I.Teleport("Steve", "minecraft:overworld", (0, 85, 0)), "Teleported Steve to 0.500000, 85.000000, 0.500000", True, None),
    (I.Teleport("Nobody", "minecraft:overworld", (0, 85, 0)), "No entity was found", False, "no entity found"),
    (I.Gamerule("mob_griefing", "false"), "Game rule mob_griefing is now set to false", True, None),
    (I.Gamerule("random_tick_speed", "3"), "Game rule random_tick_speed is already set to 3", True, None),
    (I.Gamerule("nope", "1"), "Incorrect argument for commandgamerule nope<--[HERE]", False,
     "Minecraft did not understand the command"),
    (I.Command("say hi"), "", True, None),
    (I.Fill("minecraft:overworld", (0, 0, 0), (1, 1, 1), "stone"), "Successfully filled 27 block(s)", True, None),
    (I.Fill("minecraft:overworld", (0, 0, 0), (1, 1, 1), "stone"), "No blocks were filled", True, None),
    (I.Fill("minecraft:overworld", (0, 0, 0), (1, 1, 1), "stone"), "That position is not loaded", False,
     "that part of the world is not loaded"),
    (I.Fill("minecraft:overworld", (0, 0, 0), (1, 1, 1), "stone"), "That position is out of this world!", False,
     "the position is outside the world"),
    (I.Fill("minecraft:overworld", (0, 0, 0), (1, 1, 1), "nope"),
     "Unknown block type 'minecraft:nope'... 22 92 22 minecraft:nope<--[HERE]", False, "no such block"),
    (I.SetBlock("minecraft:overworld", (0, 0, 0), "stone"), "Changed the block at 20, 90, 20", True, None),
    (I.SetBlock("minecraft:overworld", (0, 0, 0), "stone"), "Could not set the block", True, None),
    (I.SetBlock("minecraft:overworld", (0, 0, 0), "stone", mode="keep"), "Could not set the block", False,
     "something is there already (keep)"),
    (I.Forceload("minecraft:overworld", (0, 0), (1, 1)), "Marked chunk [4, 4] in minecraft:overworld to be force loaded",
     True, None),
    (I.Forceload("minecraft:overworld", (0, 0), (1, 1)), "No chunks were marked for force loading", True, None),
    (I.Motion("0000ee00-0000-4000-8000-000000000001", (0, 1, 0)), "Modified entity data of Husk", True, None),
    (I.Motion("0000ee00-0000-4000-8000-000000000001", (0, 1, 0)),
     "Nothing changed. The specified properties already have these values", True, None),
    (I.EntityData("Steve", "{Fire:1s}"), "Unable to modify player data", False, "a player's data cannot be written"),
    (I.Bossbar("queen", "Q"), 'A bossbar already exists with the ID \'everhear:queen\' | Custom bossbar [Q] has been renamed',
     True, None),
    (I.Bossbar("queen", value=3), "No bossbar exists with the ID 'everhear:queen'", False,
     "no such bossbar (give it a name to make it)"),
    (I.StopSound(), "Stopped all sounds", True, None),
    (I.StopSound(("Nobody",)), "No player was found", False, "no player found"),
    (I.Spawnpoint("Steve", "minecraft:overworld", (0, 85, 0)),
     "Set spawn point to 0, 85, 0 [0.0, 0.0] in minecraft:overworld for Steve", True, None),
    (I.Ignite("Steve", 3), "Running function everhear:igniteFunction everhear:ignite returned 1", True, None),
    (I.Ignite("Steve", 3), "Running function everhear:igniteFunction everhear:ignite returned 0", False,
     "no such target, or no air at a player's feet or head to light them from"),
    (I.Sound("x", "minecraft:overworld", (0, 0, 0), to=("Steve", "Nobody")),
     "Played sound minecraft:x to Steve | No player was found", False, "no player found"),
    (I.Command("frobnicate"), "Unknown or incomplete command. See below for error...<--[HERE]", False,
     "Minecraft did not understand the command"),
])
def test_each_reply_reads_as_a_result(op, reply, ok, error):
    r = C.result_for(op, reply)
    assert (r.ok, r.error) == (ok, error)
    assert r.reply == reply


# ---- reading the datapack's compounds -----------------------------------------------------------

TABLES = R.Tables(types=[""] * 200, blocks=[""] * 1300)
TABLES.types[70], TABLES.types[157], TABLES.types[108] = "minecraft:husk", "minecraft:zombie", "minecraft:player"
TABLES.types[7], TABLES.types[150], TABLES.types[155] = "minecraft:arrow", "minecraft:trident", "minecraft:wolf"
TABLES.blocks[448], TABLES.blocks[383] = "minecraft:grass_block", "minecraft:dirt"


def names():
    n = R.Names()
    n.update("There are 1 of a max of 20 players online: Steve (8667ba71-b85a-4004-af54-457a9734eed7)")
    return n


def test_list_uuids():
    n = R.Names()
    n.update("There are 2 of a max of 20 players online: Steve (8667ba71-b85a-4004-af54-457a9734eed7), "
             "Alex_2 (00000000-0000-0000-0000-000000000002)")
    assert n.online == {STEVE_UUID: "Steve", "00000000-0000-0000-0000-000000000002": "Alex_2"}
    n.update("There are 0 of a max of 20 players online: ")
    assert n.online == {} and n.name(STEVE_UUID) == "Steve"      # still known for a late event


@pytest.mark.parametrize("doc, kind, data", [
    ({"d": {"item": "fire_staff", "count": 1, "slot": "mainhand"}, "t": 17795, "u": STEVE, "k": "use", "n": 1},
     "use", {"item": "fire_staff", "slot": "mainhand", "count": 1}),
    ({"d": {"item": "fire_staff", "target_type": 157, "target": [1, 2, 3, 5]}, "t": 1, "u": STEVE, "k": "hit", "n": 2},
     "hit", {"item": "fire_staff", "target": "00000001-0000-0002-0000-000300000005", "target_type": "minecraft:zombie",
             "ranged": False}),
    ({"d": {"item": "boom_bow", "target_type": 70, "target": [6, 6, 6, 1], "ranged": 1}, "t": 1, "u": STEVE, "k": "hit",
      "n": 3}, "hit", {"item": "boom_bow", "target": "00000006-0000-0006-0000-000600000001",
                       "target_type": "minecraft:husk", "ranged": True}),
    ({"d": {"item": "boom_bow", "hit": "block", "pos": [0.5, 88.8, 18.9], "dim": "minecraft:overworld",
            "projectile_type": 7, "projectile": [1, 2, 3, 4], "block": "minecraft:grass_block"}, "t": 1, "u": STEVE,
      "k": "land", "n": 5},
     "land", {"item": "boom_bow", "hit": "block", "pos": (0.5, 88.8, 18.9), "dim": "minecraft:overworld",
              "projectile_type": "minecraft:arrow", "projectile": "00000001-0000-0002-0000-000300000004",
              "block": "minecraft:grass_block"}),
    ({"d": {"item": "boom_trident", "hit": "entity", "pos": [0.5, 86.0, 8.1], "dim": "minecraft:overworld",
            "projectile_type": 150, "projectile": [1, 2, 3, 4], "target": [6, 6, 6, 2], "target_type": 70},
      "t": 1, "u": STEVE, "k": "land", "n": 6},
     "land", {"item": "boom_trident", "hit": "entity", "pos": (0.5, 86.0, 8.1), "dim": "minecraft:overworld",
              "projectile_type": "minecraft:trident", "projectile": "00000001-0000-0002-0000-000300000004",
              "target": "00000006-0000-0006-0000-000600000002", "target_type": "minecraft:husk"}),
    ({"d": {"block": "minecraft:sponge", "count": 1}, "t": 1, "u": STEVE, "k": "mine", "n": 8},
     "mine", {"block": "minecraft:sponge", "count": 1}),
    ({"d": {"target_type": 70}, "t": 1, "u": STEVE, "k": "kill", "n": 4},
     "kill", {"target_type": "minecraft:husk"}),
    ({"d": {"target_type": 108, "target": STEVE}, "t": 1, "u": STEVE, "k": "kill", "n": 4},
     "kill", {"target": "Steve", "target_type": "minecraft:player"}),
    ({"d": {"amount": 3.0, "attacker": [1, 2, 3, 9], "attacker_type": 70, "absorbed": 0.0}, "t": 1, "u": STEVE,
      "k": "hurt", "n": 7},
     "hurt", {"amount": 3.0, "absorbed": 0.0, "attacker": "00000001-0000-0002-0000-000300000009",
              "attacker_type": "minecraft:husk"}),
    ({"d": {"amount": 1.0, "absorbed": 0.0}, "t": 1, "u": STEVE, "k": "hurt", "n": 14},
     "hurt", {"amount": 1.0, "absorbed": 0.0}),
    ({"d": {}, "t": 31623, "u": STEVE, "k": "death", "n": 12}, "death", {}),
    ({"d": {"pos": [-5, 85, 0], "dim": "minecraft:overworld"}, "t": 31623, "u": STEVE, "k": "death", "n": 12},
     "death", {"pos": (-5, 85, 0), "dim": "minecraft:overworld"}),
    ({"d": {}, "t": 31623, "u": STEVE, "k": "respawn", "n": 13}, "respawn", {}),
    ({"d": {}, "t": 32494, "u": STEVE, "k": "milk", "n": 16}, "milk", {}),
])
def test_events_read_in_the_designs_fields(doc, kind, data):
    ev = R.event(doc, TABLES, names())
    assert (ev.kind, ev.player, ev.n, ev.game_tick) == (kind, "Steve", doc["n"], doc["t"])
    assert ev.data == dict(data, uuid=STEVE_UUID)


SNAP = ('Storage everhear:io has the following contents: {p: [{d: "minecraft:overworld", e: [0.5d, '
        '86.62000000476837d, 0.5d], g: 0, h: 20.0f, l: {p: [0.5d, 84.99508998543024d, 3.3144786804914474d], '
        'b: 448, d: 104, k: "block"}, inv: ["fire_staff", "rod"], p: [0.5d, 85.0d, 0.5d], r: [0.0f, 30.0f], '
        'fx: [{duration: 599, show_icon: 1b, amplifier: 1b, id: "minecraft:speed"}, {duration: 1180, amplifier: '
        '2b, id: "minecraft:poison", hidden_effect: {duration: -1, amplifier: 0b, id: "minecraft:poison"}}], '
        's: 0b, og: 1b, sp: 1b, u: [I; -2040022415, -1202044924, -1353431686, -1758138665], mh: 20.0f, hd: {mainhand: '
        '"fire_staff", head: "crown", offhand: "ice_wand"}}], e: [], time: 25401}')


def test_a_players_compound_is_a_playersnap():
    doc = snbt.data_reply(SNAP)["p"][0]
    p = R.player(doc, "Steve", TABLES)
    assert (p.name, p.uuid, p.dim, p.pos, p.eye) == ("Steve", STEVE_UUID, "minecraft:overworld", (0.5, 85.0, 0.5),
                                                       (0.5, 86.62000000476837, 0.5))
    assert (p.yaw, p.pitch, p.health, p.max_health, p.alive, p.sneaking, p.gamemode) == (
        0.0, 30.0, 20.0, 20.0, True, False, "survival")
    assert p.facing == pytest.approx((0.0, -0.5, math.sqrt(3) / 2))
    assert (p.on_ground, p.sprinting) == (True, True)
    assert p.held == {"mainhand": "fire_staff", "head": "crown", "offhand": "ice_wand"}
    assert sorted(p.carried) == ["crown", "fire_staff", "ice_wand", "rod"]
    assert p.effects == (I.Effect("minecraft:speed", 1, 599),
                         I.Effect("minecraft:poison", 2, 1180, I.Effect("minecraft:poison", 0, -1)))
    look = p.looking_at
    assert (look.kind, look.block, look.block_id, look.distance) == ("block", (0, 84, 3), "minecraft:grass_block", 3.25)


def test_the_raycast_at_an_entity_and_at_nothing():
    eye, face = (0.5, 86.62, 0.5), R.facing(0, 0)
    look = R.look({"k": "entity", "d": 64, "p": [0.5, 86.62, 2.5], "e": [1, 2, 3, 5], "y": 70}, eye, face, TABLES)
    assert (look.kind, look.entity, look.entity_type, look.distance) == (
        "entity", "00000001-0000-0002-0000-000300000005", "minecraft:husk", 2.0)
    look = R.look({"k": "none", "d": 1536}, eye, face, TABLES)
    assert look.kind == "none" and look.distance == 48 and look.pos == pytest.approx((0.5, 86.62, 48.5))


def test_entities_dead_and_alive():
    e = R.entity({"p": [3.5, 85.0, 3.5], "c": "Named Husk", "d": "minecraft:overworld", "u": [1, 2, 3, 10],
                  "g": ["a", "b", "everhear.m"], "h": 20.0, "y": 70}, TABLES)
    assert e == I.EntitySnap("00000001-0000-0002-0000-00030000000a", "minecraft:husk", "minecraft:overworld",
                             (3.5, 85.0, 3.5), 20.0, frozenset({"a", "b"}), "Named Husk")
    item = R.entity({"p": [0, 85, 0], "d": "minecraft:overworld", "u": [5, 6, 7, 8], "y": 74}, TABLES)
    assert item.health is None
    assert R.entity({"p": [0, 85, 0], "u": [1, 2, 3, 4], "h": 0.0, "y": 70}, TABLES) is None   # dying: gone
    assert R.text_of({"text": "Ice ", "extra": [{"text": "Queen", "color": "aqua"}]}) == "Ice Queen"
    wolf = R.entity({"p": [-8.1, 85.0, 3.2], "d": "minecraft:overworld", "u": [1, 2, 3, 9], "h": 8.0, "y": 155,
                     "o": STEVE}, TABLES, names())
    assert (wolf.owner, wolf.in_ground) == ("Steve", False)
    arrow = R.entity({"p": [0.5, 88.8, 18.9], "d": "minecraft:overworld", "u": [1, 2, 3, 4], "y": 7, "o": [9, 9, 9, 9],
                      "ig": 1}, TABLES, names())
    assert (arrow.owner, arrow.in_ground, arrow.health) == ("00000009-0000-0009-0000-000900000009", True, None)


def test_facing_matches_minecraft():
    assert R.facing(0, 0) == pytest.approx((0, 0, 1))          # south
    assert R.facing(90, 0) == pytest.approx((-1, 0, 0))        # west
    assert R.facing(0, -90) == pytest.approx((0, 1, 0))        # up
    assert R.facing(-90, 0) == pytest.approx((1, 0, 0))        # east


# ---- bases ---------------------------------------------------------------------------------------

def test_bases():
    assert bases.can_report("carrot_on_a_stick", "use")
    assert bases.can_report("minecraft:warped_fungus_on_a_stick", "use")
    assert bases.can_report("minecraft:carrot_on_a_stick", "held")
    assert bases.can_report("minecraft:diamond_sword", "hit") and bases.can_report("stick", "unheld")
    assert not bases.can_report("minecraft:diamond_sword", "use")
    assert not bases.can_report("minecraft:carrot_on_a_stick", "fly")
    assert not bases.can_report("carrot_stick", "held")                 # no such item: refused
    assert bases.why_not("carrot_on_a_stick", "use") is None
    assert "only minecraft:carrot_on_a_stick and" in bases.why_not("minecraft:stick", "use")
    assert "not a Minecraft 26.3 item" in bases.why_not("carrot_stick", "held")
    assert "minecraft:air" not in bases.KNOWN_BASES and len(bases.KNOWN_BASES) > 1500


# ---- the datapack and the engine agree -----------------------------------------------------------

def test_the_datapack_version_is_the_engines():
    from vfg_engine.mc import datapack
    meta = open(os.path.join(datapack.SOURCE, "data", "everhear", "function", "meta.mcfunction")).read()
    assert re.search(r"everhear:meta version set value %d$" % datapack.VERSION, meta, re.M)
    gen = open(os.path.join(os.path.dirname(datapack.SOURCE), "generate.py")).read()
    assert re.search(r"^VERSION = %d$" % datapack.VERSION, gen, re.M)


def test_the_page_size_is_the_datapacks():
    from vfg_engine.mc import datapack, mcio
    events = open(os.path.join(datapack.SOURCE, "data", "everhear", "function", "io", "events.mcfunction")).read()
    assert len(re.findall(r"append from storage everhear:events q\[\d+\]", events)) == mcio.PAGE
