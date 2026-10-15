"""A server's own version of a class shadows the library's by id (the content folders are ordered:
the library first, then made/<server>/content)."""

import dataclasses
import textwrap

from vfg_engine.io import Command

BOLT = '''
from vfg_engine import *


class Bolt(Ability):
    every = 0.5

    class Config(AbilityConfig):
        summary = "Says where it comes from."
        damage = 1
        duration_s = UNTIL_STOPPED
        charge_s = 0
        cooldown_s = 0
        scale_per_point = 0

    class State(State):
        n: int = 0

    def tick(self, ctx):
        self.state.n += 1
        self.command("say {where} %d" % self.state.n)
'''


def bolt(where, extra=""):
    return textwrap.dedent(BOLT).replace("{where}", where) + extra


def _rig(rig, tmp_path):
    lib, made = tmp_path / "library", tmp_path / "made"
    lib.mkdir()
    made.mkdir()
    (lib / "bolt.py").write_text(bolt("library"))
    r = rig()
    r.settings = dataclasses.replace(r.settings, content=(str(lib), str(made)))
    assert r.start()["errors"] == []
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="bolt")
    r.run_for(0.6)
    return r, lib, made


def last_say(r):
    return [op.text for op in r.io.ops_of(Command)][-1]


def test_the_servers_version_shadows_the_librarys_and_instances_switch(rig, tmp_path):
    r, lib, made = _rig(rig, tmp_path)
    assert last_say(r).startswith("say library")
    (made / "bolt.py").write_text(bolt("server"))
    reply = r.op("reload")
    assert reply["ok"] and reply["loaded"] == ["bolt"] and reply["switched"] == 1
    r.run_for(0.6)
    assert last_say(r).startswith("say server")                     # the running instance switched
    n = r.records("ability", "bolt")[0].state.n
    assert n >= 2                                                    # and kept its state
    row = next(c for c in r.op("classes")["classes"] if c["id"] == "bolt")
    assert row["file"] == str(made / "bolt.py") and row["shadows"] == str(lib / "bolt.py")
    got = r.op("config_get", **{"class": "bolt"})
    assert got["file"] == str(made / "bolt.py") and got["shadows"] == str(lib / "bolt.py")
    assert any("shadows" in m and "bolt" in m for m in r.lines())


def test_reloading_the_shadowed_library_file_changes_nothing_and_says_why(rig, tmp_path):
    r, lib, made = _rig(rig, tmp_path)
    (made / "bolt.py").write_text(bolt("server"))
    r.op("reload")
    (lib / "bolt.py").write_text(bolt("library v2"))
    reply = r.op("reload", paths=[str(lib / "bolt.py")])
    assert reply["ok"] and reply["loaded"] == [] and reply["switched"] == 0
    assert "shadowed by" in reply["notes"][0]
    r.run_for(0.6)
    assert last_say(r).startswith("say server")


def test_when_the_shadowing_file_goes_the_librarys_comes_back(rig, tmp_path):
    r, lib, made = _rig(rig, tmp_path)
    (made / "bolt.py").write_text(bolt("server"))
    r.op("reload")
    (lib / "bolt.py").write_text(bolt("library v2"))
    r.op("reload", paths=[str(lib / "bolt.py")])          # loaded while shadowed
    (made / "bolt.py").unlink()
    reply = r.op("reload")
    assert reply["ok"] and reply["loaded"] == ["bolt"] and reply["stopped"] == []
    r.run_for(0.6)
    assert last_say(r).startswith("say library v2")       # the same instance, on the library's code
    assert "shadows" not in next(c for c in r.op("classes")["classes"] if c["id"] == "bolt")


def test_a_reload_of_just_the_servers_file_shadows_too(rig, tmp_path):
    r, lib, made = _rig(rig, tmp_path)
    path = made / "bolt.py"
    path.write_text(bolt("server"))
    reply = r.op("reload", paths=[str(path)])
    assert reply["loaded"] == ["bolt"] and reply["switched"] == 1
    r.run_for(0.6)
    assert last_say(r).startswith("say server")


def test_the_same_id_twice_in_one_folder_is_refused(rig, tmp_path):
    r, lib, made = _rig(rig, tmp_path)
    (made / "bolt.py").write_text(bolt("server"))
    (made / "bolt_again.py").write_text(bolt("again"))
    reply = r.op("reload")
    assert reply["ok"] is False and "in the same folder" in reply["errors"][0]["error"]
    r.run_for(0.6)
    assert last_say(r).startswith("say server")


def test_a_shadowing_class_of_another_kind_is_refused(rig, tmp_path):
    r, lib, made = _rig(rig, tmp_path)
    (made / "bolt.py").write_text(textwrap.dedent('''
        from vfg_engine import *

        class Bolt(Mark):
            class Config(MarkConfig):
                summary = "Not an ability."
                duration_s = 1
                damage = 0
        '''))
    reply = r.op("reload")
    assert reply["ok"] is False and "is an ability" in reply["errors"][0]["error"]


def test_a_reload_of_a_file_outside_the_content_folders_is_refused_in_words(rig, tmp_path):
    """A file in no content folder ranked above them all, so a partial reload of it shadowed the
    library's and the server's version of its id. It is refused, and nothing changes."""
    r, lib, made = _rig(rig, tmp_path)
    away = tmp_path / "elsewhere"
    away.mkdir()
    (away / "bolt.py").write_text(bolt("elsewhere"))
    reply = r.op("reload", paths=[str(away / "bolt.py")])
    assert not reply["ok"] and reply["loaded"] == [] and reply["switched"] == 0
    (err,) = reply["errors"]
    assert err["check"] == "path" and "not in a content folder" in err["error"]
    assert str(lib) in err["error"] and str(made) in err["error"]
    r.run_for(0.6)
    assert last_say(r).startswith("say library")
    assert all(str(away) not in p for p in r.rt.candidates["bolt"])


def test_dot_dots_and_symlinks_are_resolved_before_the_folder_is_judged(rig, tmp_path):
    r, lib, made = _rig(rig, tmp_path)
    away = tmp_path / "elsewhere"
    away.mkdir()
    (away / "bolt.py").write_text(bolt("elsewhere"))
    (made / "link").symlink_to(away, target_is_directory=True)
    for p in [made / "link" / "bolt.py", str(made) + "/../elsewhere/bolt.py"]:
        reply = r.op("reload", paths=[str(p)])
        assert not reply["ok"] and "not in a content folder" in reply["errors"][0]["error"], p
    (lib / "_helper.py").write_text("X = 1\n")
    reply = r.op("reload", paths=[str(lib / "_helper.py")])
    assert not reply["ok"] and "not a content file" in reply["errors"][0]["error"]
    r.run_for(0.6)
    assert last_say(r).startswith("say library")
    # a path into a content folder by way of `..` is that folder's file, under its own name
    (made / "bolt.py").write_text(bolt("server"))
    reply = r.op("reload", paths=[str(lib) + "/../made/bolt.py"])
    assert reply["ok"] and reply["loaded"] == ["bolt"]
    assert set(r.rt.candidates["bolt"]) == {str(lib / "bolt.py"), str(made / "bolt.py")}
    assert r.op("reload")["errors"] == []                       # no twin of itself on a full reload
    r.run_for(0.6)
    assert last_say(r).startswith("say server")
