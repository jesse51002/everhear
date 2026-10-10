# Not in the TypeScript tests: every action whole in an implementer's brief, read from the actions
# cache as each starts (actions.py), and what runs from running.json.
import asyncio
import json
import os
import time

import pytest
from helpers import write
from test_examples import AGENT, GALICK_ACTION, STATE, Box, parts

from orchestrator import actions, examples


@pytest.fixture(autouse=True)
def _off_after():
    yield
    examples.reset()
    actions.forget()

NEW_ACTION = '''from actions._types import Action, Players


class TntCrouch(Action):
    """Drop TNT on a player when they crouch three times."""

    script = "galick_ice"
    runs = "until_stopped"
    takes = ["player"]

    player: Players = "@a"
    """who it watches"""
'''


async def test_an_action_made_mid_session_is_in_the_next_brief_with_no_restart():
    b = Box()
    _, _, acts, _ = parts(await b.block())
    assert "tnt_crouch" not in acts
    write(os.path.join(b.dir, "made", "main", "actions", "tnt_crouch.py"), NEW_ACTION)
    _, _, acts, _ = parts(await b.block())
    made = acts.split("Made on this server (made/main/actions/):", 1)[1]
    assert "\ngalick_ice — " in made and "\ntnt_crouch — Drop TNT on a player when they crouch three times." in made
    assert b.store.get("T7").implementers[-1]["actions"] == 6


async def test_a_changed_library_action_is_read_again():
    b = Box()
    await b.block()
    path = os.path.join(b.dir, "actions", "galick_gun.py")
    write(path, GALICK_ACTION.replace("charge, then a purple beam", "charge, then a violet beam"))
    os.utime(path, (time.time() + 5, time.time() + 5))  # newer than the cache, however fast this runs
    _, _, acts, _ = parts(await b.block())
    assert "galick_gun — Give a player a Galick Gun: charge, then a violet beam that leaves a crater." in acts


async def test_the_cache_is_the_shared_one_in_vfg_home():
    b = Box()
    await b.block()
    with open(os.path.join(b.home, "actions.json"), encoding="utf-8") as f:
        data = json.load(f)
    assert sorted(data["actions"]) == ["frost_ray", "galick_gun", "galick_ice", "ice_beam", "zoo"]
    assert data["actions"]["galick_ice"]["source"] == "made"
    # a fresh cache is read as it is: what is in the file is what the brief says
    data["actions"]["zoo"]["describe"] = "zoo — as the cache has it"
    write(os.path.join(b.home, "actions.json"), json.dumps(data))
    os.utime(os.path.join(b.home, "actions.json"), (data["built_at"], data["built_at"]))
    _, _, acts, _ = parts(await b.block())
    assert "\nzoo — as the cache has it\n" in acts


async def test_an_action_that_does_not_load_is_named_with_why():
    b = Box()
    write(os.path.join(b.dir, "made", "main", "actions", "broken.py"), "def broken(:\n")
    _, _, acts, _ = parts(await b.block())
    assert acts.rstrip("\n").split("\n")[-1].startswith(
        "Left out, they did not load (vfg action check <name> says why): broken: failed to import: SyntaxError")


async def test_the_cache_unreadable_the_brief_still_goes_and_says_so(monkeypatch):
    b = Box()

    def boom(repo, server, home):
        raise RuntimeError("no pydantic")

    monkeypatch.setattr(actions, "cache", boom)
    block = await b.block()
    head, _, acts, files = parts(block)
    assert acts == ("ACTIONS: unavailable (RuntimeError: no pydantic). vfg action list names every action, and vfg "
                    "action list --json gives each one's values; run one with vfg action run <action> key=value ... "
                    "--task T7.")
    # no action to name, but what runs it is still said
    assert head.split("\n")[1].startswith("ice_beam runs this job: run its action with the values the job needs.")
    assert "Running: galick-alex (galick_gun, player=Alex, cooldown=0, T5)" in head  # all values: none is known as default
    assert files.startswith("=== scripts/ice_beam/main.py ===")
    assert b.store.get("T7").implementers[0]["actions"] == "RuntimeError: no pydantic"


