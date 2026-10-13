"""The EXAMPLES block (examples.py, route.py): on a small temp repo whose actions load through
lib/py/vfg_actions.py, with the fake vfg answering vfg route and vfg state. At most 3 router
scripts and 7 actions in full, whatever vfg route printed."""
import json
import os
import shutil
import time
from types import SimpleNamespace

import pytest

from brain.implementers import examples, route
from brain.implementers.config import ROOT, ImplementerConfig
from brain.implementers.vfg import VfgResult

from .fakes import FakeVfg, bad, never, ok

# What a temp repo needs from the real one for its actions to load.
ACTIONS_BASE = ["actions/__init__.py", "actions/_types.py", "scripts/_lib/commands.py", "scripts/_lib/kit/world.py"]

ICE_BEAM = '''"""Ice Beam: a beam from the eyes that freezes what it hits."""
ASKS = ["ice beam", "freeze ray"]
from kit import values, aim

v = values()
'''
GALICK = '''"""Galick Gun: charge, then a purple beam that leaves a crater."""
ASKS = ["galick gun"]
'''
ZOO = '''"""Zoo: animals in pens round a player."""
ASKS = ["a zoo"]
'''
CRATER = '''"""Carve a sphere out of the world at a place, leaving air, lava or fire."""
from kit import area
from actions._types import Field, Live, Values


class Crater(Values):
    crater_radius: Live[int] = Field(8, ge=2, le=64)
    """how wide the crater is, in blocks"""


def crater(at, v):
    area.sphere(at, v["crater_radius"], block="air")
'''
REPLACE = '''"""Replace one kind of block with another in a box."""
from actions._types import BlockId, Values


class ReplaceBlocks(Values):
    replace_to: BlockId = "ice"
    """what the blocks become"""


def replace_blocks(box, v):
    pass
'''
GALICK_ICE = '''"""Galick Ice: the Galick Gun, laying ice where it hits."""
ASKS = ["galick gun that freezes"]
'''
ICE_TRAIL = '''"""Lay packed ice under a player's feet as they walk."""
from actions._types import BlockId, Values


class IceTrail(Values):
    ice_trail_block: BlockId = "packed_ice"
    """what it lays"""


def ice_trail(p, v):
    pass
'''

ICE_BEAM_ACTION = '''from actions._types import Action, Field, Live, Players


class IceBeam(Action):
    """Give a player an ice beam: a beam from the eyes that freezes what it hits."""

    script = "ice_beam"
    runs = "until_stopped"
    takes = ["player", "freeze_seconds"]

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    freeze_seconds: Live[int] = Field(5, ge=1, le=60)
    """how long what it hits stays frozen, in seconds"""
'''
FROST_RAY_ACTION = '''from actions.ice_beam import IceBeam


class FrostRay(IceBeam):
    """Give a player a frost ray: an ice beam that freezes for a whole minute."""

    takes = ["player"]
    fixed = {"freeze_seconds": 60}
'''
GALICK_ACTION = '''from actions._types import Action, Field, Live, Players


class GalickGun(Action):
    """Give a player a Galick Gun: charge, then a purple beam that leaves a crater."""

    script = "galick_gun"
    runs = "until_stopped"
    takes = ["player", "cooldown"]

    player: Players = "@a"
    """who gets one"""
    cooldown: Live[float] = Field(10, ge=0, le=60)
    """seconds between shots"""
'''
ZOO_ACTION = '''from actions._types import Action, Place


class Zoo(Action):
    """Build a zoo: animals in pens at a place."""

    script = "zoo"
    runs = "once"
    takes = ["at"]

    at: Place = ""
    """where the zoo goes: a saved place, x y z, or a player"""
'''
GALICK_ICE_ACTION = '''from actions._types import Action, Players


class GalickIce(Action):
    """Give a player a Galick Ice gun: the Galick Gun, laying ice where it hits."""

    script = "galick_ice"
    runs = "until_stopped"
    takes = ["player"]

    player: Players = "@a"
    """who gets one"""
'''

