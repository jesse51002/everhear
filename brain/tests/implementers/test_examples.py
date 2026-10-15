"""The EXAMPLES block (examples.py, route.py): on a small temp repo with a library of engine classes
and a made/main/content/, the fake vfg answering vfg route, vfg state and vfg engine instances. At
most 3 router classes, whatever vfg route printed."""
import json
import os

import pytest

from brain.implementers import examples, route
from brain.implementers.config import ImplementerConfig
from brain.implementers.vfg import VfgResult

from .fakes import FakeVfg, bad, never, ok

ICE_STAFF = '''"""Ice Staff: right-click to fire a bolt that freezes what it hits."""

from vfg_engine import Item, ItemConfig


class IceStaff(Item):
    class Config(ItemConfig):
        summary = "Freezes what its bolt hits."
        base = "carrot_on_a_stick"
        name = "Ice Staff"
        active_in = "mainhand"
'''
FIREBALL = '''"""Fireball: the point the caster looks at burns, then explodes."""

from vfg_engine import Ability, AbilityConfig


class Fireball(Ability):
    class Config(AbilityConfig):
        summary = "Burns, then explodes."
'''
METEOR = '''from vfg_engine import Scenario, ScenarioConfig
import vfg_engine


class Meteor(vfg_engine.Scenario):
    """Meteor: rocks fall on a place."""

    class Config(ScenarioConfig):
        summary = "Rocks fall."
'''
FROST_RING = '''"""Frost Ring: ice round a player's feet for a while."""

from vfg_engine import Mark


class FrostRingAlex(Mark):
    pass
'''

FILES = {
    "engine/library/ice_staff.py": ICE_STAFF,
    "engine/library/fireball.py": FIREBALL,
    "engine/library/meteor.py": METEOR,
    "engine/library/_helpers.py": "x = 1\n",   # not a class's file: no id starts with _
    "engine/library/README.md": "The library.\n",
    "made/main/content/frost_ring_alex.py": FROST_RING,
    "made/main/content/broken_alex.py": "class BrokenAlex(\n",
    "engine/examples/giant.py": '"""Giant: twice the size."""\n',
}

ROUTE = {"task": "ice_staff_steve", "classes": [
    {"name": "ice_staff", "kind": "item", "source": "library", "verdict": "runs_it", "p_runs_it": 0.91, "p_close": 0.05,
     "path": "engine/library/ice_staff.py"},
    {"name": "frost_ring_alex", "kind": "mark", "source": "made", "verdict": "close", "p_runs_it": 0.1, "p_close": 0.42,
     "path": "made/main/content/frost_ring_alex.py"}],
    "fallback": False, "ms": 312, "tokens": 11840}

STATE = {"online": [{"name": "Steve", "pos": [120, 64, -40], "dimension": "overworld"},
                    {"name": "Alex", "pos": [98.53, 70, -12], "dimension": "overworld"}],
         "time": {"daytime": 18000, "day": 3, "phase": "night"}, "weather": "clear", "rcon": None, "ms": 300}

INSTANCES = {"ok": True, "instances": [
    {"instance": "ability:fireball#3", "kind": "ability", "id": "fireball", "user": "Steve", "started": 100.5},
    {"instance": "scenario:castle#2", "kind": "scenario", "id": "castle", "user": None, "started": 90.0},
    {"instance": "mark:frost_ring_alex#1", "kind": "mark", "id": "frost_ring_alex", "user": "Alex", "started": 99.0,
     "ends": 140.0}]}

JOB = "Give Steve an ice staff that freezes, and a meteor shower for the finale. Leave fireball alone."


class Repo:
    """A temp repo with a small library and a made/main/content/, and the fake vfg answering route,
    state and the engine's instances."""

    def __init__(self, tmp_path, route_json=ROUTE, state=STATE, instances=INSTANCES, files=None, env=None):
        self.dir = str(tmp_path / "repo")
        for rel, text in (FILES if files is None else files).items():
            self.write(rel, text)
        self.vfg = FakeVfg()
        if route_json is not None:
            self.vfg.on("route", answer=ok(json.dumps(route_json)))
        if state is not None:
            self.vfg.on("state", answer=ok(json.dumps(state)))
        if instances is not None:
            self.vfg.on("engine", "instances", answer=ok(json.dumps(instances)))
        self.cfg = ImplementerConfig.load({"VFG_REPO": self.dir, "VFG_HOME": str(tmp_path / "vfg-home"), **(env or {})})

    def write(self, rel: str, text: str) -> None:
        os.makedirs(os.path.dirname(os.path.join(self.dir, rel)), exist_ok=True)
        with open(os.path.join(self.dir, rel), "w") as f:
            f.write(text)

    async def block(self, job: str = JOB, task: str = "ice_staff_steve") -> str:
        return await examples.build(self.cfg, job, task, self.vfg)


