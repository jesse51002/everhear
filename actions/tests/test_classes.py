"""The action classes: they load, say what the page says, refuse what they must, and
give the vfg argv that runs them."""
from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from actions import _types
from actions._types import RCON_MAX, Action, Id, MobId, PlayerOrAll, script_names
from actions.command.action import Command
from actions.run_script.action import RunScript
from actions.save_place.action import SavePlace
from actions.stop_script.action import StopScript
from actions.summon_around.action import SummonAround
from actions.teleport.action import Teleport

from conftest import REPO, action_namespace

# The complexity selector's menu lines, verbatim from the Minecraft_Actions page.
MENU = {
    "command": "Run vanilla commands, in order: anything that takes effect at once — items, effects, time, "
               "weather, killing or summoning a few mobs, attributes, game rules, sounds, titles, looking up "
               "where someone is. Never places or removes blocks.",
    "teleport": "Move a player, or everyone, to a biome, a structure, a saved place, coordinates, far away, "
                "their spawn point or where they last died, landing on safe ground.",
    "summon_around": "Summon a number of one mob round each player, spread to safe spots, optionally scaled, "
                     "named or glowing.",
    "run_script": "Start one of the scripts in scripts/, by name, with its arguments.",
    "stop_script": "Stop a running script, by name.",
    "save_place": "Save where a player stands, or given coordinates, under a name everyone can use.",
}


def action_classes() -> dict[str, type[Action]]:
    """Load every actions/<name>/action.py the way the dispatcher does."""
    out = {}
    for folder in sorted((REPO / "actions").iterdir()):
        if not (folder / "action.py").is_file():
            continue
        mod = importlib.import_module("actions.%s.action" % folder.name)
        found = [v for v in vars(mod).values()
                 if isinstance(v, type) and issubclass(v, Action) and v is not Action and v.__module__ == mod.__name__]
        assert len(found) == 1, "%s defines %d actions" % (folder.name, len(found))
        out[folder.name] = found[0]
    return out


# ------------------------------------------------------------------ every class

def test_every_action_on_the_page_loads():
    classes = action_classes()
    assert set(classes) == set(MENU)
    for name, cls in classes.items():
        field = cls.model_fields["action"]
        assert field.annotation.__args__ == (name,), name
        assert cls.__doc__.splitlines()[0] == MENU[name], name
        assert cls.model_config.get("extra") == "forbid"


def test_every_field_is_described_in_words():
    for name, cls in action_classes().items():
        for field, info in cls.model_fields.items():
            if field == "action":
                continue
            assert info.description and len(info.description.split()) >= 3, (name, field)
        schema = cls.model_json_schema()
        assert schema["description"].startswith(MENU[name])


def test_unknown_fields_are_refused():
    with pytest.raises(ValidationError):
        Command(action="command", commands=["time set day"], also="x")


def test_handlers_exist_where_the_page_says():
    assert (REPO / "actions/teleport/handler.py").is_file()
    assert (REPO / "actions/summon_around/handler.py").is_file()
    assert not (REPO / "actions/command/handler.py").exists()


def test_handlers_are_python_39_and_stdlib_only():
    allowed = {"math", "random", "re", "mc", "places"}
    for name in ("teleport", "summon_around"):
        source = (REPO / "actions" / name / "handler.py").read_text()
        tree = ast.parse(source, feature_version=(3, 9))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert {a.name for a in node.names} <= allowed, name
            if isinstance(node, ast.ImportFrom):
                assert node.module in allowed, name


# ------------------------------------------------------------------ shared types