FILES = {
    "scripts/ice_beam/main.py": ICE_BEAM,
    "scripts/galick_gun/main.py": GALICK,
    "scripts/zoo/main.py": ZOO,
    "scripts/tnt_rain/main.py": "from mc import rcon\nrcon('say tnt')\n",
    "scripts/tnt_rain/README.md": "Rains primed TNT round a player.\nUsage: tnt_rain [player]\nEnds: when stopped\n",
    "scripts/_presets/crater.py": CRATER,
    "scripts/_presets/replace_blocks.py": REPLACE,
    "made/main/scripts/galick_ice/main.py": GALICK_ICE,
    "made/main/presets/ice_trail.py": ICE_TRAIL,
    "actions/ice_beam.py": ICE_BEAM_ACTION,
    "actions/frost_ray.py": FROST_RAY_ACTION,
    "actions/galick_gun.py": GALICK_ACTION,
    "actions/zoo.py": ZOO_ACTION,
    "made/main/actions/galick_ice.py": GALICK_ICE_ACTION,
}

FILES_MORE = dict(FILES)

ROUTE = {"task": "ice_beam_steve", "scripts": [
    {"name": "ice_beam", "source": "library", "verdict": "runs_it", "p_runs_it": 0.91, "p_close": 0.05,
     "path": "scripts/ice_beam/main.py"},
    {"name": "galick_gun", "source": "library", "verdict": "close", "p_runs_it": 0.1, "p_close": 0.42,
     "path": "scripts/galick_gun/main.py"}],
    "presets": [{"name": "crater", "source": "library", "p_use": 0.77, "path": "scripts/_presets/crater.py"},
                {"name": "replace_blocks", "source": "library", "p_use": 0.5, "path": "scripts/_presets/replace_blocks.py"}],
    "actions": [{"name": "ice_beam", "source": "library", "p_needed": 0.93, "path": "actions/ice_beam.py"},
                {"name": "no_such_action", "source": "library", "p_needed": 0.8, "path": "actions/no_such_action.py"},
                {"name": "galick_ice", "source": "made", "p_needed": 0.41, "path": "made/main/actions/galick_ice.py"}],
    "fallback": False, "ms": 312, "tokens": 11840}

STATE = {"online": [{"name": "Steve", "pos": [120, 64, -40], "dimension": "overworld"},
                    {"name": "Alex", "pos": [98, 70, -12], "dimension": "overworld"}],
         "time": {"daytime": 18000, "day": 3, "phase": "night"}, "weather": "clear",
         "instances": [
             {"instance": "galick_gun_alex", "script": "galick_gun", "source": "library", "task": "galick_duel_alex",
              "pid": 10,
              "values": {"player": "Alex", "cooldown": 0}, "changed": {"player": "Alex", "cooldown": 0},
              "args": [], "touches": ["Alex"]},
             {"instance": "horror_mode", "script": "horror_mode", "source": "library", "task": "spooky_night",
              "pid": 11,
              "values": {}, "changed": {}, "args": [], "touches": []},
             {"instance": "zoo_steve", "script": "zoo", "source": "library", "task": "zoo_steve", "pid": 12,
              "values": {"player": "Steve"}, "changed": {"player": "Steve"}, "args": [], "touches": ["Steve"]}],
         "rcon": None, "ms": 300}

# running.json: what vfg wrote through for every run (the state's instances, above, are what an older vfg gives)
RUNNING = {"fetched_at": 1760000000, "copies": {
    "galick_gun_alex": {"action": "galick_gun", "script": "galick_gun", "values": {"player": "Alex", "cooldown": 0},
                        "live": ["cooldown"], "task": "galick_duel_alex", "started": "2026-10-09T12:00:00Z",
                        "runs": "until_stopped"},
    "horror_mode": {"action": "horror_mode", "script": "horror_mode", "values": {}, "live": [], "task": "spooky_night",
                    "started": "2026-10-09T11:00:00Z", "runs": "until_stopped"},
    # a task's copy, named as the task
    "zoo_steve": {"action": "zoo", "script": "zoo", "values": {"at": "Steve"}, "live": [], "task": "zoo_steve",
                  "started": "2026-10-09T11:30:00Z", "runs": "once"}}}

JOB = "Give Steve an ice beam with a crater where it hits. Leave tnt_rain alone."


