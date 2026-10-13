"""vfg_actions and actions/_types.py, the engine-only world: the repo's actions load and pass their
check, an action that names no engine op or holds code is left out, values are read as words and
checked (clamped with a note, refused in words), the menu line and describe(), the catalog's
loading and reloading, and actions.json. No network and no server."""

import json
import os
import shutil
import sys
import textwrap
import time
from pathlib import Path

import pytest

import vfg_actions as A
from vfg_actions import Catalog, cache, check_action, check_values, describe, menu_line

REPO = Path(__file__).resolve().parents[3]
ENGINE_ACTIONS = sorted(p.stem for p in (REPO / "actions").glob("engine_*.py"))


def purge():
    for key in [k for k in sys.modules if k == "actions" or k.startswith("actions.")]:
        del sys.modules[key]


@pytest.fixture(autouse=True)
def fresh_modules():
    """No test sees another's actions: the actions package goes before and after each, and
    sys.path is put back."""
    saved = list(sys.path)
    purge()
    yield
    purge()
    sys.path[:] = saved


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


@pytest.fixture
def repo(tmp_path):
    """A small repo: actions/ with the real _types.py (linked) and engine_grant and engine_start
    copied."""
    (tmp_path / "actions").mkdir()
    (tmp_path / "actions" / "__init__.py").write_text("")
    (tmp_path / "actions" / "_types.py").symlink_to(REPO / "actions" / "_types.py")
    for name in ("engine_grant", "engine_start"):
        shutil.copy(REPO / "actions" / (name + ".py"), tmp_path / "actions")
    return tmp_path


SHRINK = '''
    from actions._types import Action, Args, ClassId, Field, Player


    class Shrink(Action):
        """Make a player small for a while."""

        engine = "apply"
        takes = ["player", "seconds"]

        player: Player = ""
        """who"""
        mark: ClassId = "shrunk"
        """the mark"""
        args: Args = {}
        """its args"""
        seconds: float = Field(30, ge=1, le=600)
        """how long"""
'''


# ------------------------------------------------------------------ the repo's actions

def test_every_repo_action_loads_named_as_its_file_names_an_op_and_passes_its_check():
    cat = Catalog(REPO)
    assert cat.errors() == {}
    assert cat.names() == ENGINE_ACTIONS and len(ENGINE_ACTIONS) == 11
    for name in cat.names():
        cls = cat.get(name)
        assert check_action(name, cls) == [], name
        assert A.engine_op(cls) == name.removeprefix("engine_"), name
        assert cat.file(name) == REPO / "actions" / (name + ".py")


# ------------------------------------------------------------------ check_action

def test_an_action_that_names_no_op_or_holds_code_is_left_out_with_why(repo):
    write(repo / "actions" / "none.py", '''
        from actions._types import Action


        class None_(Action):
            """Neither."""
    ''')
    write(repo / "actions" / "no_op.py", '''
        from actions._types import Action


        class NoOp(Action):
            """It names no op."""
    ''')
    write(repo / "actions" / "bad_op.py", '''
        from actions._types import Action


        class BadOp(Action):
            """An op that is not a word."""

            engine = "Grant Now"
    ''')
    write(repo / "actions" / "coded.py", '''
        from actions._types import Action


        class Coded(Action):
            """It holds code."""

            engine = "grant"

            def go(self):
                return 1
    ''')
    write(repo / "actions" / "quiet.py", '''
        from actions._types import Action


        class Quiet(Action):
            engine = "grant"
            takes = ["who"]

            player: str = ""
    ''')
    write(repo / "actions" / "broken.py", "raise RuntimeError('nope')\n")
    cat = Catalog(repo)
    errors = cat.errors()
    assert cat.names() == ["engine_grant", "engine_start"]
    assert errors["none"] == "no class None(Action) in it, named as the file"
    assert errors["no_op"] == "engine names none of the engine's control ops: every action is one (engine = \"grant\")"
    assert errors["bad_op"] == "engine is the engine's control op its values go to, not 'Grant Now'"
    assert errors["coded"] == "Coded.go is code: an action holds none, the engine does"
    assert "no docstring" in errors["quiet"] and "takes names who" in errors["quiet"]
    assert "player has no description" in errors["quiet"]
    assert errors["broken"] == "failed to import: RuntimeError: nope"


def test_a_type_that_is_not_one_of_types_py_is_refused(repo):
    write(repo / "actions" / "odd.py", '''
        from actions._types import Action


        class Odd(Action):
            """A set of words."""

            engine = "start"

            words: set = set()
            """the words"""
            table: dict[str, int] = {}
            """a table"""
    ''')
    why = Catalog(repo).errors()["odd"]
    assert "words: set is not one of actions/_types.py's types" in why
    assert "table: a dict is Args, dict[str, Any]: a scenario's or a mark's args" in why


def test_a_default_its_own_field_refuses_is_named(repo):
    write(repo / "actions" / "bad_default.py", '''
        from actions._types import Action, Field


        class BadDefault(Action):
            """A default out of its own range."""

            engine = "apply"

            seconds: float = Field(0, ge=1, le=10)
            """how long"""
    ''')
    assert Catalog(repo).errors()["bad_default"].startswith("seconds=0 in its default:")