def test_shared_types():
    assert TypeAdapter(Id).validate_python("minecraft:Snowy_Taiga") == "snowy_taiga"
    assert TypeAdapter(Id).validate_python("#minecraft:village") == "#village"
    assert TypeAdapter(MobId).validate_python("minecraft:zombie") == "zombie"
    for bad in ("#minecraft:skeletons", "potion", "two words", ""):  # MobId is the kit's: a gone id, no tag
        with pytest.raises(ValidationError):
            TypeAdapter(MobId).validate_python(bad)
    assert TypeAdapter(PlayerOrAll).validate_python("@a") == "@a"
    assert TypeAdapter(PlayerOrAll).validate_python(" Steve ") == "Steve"
    for bad in ("@e", "@p", "Steve Two", "a" * 17, "Steve;op"):
        with pytest.raises(ValidationError):
            TypeAdapter(PlayerOrAll).validate_python(bad)


def test_script_name_is_built_from_scripts():
    want = sorted(p.name for p in (REPO / "scripts").iterdir()
                  if p.is_dir() and (p / "main.py").is_file() and not p.name.startswith(("_", ".")))
    assert want and list(_types.SCRIPT_NAMES) == want
    assert not any(n.startswith("_") for n in _types.SCRIPT_NAMES)
    schema = RunScript.model_json_schema()
    assert schema["properties"]["name"]["enum"] == want
    with pytest.raises(ValidationError):
        RunScript(action="run_script", name="_lib")
    with pytest.raises(ValidationError):
        StopScript(action="stop_script", name="not_a_script")


def test_script_names_reads_only_folders_with_main(tmp_path):
    for d, main in (("zoo", True), ("tnt_rain", True), ("_lib", True), ("_test", True), ("_presets", True),
                    ("_kit", False), ("draft", False), (".git", True)):
        (tmp_path / d).mkdir()
        if main:
            (tmp_path / d / "main.py").write_text("")
    (tmp_path / "README.md").write_text("")
    assert script_names(tmp_path) == ("tnt_rain", "zoo")
    assert script_names(tmp_path / "missing") == ()


def refused_in(path: str) -> set[str]:
    """The REFUSED = frozenset([...]) of a Python file, read without importing it."""
    for node in ast.walk(ast.parse((REPO / path).read_text())):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "REFUSED" for t in node.targets):
            return set(ast.literal_eval(node.value.args[0]))
    raise AssertionError("no REFUSED in " + path)


def test_the_command_rules_are_one_module_and_every_refused_list_matches_it():
    commands = _types._commands  # toolkit/remote/commands.py, loaded by path
    assert Path(commands.__file__) == REPO / "toolkit" / "remote" / "commands.py"
    assert (_types.REFUSED, _types.BLOCK_COMMANDS, _types.RCON_MAX, _types.command_problem) == \
        (commands.REFUSED, commands.BLOCK_COMMANDS, commands.RCON_MAX, commands.command_problem)
    assert set(commands.REFUSED) == refused_in("toolkit/remote/commands.py") == refused_in("scripts/_lib/mc.py")
    if (REPO / "orchestrator/orchestrator/guard.py").exists():
        assert set(commands.REFUSED) == refused_in("orchestrator/orchestrator/guard.py")
    assert set(commands.BLOCK_COMMANDS) == {"setblock", "fill", "clone", "place", "fillbiome"}
    assert commands.RCON_MAX == RCON_MAX == 1400
    # EC2 gets the same file: vfg ships it ahead of action.py, which keeps no copy of its own
    ns = action_namespace()
    assert ns["commands"].command_problem("fill ~ ~ ~ ~ ~ ~ air") == commands.command_problem("fill ~ ~ ~ ~ ~ ~ air")
    assert "REFUSED" not in ns and "command_problem" not in ns
    source = (REPO / "toolkit/remote/action.py").read_text()
    assert "frozenset(" not in source and "RCON_MAX =" not in source


# ------------------------------------------------------------------ command

def cmd(*commands: str) -> Command:
    return Command(action="command", commands=list(commands))


