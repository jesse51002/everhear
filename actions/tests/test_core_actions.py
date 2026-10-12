"""The six core actions — command, teleport, summon_around, save_place, stop and edit — load,
say what the menu says, check their values as the spec says, and refuse what they must.

    uv run --no-project --with pytest --with pydantic pytest actions/tests/test_core_actions.py

They are loaded from a copy of actions/ beside a stub of each script folder, so they are held
to `vfg action check` whether or not their scripts are written yet.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CORE = ("command", "teleport", "summon_around", "save_place", "stop", "edit")

# Each one's line on the selector's menu.
MENU = {
    "command": "Run vanilla commands, in order: anything that takes effect at once — items, effects, time, "
               "weather, killing or summoning a few mobs, attributes, game rules, sounds, titles, looking up "
               "where someone is. Never places or removes blocks.",
    "teleport": "Move a player, or everyone, to a biome, a structure, a saved place, coordinates, far away, "
                "their spawn point or where they last died, landing on safe ground.",
    "summon_around": "Summon a number of one mob round each player, spread to safe spots, optionally scaled, "
                     "named or glowing.",
    "save_place": "Save where a player stands, or given coordinates, under a name everyone can use.",
    "stop": "Stop a running copy of an action, by its name: a weapon, a rain, a rule, anything that keeps going.",
    "edit": "Change values on a running copy of an action, by its name: bigger, faster, no cooldown, "
            "for another player too.",
}


def lib():
    if str(REPO / "lib" / "py") not in sys.path:
        sys.path.insert(0, str(REPO / "lib" / "py"))
    import vfg_actions
    return vfg_actions


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    """The repo's library actions, from a copy of actions/ with a stub main.py for every script
    they name, and the real _types.py and presets."""
    root = tmp_path_factory.mktemp("repo")
    (root / "actions").mkdir()
    (root / "actions" / "__init__.py").write_text("")
    (root / "actions" / "_types.py").symlink_to(REPO / "actions" / "_types.py")
    for p in (REPO / "actions").glob("*.py"):
        if not p.name.startswith("_"):
            shutil.copy(p, root / "actions")
    (root / "scripts").mkdir()
    (root / "scripts" / "_presets").symlink_to(REPO / "scripts" / "_presets")
    for name in {*CORE, *(p.name for p in (REPO / "scripts").iterdir() if not p.name.startswith((".", "_")))}:
        (root / "scripts" / name).mkdir(exist_ok=True)
        (root / "scripts" / name / "main.py").write_text('"""a stub"""\n')
    saved_path = list(sys.path)
    saved = {k: v for k, v in sys.modules.items() if k == "actions" or k.startswith("actions.")}
    for k in saved:
        del sys.modules[k]
    yield lib().Catalog(root, None)
    for k in [k for k in sys.modules if k == "actions" or k.startswith("actions.")]:
        del sys.modules[k]
    sys.modules.update(saved)
    sys.path[:] = saved_path


def check(catalog, action, **given):
    return lib().check_values(catalog.get(action), given)


# ------------------------------------------------------------------ every one

def test_the_six_load_pass_their_check_and_say_what_the_menu_says(catalog):
    A = lib()
    assert catalog.errors() == {}
    for name in CORE:
        cls = catalog.get(name)
        assert cls is not None, name
        assert A.check_action(name, cls) == [], name
        assert A.menu_line(name, cls) == "%s — %s" % (name, MENU[name])
        assert cls.script == name and cls.runs == "once"
    assert (catalog.get("teleport").timeout_s, catalog.get("summon_around").timeout_s) == (60, 60)
    assert catalog.get("command").timeout_s is None


def test_run_script_and_stop_script_are_gone():
    for name in ("run_script", "stop_script", "command", "teleport", "summon_around", "save_place"):
        assert not (REPO / "actions" / name).exists(), name
    assert not (REPO / "actions" / "run_script.py").exists() and not (REPO / "actions" / "stop_script.py").exists()


def test_what_each_takes(catalog):
    assert {n: catalog.get(n).takes for n in CORE} == {
        "command": ["commands"],
        "teleport": ["player", "to", "target", "dimension"],
        "summon_around": ["players", "mob", "count", "radius", "scale", "name", "glowing"],
        "save_place": ["name", "player", "at"],
        "stop": ["copy"],
        "edit": ["copy", "values"],
    }


# ------------------------------------------------------------------ command

@pytest.mark.parametrize("word", ["setblock", "fill", "clone", "place", "fillbiome"])
def test_command_refuses_blocks_alone_and_after_run(catalog, word):
    for c in (word + " ~ ~ ~ stone", "/" + word + " ~ ~ ~ stone", word.upper() + " ~ ~ ~ stone",
              "minecraft:" + word + " ~ ~ ~ stone", "execute as @a at @s run " + word + " ~ ~-1 ~ stone",
              "execute if entity @a run execute at @r run " + word + " ~ ~ ~ air",
              "return run " + word + " ~ ~ ~ stone"):
        with pytest.raises(ValueError, match="else an implementer's: hand it off"):
            check(catalog, "command", commands=[c])
    with pytest.raises(ValueError):
        check(catalog, "command", commands=["time set day", "execute at Steve run " + word + " ~ ~ ~ tnt"])


def test_command_refuses_the_owners_commands(catalog):
    from actions import _types
    for word in sorted(_types.REFUSED):
        for c in (word + " Steve", "/" + word.upper() + " Steve", "execute as @a run " + word + " Steve"):
            with pytest.raises(ValueError, match="owner"):
                check(catalog, "command", commands=[c])


def test_command_keeps_what_is_allowed_and_strips_the_slash(catalog):
    got = check(catalog, "command", commands=[
        "/give Steve minecraft:golden_apple 64", "  effect give @a minecraft:speed 30 1  ",
        "say stop it, we fill the gaps", "execute as @a at @s run summon minecraft:zombie ~ ~ ~",
        "kill @e[type=minecraft:wither]"])
    assert got.values == {"commands": [
        "give Steve minecraft:golden_apple 64", "effect give @a minecraft:speed 30 1", "say stop it, we fill the gaps",
        "execute as @a at @s run summon minecraft:zombie ~ ~ ~", "kill @e[type=minecraft:wither]"]}
    # from the command line: a JSON list
    assert check(catalog, "command", commands='["time set day", "weather clear"]').values["commands"] == \
        ["time set day", "weather clear"]


def test_command_size_limits(catalog):
    from actions._types import RCON_MAX
    ok = "say " + "a" * (RCON_MAX - 4)
    assert len(check(catalog, "command", commands=[ok]).values["commands"][0].encode()) == RCON_MAX
    for bad in ([ok + "a"], ["say " + "é" * 699]):
        with pytest.raises(ValueError, match="RCON takes at most 1400"):
            check(catalog, "command", commands=bad)
    for bad in (["time set day"] * 6, ["", "time set day"], ["/"], ["say a\nop Steve"]):
        with pytest.raises(ValueError):
            check(catalog, "command", commands=bad)


# ------------------------------------------------------------------ teleport, summon_around, save_place

def test_teleport_values(catalog):
    got = check(catalog, "teleport", player=" Steve , Alex ", to="Structure", target="#village")
    assert got.values == {"player": "Steve,Alex", "to": "structure", "target": "#village", "dimension": "overworld"}
    for bad in (dict(player="@e"), dict(to="village"), dict(dimension="moon"), dict(target="a\nb")):
        with pytest.raises(ValueError):
            check(catalog, "teleport", **bad)


def test_summon_around_clamps_its_numbers_and_refuses_the_rest(catalog):
    got = check(catalog, "summon_around", players="Steve", mob="minecraft:Zombie", count="500", radius=1)
    assert (got.values["mob"], got.values["count"], got.values["radius"]) == ("zombie", 100, 4)
    assert got.notes == ["count=500 is over its max, clamped to 100", "radius=1 is under its min, clamped to 4"]
    assert check(catalog, "summon_around").values == {"players": "@a", "mob": "zombie", "count": 10, "radius": 16,
                                                     "scale": 1.0, "name": "", "glowing": False}
    for bad in (dict(name="a" * 33), dict(name="two\nlines"), dict(mob="#zombies"), dict(mob="potion"),
                dict(players="@e"), dict(glowing="maybe")):
        with pytest.raises(ValueError):
            check(catalog, "summon_around", **bad)


def test_save_place_values(catalog):
    assert check(catalog, "save_place", name="castle", player="Steve").values == \
        {"name": "castle", "player": "Steve", "at": ""}
    assert check(catalog, "save_place", name="home", at="1, 64, -3 nether").values["at"] == "1 64 -3 the_nether"
    for bad in (dict(name="My Castle"), dict(name="ca$tle"), dict(player="Steve Two"), dict(at="0 400 0")):
        with pytest.raises(ValueError):
            check(catalog, "save_place", **bad)


# ------------------------------------------------------------------ stop and edit

def test_stop_and_edit_take_a_copys_name(catalog):
    assert check(catalog, "stop", copy="galick_gun_2").values == {"copy": "galick_gun_2"}
    got = check(catalog, "edit", copy="galick_gun_2", values={"cooldown": "0", "player": "Steve,Alex"})
    assert got.values == {"copy": "galick_gun_2", "values": {"cooldown": "0", "player": "Steve,Alex"}}
    for bad in ("Galick", "_gun", "a" * 49, "two words", "gun;stop"):
        with pytest.raises(ValueError):
            check(catalog, "stop", copy=bad)
    with pytest.raises(ValueError):
        check(catalog, "edit", copy="galick_gun", values="not a dict")


def test_every_pattern_reaches_the_planners_schema(catalog):
    """The planner's answer is held to the schema: each pattern is in it."""
    from actions import _types
    A = lib()

    def prop(name, field):
        return A.planner_model(catalog.get(name)).model_json_schema()["properties"][field]

    assert prop("teleport", "player")["pattern"] == _types.PLAYERS_PATTERN
    assert prop("summon_around", "mob")["pattern"] == _types.ID_PATTERNS["mob"]
    assert prop("save_place", "name")["pattern"] == r"^[a-z0-9_]{0,32}$"
    assert prop("stop", "copy")["pattern"] == r"^(|[a-z0-9][a-z0-9_]{0,47})$"
    assert prop("command", "commands")["maxItems"] == 5
    assert prop("teleport", "to")["enum"][0] == "biome"


def test_the_command_rules_are_the_one_module():
    """The actions use scripts/_lib/commands.py, the one list; that mc.py, vfg and the guard
    import it too, and nothing writes the words out again, is toolkit/tests' TestOneRefusedList."""
    from actions import _types
    commands = _types._commands  # scripts/_lib/commands.py, loaded by path
    assert Path(commands.__file__) == REPO / "scripts" / "_lib" / "commands.py"
    assert (_types.REFUSED, _types.BLOCK_COMMANDS, _types.RCON_MAX, _types.command_problem) == \
        (commands.REFUSED, commands.BLOCK_COMMANDS, commands.RCON_MAX, commands.command_problem)
    assert set(commands.BLOCK_COMMANDS) == {"setblock", "fill", "clone", "place", "fillbiome"}
    assert commands.RCON_MAX == _types.RCON_MAX == 1400
