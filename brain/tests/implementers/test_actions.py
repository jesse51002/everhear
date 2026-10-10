"""Every action whole in an implementer's brief, read from vfg's actions cache as each starts
(actions.py), and what runs from running.json."""
import json
import os
import threading
import time

from brain.implementers import actions, examples

from .test_examples import GALICK_ACTION, STATE, Repo, parts

NEW_ACTION = '''from actions._types import Action, Players


class TntCrouch(Action):
    """Drop TNT on a player when they crouch three times."""

    script = "galick_ice"
    runs = "until_stopped"
    takes = ["player"]

    player: Players = "@a"
    """who it watches"""
'''


async def test_an_action_saved_mid_game_is_in_the_next_brief_and_one_that_does_not_load_is_named(tmp_path):
    r = Repo(tmp_path)
    assert "tnt_crouch" not in parts(await r.block())[2]
    r.write("made/main/actions/tnt_crouch.py", NEW_ACTION)
    r.write("made/main/actions/broken.py", "def broken(:\n")
    path = os.path.join(r.dir, "actions", "galick_gun.py")
    r.write("actions/galick_gun.py", GALICK_ACTION.replace("then a purple beam", "then a violet beam"))
    os.utime(path, (time.time() + 5, time.time() + 5))  # newer than the cache, however fast this runs
    acts = parts(await r.block())[2]
    made = acts.split("Made on this server (made/main/actions/):", 1)[1]
    assert "\ntnt_crouch — Drop TNT on a player when they crouch three times." in made
    assert "galick_gun — Give a player a Galick Gun: charge, then a violet beam that leaves a crater." in acts
    assert acts.rstrip("\n").split("\n")[-1].startswith(
        "Left out, they did not load (vfg action check <name> says why): broken: failed to import: SyntaxError")
    # the cache is vfg's own, in VFG_HOME
    with open(os.path.join(r.home, "actions.json"), encoding="utf-8") as f:
        assert sorted(json.load(f)["actions"]) == ["frost_ray", "galick_gun", "galick_ice", "ice_beam", "tnt_crouch",
                                                   "zoo"]


async def test_the_cache_unreadable_or_slow_the_brief_still_goes_and_says_so(tmp_path, monkeypatch):
    r = Repo(tmp_path)

    def boom(repo, server, home):
        raise RuntimeError("no pydantic")

    monkeypatch.setattr(actions, "cache", boom)
    head, _, acts, files = parts(await r.block())
    assert acts == ("ACTIONS: unavailable (RuntimeError: no pydantic). vfg action list names every action, and vfg "
                    "action list --json gives each one's values; run one with vfg action run <action> key=value ... "
                    "--task T7.")
    assert head.split("\n")[1].startswith("ice_beam runs this job: run its action with the values the job needs.")
    assert files.startswith("=== scripts/ice_beam/main.py ===")
    # a cache that does not answer is cut off at its own limit
    held = threading.Event()
    monkeypatch.setattr(actions, "cache", lambda repo, server, home: held.wait(5))
    monkeypatch.setattr(examples, "ACTIONS_TIMEOUT_S", 0.01)
    try:
        assert "ACTIONS: unavailable (not read within 0.01 s)." in await r.block()
    finally:
        held.set()


def test_copies_show_only_the_values_that_differ_from_their_actions_own():
    data = {"actions": {"railgun": {"fields": {"player": {"default": "@a"}, "cooldown": {"default": 2},
                                               "fire": {"default": "click"}, "spare": {"default": []}},
                                    "fixed": {"fire": "click"}}}}
    copy = {"action": "railgun", "values": {"player": "Alex", "cooldown": 2.0, "fire": "hold", "spare": ["Bob"]},
            "task": "T5"}
    assert actions.copy_line("railgun_2", copy, data) == (
        'railgun_2 (railgun, player=Alex, fire=hold, spare=["Bob"], T5)')
    assert actions.copy_line("railgun", {"action": "railgun", "values": {"cooldown": 2}}, data) == "railgun"
    assert actions.copy_line("x", {"action": "gone", "values": {"a": True, "b": 0.5}}, data) == (
        "x (gone, a=true, b=0.5)")


def test_the_actions_go_library_first_and_a_scripts_namesake_first():
    data = {"actions": {"b": {"source": "made", "script": "s"}, "s": {"source": "library", "script": "s"},
                        "a": {"source": "library", "script": "s"}, "z": {"source": "library", "script": "t"}}}
    assert [n for n, _ in actions.entries(data)] == ["a", "s", "z", "b"]
    assert actions.actions_of(data, "s") == ["s", "a", "b"]
    assert actions.describe("x", {"line": "Do x.", "script": "x", "runs": "until_stopped"}) == (
        "x — Do x.\nscript x · runs until stopped")
    text = actions.section({"actions": {}}, None, "main", None)
    assert text.split("\n")[2:] == ["The library has no action.", "",
                                    "Made on this server (made/main/actions/): none yet.", ""]


def test_what_runs_is_running_json_else_the_states_instances(tmp_path):
    (tmp_path / "running.json").write_text("{not json")
    assert actions.read_running(str(tmp_path)) is None
    (tmp_path / "running.json").write_text('{"copies": []}')
    assert actions.read_running(str(tmp_path)) is None
    assert actions.from_state(STATE["instances"] + ["junk", {}])["galick-alex"] == {
        "action": "galick_gun", "script": "galick_gun", "values": {"player": "Alex", "cooldown": 0}, "task": "T5"}