@pytest.mark.parametrize("word", ["setblock", "fill", "clone", "place", "fillbiome"])
def test_command_refuses_blocks_alone_and_after_run(word):
    for c in (word + " ~ ~ ~ stone",
              "/" + word + " ~ ~ ~ stone",
              word.upper() + " ~ ~ ~ stone",
              "minecraft:" + word + " ~ ~ ~ stone",
              "execute as @a at @s run " + word + " ~ ~-1 ~ stone",
              "execute if entity @a run execute at @r run " + word + " ~ ~ ~ air",
              "execute as @a run /" + word + " ~ ~ ~ air",
              "return run " + word + " ~ ~ ~ stone"):
        with pytest.raises(ValidationError, match="building is the orchestrator's"):
            cmd(c)
    with pytest.raises(ValidationError):
        cmd("time set day", "execute at Steve run " + word + " ~ ~ ~ tnt")


@pytest.mark.parametrize("word", sorted(_types.REFUSED))
def test_command_refuses_the_owners_commands(word):
    for c in (word + " Steve", "/" + word.upper() + " Steve", "execute as @a run " + word + " Steve"):
        with pytest.raises(ValidationError, match="owner"):
            cmd(c)


def test_command_keeps_what_is_allowed_and_strips_the_slash():
    c = cmd("/give Steve minecraft:golden_apple 64", "  effect give @a minecraft:speed 30 1  ",
            "say stop it, we fill the gaps", "execute as @a at @s run summon minecraft:zombie ~ ~ ~",
            "kill @e[type=minecraft:wither]")
    assert c.commands == ["give Steve minecraft:golden_apple 64", "effect give @a minecraft:speed 30 1",
                          "say stop it, we fill the gaps", "execute as @a at @s run summon minecraft:zombie ~ ~ ~",
                          "kill @e[type=minecraft:wither]"]


def test_command_size_limits():
    ok = "say " + "a" * (RCON_MAX - 4)
    assert len(cmd(ok).commands[0].encode()) == RCON_MAX
    with pytest.raises(ValidationError, match="RCON takes at most 1400"):
        cmd(ok + "a")
    with pytest.raises(ValidationError, match="RCON takes at most 1400"):
        cmd("say " + "é" * 699)  # 1402 bytes of UTF-8, 703 characters
    for bad in ([], ["time set day"] * 6, ["", "time set day"], ["/"], ["say a\nop Steve"]):
        with pytest.raises(ValidationError):
            Command(action="command", commands=bad)


# ------------------------------------------------------------------ vfg argv

def test_vfg_argv_for_handler_and_command_actions():
    c = cmd("give @a minecraft:golden_apple 64", "give @a minecraft:cooked_beef 64")
    assert c.vfg_argv("T7") == ["action", "run", "command", "--args",
                                '{"commands":["give @a minecraft:golden_apple 64","give @a minecraft:cooked_beef 64"]}',
                                "--task", "T7"]
    t = Teleport(action="teleport", player="Steve", to="biome", target="snowy_taiga")
    argv = t.vfg_argv("T12")
    assert argv[:3] == ["action", "run", "teleport"] and argv[5:] == ["--task", "T12", "--timeout", "60"]
    assert json.loads(argv[4]) == {"player": "Steve", "to": "biome", "target": "snowy_taiga",
                                   "dimension": "overworld"}
    s = SummonAround(action="summon_around", players="@a", mob="minecraft:zombie", count=100)
    argv = s.vfg_argv("T3")
    assert argv[:3] == ["action", "run", "summon_around"] and argv[5:7] == ["--task", "T3"]
    assert json.loads(argv[4]) == {"players": "@a", "mob": "zombie", "count": 100, "radius": 16, "scale": 1.0,
                                   "name": "", "glowing": False}