def whole(rel: str, files=FILES) -> str:
    return "=== %s ===\n%s" % (rel, files[rel])


def parts(block: str) -> tuple[str, str, str]:
    """The block's header, its made first lines and its files."""
    head, rest = block.split("\n\n", 1)
    made, _, files = rest.partition("\n\n=== ")
    return head, made, ("=== " + files if files else "")


def loaded(files: str) -> list[str]:
    return [f.split(" ===")[0] for f in files.split("=== ")[1:]]


# ---------------------------------------------------------------- the block

async def test_the_block_in_its_shape_with_every_file_whole_in_order(tmp_path):
    r = Repo(tmp_path)
    block = await r.block()
    head, made, files = parts(block)
    assert head.split("\n") == [
        "EXAMPLES, picked for ice_staff_steve by Jev in 0.3 s.",
        "ice_staff (item, library) runs this job: vfg engine give <player> ice_staff. frost_ring_alex (mark, made) is "
        "close: copy what you need into your own class.",
        "Named in your brief, in full below: fireball (ability, library) and meteor (scenario, library).",
        "Running: ability:fireball#3 (Steve), scenario:castle#2, mark:frost_ring_alex#1 (Alex).",
        "Online: Steve 120 64 -40 overworld, Alex 98.5 70 -12 overworld. Night, clear.",
        examples.NOW,
    ]
    assert made.split("\n") == [
        "Made on this server (made/main/content/), by first line:",
        "  broken_alex (?): (no first line) [does not parse: '(' was never closed, line 1]",
        "  frost_ring_alex (mark): Frost Ring: ice round a player's feet for a while.",
    ]
    # whole files, never cut: the router's classes (runs_it first), then the named, in the library's order
    order = ["engine/library/ice_staff.py", "made/main/content/frost_ring_alex.py", "engine/library/fireball.py",
             "engine/library/meteor.py"]
    assert files == "\n".join(whole(rel) for rel in order)
    # the router read the brief for ice_staff_steve, on its stdin; the state and the engine were asked
    assert ["route", "--task", "ice_staff_steve"] in r.vfg.calls and r.vfg.stdin["route"] == JOB
    assert ["state", "--json"] in r.vfg.calls and ["engine", "instances", "--json"] in r.vfg.calls


async def test_what_fails_is_said_and_the_block_still_goes(tmp_path, monkeypatch):
    r = Repo(tmp_path)
    r.vfg.on("route", answer=bad("vfg: not configured: VFG_ROUTE_LOAD must be a number"))
    r.vfg.on("state", answer=bad("vfg: EC2 unreachable"))
    r.vfg.on("engine", "instances", answer=bad("vfg: the engine is not running", 6))
    lines = (await r.block()).split("\n")
    assert lines[:5] == [
        "EXAMPLES for ice_staff_steve: nothing was picked, the router failed (exit 1: vfg: not configured: "
        "VFG_ROUTE_LOAD must be a number). Every library class's kind, first line, Config and Args are in your "
        "system prompt, and you can read any of them in engine/library/.",
        "Named in your brief, in full below: fireball (ability, library), ice_staff (item, library) and meteor "
        "(scenario, library).",
        "Running: unknown (exit 6: vfg: the engine is not running); vfg engine instances lists it.",
        "Server state: unavailable (exit 1: vfg: EC2 unreachable).", examples.UNKNOWN]
    # hung: cut off at VFG_ROUTE_TIMEOUT_S and the step's slack
    monkeypatch.setattr(examples, "STEP_SLACK_S", 0)
    r.cfg.route_timeout_s = 0.01
    r.vfg.on("route", answer=never).on("state", answer=never).on("engine", "instances", answer=never)
    block = await r.block()
    assert "the router failed (no answer within 0.01 s)" in block
    assert "Running: unknown (no answer within 0.01 s)" in block
    assert "Server state: unavailable (no answer within 0.01 s).\n" + examples.UNKNOWN in block
    # Minecraft not answering (exit 5): who is online is not known, what the engine runs still is
    r.cfg.route_timeout_s = 3
    r.vfg.on("state", answer=VfgResult(5, json.dumps({**STATE, "online": [], "rcon": "RCON refused"}), ""))
    r.vfg.on("engine", "instances", answer=ok(json.dumps(INSTANCES)))
    lines = (await r.block()).split("\n")
    assert lines[lines.index("Minecraft is not answering (RCON refused): who is online, the time and the weather are "
                             "unknown.") + 1] == examples.UNKNOWN
    assert any(line.startswith("Running: ability:fireball#3 (Steve)") for line in lines)


