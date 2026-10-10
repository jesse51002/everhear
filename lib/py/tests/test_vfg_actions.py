"""vfg_actions and actions/_types.py: the repo's actions load and pass their check, values are
read and checked as the kit reads them, the planner's and the editor's models, the catalog's
loading and reloading, and actions.json. No network and no server; the kit itself runs only in
a subprocess, to compare its value checks with these."""

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

import vfg_actions as A
from vfg_actions import Catalog, cache, check_action, check_edit, check_values, describe, edit_model, menu_line, \
    planner_model

REPO = Path(__file__).resolve().parents[3]


def purge():
    for key in [k for k in sys.modules
                if k == "actions" or k.startswith(("actions.", "vfg_made__", "vfg_preset__"))]:
        del sys.modules[key]


@pytest.fixture(autouse=True)
def fresh_modules():
    """No test sees another's actions: the actions package and the made and preset modules
    go before and after each, and sys.path is put back."""
    saved = list(sys.path)
    purge()
    yield
    purge()
    sys.path[:] = saved


@pytest.fixture
def lib():
    """The repo's own library actions."""
    return Catalog(REPO, None)


def T():
    """actions/_types.py: the one the last catalog loaded, else the repo's."""
    if "actions._types" not in sys.modules and str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    import actions._types as t
    return t


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


@pytest.fixture
def repo(tmp_path):
    """A small repo: actions/ with the real _types.py (linked, so the kit's GONE and the command
    rules are the repo's), railgun and gatling_gun copied with the damage_hits preset, and a
    few script folders."""
    (tmp_path / "actions").mkdir()
    (tmp_path / "actions" / "__init__.py").write_text("")
    (tmp_path / "actions" / "_types.py").symlink_to(REPO / "actions" / "_types.py")
    for name in ("railgun", "gatling_gun"):
        shutil.copy(REPO / "actions" / (name + ".py"), tmp_path / "actions")
    (tmp_path / "scripts" / "_presets").mkdir(parents=True)
    shutil.copy(REPO / "scripts" / "_presets" / "damage_hits.py", tmp_path / "scripts" / "_presets")
    for name in ("railgun", "zoo"):
        write(tmp_path / "scripts" / name / "main.py", '"""%s"""\n' % name)
    return tmp_path


ZOO = '''
    from actions._types import Action, Field, Live, MobId, Players


    class Zoo(Action):
        """Spawn random animals around a player."""

        script = "zoo"
        runs = "until_stopped"
        takes = ["player", "count"]

        player: Players = "@a"
        """round whom"""
        count: Live[int] = Field(10, ge=1, le=100)
        """how many"""
        mob: MobId = "cow"
        """which animal"""
'''


# ------------------------------------------------------------------ the repo's actions

def test_every_library_action_loads_named_as_its_file_and_passes_its_check(lib):
    files = sorted(p.stem for p in (REPO / "actions").glob("*.py") if not p.name.startswith("_"))
    assert files and lib.names() == files and lib.errors() == {}
    for name in lib.names():
        cls = lib.get(name)
        assert cls.__name__ == T().camel(name) and lib.source(name) == "library"
        assert check_action(name, cls) == [], name
        assert lib.file(name) == REPO / "actions" / (name + ".py")


def test_railgun_and_gatling_gun_are_as_the_spec_lists_them(lib):
    rail, gat = lib.get("railgun"), lib.get("gatling_gun")
    assert menu_line("railgun", rail) == ("railgun — Give a player a railgun: click to fire a beam that hurts "
                                          "everything it passes, through walls.")
    assert menu_line("gatling_gun", gat) == "gatling_gun — Give a player a gatling gun: hold right-click to rapid-fire."
    assert (rail.script, rail.runs, rail.takes, rail.fixed) == \
        ("railgun", "until_stopped", ["player", "damage_amount", "cooldown", "range", "through_walls"], {"fire": "click"})
    assert issubclass(gat, rail) and (gat.script, gat.runs) == ("railgun", "until_stopped")
    assert gat.takes == ["player", "damage_amount", "damage_what"]
    assert gat.fixed == {"fire": "hold", "item": "Gatling Gun", "cooldown": 0.1, "range": 24,
                         "particle": "crit", "sound": "entity.arrow.shoot"}
    # The preset's values are inherited, with railgun's own defaults over them; the fixed
    # values are the variant's defaults; the preset's class itself is untouched.
    damage = T().preset("damage_hits")
    assert issubclass(rail, damage) and damage.model_fields["damage_amount"].default == 20
    assert rail().damage_amount == 30 and gat().damage_amount == 30 and gat().damage_type == "magic"
    assert (rail().fire, gat().fire, gat().cooldown, rail().cooldown) == ("click", "hold", 0.1, 2)
    assert A.live_fields(rail) == [k for k, f in rail.model_fields.items() if T().LIVE in f.metadata]
    assert "fire" not in A.live_fields(rail) and "cooldown" in A.live_fields(rail)