def test_vfg_argv_for_scripts_and_places():
    name = _types.SCRIPT_NAMES[0]
    assert RunScript(action="run_script", name=name, args=["Steve", "200"]).vfg_argv("T1") == \
        ["script", "run", "--task", "T1", name, "Steve", "200"]
    assert RunScript(action="run_script", name=name).vfg_argv("T1") == ["script", "run", "--task", "T1", name]
    assert RunScript(action="run_script", name=name, args=["-5", "--wait"]).vfg_argv("T1") == \
        ["script", "run", "--task", "T1", name, "--", "-5", "--wait"]
    assert StopScript(action="stop_script", name=name).vfg_argv("T1") == ["script", "stop", name]
    assert SavePlace(action="save_place", name="castle", player="Steve").vfg_argv("T23") == \
        ["place", "save", "castle", "--player", "Steve", "--by", "T23"]
    assert SavePlace(action="save_place", name="home", at="-368.5 70 912").vfg_argv("T41") == \
        ["place", "save", "home", "--at", "-369", "70", "912", "--by", "T41"]


def test_run_script_args():
    name = _types.SCRIPT_NAMES[0]
    with pytest.raises(ValidationError):
        RunScript(action="run_script", name=name, args=["a\nb"])
    with pytest.raises(ValidationError):
        RunScript(action="run_script", name=name, args=["x"] * 11)


def script_values(name: str) -> dict | None:
    """scripts/<name>/main.py's VALUES literal, read without running it; None without one."""
    for node in ast.parse((REPO / "scripts" / name / "main.py").read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "VALUES" for t in node.targets):
            return ast.literal_eval(node.value)
    return None


def test_run_script_key_value_args_reach_vfg_script_run_as_its_values():
    """A new-format script's key=value args, its names read from its VALUES (never written
    here: they change), go through vfg's own parser and its value check unchanged."""
    from conftest import toolkit

    with_values = [(n, v) for n in _types.SCRIPT_NAMES if (v := script_values(n))]
    if not with_values:
        pytest.skip("no script in scripts/ declares VALUES yet")
    name, values = with_values[0]
    picked = list(values)[:2]

    def word(x) -> str:  # a default as key=value carries it
        return json.dumps(x, separators=(",", ":")) if isinstance(x, (bool, list)) else str(x)

    args = ["%s=%s" % (k, word(values[k]["default"])) for k in picked]
    action = RunScript(action="run_script", name=name, args=args)
    argv = action.vfg_argv("T7")
    assert argv == ["script", "run", "--task", "T7", name, *args]

    vfg = toolkit()
    a = vfg.build_parser().parse_args(vfg.rewrite_argv(argv))
    run = vfg.run_words(a)
    assert (run.name, run.task, run.words, run.instance) == (name, "T7", args, None)
    library = vfg.lib_module("library")
    given, _ = library.parse_given(values, run.words)
    assert list(given) == picked


# ------------------------------------------------------------------ teleport

@pytest.mark.parametrize("to,target,want", [
    ("biome", "minecraft:Snowy_Taiga", "snowy_taiga"),
    ("biome", "cherry grove", "cherry_grove"),
    ("biome", "#minecraft:is_forest", "#is_forest"),
    ("structure", "#village", "#village"),
    ("structure", "minecraft:ancient_city", "ancient_city"),
    ("place", "My Castle", "my_castle"),
    ("coords", "100, -200", "100 -200"),
    ("coords", "-12.5 64 30.9", "-13 64 30"),
    ("far", "", "1000"),
    ("far", "5000", "5000"),
    ("spawn_point", "", ""),
    ("spawn_point", "anything", ""),
    ("last_death", "", ""),
])
def test_teleport_targets(to, target, want):
    assert Teleport(action="teleport", player="Steve", to=to, target=target).target == want


@pytest.mark.parametrize("to,target", [
    ("biome", ""), ("biome", "snowy taiga!"), ("structure", "mod:thing"), ("place", ""), ("place", "a" * 33),
    ("coords", "a b"), ("coords", "100"), ("coords", "1 2 3 4"), ("coords", "40000000 0"), ("coords", "0 400 0"),
    ("far", "-5"), ("far", "far"), ("far", "2000000"),
])
def test_teleport_bad_targets(to, target):
    with pytest.raises(ValidationError):
        Teleport(action="teleport", player="Steve", to=to, target=target)