# ------------------------------------------------------------------ values

def test_check_values_fills_every_field_reads_words_and_clamps_with_a_note(repo):
    write(repo / "actions" / "shrink.py", SHRINK)
    cls = Catalog(repo).get("shrink")
    got = check_values(cls, {"player": "Steve", "seconds": "900", "args": '{"factor": 0.5}'})
    assert got.values == {"player": "Steve", "mark": "shrunk", "args": {"factor": 0.5}, "seconds": 600}
    assert got.notes == ["seconds=900 is over its max, clamped to 600"]
    assert check_values(cls, {}).values["seconds"] == 30


@pytest.mark.parametrize("given,says", [
    ({"who": "Steve"}, "shrink has no value who; its values: player, mark, args, seconds"),
    ({"player": "@a"}, "player=@a: should be one player's exact name"),
    ({"mark": "Shrunk Now"}, "mark=Shrunk Now: should be a name as ENGINE shows it"),
    ({"seconds": "soon"}, "seconds=soon:"),
    ({"seconds": True}, "seconds takes a number, not true"),
    ({"args": "factor=2"}, "args is an object: one key and value per arg"),
    ({"player": "Ste\nve"}, "player holds a newline or another control character"),
])
def test_check_values_refuses_in_words(repo, given, says):
    write(repo / "actions" / "shrink.py", SHRINK)
    with pytest.raises(ValueError) as e:
        check_values(Catalog(repo).get("shrink"), given)
    assert says in str(e.value)


# ------------------------------------------------------------------ rendering

def test_the_menu_line_and_describe_show_the_op_takes_types_limits_and_defaults(repo):
    write(repo / "actions" / "shrink.py", SHRINK)
    cls = Catalog(repo).get("shrink")
    assert menu_line("shrink", cls) == "shrink — Make a player small for a while."
    assert describe("shrink", cls).splitlines() == [
        "shrink — Make a player small for a while.",
        "engine apply",
        "values (* = the brain fills it):",
        "* player (one player's exact name (letters, digits and _, at most 16); default \"\"): who",
        "* seconds (a number 1..600; default 30): how long",
        "  mark (a name as ENGINE shows it (a-z, 0-9 and _, starting with a letter), such as fireball; "
        "default \"shrunk\"): the mark",
        "  args (the class's args, each by its name as ENGINE lists them; default {}): its args",
    ]
    assert describe("engine_start", Catalog(repo).get("engine_start")).splitlines()[1] == "engine start · timeout 45 s"


# ------------------------------------------------------------------ the catalog

def bump(path: Path, text: str) -> None:
    """Write `text` with an mtime surely after the last one (a coarse clock would hide it)."""
    old = path.stat().st_mtime if path.exists() else time.time()
    write(path, text)
    os.utime(path, (old + 2, old + 2))


def test_refresh_picks_up_a_new_changed_or_gone_action_without_a_restart(repo):
    cat = Catalog(repo)
    assert cat.refresh() == []
    bump(repo / "actions" / "shrink.py", SHRINK)
    # one file changed: every action is loaded again, and named so
    assert cat.refresh() == ["engine_grant", "engine_start", "shrink"]
    assert cat.get("shrink").model_fields["seconds"].default == 30
    bump(repo / "actions" / "shrink.py", SHRINK.replace("Field(30,", "Field(60,"))
    assert "shrink" in cat.refresh() and cat.get("shrink").model_fields["seconds"].default == 60
    (repo / "actions" / "shrink.py").unlink()
    assert "shrink" in cat.refresh() and cat.get("shrink") is None and cat.names() == ["engine_grant", "engine_start"]


# ------------------------------------------------------------------ actions.json

def test_cache_writes_every_action_and_rebuilds_only_when_a_file_is_newer(repo, tmp_path):
    home = tmp_path / "home"
    for p in (repo / "actions").glob("engine_*.py"):     # written a minute ago: older than any cache
        os.utime(p, (time.time() - 60, time.time() - 60))
    data = cache(repo, home)
    on_disk = json.loads((home / "actions.json").read_text())
    assert on_disk == data and (home / "actions.json").stat().st_mode & 0o777 == 0o600
    assert sorted(data["actions"]) == ["engine_grant", "engine_start"] and data["errors"] == {}
    grant = data["actions"]["engine_grant"]
    assert (grant["engine"], grant["takes"], grant["timeout_s"], grant["file"]) == (
        "grant", ["player", "ability"], None, "actions/engine_grant.py")
    assert grant["fields"]["player"] == {"type": "player", "default": "", "ge": None, "le": None, "choices": None,
                                         "description": "whose: one player's exact name"}
    assert data["actions"]["engine_start"]["fields"]["args"]["type"] == "args"
    assert grant["describe"] == describe("engine_grant", Catalog(repo).get("engine_grant"))
    built = data["built_at"]
    assert cache(repo, home)["built_at"] == built        # nothing newer: the same file
    bump(repo / "actions" / "shrink.py", SHRINK)
    again = cache(repo, home)
    assert again["built_at"] > built and "shrink" in again["actions"]