def picked(name: str, verdict: str = "close", kind: str = "ability", p: float = 0.5) -> dict:
    return {"name": name, "kind": kind, "source": "library", "verdict": verdict,
            "p_runs_it": p if verdict == "runs_it" else 0.1, "p_close": p, "path": "engine/library/%s.py" % name}


async def test_at_most_three_classes_runs_it_first_whatever_vfg_route_printed(tmp_path):
    picks = {**ROUTE, "classes": [picked("fireball", p=0.9), picked("meteor", kind="scenario", p=0.8),
                                  picked("frost_ring_alex", kind="mark", p=0.7),
                                  picked("ice_staff", "runs_it", "item", 0.6)]}
    r = Repo(tmp_path, route_json=picks)
    head, made, files = parts(await r.block("Something cold."))
    assert loaded(files) == ["engine/library/ice_staff.py", "engine/library/fireball.py", "engine/library/meteor.py"]
    assert head.split("\n")[1] == ("ice_staff (item, library) runs this job: vfg engine give <player> ice_staff. "
                                   "fireball (ability, library) and meteor (scenario, library) are close: copy what "
                                   "you need into your own class.")
    assert route.trim(picks)["cut"] == ["frost_ring_alex"]
    assert route.trim(dict(picks, cut=["glacier"]))["cut"] == ["frost_ring_alex", "glacier"]   # vfg route's own cut


def gathered(**over) -> examples.Gathered:
    g = dict(task="ice_staff_steve", picks={**ROUTE}, route_failed=None, state=STATE, state_failed=None, server="main",
             instances=[])
    g.update(over)
    return examples.Gathered(**g)


def test_the_header_says_how_the_picks_were_made_and_how_each_kind_is_used():
    words = {**ROUTE, "fallback": True, "ms": 3012, "why": "jev did not answer within 3 s",
             "classes": [{**ROUTE["classes"][0], "verdict": "words"}]}
    assert examples.header(gathered(picks=words))[:2] == [
        "EXAMPLES, picked for ice_staff_steve by words in 3.0 s: Jev did not answer (jev did not answer within 3 s).",
        "Matched by words: ice_staff (item, library)."]
    assert examples.header(gathered(picks={**ROUTE, "classes": []}))[1] == "No class runs this job or is close to it."
    empty = examples.header(gathered(state={**STATE, "online": [], "weather": "rain", "time": {"phase": "day"}}))
    assert empty[-3:] == ["Running: nothing.", "Online: nobody. Day, rain.", examples.NOW]
    runs = [picked("meteor", "runs_it", "scenario"), picked("fireball", "runs_it", "ability"),
            picked("frozen", "runs_it", "mark"), picked("giant", "runs_it", "actor"), picked("odd", "runs_it", "?")]
    assert examples.runs_line(runs) == (
        "meteor (scenario, library), fireball (ability, library), frozen (mark, library), giant (actor, library) and "
        "odd (?, library) run this job: vfg engine start meteor key=value ..., with the args the job needs; vfg engine "
        "grant <player> fireball, and bind the phrase to it if the job names one; vfg engine apply <player> frozen "
        "key=value ...; a scenario spawns it (self.spawn); use it as it is.")


def test_names_match_as_words_in_a_row_however_they_are_written():
    assert examples.names_in("Run the Galick-Gun at Steve", ["galick_gun", "gun", "galick_ice"]) == ["galick_gun", "gun"]
    assert examples.names_in("a zookeeper", ["zoo"]) == []
    assert examples.names_in("galick then gun", ["galick_gun"]) == []


