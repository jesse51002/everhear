"""The game engine's menu actions (the game engine spec) — engine_grant, engine_revoke, engine_give,
engine_start, engine_stop, engine_bind, engine_unbind and engine_config — load with no script, name
the engine's op they go to, take what the menu says, and refuse what the engine would.

    uv run --no-project --with pytest --with pydantic pytest actions/tests/test_engine_actions.py

vfg sends their values to the engine (toolkit/vfg, run_engine_action); here they are only loaded
and checked, from the repo's actions/ with no script folder beside them.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
OPS = {"engine_grant": "grant", "engine_revoke": "revoke", "engine_give": "give", "engine_start": "start",
       "engine_stop": "stop", "engine_bind": "bind", "engine_unbind": "unbind", "engine_config": "config"}
TAKES = {"engine_grant": ["player", "ability"], "engine_revoke": ["player", "ability"],
         "engine_give": ["player", "item"], "engine_start": ["scenario"], "engine_stop": ["scenario", "instance"],
         "engine_bind": ["player", "phrase", "ability"], "engine_unbind": ["player", "phrase"],
         "engine_config": ["class_id", "config"]}
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
    """The engine actions alone, from a copy of them beside the real _types.py: no script anywhere."""
    root = tmp_path_factory.mktemp("repo")
    (root / "actions").mkdir()
    (root / "actions" / "__init__.py").write_text("")
    (root / "actions" / "_types.py").symlink_to(REPO / "actions" / "_types.py")
    for name in OPS:
        shutil.copy(REPO / "actions" / (name + ".py"), root / "actions")
    (root / "scripts" / "_presets").mkdir(parents=True)
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


def test_each_loads_with_no_script_names_its_op_and_takes_what_the_menu_shows(catalog):
    A = lib()
    assert catalog.errors() == {} and catalog.names() == sorted(OPS)
    for name, op in OPS.items():
        cls = catalog.get(name)
        assert A.check_action(name, cls) == [], name
        assert (A.engine_op(cls), cls.script, cls.runs, cls.takes) == (op, "", "once", TAKES[name]), name
        assert "ENGINE" in A.describe(name, cls) or name == "engine_unbind", name   # the menu points at THE WORLD NOW
    assert (catalog.get("engine_bind").timeout_s, catalog.get("engine_config").timeout_s) == (40, 60)


def test_a_player_is_one_name_and_never_everyone(catalog):
    assert check(catalog, "engine_grant", player="Steve", ability="fireball").values == {
        "player": "Steve", "ability": "fireball"}
    for bad in ("@a", "Steve,Alex", "Ste ve", "a" * 17):
        with pytest.raises(ValueError):
            check(catalog, "engine_grant", player=bad, ability="fireball")
        with pytest.raises(ValueError):
            check(catalog, "engine_bind", player=bad, phrase="fire ball", ability="fireball")


def test_a_class_is_named_as_the_engine_names_it(catalog):
    for action, key in (("engine_grant", "ability"), ("engine_give", "item"), ("engine_start", "scenario"),
                        ("engine_config", "class_id")):
        assert check(catalog, action, **{key: "fire_staff"}).values[key] == "fire_staff"
        for bad in ("Fire Staff", "fire-staff", "1fire", "a" * 65):
            with pytest.raises(ValueError):
                check(catalog, action, **{key: bad})
    assert check(catalog, "engine_stop", instance="scenario:horde#1").values["instance"] == "scenario:horde#1"
    with pytest.raises(ValueError):
        check(catalog, "engine_stop", instance="horde; stop")


def test_a_config_is_python_kept_whole(catalog):
    A = lib()
    assert A.python_fields(catalog.get("engine_config")) == ["config"]
    assert check(catalog, "engine_config", class_id="fireball", config=CONFIG).values["config"] == CONFIG
    with pytest.raises(ValueError):
        check(catalog, "engine_bind", player="Steve", phrase="fire\nball", ability="fireball")   # a phrase is one line
    schema = A.planner_model(catalog.get("engine_config")).model_json_schema()["properties"]
    assert schema["config"]["type"] == "string" and "class Config" in schema["config"]["description"]