def test_the_preset_class_is_read_alone_and_describes_every_value():
    had_mc = "mc" in sys.modules
    damage = T().preset("damage_hits")
    assert ("mc" in sys.modules) == had_mc  # only the class ran: no mc, no kit
    assert damage.__name__ == "DamageHits" and issubclass(damage, T().Values)
    for key, f in damage.model_fields.items():
        assert key.startswith("damage_") and len((f.description or "").split()) >= 3, key
    assert T().preset("damage_hits") is damage  # read once while the file is unchanged
    with pytest.raises(ImportError):
        T().preset("no_such_preset")


def test_describe_marks_takes_and_shows_types_limits_defaults_and_the_values_now(lib):
    rail, gat = lib.get("railgun"), lib.get("gatling_gun")
    text = describe("railgun", rail)
    lines = text.splitlines()
    assert lines[0] == menu_line("railgun", rail) and lines[1] == "script railgun · runs until stopped"
    assert [l.split()[1] for l in lines[3:8]] == rail.takes and all(l.startswith("* ") for l in lines[3:8])
    assert "* cooldown (a number 0..60; live; default 2): seconds between shots" in lines
    assert any(l.startswith("  fire (one of click, hold; fixed \"click\"): ") for l in lines)
    assert any(l.startswith("  damage_type (one of player_attack, ") and 'default "magic"' in l for l in lines)
    assert len(lines) == 3 + len(rail.model_fields)
    now = describe("gatling_gun", gat, current={"cooldown": 0.5})
    assert "  cooldown (a number 0..60; live; now 0.5): seconds between shots" in now.splitlines()
    assert "  range (a whole number 4..300; live; now 24): " in now


# ------------------------------------------------------------------ values

def test_check_values_fills_every_field_and_reads_words_as_the_kit_does(lib):
    rail = lib.get("railgun")
    got = check_values(rail, {"player": " Steve , Alex ", "cooldown": "500", "range": "2", "through_walls": "no",
                              "particle": "minecraft:flame", "damage_spare": "Steve,Alex", "damage_what": "HOSTILE",
                              "damage_most": "2.6"})
    assert set(got.values) == set(rail.model_fields)
    assert got.values["player"] == "Steve,Alex" and got.values["cooldown"] == 60 and got.values["range"] == 4
    assert got.values["through_walls"] is False and got.values["particle"] == "flame"
    assert got.values["damage_spare"] == ["Steve", "Alex"] and got.values["damage_what"] == "hostile"
    assert got.values["damage_most"] == 3 and got.values["damage_amount"] == 30 and got.values["fire"] == "click"
    assert got.notes == ["cooldown=500 is over its max, clamped to 60", "range=2 is under its min, clamped to 4"]
    assert got.live == A.live_fields(rail)
    assert json.loads(json.dumps(got.values)) == got.values
    assert check_values(rail, {"damage_spare": '["Steve"]'}).values["damage_spare"] == ["Steve"]
    assert check_values(rail, {"damage_spare": ""}).values["damage_spare"] == []
    assert check_values(rail, {}).values == check_values(rail, {}).values
    assert check_values(rail, {}).notes == []


