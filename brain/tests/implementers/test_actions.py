"""The actions in an implementer's brief, read from vfg's actions cache as each starts (actions.py):
at most seven in full, the router's picks, and this server's made ones one menu line each; and
what runs, from running.json."""
import json
import os
import threading
import time

from brain.implementers import actions, examples

from .fakes import ok
from .test_examples import GALICK_ACTION, ROUTE, STATE, Repo, parts

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
    r.vfg.on("route", answer=ok(json.dumps({**ROUTE, "actions": [{"name": "galick_gun"}, {"name": "tnt_crouch"}]})))
    assert "tnt_crouch" not in parts(await r.block())[2]
    r.write("made/main/actions/tnt_crouch.py", NEW_ACTION)
    r.write("made/main/actions/broken.py", "def broken(:\n")
    path = os.path.join(r.dir, "actions", "galick_gun.py")
    r.write("actions/galick_gun.py", GALICK_ACTION.replace("then a purple beam", "then a violet beam"))
    os.utime(path, (time.time() + 5, time.time() + 5))  # newer than the cache, however fast this runs
    acts = parts(await r.block())[2]
    made = acts.split("Made on this server (made/main/actions/), one menu line each:", 1)[1]
    assert made.split("\n")[1:3] == ["galick_ice — Give a player a Galick Ice gun: the Galick Gun, laying ice where it "
                                     "hits.", "tnt_crouch — Drop TNT on a player when they crouch three times."]
    # picked by the router: in full, the new line and the new action both
    assert "\n\ngalick_gun — Give a player a Galick Gun: charge, then a violet beam that leaves a crater.\n" in acts
    assert "\n\ntnt_crouch — Drop TNT on a player when they crouch three times.\nscript galick_ice" in acts
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
    assert acts == ("ACTIONS: unavailable (RuntimeError: no pydantic). The library's are in your system prompt, one "
                    "menu line each; vfg action list names every action, and vfg action show <name> prints one in "
                    "full. Run one with vfg action run <action> key=value ... --task ice_beam_steve.")
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
            "task": "sniper_nest_alex"}
    assert actions.copy_line("railgun_2", copy, data) == (
        'railgun_2 (railgun, player=Alex, fire=hold, spare=["Bob"], sniper_nest_alex)')
    assert actions.copy_line("railgun", {"action": "railgun", "values": {"cooldown": 2}}, data) == "railgun"
    assert actions.copy_line("x", {"action": "gone", "values": {"a": True, "b": 0.5}}, data) == (
        "x (gone, a=true, b=0.5)")
    # a task's copy named as the task, of the action named so too, is its name alone
    assert actions.copy_line("torch_ring_steve", {"action": "torch_ring_steve", "values": {},
                                                  "task": "torch_ring_steve"}, data) == "torch_ring_steve"
    assert actions.copy_line("railgun_steve", {"action": "railgun", "values": {"player": "Steve"}, "task": None},
                             data) == "railgun_steve (railgun, player=Steve)"
    assert actions.copy_line("fireball_2", {"action": "fireball", "values": {}, "task": "cast"}, data) == (
        "fireball_2 (fireball, cast)")


def test_the_actions_go_library_first_and_a_scripts_namesake_first():
    data = {"actions": {"b": {"source": "made", "script": "s"}, "s": {"source": "library", "script": "s"},
                        "a": {"source": "library", "script": "s"}, "z": {"source": "library", "script": "t"}}}
    assert [n for n, _ in actions.entries(data)] == ["a", "s", "z", "b"]
    assert actions.actions_of(data, "s") == ["s", "a", "b"]
    assert actions.describe("x", {"line": "Do x.", "script": "x", "runs": "until_stopped"}) == (
        "x — Do x.\nscript x · runs until stopped")
    assert actions.describe("x", {"describe": "x — Do x.\nscript x · runs once\n"}) == "x — Do x.\nscript x · runs once"
    assert actions.menu_line("x", {"line": "Do x."}) == "x — Do x."
    text = actions.section({"actions": {}}, None, "main", None)
    assert "None was picked for your job; vfg action show <name> prints any other in full." in text.split("\n")[0]
    assert "--task <your task>;" in text.split("\n")[0]
    assert text.split("\n")[2:] == ["Made on this server (made/main/actions/): none yet.", ""]


def test_seven_in_full_at_most_in_the_order_given_and_only_those_the_cache_has():
    data = {"actions": dict(("a%d" % i, {"source": "library", "line": "A%d." % i, "describe": "a%d — A%d." % (i, i)})
                            for i in range(10))}
    data["actions"]["m"] = {"source": "made", "line": "Made one."}
    text = actions.section(data, None, "main", "x_steve", ["a9", "gone", "a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8"],
                           "by Jev")
    assert text.split("\n")[0].startswith("ACTIONS: the library's are in your system prompt, one menu line each, and "
                                          "this server's are listed below. Here, in full, are the 7 picked for your job "
                                          "by Jev, best first; vfg action show <name> prints any other in full. Run one "
                                          "with vfg action run <action> key=value ... --task x_steve;")
    assert [line for line in text.split("\n") if " — " in line] == [
        "a9 — A9.", "a1 — A1.", "a2 — A2.", "a3 — A3.", "a4 — A4.", "a5 — A5.", "a6 — A6.", "m — Made one."]
    # the router's picks, capped; with none from it, the job's words
    picks = {"actions": [{"name": "a%d" % i} for i in range(9)] + [{"name": "gone"}], "fallback": False}
    assert actions.chosen(data, picks, "anything") == (["a%d" % i for i in range(7)], "by Jev")
    assert actions.chosen(data, {**picks, "fallback": True}, "anything")[1] == "by words"
    words = {"actions": {"nuke": {"source": "library", "line": "Drop a nuke on a place."},
                         "zoo": {"source": "library", "line": "Build a zoo."},
                         "fire_nuke": {"source": "made", "line": "A nuke made of fire."}}}
    assert actions.chosen(words, None, "nuke the castle") == (["fire_nuke", "nuke"], "by words")
    assert actions.chosen(words, {"scripts": []}, "a zoo") == (["zoo"], "by words")   # an older vfg: no actions


def test_what_runs_is_running_json_else_the_states_instances(tmp_path):
    (tmp_path / "running.json").write_text("{not json")
    assert actions.read_running(str(tmp_path)) is None
    (tmp_path / "running.json").write_text('{"copies": []}')
    assert actions.read_running(str(tmp_path)) is None
    assert actions.from_state(STATE["instances"] + ["junk", {}])["galick_gun_alex"] == {
        "action": "galick_gun", "script": "galick_gun", "values": {"player": "Alex", "cooldown": 0},
        "task": "galick_duel_alex"}
