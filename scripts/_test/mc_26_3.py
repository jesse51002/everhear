"""Replies a real Minecraft 26.3 server gave over RCON, recorded on 2026-10-09 from a
throwaway local server (the owner's 26.3 server.jar, a fresh world, no player online).

The fakes answer with these, so a parser tested against a fake reads what the real
server prints. Each is verbatim; a template's %s is where the recorded value was.
Notes from the recording:

- Several outputs of one command (`execute as @e ... run ...`) arrive concatenated,
  with no separator: "Test passedTest passed".
- A parse error is the message, the command up to the error and "<--[HERE]", with no
  space between them, and a leading "..." when the command was cut.
- Inside a forked `execute as|at <entities> run ...`, a failure prints nothing: the
  reply is "" both when the inner command fails and when no entity matched. With
  `execute in <dim> positioned <x y z> ...` the failure message comes through.
- RCON's own commands run at the world spawn, in its dimension: `spreadplayers ~ ~`
  from RCON centres on the world spawn.
- Since 1.21.9 spawn chunks no longer stay loaded: an entity summoned where no player
  is can be gone from selectors at once.
- Player data (from the wiki, 26.x): `respawn: {pos: [I; x, y, z], dimension: "...",
  yaw: f, pitch: f, forced: b}` (SpawnX/Y/Z and SpawnDimension are gone since 25w07a),
  `LastDeathLocation: {dimension: "...", pos: [I; x, y, z]}`, `Dimension: "..."`.
"""

LIST = "There are %d of a max of 20 players online: %s"
LIST_NOBODY = "There are 0 of a max of 20 players online: "

ENTITY_DATA = "%s has the following entity data: %s"
POS = "[0.5d, 100.0d, 0.5d]"
POS_AFTER_SPREAD = "[-1408.5d, 68.0d, 1410.5d]"
NO_ELEMENTS = "Found no elements matching %s"
NO_ENTITY = "No entity was found"
NO_PLAYER = "No player was found"
# How an int-array compound prints (recorded from storage holding a player's respawn shape).
RESPAWN_SNBT = '{pos: [I; 1, 2, 3], forced: 0b, pitch: 0.0f, dimension: "minecraft:overworld", yaw: 0.0f}'
ATTRIBUTES_SNBT = ('[{id: "minecraft:movement_speed", base: 0.23000000417232513d}, '
                   '{id: "minecraft:scale", base: 2.0d}]')
STRING_SNBT = '"minecraft:overworld"'
GLOWING_SNBT = "1b"
TAGS_SNBT = '["vfg_summon_zombies_steve_new", "vfg", "vfg_summon_zombies_steve"]'

LOCATE_BIOME = "The nearest minecraft:snowy_taiga is at [-1408, 64, 1408] (1992 blocks away)"
LOCATE_BIOME_TAG = "The nearest #minecraft:is_forest (minecraft:forest) is at [260, 0, -264] (172 blocks away)"
LOCATE_HERE = "The nearest minecraft:plains is at [0, 72, 0] (0 blocks away)"
LOCATE_STRUCTURE = "The nearest minecraft:village_plains is at [-544, ~, 320] (631 blocks away)"
LOCATE_STRUCTURE_TAG = "The nearest #minecraft:village (minecraft:village_plains) is at [-544, ~, 320] (631 blocks away)"
LOCATE_FOUND = "The nearest %s is at [%d, %s, %d] (%d blocks away)"
LOCATE_FOUND_TAG = "The nearest %s (%s) is at [%d, %s, %d] (%d blocks away)"
LOCATE_BIOME_NOT_FOUND = 'Could not find a biome of type "%s" within a reasonable distance'
LOCATE_STRUCTURE_NOT_FOUND = 'Could not find a structure of type "%s" nearby'
LOCATE_STRUCTURE_INVALID = 'There is no structure with type "%s"'
LOCATE_BIOME_INVALID = ("Can't find element 'minecraft:nonsense' of type 'minecraft:worldgen/biome'"
                        "...t:nonsense<--[HERE]")
LOCATE_BIOME_TAG_INVALID = ("Can't find tag 'minecraft:nonsense' of type 'minecraft:worldgen/biome'"
                            "...t:nonsense<--[HERE]")

