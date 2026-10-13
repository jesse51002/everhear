"""The game engine's menu actions in vfg_actions: an action that names an engine op in place of a
script (engine_op), checked as any action but for its script, and a field of the kind "python"
(python_fields), taken as text whole, its lines, indentation and comments kept."""

import shutil
import sys
import textwrap
from pathlib import Path

import pytest

import vfg_actions as A
from vfg_actions import Catalog, cache, check_action, check_values, describe

REPO = Path(__file__).resolve().parents[3]
OPS = {"engine_bind": "bind", "engine_config": "config", "engine_give": "give", "engine_grant": "grant",
       "engine_revoke": "revoke", "engine_start": "start", "engine_stop": "stop", "engine_unbind": "unbind"}
CONFIG = '''    class Config(AbilityConfig):
        # the blast, at power 0
        explosion_base: float = Field(12, ge=0, le=30)\t# tabs and all

        def damage(self, ctx):                 # required: the total
            return self.explosion_base
'''


def purge():
    for key in [k for k in sys.modules if k == "actions" or k.startswith(("actions.", "vfg_made__", "vfg_preset__"))]:
        del sys.modules[key]


@pytest.fixture
def repo(tmp_path):
    """A repo of the engine actions alone, beside the real _types.py; no script folder at all."""
    purge()
    saved = list(sys.path)
    (tmp_path / "actions").mkdir()
    (tmp_path / "actions" / "__init__.py").write_text("")
    (tmp_path / "actions" / "_types.py").symlink_to(REPO / "actions" / "_types.py")
    for name in OPS:
        shutil.copy(REPO / "actions" / (name + ".py"), tmp_path / "actions")
    (tmp_path / "scripts" / "_presets").mkdir(parents=True)
    yield tmp_path
    purge()
    sys.path[:] = saved


def write(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


def test_the_engine_actions_load_with_no_script_and_name_their_op(repo):
    cat = Catalog(repo, None)
    assert cat.errors() == {} and cat.names() == sorted(OPS)
    for name, op in OPS.items():
        cls = cat.get(name)
        assert (A.engine_op(cls), cls.script, cls.runs, check_action(name, cls)) == (op, "", "once", []), name
    assert A.python_fields(cat.get("engine_config")) == ["config"]
    assert [A.python_fields(cat.get(n)) for n in OPS if n != "engine_config"] == [[]] * 7
    head = describe("engine_config", cat.get("engine_config")).splitlines()[:2]
    assert head[1] == "engine config · runs once · timeout 60 s"
    assert "Python source, every line, its indentation and its comments as written" in describe(
        "engine_config", cat.get("engine_config"))


def test_an_engine_action_runs_no_script_and_an_action_with_neither_is_refused(repo):
    write(repo / "actions" / "half.py", '''
        from typing import ClassVar

        from actions._types import Action


        class Half(Action):
            """An engine action that names a script too."""

            engine: ClassVar[str] = "grant"
            script = "railgun"
            runs = "once"
    ''')
    write(repo / "actions" / "none.py", '''
        from actions._types import Action


        class None_(Action):
            """Neither."""
    ''')
    write(repo / "actions" / "bad_op.py", '''
        from typing import ClassVar

        from actions._types import Action


        class BadOp(Action):
            """An op that is not a word."""

            engine: ClassVar[str] = "Grant Now"
            runs = "once"
    ''')
    errors = Catalog(repo, None).errors()
    assert errors["half"] == "an engine action runs no script: script is 'railgun'"
    assert errors["bad_op"] == "engine is the engine's control op its values go to, not 'Grant Now'"
    assert "none" in errors


def test_python_source_goes_through_whole_and_the_rest_is_checked_as_words(repo):
    cls = Catalog(repo, None).get("engine_config")
    got = check_values(cls, {"class_id": "fireball", "config": CONFIG})
    assert got.values == {"class_id": "fireball", "config": CONFIG}
    assert check_values(cls, {"config": CONFIG.replace("\n", "\r\n")}).values["config"] == CONFIG
    with pytest.raises(ValueError, match="a control character that is not a line end or a tab"):
        check_values(cls, {"config": CONFIG + "\x1b[2J"})
    with pytest.raises(ValueError, match="at most 20000"):
        check_values(cls, {"config": "#" * 20001})
    with pytest.raises(ValueError, match="config is Python source, as text"):
        check_values(cls, {"config": ["class Config:"]})
    with pytest.raises(ValueError, match="class_id=Fire Ball"):
        check_values(cls, {"class_id": "Fire Ball", "config": CONFIG})
    grant = Catalog(repo, None).get("engine_grant")
    with pytest.raises(ValueError, match="holds a newline"):   # any other text is one line of words
        check_values(grant, {"player": "Steve", "ability": "fire\nball"})


def test_the_cache_says_which_op(repo, tmp_path):
    data = cache(repo, "main", tmp_path / "home")
    assert data["actions"]["engine_grant"]["engine"] == "grant"
    assert data["actions"]["engine_grant"]["script"] == ""
    assert data["actions"]["engine_config"]["fields"]["config"]["type"] == "python"