def test_teleport_dimension_words():
    for said, want in (("nether", "the_nether"), ("minecraft:the_end", "the_end"), ("Overworld", "overworld")):
        assert Teleport(action="teleport", player="@a", to="far", dimension=said).dimension == want
    with pytest.raises(ValidationError):
        Teleport(action="teleport", player="Steve", to="far", dimension="moon")
    with pytest.raises(ValidationError):
        Teleport(action="teleport", player="Steve", to="village")


# ------------------------------------------------------------------ summon_around and save_place

def test_summon_around_limits():
    base = dict(action="summon_around", players="Steve", mob="zombie")
    s = SummonAround(**base)
    assert (s.count, s.radius, s.scale, s.name, s.glowing) == (10, 16, 1.0, "", False)
    for bad in (dict(count=0), dict(count=101), dict(radius=3), dict(radius=65), dict(scale=0.1), dict(scale=9),
                dict(name="a" * 33), dict(name="two\nlines"), dict(mob="#zombies"), dict(players="@e")):
        with pytest.raises(ValidationError):
            SummonAround(**{**base, **bad})


def test_save_place():
    assert SavePlace(action="save_place", name="Zoo Entrance", player="Steve").name == "zoo_entrance"
    assert SavePlace(action="save_place", name="home", at="1, 64, -3").at == "1 64 -3"
    for bad in (dict(), dict(player="Steve", at="1 2 3"), dict(at="1 2"), dict(at="up there"),
                dict(player="Steve Two")):
        with pytest.raises(ValidationError):
            SavePlace(action="save_place", name="home", **bad)
    with pytest.raises(ValidationError):
        SavePlace(action="save_place", name="ca$tle", player="Steve")


def test_actions_round_trip_through_json():
    """What the action planner returns is JSON; each class reads back what it wrote."""
    for a in (cmd("time set day"), Teleport(action="teleport", player="@a", to="place", target="castle"),
              SummonAround(action="summon_around", players="Steve", mob="cow", count=3, glowing=True),
              SavePlace(action="save_place", name="home", at="1 2 3")):
        again = type(a).model_validate_json(a.model_dump_json())
        assert again == a
        assert isinstance(again, BaseModel)


def test_every_pattern_reaches_the_json_schema():
    """The model's structured output is held to the schema: each pattern must be in it,
    through Optional too, and so must each Literal's choices."""
    from actions._types import ID_PATTERN, ID_PATTERNS, PLACE_PATTERN, PLAYER_OR_ALL_PATTERN, PLAYER_PATTERN

    def pattern(schema: dict, field: str) -> str | None:
        prop = schema["properties"][field]
        for alt in [prop, *prop.get("anyOf", [])]:
            if "pattern" in alt:
                return alt["pattern"]
        return None

    want = {
        (Teleport, "player"): PLAYER_OR_ALL_PATTERN,
        (SummonAround, "players"): PLAYER_OR_ALL_PATTERN,
        (SummonAround, "mob"): ID_PATTERNS["mob"],
        (SavePlace, "name"): PLACE_PATTERN,
        (SavePlace, "player"): PLAYER_PATTERN,
    }
    for (cls, field), pat in want.items():
        assert pattern(cls.model_json_schema(), field) == pat, (cls.__name__, field)
    # And no field validated by a pattern is missing one in the schema.
    for name, cls in action_classes().items():
        schema = cls.model_json_schema()
        for field, info in cls.model_fields.items():
            metas = [m for m in info.metadata if getattr(m, "pattern", None)]
            if metas:
                assert pattern(schema, field) == metas[0].pattern, (name, field)
    assert Teleport.model_json_schema()["properties"]["to"]["enum"][0] == "biome"
    # The normalising still runs.
    assert SavePlace(action="save_place", name=" My Castle ", player=" Steve ").player == "Steve"
    assert SummonAround(action="summon_around", players=" @a ", mob=" Minecraft:Zombie ").mob == "zombie"