class Repo:
    """A temp repo with a small library, its actions and a made/main/, its own VFG_HOME with
    running.json, and the fake vfg answering route and state."""

    def __init__(self, tmp_path, route_json=ROUTE, state=STATE, running=RUNNING, env=None, files=None):
        self.dir = str(tmp_path / "repo")
        files = FILES if files is None else files
        for rel in ACTIONS_BASE:
            os.makedirs(os.path.dirname(os.path.join(self.dir, rel)), exist_ok=True)
            shutil.copy(os.path.join(ROOT, rel), os.path.join(self.dir, rel))
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(self.dir, rel)), exist_ok=True)
            with open(os.path.join(self.dir, rel), "w") as f:
                f.write(text)
        for rel in ACTIONS_BASE + list(files):  # written a minute ago: a cache built now is fresh
            os.utime(os.path.join(self.dir, rel), (time.time() - 60, time.time() - 60))
        self.home = str(tmp_path / "vfg-home")
        os.makedirs(self.home)
        if running is not None:
            self.write_home("running.json", json.dumps(running))
        self.vfg = FakeVfg()
        if route_json is not None:
            self.vfg.on("route", answer=ok(json.dumps(route_json)))
        if state is not None:
            self.vfg.on("state", answer=ok(json.dumps(state)))
        self.cfg = ImplementerConfig.load({"VFG_REPO": self.dir, "VFG_HOME": self.home, **(env or {})})

    def write(self, rel: str, text: str) -> None:
        os.makedirs(os.path.dirname(os.path.join(self.dir, rel)), exist_ok=True)
        with open(os.path.join(self.dir, rel), "w") as f:
            f.write(text)

    def write_home(self, rel: str, text: str) -> None:
        with open(os.path.join(self.home, rel), "w") as f:
            f.write(text)

    async def block(self, job: str = JOB, task: str = "ice_beam_steve") -> str:
        return await examples.build(self.cfg, job, task, self.vfg)


def whole(rel: str) -> str:
    return "=== %s ===\n%s" % (rel, FILES[rel])


def parts(block: str) -> tuple[str, str, str, str]:
    """The block's header, its made first lines, its ACTIONS and its files."""
    head, rest = block.split("\n\n", 1)
    made, rest = rest.split("\n\n", 1)
    acts, _, files = rest.partition("\n\n=== ")
    return head, made, acts, ("=== " + files if files else "")


# ---------------------------------------------------------------- the block

async def test_the_block_in_the_specs_shape_with_every_file_whole_in_order(tmp_path):
    r = Repo(tmp_path)
    block = await r.block()
    head, made, acts, files = parts(block)
    assert head.split("\n") == [
        "EXAMPLES, picked for ice_beam_steve by Jev in 0.3 s.",
        "ice_beam runs this job: run one of its actions, ice_beam or frost_ray, with the values the job needs. "
        "galick_gun is close.",
        "Presets to copy: crater and replace_blocks.",
        "Named in your brief, in full below: tnt_rain.",
        "Running: galick_gun_alex (galick_gun, player=Alex, cooldown=0, galick_duel_alex), horror_mode (spooky_night), "
        "zoo_steve (zoo, at=Steve).",
        "Online: Steve 120 64 -40 overworld, Alex 98 70 -12 overworld. Night, clear.",
        examples.NOW,
    ]
    assert made.split("\n") == [
        "Made on this server (made/main/), by first line:",
        "  galick_ice (script): Galick Ice: the Galick Gun, laying ice where it hits.",
        "  ice_trail (preset): Lay packed ice under a player's feet as they walk.",
    ]
    # the router's actions whole, best first, those the cache has; then this server's, one menu line each
    assert acts.startswith("ACTIONS: the library's are in your system prompt, one menu line each, and this server's "
                           "are listed below. Here, in full, are the 2 picked for your job by Jev, best first; vfg "
                           "action show <name> prints any other in full. Run one with vfg action run <action> "
                           "key=value ... --task ice_beam_steve; * marks the values it takes, and any value but a "
                           "fixed one can be given.\n")
    firsts = [line.split(" — ")[0] for line in acts.split("\n") if " — " in line]
    assert firsts == ["ice_beam", "galick_ice", "galick_ice"]
    assert acts.endswith("\n\nMade on this server (made/main/actions/), one menu line each:\n"
                         "galick_ice — Give a player a Galick Ice gun: the Galick Gun, laying ice where it hits.")
    ice = acts[acts.index("ice_beam — "):].split("\n\n")[0].split("\n")
    assert ice == ["ice_beam — Give a player an ice beam: a beam from the eyes that freezes what it hits.",
                   "script ice_beam · runs until stopped",
                   "values (* = the planner fills it):",
                   "* player (player names separated by commas, or @a, @r, @p; default \"@a\"): who gets one: a name, "
                   "several separated by commas, or @a",
                   "* freeze_seconds (a whole number 1..60; live; default 5): how long what it hits stays frozen, in seconds"]
    assert "frost_ray" not in acts and "zoo" not in acts   # not picked: in the system prompt's menu
    # whole files, never cut: the router's scripts (runs_it first), its presets, then the named
    order = ["scripts/ice_beam/main.py", "scripts/galick_gun/main.py", "scripts/_presets/crater.py",
             "scripts/_presets/replace_blocks.py", "scripts/tnt_rain/main.py"]
    assert files == "\n".join(whole(rel) for rel in order)
    # the router read the brief for ice_beam_steve, on its stdin; the state was asked for
    assert ["route", "--task", "ice_beam_steve"] in r.vfg.calls and r.vfg.stdin["route"] == JOB
    assert ["state", "--json"] in r.vfg.calls