SPREAD = "Spread %d entity/entities around %s, %s with an average distance of %s block(s) apart"
SPREAD_ONE = "Spread 1 entity/entities around -1407.5, 1408.5 with an average distance of 0.00 block(s) apart"
SPREAD_FAILED = ("Could not spread %d entity/entities around %s, %s (too many entities for space - "
                 "try using spread of at most 0.00)")
SPREAD_FAILED_OCEAN = ("Could not spread 1 entity/entities around -95.5, -319.5 (too many entities for "
                       "space - try using spread of at most 0.00)")

TEST_PASSED = "Test passed"
TEST_FAILED = "Test failed"
TEST_PASSED_COUNT = "Test passed. Count: %d"
OUT_OF_WORLD = "That position is out of this world!"

STORAGE = "Storage everhear:places has the following contents: %s"
STORAGE_EMPTY = "Storage everhear:places has the following contents: {}"
STORAGE_THREE = ('Storage everhear:places has the following contents: {123: {pos: [1, 2, 3], by: "", '
                 'dim: "minecraft:overworld"}, castle: {pos: [198, 68, 251], by: "castle_alex", dim: '
                 '"minecraft:overworld"}, 1st_base: {pos: [1, -5, 2], by: "base_alex", dim: "minecraft:the_nether"}}')
STORAGE_ONE = ('Storage everhear:places has the following contents: {pos: [197, 68, 251], by: "castle_steve", '
               'dim: "minecraft:overworld"}')
STORAGE_MODIFIED = "Modified storage everhear:places"
NOTHING_CHANGED = "Nothing changed. The specified properties already have these values"

SUMMONED = "Summoned new %s"
SUMMON_PLAYER = "Can't summon entity of type minecraft:player"
SUMMON_UNKNOWN = ("Can't find element 'minecraft:%s' of type 'minecraft:entity_type'"
                  "...t:%s ~ ~ ~<--[HERE]")
SUMMON_PEACEFUL = "Monsters cannot be summoned in Peaceful difficulty"
TAG_ADDED = "Added tag '%s' to %d entities"                                  # these two: 2026-10-10, realmc
TAG_ADD_FAILED = "Target either already has the tag or has too many tags"   # all it matched have it
TAG_REMOVED = "Removed tag '%s' from %d entities"
TAG_REMOVED_ONE = "Removed tag '%s' from %s"
KILLED = "Killed %d entities"
EFFECT = "Applied effect %s to %s"
EFFECT_STRONGER = "Unable to apply this effect (target is either immune to effects, or has something stronger)"
TELEPORTED = "Teleported %s to %s"
TELEPORTED_TO = "Teleported %s to %.6f, %.6f, %.6f"
SPAWNPOINT = "Set spawn point to %d, %d, %d [0.0, 0.0] in %s for %s"
SPAWNPOINT_NOT_PLAYER = ("Only players may be affected by this command, but the provided selector includes "
                         "entitiesexecute in minecraft:overworld run spawnpoint 00000001-0000-0002-0000-000300000004"
                         " -1416 69 1408<--[HERE]")
WORLDSPAWN = "Set the world spawn point to 20, 80, -20 [0.0, 0.0] in minecraft:overworld"

# A player's state, as `vfg state --items` reads it (op_state, toolkit/remote/scripts.py), each
# after ENTITY_DATA's "<p> has the following entity data: ". 2026-10-10: NOT all recorded here.
# Checked on 26.3: playerGameType is a bare int (realmc's smoke test, 'entity data: 0$'); Health
# a float with f, an effect's id quoted with its namespace and its amplifier a byte left out at
# 0 (scripts/_test/live_examples_*.py read them from real players); an attribute's reply is
# ATTRIBUTE_GOT's shape (Scale, below in COMMANDS; gravity in live_examples_powers.py). Written
# from the wiki's 26.x player data and not yet seen on 26.3: foodLevel and XpLevel as bare ints,
# an effect's duration as a bare int of ticks (-1: infinite), a hidden_effect nested in the one
# that hides it, and the armor and max_health attributes' names. Record them on a realmc server.
HEALTH_SNBT = "14.5f"
FOOD_SNBT = "18"
XP_LEVEL_SNBT = "5"
GAME_TYPE_SNBT = "0"            # 0 survival, 1 creative, 2 adventure, 3 spectator
EFFECTS_SNBT = ('[{duration: 600, show_icon: 1b, id: "minecraft:speed", amplifier: 1b, show_particles: 1b, '
                'hidden_effect: {duration: 9600, show_icon: 1b, id: "minecraft:speed", show_particles: 1b}}, '
                '{duration: -1, show_icon: 1b, id: "minecraft:night_vision", show_particles: 1b}]')
