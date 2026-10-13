"""The brain's menu: the game engine's eleven ops as actions (the game engine spec, part 2), each
loading, naming the op it goes to, taking what the menu says, and refusing what the engine would.

    uv run --no-project --with pytest --with pydantic pytest actions/tests/test_engine_actions.py

vfg sends their values to the engine (toolkit/vfg, run_engine_action); here they are only loaded
and checked, from a copy of the repo's actions/. The six core actions of the old system (command,
teleport, save_place, summon_around, stop, edit) are gone: the first four are library scenarios,
started with engine_start and their args, which the engine checks; stop and edit are engine_stop,
engine_revoke, engine_clear and engine_config.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
OPS = {"engine_grant": "grant", "engine_revoke": "revoke", "engine_give": "give", "engine_take": "take",
       "engine_apply": "apply", "engine_clear": "clear", "engine_start": "start", "engine_stop": "stop",
       "engine_bind": "bind", "engine_unbind": "unbind", "engine_config": "config"}
TAKES = {"engine_grant": ["player", "ability"], "engine_revoke": ["player", "ability"],
         "engine_give": ["player", "item"], "engine_take": ["player", "item"],
         "engine_apply": ["player", "mark", "args", "seconds"], "engine_clear": ["player", "mark"],
         "engine_start": ["scenario", "args"], "engine_stop": ["scenario", "instance"],
         "engine_bind": ["player", "phrase", "ability", "scenario", "args", "cooldown_s"],
         "engine_unbind": ["player", "phrase"], "engine_config": ["class_id", "config"]}
CONFIG = '''    class Config(AbilityConfig):
        # the blast, at power 0
        explosion_base: float = Field(12, ge=0, le=30)

        def damage(self, ctx):                 # required: the total
            return self.explosion_base
'''


def lib():
    if str(REPO / "lib" / "py") not in sys.path:
        sys.path.insert(0, str(REPO / "lib" / "py"))
    import vfg_actions
    return vfg_actions


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    """The repo's actions, copied beside the real _types.py."""
    root = tmp_path_factory.mktemp("repo")
    (root / "actions").mkdir()
    (root / "actions" / "__init__.py").write_text("")
    (root / "actions" / "_types.py").symlink_to(REPO / "actions" / "_types.py")
    for p in (REPO / "actions").glob("*.py"):
        if not p.name.startswith("_"):
            shutil.copy(p, root / "actions")
    saved_path = list(sys.path)
    saved = {k: v for k, v in sys.modules.items() if k == "actions" or k.startswith("actions.")}
    for k in saved:
        del sys.modules[k]
    yield lib().Catalog(root)
    for k in [k for k in sys.modules if k == "actions" or k.startswith("actions.")]:
        del sys.modules[k]
    sys.modules.update(saved)
    sys.path[:] = saved_path


def check(catalog, action, **given):
    return lib().check_values(catalog.get(action), given)


def test_the_menu_is_the_engine_ops_and_nothing_else(catalog):
    A = lib()
    assert catalog.errors() == {} and catalog.names() == sorted(OPS)
    for name, op in OPS.items():
        cls = catalog.get(name)
        assert A.check_action(name, cls) == [], name
        assert (A.engine_op(cls), cls.takes) == (op, TAKES[name]), name
        assert A.describe(name, cls).splitlines()[1].startswith("engine %s" % op), name
    assert {n: catalog.get(n).timeout_s for n in OPS if catalog.get(n).timeout_s} == {
        "engine_start": 45, "engine_bind": 40, "engine_config": 60}


def test_a_player_is_one_name_and_never_everyone(catalog):
    assert check(catalog, "engine_grant", player="Steve", ability="fireball").values == {
        "player": "Steve", "ability": "fireball"}
    for bad in ("@a", "Steve,Alex", "Ste ve", "a" * 17):
        for action, rest in (("engine_grant", {"ability": "fireball"}), ("engine_apply", {"mark": "giant"}),
                             ("engine_bind", {"phrase": "fire ball", "ability": "fireball"})):
            with pytest.raises(ValueError, match="one player's exact name"):
                check(catalog, action, player=bad, **rest)