async def test_a_running_copy_the_brief_names_comes_with_its_values_and_its_script(tmp_path):
    r = Repo(tmp_path)
    lines = (await r.block("Make the zoo_steve pens bigger, and galick_gun_alex fires faster.")).split("\n")
    # another task's copies: their code is copied to be changed, under this task's name, never edited (the guard
    # refuses it); a task's copy named as the task is not named twice
    assert ("Your brief names galick_gun_alex, a copy of galick_gun running for galick_duel_alex with player=Alex, "
            "cooldown=0: vfg script set galick_gun_alex key=value changes it; its script is below; to change its code, "
            "copy it: vfg script new ice_beam_steve --from galick_gun.") in lines
    assert ("Your brief names zoo_steve, a copy of zoo running with at=Steve: vfg script set zoo_steve key=value "
            "changes it; its script is below; to change its code, copy it: vfg script new ice_beam_steve --from zoo.") in lines
    assert "\n".join(lines).count("=== scripts/galick_gun/main.py ===") == 1
    assert "\n".join(lines).endswith(whole("scripts/zoo/main.py"))
    # the task that started it may change it
    lines = (await r.block("galick_gun_alex fires faster.", task="galick_duel_alex")).split("\n")
    assert ("Your brief names galick_gun_alex, a copy of galick_gun running for galick_duel_alex with player=Alex, "
            "cooldown=0: vfg script set galick_gun_alex key=value changes it; its script is below.") in lines
    # a task with a script of its name already copies under another
    lines = (await r.block("galick_gun_alex fires faster.", task="galick_ice")).split("\n")
    assert any(line.endswith("to change its code, copy it: vfg script new <new_name> --from galick_gun.")
               for line in lines)


async def test_what_fails_is_said_and_the_block_still_goes(tmp_path, monkeypatch):
    r = Repo(tmp_path, running=None)
    r.vfg.on("route", answer=bad("vfg: not configured: VFG_ROUTE_LOAD must be a number"))
    r.vfg.on("state", answer=bad("vfg: EC2 unreachable"))
    lines = (await r.block()).split("\n")
    assert lines[:2] == ["EXAMPLES for ice_beam_steve: nothing was picked, the router failed (exit 1: vfg: not configured: "
                         "VFG_ROUTE_LOAD must be a number). Every library script's, preset's and action's first line is "
                         "in your system prompt, and you can read any of them.",
                         "Named in your brief, in full below: ice_beam and tnt_rain."]
    assert lines[2:4] == ["Server state: unavailable (exit 1: vfg: EC2 unreachable).", examples.UNKNOWN]
    # hung: cut off at VFG_ROUTE_TIMEOUT_S and the step's slack
    monkeypatch.setattr(examples, "STEP_SLACK_S", 0)
    r.cfg.route_timeout_s = 0.01
    r.vfg.on("route", answer=never).on("state", answer=never)
    block = await r.block()
    assert "the router failed (no answer within 0.01 s)" in block
    assert "Server state: unavailable (no answer within 0.01 s).\n" + examples.UNKNOWN in block
    # Minecraft not answering (exit 5): what runs is still listed, who is online is not
    r.vfg.on("state", answer=VfgResult(5, json.dumps({**STATE, "online": [], "rcon": "RCON refused"}), ""))
    lines = (await r.block()).split("\n")
    assert lines[lines.index("Minecraft is not answering (RCON refused): who is online, the time and the weather are "
                             "unknown.") + 1] == examples.UNKNOWN
    assert any(line.startswith("Running: galick_gun_alex (galick_gun, player=Alex, cooldown=0, galick_duel_alex)")
               for line in lines)