@pytest.mark.parametrize("given,says", [
    ({"nope": 1}, "railgun has no value nope"),
    ({"range": "far"}, "range=far:"),
    ({"range": True}, "range takes a number, not true"),
    ({"cooldown": "nan"}, "cooldown takes a finite number"),
    ({"through_walls": "maybe"}, "through_walls is true or false, not maybe"),
    ({"damage_type": "burst"}, "damage_type=burst: Input should be 'player_attack', "),
    ({"player": "@e"}, "player=@e: should be player names separated by commas, or @a, @r, @p"),
    ({"particle": "two words"}, "particle=two words: should be a particle id"),
    ({"damage_spare": "Steve,@a"}, "damage_spare=@a: each should be a player's name"),
    ({"item": "two\nlines"}, "item holds a newline"),
    ({"damage_spare": "[oops"}, "damage_spare is a list"),
])
def test_check_values_refuses_in_words(lib, given, says):
    with pytest.raises(ValueError) as e:
        check_values(lib.get("railgun"), given)
    assert says in str(e.value)


def test_a_fixed_value_is_refused_unless_allowed(lib):
    gat = lib.get("gatling_gun")
    with pytest.raises(ValueError, match="gatling_gun: fire is fixed at hold; cooldown is fixed at 0.1"):
        check_values(gat, {"fire": "click", "cooldown": 3})
    assert check_values(gat, {"fire": "click"}, allow_fixed=True).values["fire"] == "click"
    assert check_values(gat, {}).values["cooldown"] == 0.1


def test_check_edit_changes_values_on_a_copy(lib):
    gat = lib.get("gatling_gun")
    current = check_values(gat, {"player": "Alex"}).values
    e = check_edit(gat, current, {"cooldown": "0", "damage_what": "mobs"})
    assert e.changed == ["damage_what", "cooldown"]  # in the fields' order
    assert e.live_only and e.values["player"] == "Alex" and e.values["cooldown"] == 0 and e.notes == []
    e = check_edit(gat, current, {"fire": "click", "player": "Alex"})  # fixed may change; player is the same
    assert e.changed == ["fire"] and not e.live_only and e.values["fire"] == "click"
    e = check_edit(gat, current, {"damage_amount": 10 ** 9})
    assert e.values["damage_amount"] == 100000 and e.notes == ["damage_amount=1000000000 is over its max, clamped to 100000"]
    e = check_edit(gat, {"player": "Alex", "gone_field": 1}, {})
    assert e.changed == [] and e.live_only and e.values["player"] == "Alex"
    with pytest.raises(ValueError, match="has no value"):
        check_edit(gat, current, {"colour": "red"})


# ------------------------------------------------------------------ the value types

KIT_SAMPLES = {
    "mob": ["zombie", "minecraft:zombie", "Zombie", "ender_dragon", "mod:thing", "potion", "minecraft:boat",
            "chest_boat", "#minecraft:zombies", "two words", "", "zombie[x]", " cow "],
    "block": ["stone", "minecraft:stone", "#minecraft:logs", "#minecraft:tall_flowers", "#mineable/sword",
              "#minecraft:mineable/sword", "oak_stairs[facing=east]", "chest{Items:[]}", "Stone", "two words", "",
              "minecraft:oak_log[axis=y]"],
    "item": ["diamond_sword", "minecraft:diamond_sword[enchantments={sharpness:5}]", "#minecraft:logs",
             "#minecraft:tall_flowers", "potion", "a b", "Diamond", ""],
    "particle": ["flame", "dust{color:[1.0,0.0,0.0],scale:1.0}", "minecraft:end_rod", "a b", "#flame", "Flame"],
    "sound": ["entity.wither.spawn", "minecraft:entity.arrow.shoot", "Entity.Wither.Spawn", "a b", "#x"],
    "player": ["@a", "@r", "@p", "@e", "@A", "Steve", "Steve,Alex", " Steve , Alex ", "a b", "", "a" * 17],
}