def test_a_class_is_read_from_its_file_and_the_examples_stand_in_for_a_missing_library(tmp_path):
    r = Repo(tmp_path)
    classes = examples.content_classes(r.dir, "main")
    assert [(c.name, c.kind, c.source, c.first_line) for c in classes] == [
        ("fireball", "ability", "library", "Fireball: the point the caster looks at burns, then explodes."),
        ("ice_staff", "item", "library", "Ice Staff: right-click to fire a bolt that freezes what it hits."),
        ("meteor", "scenario", "library", "Meteor: rocks fall on a place."),  # its class's docstring, as vfg_engine.Scenario
        ("broken_alex", "?", "made", ""), ("frost_ring_alex", "mark", "made",
                                           "Frost Ring: ice round a player's feet for a while.")]
    no_library = Repo(tmp_path / "b", files={"engine/examples/giant.py": '"""Giant: twice the size."""\n'})
    assert examples.content_classes(no_library.dir, "main") == []          # the engine's own examples: never read
    assert examples.library_dir(no_library.dir) == "engine/library"


async def test_route_run_refuses_what_is_not_its_json():
    vfg = FakeVfg().on("route", answer=ok('{"scripts": [], "presets": []}'))  # an older vfg route's
    with pytest.raises(route.RouteFailed, match="no classes"):
        await route.run("x", "torch_ring_steve", 1, vfg)
    vfg.on("route", answer=ok("Traceback"))
    with pytest.raises(route.RouteFailed, match="not JSON"):
        await route.run("x", None, 1, vfg)
    assert vfg.calls == [["route", "--task", "torch_ring_steve"], ["route"]] and vfg.stdin["route"] == "x"
    assert route.summary(ROUTE) == {"classes": [
        {"name": "ice_staff", "kind": "item", "source": "library", "verdict": "runs_it", "p_runs_it": 0.91,
         "p_close": 0.05},
        {"name": "frost_ring_alex", "kind": "mark", "source": "made", "verdict": "close", "p_runs_it": 0.1,
         "p_close": 0.42}], "fallback": False, "ms": 312, "tokens": 11840}
    assert route.summary(None, "timed out") == {"failed": "timed out"}


async def test_a_made_classs_test_comes_after_it_and_the_tasks_own_test_too(tmp_path):
    files = dict(FILES)
    files["made/main/tests/frost_ring_alex.py"] = '"""The test of frost_ring_alex."""\ndef test(t):\n    pass\n'
    files["made/main/tests/ice_staff_steve.py"] = '"""The test of ice_staff_steve."""\ndef test(t):\n    pass\n'
    files["made/main/tests/zoo_alex.py"] = '"""Another task\'s test."""\n'
    r = Repo(tmp_path, files=files)
    sizes: dict = {}
    block = await examples.build(r.cfg, JOB, "ice_staff_steve", r.vfg, sizes=sizes)
    head, made, rest = parts(block)
    assert loaded(rest) == ["engine/library/ice_staff.py", "made/main/content/frost_ring_alex.py",
                            "made/main/tests/frost_ring_alex.py", "made/main/tests/ice_staff_steve.py",
                            "engine/library/fireball.py", "engine/library/meteor.py"]
    assert ("Tests below, each after its class: made/main/tests/frost_ring_alex.py and "
            "made/main/tests/ice_staff_steve.py (yours); vfg test <id> runs one on the test server.") in head.split("\n")
    assert (sizes["tests_n"], sizes["classes_n"], sizes["named_n"]) == (2, 2, 2)
    assert sizes["tests"] == sum(len("\n" + whole(rel, files)) for rel in (
        "made/main/tests/frost_ring_alex.py", "made/main/tests/ice_staff_steve.py"))
    # another task's test, or none: nothing
    assert "zoo_alex" not in block
    r.vfg.on("route", answer=ok(json.dumps({**ROUTE, "classes": ROUTE["classes"][:1]})))
    block = await examples.build(r.cfg, "An ice staff.", "zoo_alex_2", r.vfg)
    assert "made/main/tests/" not in block


async def test_each_parts_size_goes_to_sizes(tmp_path):
    r = Repo(tmp_path)
    sizes: dict = {}
    block = await examples.build(r.cfg, JOB, "ice_staff_steve", r.vfg, sizes=sizes)
    head, made, files = parts(block)
    assert sum(sizes[k] for k in examples.PARTS) == len(block)
    assert sizes["header"] == len(head) + 2 and sizes["made"] == len(made) + 1
    assert (sizes["classes_n"], sizes["tests_n"], sizes["named_n"]) == (2, 0, 2)
    assert sizes["named"] == len("\n" + whole("engine/library/fireball.py")) + len("\n" + whole("engine/library/meteor.py"))
    assert (sizes["route"], sizes["state"], sizes["instances"]) == ("jev", "ok", "ok")