def script(name: str, verdict: str = "close", p: float = 0.5, rel: str | None = None) -> dict:
    return {"name": name, "source": "library", "verdict": verdict, "p_runs_it": p if verdict == "runs_it" else 0.1,
            "p_close": p, "path": rel or "scripts/%s/main.py" % name}


async def test_at_most_three_scripts_runs_it_first_and_seven_actions_whatever_vfg_route_printed(tmp_path):
    for i in range(8):
        FILES_MORE["actions/extra_%d.py" % i] = ZOO_ACTION.replace("class Zoo", "class Extra%d" % i).replace(
            "Build a zoo", "Build extra pens number %d" % i)
    picks = {**ROUTE, "presets": [], "held": [
        {"name": "crater", "source": "library", "p_use": 0.7, "path": "scripts/_presets/crater.py", "in": "zoo"},
        {"name": "replace_blocks", "source": "library", "p_use": 0.6, "path": "scripts/_presets/replace_blocks.py",
         "in": "galick_gun"}],
        "scripts": [script("zoo", p=0.9), script("galick_gun", p=0.8), script("tnt_rain", p=0.7),
                    script("ice_beam", "runs_it", 0.6)],
        "actions": [{"name": "extra_%d" % i, "source": "library", "p_needed": 0.9 - i / 100} for i in range(8)]}
    r = Repo(tmp_path, route_json=picks, files=FILES_MORE)
    head, made, acts, files = parts(await r.block("Pens for the animals."))
    # runs_it first, then the router's order: tnt_rain cut
    assert [f.split(" ===")[0] for f in files.split("=== ")[1:]] == [
        "scripts/ice_beam/main.py", "scripts/zoo/main.py", "scripts/galick_gun/main.py"]
    assert head.split("\n")[1:3] == ["ice_beam runs this job: run one of its actions, ice_beam or frost_ray, with the "
                                     "values the job needs. zoo and galick_gun are close.",
                                     "Presets to copy: none. Already in a loaded script: crater (in zoo) and "
                                     "replace_blocks (in galick_gun)."]
    assert [line.split(" — ")[0] for line in acts.split("\n") if line.startswith("extra_")] == [
        "extra_%d" % i for i in range(7)]
    assert "Here, in full, are the 7 picked for your job by Jev, best first" in acts
    # a preset held by a script the cap cut is loaded itself
    picks["scripts"] = [script("tnt_rain", p=0.95)] + picks["scripts"]
    r.vfg.on("route", answer=ok(json.dumps(picks)))
    head, made, acts, files = parts(await r.block("Pens for the animals."))
    assert [f.split(" ===")[0] for f in files.split("=== ")[1:]] == [
        "scripts/ice_beam/main.py", "scripts/tnt_rain/main.py", "scripts/zoo/main.py",
        "scripts/_presets/replace_blocks.py"]
    assert head.split("\n")[2] == "Presets to copy: replace_blocks. Already in a loaded script: crater (in zoo)."