NO_EFFECTS = NO_ELEMENTS % "active_effects"   # active_effects is absent with no effect on
ATTRIBUTE_GOT = "The value of attribute %s for entity %s is %s"
ATTRIBUTE_NAMES = {"armor": "Armor", "max_health": "Max Health", "scale": "Scale"}

# Commands-mode replies: (command, reply, is it an error).
COMMANDS = [
    ("time set day", "Set minecraft:overworld to time marker minecraft:day", False),
    ("time set day", "Clock minecraft:overworld is already at time marker minecraft:day", False),
    ("weather rain", "Set the weather to rain", False),
    ("gamerule keep_inventory true", "Game rule keep_inventory is now set to true", False),
    ("gamerule keep_inventory true", "Game rule keep_inventory is already set to true", False),
    ("difficulty easy", "The difficulty did not change; it is already set to Easy", False),
    ("execute as @a run say hi", "", False),
    ("execute if entity @e[tag=probe]", "Test passed. Count: 1", False),
    ("execute if entity @e[tag=nothing]", "Test failed", False),
    ("kill @e[tag=vfg_summon_zombies_steve]", "Killed Bob", False),
    ("data get storage everhear:places castle", STORAGE_ONE, False),
    ("data get entity @e[tag=probe,limit=1] Pos", "Armor Stand has the following entity data: [0.5d, 100.0d, 0.5d]",
     False),
    ("attribute @e[tag=vfg_summon_zombies_steve,limit=1] minecraft:scale get",
     "The value of attribute Scale for entity Bob is 2.0", False),
    ("bogus", "Unknown or incomplete command. See below for errorbogus<--[HERE]", True),
    ("give", "Unknown or incomplete command. See below for errorgive<--[HERE]", True),
    ("locate biome", "Unknown or incomplete command. See below for error...cate biome<--[HERE]", True),
    ("give @a minecraft:nonsense", "Unknown item 'minecraft:nonsense'give @a minecraft:nonsense<--[HERE]", True),
    ("give @a minecraft:diamond abc", "Expected integer...t:diamond abc<--[HERE]", True),
    ("effect give @a minecraft:speed 10 300", "Integer must not be more than 255: found 300...:speed 10 300<--[HERE]",
     True),
    ("tp @a 1 2", "Incorrect argument for commandtp @a 1 2<--[HERE]", True),
    ("summon minecraft:zombie ~ ~ ~ {bad", "Expected literal :...~ ~ ~ {bad<--[HERE]", True),
    ("give @a minecraft:diamond", "No player was found", True),
    ("give Steve minecraft:diamond", "No player was found", True),
    ("effect give Steve minecraft:resistance 5 4 true", "No entity was found", True),
    ("kill @e[type=minecraft:wither]", "No entity was found", True),
    ("data get entity @e[tag=probe,limit=1] foo", "Found no elements matching foo", True),
    ("execute if block 0 -100 0 minecraft:stone", "That position is out of this world!", True),
    ("summon minecraft:player ~ ~ ~", "Can't summon entity of type minecraft:player", True),
    ("summon minecraft:nonsense ~ ~ ~",
     "Can't find element 'minecraft:nonsense' of type 'minecraft:entity_type'...t:nonsense ~ ~ ~<--[HERE]", True),
    ("execute positioned 0 0 0 run locate structure minecraft:fortress",
     'Could not find a structure of type "minecraft:fortress" nearby', True),
    ("spreadplayers -96 -320 0 8 under 320 false @e[tag=probe]", SPREAD_FAILED_OCEAN, True),
    ("gamerule spawn_chunk_radius", "Incorrect argument for commandgamerule spawn_chunk_radius<--[HERE]", True),
]