async def test_a_slow_cache_is_cut_off_at_its_own_limit(monkeypatch):
    b = Box()
    monkeypatch.setattr(examples, "ACTIONS_TIMEOUT_S", 0.3)

    def slow(repo, server, home):
        time.sleep(1.5)
        return {"actions": {}}

    monkeypatch.setattr(actions, "cache", slow)
    t = time.monotonic()
    block = await b.block()
    assert time.monotonic() - t < 1.3 + 0.25  # the router's limit (VFG_ROUTE_TIMEOUT_S 0 + 1 s) is the longer one
    assert "ACTIONS: unavailable (not read within 0.3 s)." in block
    await asyncio.sleep(1.4)  # the thread ends before the test does


async def test_two_briefs_at_once_read_the_cache_one_after_the_other():
    b = Box()
    one, two = await asyncio.gather(b.block(), b.block({**AGENT, "description": "T7 second part"}))
    assert parts(one)[2] == parts(two)[2]


async def test_what_runs_comes_from_the_state_when_running_json_cannot_be_read():
    b = Box(running=None)
    head = parts(await b.block())[0]
    # the state's instances, each as a copy of the action named as its script
    assert ("Running: galick-alex (galick_gun, player=Alex, cooldown=0, T5), horror_mode (T2), zoo-steve (zoo, "
            "player=Steve, T3)."
            in head.split("\n"))
    write(os.path.join(b.home, "running.json"), "{not json")
    assert actions.read_running(b.home) is None
    write(os.path.join(b.home, "running.json"), json.dumps({"copies": []}))
    assert actions.read_running(b.home) is None
    assert actions.from_state(STATE["instances"] + ["junk", {}])["galick-alex"] == {
        "action": "galick_gun", "script": "galick_gun", "values": {"player": "Alex", "cooldown": 0}, "task": "T5"}


def test_copy_lines_show_only_what_differs_from_the_actions_own_values():
    data = {"actions": {"railgun": {"fields": {"player": {"default": "@a"}, "cooldown": {"default": 2},
                                               "fire": {"default": "click"}, "spare": {"default": []}},
                                    "fixed": {"fire": "click"}}}}
    copy = {"action": "railgun", "values": {"player": "Alex", "cooldown": 2.0, "fire": "hold", "spare": ["Bob"]},
            "task": "T5"}
    assert actions.changed(data, copy) == {"player": "Alex", "fire": "hold", "spare": ["Bob"]}
    assert actions.copy_line("railgun_2", copy, data) == 'railgun_2 (railgun, player=Alex, fire=hold, spare=["Bob"], T5)'
    assert actions.copy_line("railgun", {"action": "railgun", "values": {"cooldown": 2}}, data) == "railgun"
    assert actions.copy_line("x", {"action": "gone", "values": {"a": True, "b": 0.5}}, data) == "x (gone, a=true, b=0.5)"


def test_the_actions_go_library_first_and_a_scripts_namesake_first():
    data = {"actions": {"b": {"source": "made", "script": "s"}, "s": {"source": "library", "script": "s"},
                        "a": {"source": "library", "script": "s"}, "z": {"source": "library", "script": "t"}}}
    assert [n for n, _ in actions.entries(data)] == ["a", "s", "z", "b"]
    assert actions.actions_of(data, "s") == ["s", "a", "b"]
    assert actions.actions_of(None, "s") == []
    # an entry with no describe is still named, with its line and script
    assert actions.describe("x", {"line": "Do x.", "script": "x", "runs": "until_stopped"}) == (
        "x — Do x.\nscript x · runs until stopped")


def test_no_action_in_the_library_and_none_made():
    text = actions.section({"actions": {}}, None, "main", None)
    assert text.split("\n")[2:] == ["The library has no action.", "", "Made on this server (made/main/actions/): none yet.", ""]
    assert "--task <task id>" in text.split("\n")[0]


async def test_the_runner_reads_the_cache_once_as_it_starts(monkeypatch):
    b = Box()
    seen = []
    monkeypatch.setattr(examples, "log", lambda area, msg: seen.append(msg))
    await examples.warm(b.cfg)
    assert seen == ["actions: 5 in the cache (1 made on main)"]
    assert os.path.exists(os.path.join(b.home, "actions.json"))
    monkeypatch.setattr(actions, "cache", lambda *a: (_ for _ in ()).throw(OSError("read-only")))
    seen.clear()
    await examples.warm(b.cfg)
    assert seen[-1] == "actions: unavailable at start (OSError: read-only); each brief tries again"