def test_a_class_is_named_as_the_engine_names_it(catalog):
    for action, key in (("engine_grant", "ability"), ("engine_give", "item"), ("engine_take", "item"),
                        ("engine_start", "scenario"), ("engine_apply", "mark"), ("engine_clear", "mark"),
                        ("engine_config", "class_id"), ("engine_bind", "scenario")):
        assert check(catalog, action, **{key: "fire_staff"}).values[key] == "fire_staff"
        for bad in ("Fire Staff", "fire-staff", "1fire", "a" * 65):
            with pytest.raises(ValueError, match="a name as ENGINE shows it"):
                check(catalog, action, **{key: bad})
    assert check(catalog, "engine_stop", instance="scenario:horde#1").values["instance"] == "scenario:horde#1"
    with pytest.raises(ValueError, match="a run's name as RUNNING NOW shows it"):
        check(catalog, "engine_stop", instance="horde; stop")


def test_args_are_an_object_the_engine_checks(catalog):
    got = check(catalog, "engine_start", scenario="command", args={"commands": ["time set day"], "count": 3})
    assert got.values == {"scenario": "command", "args": {"commands": ["time set day"], "count": 3}}
    # a word holding JSON is read as the object (vfg action run's args={...})
    assert check(catalog, "engine_start", scenario="meteor", args='{"at": "caster"}').values["args"] == {"at": "caster"}
    assert check(catalog, "engine_start", scenario="meteor", args="").values["args"] == {}
    assert check(catalog, "engine_start", scenario="meteor").values["args"] == {}
    for bad in ("at=home", "[1, 2]", ["at"], 3):
        with pytest.raises(ValueError, match="args is an object"):
            check(catalog, "engine_start", scenario="meteor", args=bad)
    # a bind sets a scenario's args once, "caster" left for the engine to fill
    got = check(catalog, "engine_bind", player="Alex", phrase="meteor", scenario="meteor", args={"at": "caster"})
    assert (got.values["ability"], got.values["args"], got.values["cooldown_s"]) == ("", {"at": "caster"}, 3)


def test_numbers_are_clamped_with_a_note(catalog):
    got = check(catalog, "engine_apply", player="Steve", mark="giant", seconds="90000")
    assert got.values["seconds"] == 86400 and got.notes == ["seconds=90000 is over its max, clamped to 86400"]
    got = check(catalog, "engine_bind", player="Steve", phrase="boom", scenario="meteor", cooldown_s=-4)
    assert got.values["cooldown_s"] == 0 and got.notes == ["cooldown_s=-4 is under its min, clamped to 0"]
    with pytest.raises(ValueError, match="seconds takes a finite number"):
        check(catalog, "engine_apply", player="Steve", mark="giant", seconds="inf")


def test_an_unknown_value_is_refused_naming_the_ones_there_are(catalog):
    with pytest.raises(ValueError, match="engine_grant has no value who; its values: player, ability"):
        check(catalog, "engine_grant", who="Steve")


def test_a_phrase_is_one_line_of_at_most_80(catalog):
    assert check(catalog, "engine_unbind", player="Steve", phrase=" fire ball ").values["phrase"] == "fire ball"
    with pytest.raises(ValueError, match="holds a newline"):
        check(catalog, "engine_bind", player="Steve", phrase="fire\nball", ability="fireball")
    with pytest.raises(ValueError):
        check(catalog, "engine_unbind", player="Steve", phrase="x" * 81)


def test_a_config_is_python_kept_whole(catalog):
    A = lib()
    assert A.python_fields(catalog.get("engine_config")) == ["config"]
    assert A.args_fields(catalog.get("engine_start")) == ["args"]
    assert check(catalog, "engine_config", class_id="fireball", config=CONFIG).values["config"] == CONFIG
    assert check(catalog, "engine_config", config=CONFIG.replace("\n", "\r\n")).values["config"] == CONFIG
    with pytest.raises(ValueError, match="a control character that is not a line end or a tab"):
        check(catalog, "engine_config", config=CONFIG + "\x1b[2J")
    with pytest.raises(ValueError, match="at most 20000"):
        check(catalog, "engine_config", config="#" * 20001)
    with pytest.raises(ValueError, match="config is Python source, as text"):
        check(catalog, "engine_config", config=["class Config:"])