def kit_check(pairs: list) -> list:
    """kit/values.py's check of each (kind, raw), run in a subprocess as a script would:
    [ok, value] each."""
    code = textwrap.dedent("""
        import json, sys
        from kit import values
        out = []
        for kind, raw in json.load(sys.stdin):
            try:
                out.append([True, values.check("k", {"type": kind, "default": ""}, raw)[0]])
            except ValueError:
                out.append([False, None])
        print(json.dumps(out))
    """)
    env = {**os.environ, "PYTHONPATH": str(REPO / "scripts" / "_lib"), "VFG_INSTANCE": "t", "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([sys.executable, "-c", code], input=json.dumps(pairs), env=env, capture_output=True,
                       text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def drop_namespace(x: str) -> str:
    tag = x.startswith("#")
    x = x[1:] if tag else x
    return ("#" if tag else "") + (x[len("minecraft:"):] if x.startswith("minecraft:") else x)


def test_the_id_and_player_types_take_and_refuse_what_the_kit_does():
    t = T()
    types = {"mob": t.MobId, "block": t.BlockId, "item": t.ItemId, "particle": t.ParticleId,
             "sound": t.SoundId, "player": t.Players}
    pairs = [(kind, raw) for kind, raws in KIT_SAMPLES.items() for raw in raws]
    ours = []
    for kind, raw in pairs:
        try:
            ours.append([True, TypeAdapter(types[kind]).validate_python(raw)])
        except ValidationError:
            ours.append([False, None])
    kit = kit_check(pairs)
    for (kind, raw), (ok, value), (kit_ok, kit_value) in zip(pairs, ours, kit):
        assert ok == kit_ok, (kind, raw, value, kit_value)
        if ok:
            assert value == drop_namespace(kit_value), (kind, raw)  # minecraft: dropped, as the spec says
    # What we send passes the kit too, unchanged.
    sent = [(kind, value) for (kind, _), (ok, value) in zip(pairs, ours) if ok]
    assert all(ok and back == value for (_, value), (ok, back) in zip(sent, kit_check(sent)))


def test_a_gone_id_says_what_to_use():
    with pytest.raises(ValidationError, match="potion is gone in 26.3, use entity type: splash_potion"):
        TypeAdapter(T().MobId).validate_python("minecraft:potion")
    with pytest.raises(ValidationError, match="#tall_flowers is gone in 26.3"):
        TypeAdapter(T().BlockId).validate_python("#tall_flowers")


def test_player_takes_one_name():
    p = TypeAdapter(T().Player)
    assert p.validate_python(" Steve ") == "Steve"
    for bad in ("@a", "Steve,Alex", "a b", ""):
        with pytest.raises(ValidationError):
            p.validate_python(bad)


@pytest.mark.parametrize("raw,want", [
    ("", ""), ("100 64 -20", "100 64 -20"), ("100, 64, -20", "100 64 -20"), ("1.5 70 2.25", "1.5 70 2.25"),
    ("1 64 2 nether", "1 64 2 the_nether"), ("1 64 2 minecraft:the_end", "1 64 2 the_end"),
    ("1 64 2 overworld", "1 64 2"), ("Steve", "Steve"), ("home", "home"), ("My Castle", "my_castle"),
])
def test_place(raw, want):
    places = {"home": {}, "my_castle": {}}
    assert TypeAdapter(T().Place).validate_python(raw, context={"places": places}) == want


@pytest.mark.parametrize("raw,says", [
    ("0 400 0", "outside the world"), ("0 300 0 nether", "outside the world"), ("40000000 64 0", "outside the world"),
    ("1 2 3 moon", "overworld, the_nether or the_end"), ("not a place", "not a saved place (saved: home)"),
    ("a" * 20, "not a saved place"),
])
def test_place_refuses(raw, says):
    with pytest.raises(ValidationError, match=re.escape(says)):
        TypeAdapter(T().Place).validate_python(raw, context={"places": {"home": {}}})


def test_a_place_name_is_checked_only_when_the_places_are_given_and_only_where_given(repo):
    write(repo / "scripts" / "castle" / "main.py", '"""castle"""\n')
    write(repo / "actions" / "castle.py", '''
        from actions._types import Action, Place, Players


        class Castle(Action):
            """Build a castle."""

            script = "castle"
            runs = "once"
            takes = ["player", "castle_at"]

            player: Players = "@p"
            """for whom"""
            castle_at: Place = ""
            """where: empty for in front of the player"""
            gate_at: Place = "spawn_square"
            """where the gate faces"""
    ''')
    castle = Catalog(repo, None).get("castle")
    assert TypeAdapter(T().Place).validate_python("nowhere_at_all") == "nowhere_at_all"  # no places: unchecked
    assert check_values(castle, {"castle_at": "home"}, places={"home": {}}).values["castle_at"] == "home"
    with pytest.raises(ValueError, match="castle_at=two words: 'two words' is not a saved place"):
        check_values(castle, {"castle_at": "two words"}, places={"home": {}})
    # gate_at's default is not one of the places, but nobody gave it.
    assert check_values(castle, {}, places={"home": {}}).values["gate_at"] == "spawn_square"


def test_command():
    c = TypeAdapter(T().Command)
    assert c.validate_python("/give Steve diamond 3") == "give Steve diamond 3"
    for bad, says in (("fill ~ ~ ~ ~ ~ ~ air", "places or removes blocks"), ("op Steve", "owner"),
                      ("execute as @a run setblock ~ ~ ~ tnt", "blocks"), ("", "empty")):
        with pytest.raises(ValidationError, match=says):
            c.validate_python(bad)


def test_live_marks_and_keeps_limits():
    Field, Live, Values = T().Field, T().Live, T().Values

    class V(Values):
        a: Live[float] = Field(1, ge=0, le=2)
        """a"""
        b: int = 0
        """b"""

    assert T().is_live(V.model_fields["a"].metadata) and not T().is_live(V.model_fields["b"].metadata)
    assert T().limits(V.model_fields["a"].annotation, V.model_fields["a"].metadata) == (0, 2)
    assert V.model_json_schema()["properties"]["a"]["maximum"] == 2
    notes = []
    assert V.model_validate({"a": "5"}, context={"notes": notes}).a == 2.0 and notes == ["a=5 is over its max, clamped to 2"]
    with pytest.raises(ValidationError):
        V.model_validate({"a": 5}, context={"clamp": False})


# ------------------------------------------------------------------ the models for structured()

def test_the_planner_model_holds_only_takes_with_their_schema(lib):
    gat = lib.get("gatling_gun")
    m = planner_model(gat)
    schema = m.model_json_schema()
    assert list(schema["properties"]) == gat.takes and schema["additionalProperties"] is False
    assert schema["description"].startswith("Give a player a gatling gun")
    p = schema["properties"]
    assert p["player"]["pattern"] == T().PLAYERS_PATTERN and p["player"]["default"] == "@a"
    assert (p["damage_amount"]["minimum"], p["damage_amount"]["maximum"], p["damage_amount"]["default"]) == (0, 100000, 30)
    assert p["damage_what"]["enum"] == ["all", "mobs", "hostile", "players"]
    assert p["damage_what"]["description"].startswith("what it hurts")
    assert m.model_validate_json('{"damage_amount": 5000000}').damage_amount == 100000  # clamped, not refused
    with pytest.raises(ValidationError):
        m.model_validate_json('{"fire": "click"}')
    answer = m.model_validate_json('{"player": "Alex"}').model_dump(exclude_unset=True)
    assert answer == {"player": "Alex"} and check_values(gat, answer).values["player"] == "Alex"
    assert planner_model(gat) is m


def test_the_edit_model_has_every_field_optional(lib):
    rail = lib.get("railgun")
    m = edit_model(rail)
    schema = m.model_json_schema()
    assert set(schema["properties"]) == set(rail.model_fields) and not schema.get("required")
    cooldown = schema["properties"]["cooldown"]
    assert cooldown["default"] is None and {"type": "null"} in cooldown["anyOf"]
    assert any(a.get("maximum") == 60 for a in cooldown["anyOf"])
    assert m.model_validate({"cooldown": 1}).model_dump(exclude_none=True) == {"cooldown": 1.0}


# ------------------------------------------------------------------ check_action

def test_check_action_refuses_what_the_spec_says(repo):
    write(repo / "actions" / "bad.py", '''
        import datetime
        from typing import Optional
        from pydantic import AfterValidator
        from typing import Annotated
        from actions._types import Action, Field, Literal, Live, Players


        class Bad(Action):
            script = "nowhere"
            runs = "sometimes"
            takes = ["player", "missing", "mode"]
            defaults = {"count": 500, "ghost": 1}
            fixed = {"mode": "loud", "count": 3}

            player: Players = "@a"
            """who"""
            count: Live[int] = Field(5, ge=1, le=10)
            """how many"""
            mode: Literal["quiet", "calm"] = "quiet"
            """how"""
            when: datetime.date = datetime.date(2026, 1, 1)
            """when"""
            maybe: Optional[int] = None
            """maybe"""
            shout: Annotated[str, AfterValidator(str.upper)] = "x"
            """shout"""
            silent: int = 0
    ''')
    cat = Catalog(repo, None)
    assert cat.get("bad") is None
    why = cat.errors()["bad"]
    for says in ("no docstring", "script nowhere: there is no scripts/nowhere/main.py",
                 "runs is once or until_stopped, not 'sometimes'", "count is in both defaults and fixed",
                 "defaults names ghost, which is not one of its fields", "takes names missing, which is not one of its fields",
                 "takes names mode, which it fixes", "when: date is not one of actions/_types.py's types",
                 "maybe: Optional", "shout: str is not one of actions/_types.py's types",
                 "silent has no description"):
        assert says in why, says


def test_check_action_refuses_fixed_and_default_values_their_fields_refuse(repo):
    write(repo / "actions" / "loud.py", '''
        from actions._types import Action, Field, Literal, MobId


        class Loud(Action):
            """Be loud."""

            script = "zoo"
            runs = "once"
            takes = ["mob"]
            defaults = {"count": 500}
            fixed = {"mode": "deafening"}

            mob: MobId = "potion"
            """what"""
            count: int = Field(5, ge=1, le=10)
            """how many"""
            mode: Literal["quiet", "loud"] = "quiet"
            """how"""
    ''')
    why = Catalog(repo, None).errors()["loud"]
    assert "count=500 in defaults: Input should be less than or equal to 10" in why
    assert "mode=deafening in fixed:" in why
    assert "mob=potion in its default: potion is gone in 26.3" in why


def test_check_action_refuses_code_and_a_misnamed_class(repo):
    write(repo / "actions" / "coded.py", '''
        from pydantic import field_validator
        from actions._types import Action


        class Coded(Action):
            """Has code."""

            script = "zoo"
            runs = "once"

            n: int = 1
            """n"""

            @field_validator("n")
            @classmethod
            def _n(cls, v):
                return v

            def go(self):
                return 1
    ''')
    write(repo / "actions" / "misnamed.py", ZOO.replace("class Zoo", "class Zoological"))
    write(repo / "actions" / "two.py", ZOO + ZOO.replace("class Zoo", "class Two").split("\n", 2)[2])
    write(repo / "actions" / "broken.py", "import nothing_here_at_all\n")
    cat = Catalog(repo, None)
    errors = cat.errors()
    assert "Coded._n is code" in errors["coded"] and "Coded.go is code" in errors["coded"]
    assert errors["misnamed"] == "no class Misnamed(Action) in it, named as the file"
    assert errors["two"] == "one action class per file, not Two, Zoo"
    assert errors["broken"].startswith("failed to import: ModuleNotFoundError")
    assert cat.names() == ["gatling_gun", "railgun"]


def test_a_variant_may_take_back_a_fixed_value_as_its_own_default(repo):
    write(repo / "actions" / "slow_gatling_gun.py", '''
        from actions.gatling_gun import GatlingGun


        class SlowGatlingGun(GatlingGun):
            """Give a player a slow gatling gun."""

            takes = ["player", "cooldown"]
            defaults = {"cooldown": 1}
    ''')
    cat = Catalog(repo, None)
    slow = cat.get("slow_gatling_gun")
    assert cat.errors() == {} and check_action("slow_gatling_gun", slow) == []
    assert "cooldown" not in slow.fixed and slow.defaults["cooldown"] == 1 and slow.fixed["fire"] == "hold"
    assert slow().cooldown == 1 and slow.__vfg__["unfixed"]["cooldown"] == 1
    assert check_values(slow, {"cooldown": 3}).values["cooldown"] == 3


# ------------------------------------------------------------------ made actions and reloading

def test_made_actions_load_beside_the_library_and_may_subclass_it(repo):
    write(repo / "made" / "main" / "scripts" / "tnt_crouch" / "main.py", '"""tnt"""\n')
    write(repo / "made" / "main" / "actions" / "tnt_crouch.py", '''
        from actions._types import Action, Field, Live, Players


        class TntCrouch(Action):
            """Whenever a player crouches three times, TNT falls on them."""

            script = "tnt_crouch"
            runs = "until_stopped"
            takes = ["player"]

            player: Players = "@a"
            """whose crouching counts"""
            fuse: Live[int] = Field(40, ge=0, le=200)
            """ticks before it goes off"""
    ''')
    write(repo / "made" / "main" / "actions" / "steve_gun.py", '''
        from actions.railgun import Railgun


        class SteveGun(Railgun):
            """Give Steve his own railgun."""

            fixed = {"player": "Steve", "item": "Steve Gun"}
            takes = ["damage_amount"]
    ''')
    write(repo / "made" / "main" / "actions" / "railgun.py", ZOO.replace("class Zoo", "class Railgun"))
    write(repo / "made" / "other" / "actions" / "elsewhere.py", ZOO.replace("class Zoo", "class Elsewhere"))
    cat = Catalog(repo, "main")
    assert cat.names() == ["gatling_gun", "railgun", "steve_gun", "tnt_crouch"]
    assert cat.source("tnt_crouch") == "made" and cat.source("railgun") == "library"
    assert issubclass(cat.get("steve_gun"), cat.get("railgun")) and cat.get("steve_gun")().player == "Steve"
    assert "railgun is a library action's name" in cat.errors()["railgun"]
    assert check_action("tnt_crouch", cat.get("tnt_crouch")) == []
    assert cat.get("tnt_crouch").__module__ == "vfg_made__main__tnt_crouch"
    assert Catalog(repo, None).names() == ["gatling_gun", "railgun"]


def test_a_made_action_may_use_a_made_preset(repo):
    write(repo / "made" / "main" / "presets" / "crouch_count.py", '''
        """Count a player's crouches."""
        import mc
        from actions._types import Field, Live, Values


        class CrouchCount(Values):
            crouch_count_needed: Live[int] = Field(3, ge=1, le=10)
            """crouches in a row that count"""


        def crouch_count(p, v):
            return mc.rcon("x")
    ''')
    write(repo / "made" / "main" / "actions" / "tnt_crouch.py", '''
        from actions._types import Action, Players, preset


        class TntCrouch(Action, preset("crouch_count")):
            """TNT on a crouch."""

            script = "zoo"
            runs = "until_stopped"
            takes = ["player", "crouch_count_needed"]

            player: Players = "@a"
            """whose crouching counts"""
    ''')
    cat = Catalog(repo, "main")
    cls = cat.get("tnt_crouch")
    assert cat.errors() == {} and cls.model_fields["crouch_count_needed"].description == "crouches in a row that count"


def bump(path: Path, text: str) -> None:
    """Write a file so that its stamp surely changes, even within one clock tick."""
    write(path, text)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def test_refresh_picks_up_a_new_changed_or_gone_made_action_without_a_restart(repo):
    made = repo / "made" / "main" / "actions"
    cat = Catalog(repo, "main")
    assert cat.refresh() == []
    rail = cat.get("railgun")
    bump(made / "zoo.py", ZOO)
    assert cat.refresh() == ["zoo"] and "zoo" in cat.names() and cat.get("railgun") is rail
    bump(made / "zoo.py", ZOO.replace("Spawn random animals", "Spawn a zoo"))
    assert cat.refresh() == ["zoo"] and menu_line("zoo", cat.get("zoo")) == "zoo — Spawn a zoo around a player."
    assert cat.get("zoo").model_fields["count"].description == "how many"  # read from the new file
    bump(made / "zoo.py", "import nothing_here\n")
    assert cat.refresh() == ["zoo"] and cat.get("zoo") is None and "zoo" in cat.errors()
    (made / "zoo.py").unlink()
    assert cat.refresh() == [] and "zoo" not in cat.errors()
    assert cat.get("railgun") is rail


def test_refresh_reloads_the_library_when_a_library_file_changes(repo):
    cat = Catalog(repo, None)
    rail, gat = cat.get("railgun"), cat.get("gatling_gun")
    text = (repo / "actions" / "railgun.py").read_text().replace('"Railgun"', '"Rail Gun"')
    bump(repo / "actions" / "railgun.py", text)
    assert cat.refresh() == ["gatling_gun", "railgun"]
    assert cat.get("railgun") is not rail and cat.get("railgun")().item == "Rail Gun"
    assert issubclass(cat.get("gatling_gun"), cat.get("railgun")) and cat.get("gatling_gun")().item == "Gatling Gun"
    preset = repo / "scripts" / "_presets" / "damage_hits.py"
    bump(preset, preset.read_text().replace("Field(20, ge=0, le=100000)", "Field(20, ge=0, le=500)"))
    cat.refresh()
    assert T().limits(cat.get("railgun").model_fields["damage_amount"].annotation,
                      cat.get("railgun").model_fields["damage_amount"].metadata) == (0, 500)


# ------------------------------------------------------------------ actions.json

def test_cache_writes_every_action_and_rebuilds_only_when_a_file_is_newer(repo, tmp_path):
    home = tmp_path / "home"
    data = cache(repo, "main", home)
    path = home / "actions.json"
    assert json.loads(path.read_text()) == data and (path.stat().st_mode & 0o777) == 0o600
    assert (home / "actions.lock").exists()
    assert set(data["actions"]) == {"railgun", "gatling_gun"} and data["errors"] == {}
    assert data["files"] == ["actions/gatling_gun.py", "actions/railgun.py"]
    g = data["actions"]["gatling_gun"]
    assert g["line"] == "Give a player a gatling gun: hold right-click to rapid-fire."
    assert (g["script"], g["runs"], g["source"], g["file"], g["takes"]) == \
        ("railgun", "until_stopped", "library", "actions/gatling_gun.py", ["player", "damage_amount", "damage_what"])
    assert g["fixed"]["cooldown"] == 0.1 and g["defaults"] == {"damage_amount": 30, "damage_type": "magic"}
    assert g["fields"]["cooldown"] == {"type": "number", "default": 0.1, "ge": 0, "le": 60, "choices": None,
                                       "live": True, "description": "seconds between shots"}
    assert g["fields"]["damage_what"]["choices"] == ["all", "mobs", "hostile", "players"]
    assert g["fields"]["damage_spare"]["type"] == "list of player"
    assert g["fields"]["player"]["type"] == "players" and g["describe"] == describe("gatling_gun", Catalog(repo).get("gatling_gun"))
    old = os.path.getmtime(repo / "actions" / "railgun.py") - 10
    for p in [*(repo / "actions").glob("*.py"), *(repo / "scripts" / "_presets").glob("*.py")]:
        os.utime(p, (old, old))
    data = cache(repo, "main", home)
    again = cache(repo, "main", home)
    assert again["built_at"] == data["built_at"]  # fresh: read, not built
    write(repo / "made" / "main" / "actions" / "zoo.py", ZOO)
    os.utime(repo / "made" / "main" / "actions" / "zoo.py", (old, old))  # restored with an old mtime: still seen
    third = cache(repo, "main", home)
    assert third["built_at"] > data["built_at"] and third["actions"]["zoo"]["source"] == "made"
    (repo / "made" / "main" / "actions" / "zoo.py").unlink()
    assert "zoo" not in cache(repo, "main", home)["actions"]
    bump(repo / "actions" / "railgun.py", (repo / "actions" / "railgun.py").read_text().replace('"Railgun"', '"Rail"'))
    assert cache(repo, "main", home)["actions"]["railgun"]["fields"]["item"]["default"] == "Rail"