async def test_with_no_actions_from_the_router_the_jobs_words_pick_them(tmp_path):
    # the router failed: by words (toolkit/lib/menu.py's, as vfg route's fallback), those it names first
    r = Repo(tmp_path)
    r.vfg.on("route", answer=bad("vfg: EC2 unreachable"))
    acts = parts(await r.block("Give Steve a zoo, and a galick gun that freezes.", task="zoo_steve"))[2]
    assert "Here, in full, are the 5 picked for your job by words, best first" in acts
    assert [part.split(" — ")[0] for part in acts.split("\n\n")[1:6]] == [
        "galick_gun", "galick_ice", "zoo", "frost_ray", "ice_beam"]
    # an older vfg route, which picks no actions: the same
    old = {k: v for k, v in ROUTE.items() if k != "actions"}
    r.vfg.on("route", answer=ok(json.dumps(old)))
    acts = parts(await r.block("A zoo for Steve.", task="zoo_steve"))[2]
    assert "Here, in full, is the one picked for your job by words; vfg action show <name> prints any other" in acts
    assert acts.split("\n\n")[1].startswith("zoo — Build a zoo: animals in pens at a place.")
    # the router picked none: none in full
    r.vfg.on("route", answer=ok(json.dumps({**ROUTE, "actions": []})))
    acts = parts(await r.block())[2]
    assert acts.startswith("ACTIONS: the library's are in your system prompt, one menu line each, and this server's "
                           "are listed below. None was picked for your job; vfg action show <name> prints any other "
                           "in full.")
    assert acts.split("\n\n")[1].startswith("Made on this server (made/main/actions/), one menu line each:")


async def test_each_parts_size_goes_to_sizes(tmp_path):
    r = Repo(tmp_path)
    sizes: dict = {}
    block = await examples.build(r.cfg, JOB, "ice_beam_steve", r.vfg, sizes=sizes)
    head, made, acts, files = parts(block)
    assert sum(sizes[k] for k in examples.PARTS) == len(block)
    assert sizes["header"] == len(head) + 2 and sizes["made"] == len(made) + 2
    assert sizes["actions"] + sizes["made_actions"] == len(acts) + 1
    assert (sizes["actions_n"], sizes["made_actions_n"], sizes["scripts_n"], sizes["presets_n"], sizes["named_n"],
            sizes["running_n"]) == (2, 1, 2, 2, 1, 0)
    assert sizes["named"] == len("\n" + whole("scripts/tnt_rain/main.py"))
    assert (sizes["route"], sizes["state"], sizes["actions_how"]) == ("jev", "ok", "by Jev")


def gathered(**over) -> examples.Gathered:
    g = dict(task="ice_beam_steve", picks={**ROUTE}, route_failed=None, state=STATE, state_failed=None, server="main",
             acts={"actions": {"ice_beam": {"script": "ice_beam", "source": "library"}}}, copies={})
    g.update(over)
    return examples.Gathered(**g)


def test_the_header_says_how_the_picks_were_made_and_what_is_held():
    words = {**ROUTE, "fallback": True, "ms": 3012, "why": "jev did not answer within 3 s",
             "scripts": [{**ROUTE["scripts"][0], "verdict": "words"}], "presets": [ROUTE["presets"][0]]}
    assert examples.header(gathered(picks=words))[:3] == [
        "EXAMPLES, picked for ice_beam_steve by words in 3.0 s: Jev did not answer (jev did not answer within 3 s).",
        "Matched by words: ice_beam.", "Presets to copy: crater."]
    held = [{"name": "beam_clash", "source": "library", "in": "galick_gun"},
            {"name": "ice_trail", "source": "made", "in": "galick_ice"}]
    assert examples.header(gathered(picks={**ROUTE, "presets": [], "held": held}))[2] == (
        "Presets to copy: none. Already in a loaded script: beam_clash (in galick_gun) and ice_trail (made) "
        "(in galick_ice).")
    nothing = {**ROUTE, "scripts": [], "presets": [{"name": "ice_trail", "source": "made"}]}
    assert examples.header(gathered(picks=nothing))[1:3] == ["No example script runs this job or is close to it.",
                                                             "Presets to copy: ice_trail (made)."]
    empty = examples.header(gathered(state={**STATE, "online": [], "weather": "rain", "time": {"phase": "day"}}))
    assert empty[-3:] == ["Running: nothing.", "Online: nobody. Day, rain.", examples.NOW]


def test_the_brains_own_copy_of_the_tasks_action_is_the_tasks_to_change():
    g = gathered(task="galick_ice", made=[SimpleNamespace(kind="script", name="galick_ice")])
    copy = {"action": "galick_ice", "script": "galick_ice", "values": {}, "task": None}
    assert examples.changing_line(g, "galick_ice_steve", copy, object()) == (
        "Your brief names galick_ice_steve, a copy of galick_ice running with its action's values: vfg script set "
        "galick_ice_steve key=value changes it; its script is below.")


def test_which_actions_run_the_picked_scripts():
    acts = {"actions": {"ice_beam": {"script": "ice_beam", "source": "library"},
                        "frost_ray": {"script": "ice_beam", "source": "library"},
                        "galick_ice": {"script": "galick_ice", "source": "made"}}}
    ice, made, seed = ({"name": "ice_beam", "source": "library"}, {"name": "galick_ice", "source": "made"},
                       {"name": "tnt_rain", "source": "library"})
    assert examples.runs_line([ice, made], acts, "main") == (
        "ice_beam and galick_ice (made) run this job: run one of their actions, ice_beam, frost_ray or galick_ice, "
        "with the values the job needs.")
    assert examples.runs_line([seed], acts, "main") == (
        "tnt_rain runs this job, but no action runs it yet: write one in made/main/actions/<name>.py, vfg action "
        "check <name>, then run it.")
    assert examples.runs_line([ice], None, "main") == "ice_beam runs this job: run its action with the values the job needs."


def test_names_match_as_words_in_a_row_however_they_are_written():
    assert examples.names_in("Run the Galick-Gun at Steve", ["galick_gun", "gun", "galick_ice"]) == ["galick_gun", "gun"]
    assert examples.names_in("a zookeeper", ["zoo"]) == []
    assert examples.names_in("galick then gun", ["galick_gun"]) == []


async def test_route_run_refuses_what_is_not_its_json():
    vfg = FakeVfg().on("route", answer=ok('{"scripts": "no"}'))
    with pytest.raises(route.RouteFailed, match="no scripts and presets"):
        await route.run("x", "torch_ring_steve", 1, vfg)
    vfg.on("route", answer=ok("Traceback"))
    with pytest.raises(route.RouteFailed, match="not JSON"):
        await route.run("x", None, 1, vfg)
    assert vfg.calls == [["route", "--task", "torch_ring_steve"], ["route"]] and vfg.stdin["route"] == "x"


async def test_a_picked_scripts_test_comes_after_it_and_the_tasks_own_test_too(tmp_path):
    files = dict(FILES)
    files["scripts/ice_beam/test.py"] = '"""The test of ice_beam."""\nfrom kit import test\n'
    files["made/main/tests/ice_beam_steve.py"] = '"""The test of ice_beam_steve."""\nfrom kit import test\n'
    files["made/main/tests/zoo_alex.py"] = '"""Another task\'s test."""\n'
    r = Repo(tmp_path, files=files)
    sizes: dict = {}
    block = await examples.build(r.cfg, JOB, "ice_beam_steve", r.vfg, sizes=sizes)
    head, made, acts, rest = parts(block)
    assert [f.split(" ===")[0] for f in rest.split("=== ")[1:]] == [
        "scripts/ice_beam/main.py", "scripts/ice_beam/test.py", "scripts/galick_gun/main.py",
        "made/main/tests/ice_beam_steve.py", "scripts/_presets/crater.py", "scripts/_presets/replace_blocks.py",
        "scripts/tnt_rain/main.py"]
    assert ("Tests below, each after its script: scripts/ice_beam/test.py and made/main/tests/ice_beam_steve.py "
            "(yours); vfg test <name> runs one on the test server.") in head.split("\n")
    assert (sizes["tests_n"], sizes["scripts_n"]) == (2, 2)
    assert sizes["tests"] == len("\n=== scripts/ice_beam/test.py ===\n" + files["scripts/ice_beam/test.py"]) + len(
        "\n=== made/main/tests/ice_beam_steve.py ===\n" + files["made/main/tests/ice_beam_steve.py"])
    # another task's test, or none: nothing
    assert "zoo_alex" not in block
    block = await examples.build(r.cfg, JOB, "zoo_alex_2", r.vfg)
    assert "made/main/tests/" not in block
