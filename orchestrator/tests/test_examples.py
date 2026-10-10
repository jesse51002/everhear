# Not in the TypeScript tests: the script library's EXAMPLES block, which the guard adds to every
# implementer's brief after the saved places (examples.py, route.py).
import json
import os
import time

import pytest
from helpers import config_in, new_store, read, temp_dir, use_fake_vfg, write

from orchestrator import brief, examples, route
from orchestrator.guard import GuardContext, make_guard

ICE_BEAM = '''"""Ice Beam: a beam from the eyes that freezes what it hits."""
ASKS = ["ice beam", "freeze ray"]
from kit import values, aim

VALUES = {
    "player": {"type": "player", "default": "@a"},
    "freeze_seconds": {"type": "int", "min": 1, "max": 60, "default": 5, "live": True},
}

v = values(VALUES)
'''
GALICK = '''"""Galick Gun: charge, then a purple beam that leaves a crater."""
ASKS = ["galick gun"]
VALUES = {"player": {"type": "player", "default": "@a"}, "cooldown": {"type": "number", "min": 0, "max": 60, "default": 10, "live": True}}
'''
ZOO = '''"""Zoo: animals in pens round a player."""
ASKS = ["a zoo"]
VALUES = {"player": {"type": "player", "default": "@a"}}
'''
CRATER = '''"""Carve a sphere out of the world at a place, leaving air, lava or fire."""
from kit import area

VALUES = {"crater_radius": {"type": "int", "min": 2, "max": 64, "default": 8, "live": True}}


def crater(at, v):
    area.sphere(at, v["crater_radius"], block="air")
'''
REPLACE = '''"""Replace one kind of block with another in a box."""
VALUES = {"replace_to": {"type": "block", "default": "ice"}}


def replace_blocks(box, v):
    pass
'''
GALICK_ICE = '''"""Galick Ice: the Galick Gun, laying ice where it hits."""
ASKS = ["galick gun that freezes"]
VALUES = {"player": {"type": "player", "default": "@a"}}
'''
ICE_TRAIL = '''"""Lay packed ice under a player's feet as they walk."""
VALUES = {"ice_trail_block": {"type": "block", "default": "packed_ice"}}


def ice_trail(p, v):
    pass
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
}

ROUTE = {"task": "T7", "scripts": [
    {"name": "ice_beam", "source": "library", "verdict": "runs_it", "p_runs_it": 0.91, "p_close": 0.05,
     "path": "scripts/ice_beam/main.py"},
    {"name": "galick_gun", "source": "library", "verdict": "close", "p_runs_it": 0.1, "p_close": 0.42,
     "path": "scripts/galick_gun/main.py"}],
    "presets": [{"name": "crater", "source": "library", "p_use": 0.77, "path": "scripts/_presets/crater.py"},
                {"name": "replace_blocks", "source": "library", "p_use": 0.5, "path": "scripts/_presets/replace_blocks.py"}],
    "fallback": False, "ms": 312, "tokens": 11840}

STATE = {"online": [{"name": "Steve", "pos": [120, 64, -40], "dimension": "overworld"},
                    {"name": "Alex", "pos": [98, 70, -12], "dimension": "overworld"}],
         "time": {"daytime": 18000, "day": 3, "phase": "night"}, "weather": "clear",
         "instances": [
             {"instance": "galick-alex", "script": "galick_gun", "source": "library", "task": "T5", "pid": 10,
              "values": {"player": "Alex", "cooldown": 0}, "changed": {"player": "Alex", "cooldown": 0},
              "args": [], "touches": ["Alex"]},
             {"instance": "horror_mode", "script": "horror_mode", "source": "library", "task": "T2", "pid": 11,
              "values": {}, "changed": {}, "args": [], "touches": []},
             {"instance": "zoo-steve", "script": "zoo", "source": "library", "task": "T3", "pid": 12,
              "values": {"player": "Steve"}, "changed": {"player": "Steve"}, "args": [], "touches": ["Steve"]}],
         "rcon": None, "ms": 300}

JOB = "T7: give Steve an ice beam with a crater where it hits. Leave tnt_rain alone."
AGENT = {"subagent_type": "implementer", "description": "T7 ice beam for Steve", "prompt": JOB, "run_in_background": True}


@pytest.fixture(autouse=True)
def _off_after():
    yield
    examples.reset()


class Box:
    """A temp repo with a small library and a made/main/, the fake vfg answering route and state."""

    def __init__(self, route_json=ROUTE, state=STATE, extra=None):
        self.dir = temp_dir()
        self.fake = use_fake_vfg(self.dir)
        for rel, text in FILES.items():
            path = os.path.join(self.dir, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write(path, text)
        self.stdin = os.path.join(self.dir, "route-stdin")
        os.environ["VFG_FAKE_ROUTE_STDIN"] = self.stdin
        if route_json is not None:
            os.environ["VFG_FAKE_ROUTE"] = os.path.join(self.dir, "route.json")
            write(os.environ["VFG_FAKE_ROUTE"], json.dumps(route_json))
        if state is not None:
            os.environ["VFG_FAKE_STATE"] = os.path.join(self.dir, "state.json")
            write(os.environ["VFG_FAKE_STATE"], json.dumps(state))
        self.cfg = config_in(self.dir, extra)
        self.store = new_store(os.path.join(self.dir, "orch"))
        self.store.add("T7", at="2026-10-09T12:00:00.000Z", task="an ice beam for Steve", context="")
        self.store.save()
        examples.use(self.cfg, self.store)

    async def block(self, tool_input=AGENT) -> str:
        out = await examples.examples(tool_input["prompt"], tool_input)
        assert out.startswith(tool_input["prompt"] + "\n\nEXAMPLES")
        return out[len(tool_input["prompt"]) + 2:]


def whole(rel: str) -> str:
    return "=== %s ===\n%s" % (rel, FILES[rel])


# ---------------------------------------------------------------- the block

async def test_the_block_in_the_specs_shape_with_every_file_whole_in_order():
    b = Box()
    block = await b.block()
    head, rest = block.split("\n\n", 1)
    assert head.split("\n") == [
        "EXAMPLES, picked for T7 by Jev in 0.3 s.",
        "ice_beam runs this job: run it, or change its values. galick_gun is close.",
        "Presets to copy: crater and replace_blocks.",
        "Named in your brief, in full below: tnt_rain.",
        "Running: galick-alex (galick_gun, player=Alex, cooldown=0, T5), horror_mode (T2), zoo-steve (zoo, player=Steve, T3).",
        "Online: Steve 120 64 -40 overworld, Alex 98 70 -12 overworld. Night, clear.",
        examples.NOW,
    ]
    made, files = rest.split("\n\n", 1)
    assert made.split("\n") == [
        "Made on this server (made/main/), by first line:",
        "  galick_ice (script): Galick Ice: the Galick Gun, laying ice where it hits.",
        "  ice_trail (preset): Lay packed ice under a player's feet as they walk.",
    ]
    # whole files, never cut: the router's scripts (runs_it first), its presets, then the named
    order = ["scripts/ice_beam/main.py", "scripts/galick_gun/main.py", "scripts/_presets/crater.py",
             "scripts/_presets/replace_blocks.py", "scripts/tnt_rain/main.py"]
    assert [line[4:-4] for line in files.split("\n") if line.startswith("=== ")] == order
    for rel in order:
        assert whole(rel) in block
    assert files == "\n".join(whole(rel) for rel in order)
    # the made ones are first lines only; the brief named no running instance
    assert "=== made/" not in block
    assert "Your brief names" not in block


async def test_a_running_instance_the_brief_names_comes_with_its_values_and_its_script():
    b = Box()
    job = "T7: make the zoo-steve pens bigger, and galick-alex fires faster."
    block = await b.block({**AGENT, "prompt": job})
    lines = block.split("\n")
    # another task's instances: their code is copied to be changed, never edited (the guard refuses it)
    assert ("Your brief names galick-alex, running galick_gun for T5 now with player=Alex, cooldown=0; its script is "
            "below; to change its code, copy it: vfg script new <new_name> --from galick_gun.") in lines
    assert ("Your brief names zoo-steve, running zoo for T3 now with player=Steve; its script is below; to change its "
            "code, copy it: vfg script new <new_name> --from zoo.") in lines
    # galick_gun is the router's already, so it is not there twice; zoo comes last, whole
    assert block.count("=== scripts/galick_gun/main.py ===") == 1
    assert block.endswith(whole("scripts/zoo/main.py"))


async def test_an_instance_this_task_started_is_its_own_to_change():
    b = Box()
    job = "T5: galick-alex fires faster."
    lines = (await b.block({**AGENT, "description": "T5 galick gun faster", "prompt": job})).split("\n")
    assert "Your brief names galick-alex, running galick_gun for T5 now with player=Alex, cooldown=0; its script is below." in lines


async def test_names_match_as_words_in_a_row_however_they_are_written():
    assert examples.names_in("Run the Galick-Gun at Steve", ["galick_gun", "gun", "galick_ice"]) == ["galick_gun", "gun"]
    assert examples.names_in("a zookeeper", ["zoo"]) == []
    assert examples.names_in("tnt_rain please", ["tnt_rain", "rain", "tnt"]) == ["tnt_rain", "rain", "tnt"]
    assert examples.names_in("galick then gun", ["galick_gun"]) == []


async def test_the_router_gets_the_orchestrators_brief_and_the_task_and_the_picks_go_into_state_json():
    b = Box()
    # the places line the step before added is not part of the job the router reads
    out = await examples.examples(JOB + "\n\nSAVED PLACES: none", AGENT)
    assert out.startswith(JOB + "\n\nSAVED PLACES: none\n\nEXAMPLES, picked for T7")
    assert read(b.stdin) == JOB
    assert ["route", "--task", "T7"] in b.fake.calls()
    assert ["state", "--json"] in b.fake.calls()
    t = new_store(os.path.join(b.dir, "orch")).get("T7")  # from disk
    [entry] = t.implementers
    assert entry["description"] == "T7 ice beam for Steve"
    assert entry["route"]["scripts"][0] == {"name": "ice_beam", "source": "library", "verdict": "runs_it",
                                            "p_runs_it": 0.91, "p_close": 0.05}
    assert [p["name"] for p in entry["route"]["presets"]] == ["crater", "replace_blocks"]
    assert entry["route"]["fallback"] is False and entry["route"]["ms"] == 312 and entry["route"]["tokens"] == 11840
    assert entry["loaded"][0] == "scripts/ice_beam/main.py" and entry["loaded"][-1] == "scripts/tnt_rain/main.py"
    assert entry["named"] == ["tnt_rain"]
    assert entry["state"] == "ok"
    assert json.load(open(b.store.file))["tasks"]["T7"]["implementers"][0]["route"]["tokens"] == 11840


async def test_a_second_implementer_for_the_task_gets_its_own_entry():
    b = Box()
    await b.block()
    await b.block({**AGENT, "description": "T7 ice beam, second part"})
    assert [e["description"] for e in b.store.get("T7").implementers] == ["T7 ice beam for Steve", "T7 ice beam, second part"]


async def test_jev_slow_the_routers_fallback_to_words_is_said():
    words = {**ROUTE, "fallback": True, "ms": 3012, "tokens": 0, "why": "jev did not answer within 3 s",
             "scripts": [{**ROUTE["scripts"][0], "verdict": "words", "p_runs_it": None, "p_close": None}],
             "presets": [{**ROUTE["presets"][0], "p_use": None}]}
    b = Box(route_json=words)
    head = (await b.block()).split("\n")[:3]
    assert head == ["EXAMPLES, picked for T7 by words in 3.0 s: Jev did not answer (jev did not answer within 3 s).",
                    "Matched by words: ice_beam.", "Presets to copy: crater."]
    assert b.store.get("T7").implementers[0]["route"]["why"] == "jev did not answer within 3 s"


async def test_presets_a_loaded_script_already_holds_are_named_with_the_script_they_are_in():
    held = [{"name": "beam_clash", "source": "library", "p_use": 0.68, "path": "scripts/_presets/beam_clash.py",
             "in": "galick_gun"},
            {"name": "ice_trail", "source": "made", "p_use": 0.4, "path": "made/main/presets/ice_trail.py",
             "in": "galick_ice"}]
    b = Box(route_json={**ROUTE, "held": held})
    lines = (await b.block()).split("\n")
    assert lines[2] == ("Presets to copy: crater and replace_blocks. Already in a loaded script: beam_clash "
                        "(in galick_gun) and ice_trail (made) (in galick_ice).")
    # not loaded again: the copy in the script is what the implementer reads
    assert "=== scripts/_presets/beam_clash.py ===" not in "\n".join(lines)
    assert "=== made/main/presets/ice_trail.py ===" not in "\n".join(lines)
    assert b.store.get("T7").implementers[0]["route"]["held"] == [
        {"name": "beam_clash", "source": "library", "p_use": 0.68, "in": "galick_gun"},
        {"name": "ice_trail", "source": "made", "p_use": 0.4, "in": "galick_ice"}]
    # with none to copy, it still says where the held ones are
    b = Box(route_json={**ROUTE, "presets": [], "held": held[:1]})
    assert (await b.block()).split("\n")[2] == "Presets to copy: none. Already in a loaded script: beam_clash (in galick_gun)."
    # and a routing with none held keeps no "held" in state.json
    b = Box()
    await b.block()
    assert "held" not in b.store.get("T7").implementers[0]["route"]


async def test_the_server_state_line_says_to_act_on_it_only_when_the_state_was_read():
    b = Box()
    lines = (await b.block()).split("\n")
    assert lines[lines.index("Online: Steve 120 64 -40 overworld, Alex 98 70 -12 overworld. Night, clear.") + 1] == examples.NOW
    assert "no vfg state" in examples.NOW
    assert examples.UNKNOWN not in lines
    # vfg state failed: the state is unknown, and one vfg state is worth running
    os.environ["VFG_FAKE_STATE_FAIL"] = "EC2 unreachable (fake)"
    lines = (await Box().block()).split("\n")
    assert examples.NOW not in lines
    assert lines[lines.index("Server state: unavailable (exit 1: vfg: EC2 unreachable (fake)).") + 1] == examples.UNKNOWN
    assert "one vfg state is worth running" in examples.UNKNOWN
    del os.environ["VFG_FAKE_STATE_FAIL"]
    # Minecraft not answering (exit 5): what runs is listed, who is online is not, so the same
    os.environ["VFG_FAKE_STATE_CODE"] = "5"
    b = Box(state={**STATE, "online": [], "rcon": "Minecraft is not answering on RCON"})
    lines = (await b.block()).split("\n")
    assert examples.NOW not in lines
    assert lines[lines.index("Minecraft is not answering (Minecraft is not answering on RCON): who is online, the time "
                             "and the weather are unknown.") + 1] == examples.UNKNOWN


async def test_nothing_close_and_a_made_pick_say_so():
    picks = {**ROUTE, "scripts": [], "presets": [{"name": "ice_trail", "source": "made", "p_use": 0.8,
                                                  "path": "made/main/presets/ice_trail.py"}]}
    b = Box(route_json=picks)
    block = await b.block({**AGENT, "prompt": "T7: something new"})
    assert block.split("\n")[1:3] == ["No example script runs this job or is close to it.", "Presets to copy: ice_trail (made)."]
    assert whole("made/main/presets/ice_trail.py") in block


async def test_an_empty_server_folder_and_nobody_online():
    b = Box(state={**STATE, "online": [], "instances": [], "weather": "rain", "time": {"phase": "day"}})
    for rel in ("made/main/scripts/galick_ice/main.py", "made/main/presets/ice_trail.py"):
        os.remove(os.path.join(b.dir, rel))
    block = await b.block()
    assert "Running: nothing." in block.split("\n")
    assert "Online: nobody. Day, rain." in block.split("\n")
    assert "Made on this server (made/main/): nothing yet." in block.split("\n")


async def test_the_server_folder_is_vfg_server():
    b = Box(extra={"VFG_SERVER": "lobby"})
    os.makedirs(os.path.join(b.dir, "made", "lobby", "scripts", "moat"))
    write(os.path.join(b.dir, "made", "lobby", "scripts", "moat", "main.py"), '"""Moat: water round a build."""\nVALUES = {}\n')
    block = await b.block()
    assert "Made on this server (made/lobby/), by first line:\n  moat (script): Moat: water round a build." in block
    assert "galick_ice" not in block


# ---------------------------------------------------------------- what fails, and how long it may take

async def test_the_router_timing_out_never_holds_the_implementer_past_its_timeout_and_a_second():
    b = Box(extra={"VFG_ROUTE_TIMEOUT_S": "0.3"})
    assert b.cfg.route_timeout_s == 0.3
    os.environ["VFG_FAKE_ROUTE_SLEEP"] = "5"
    t = time.monotonic()
    block = await b.block()
    took = time.monotonic() - t
    assert took < 0.3 + 1 + 0.25, took
    assert block.startswith("EXAMPLES for T7: nothing was picked, the router failed (timed out). ")
    # what does not need the router is still there: the state, the made first lines, the named script
    assert "Online: Steve 120 64 -40 overworld, Alex 98 70 -12 overworld. Night, clear." in block
    assert "  ice_trail (preset): Lay packed ice under a player's feet as they walk." in block
    assert whole("scripts/tnt_rain/main.py") in block
    assert b.store.get("T7").implementers[0]["route"] == {"failed": "timed out"}


async def test_a_hung_vfg_is_cut_off_by_the_steps_own_limit(monkeypatch):
    b = Box(extra={"VFG_ROUTE_TIMEOUT_S": "0.2"})

    async def hangs(*args, **kwargs):
        import asyncio
        await asyncio.sleep(30)

    monkeypatch.setattr(examples.vfg_module, "vfg", hangs)
    t = time.monotonic()
    block = await b.block()
    assert time.monotonic() - t < 0.2 + 1 + 0.25
    assert "the router failed (no answer within 1.2 s)" in block
    assert "Server state: unavailable (no answer within 1.2 s).\n" + examples.UNKNOWN in block


async def test_the_server_down_the_block_still_goes_and_says_what_is_missing():
    b = Box()
    os.environ["VFG_FAKE_STATE_FAIL"] = "EC2 unreachable (fake)"
    block = await b.block()
    lines = block.split("\n")
    assert lines[:4] == ["EXAMPLES, picked for T7 by Jev in 0.3 s.",
                         "ice_beam runs this job: run it, or change its values. galick_gun is close.",
                         "Presets to copy: crater and replace_blocks.",
                         "Named in your brief, in full below: tnt_rain."]
    assert lines[4:6] == ["Server state: unavailable (exit 1: vfg: EC2 unreachable (fake)).", examples.UNKNOWN]
    assert whole("scripts/ice_beam/main.py") in block
    assert b.store.get("T7").implementers[0]["state"] == "exit 1: vfg: EC2 unreachable (fake)"


async def test_minecraft_not_answering_is_said_and_what_runs_is_still_listed():
    b = Box(state={**STATE, "online": [], "rcon": "Minecraft is not answering on RCON"})
    os.environ["VFG_FAKE_STATE_CODE"] = "5"
    lines = (await b.block()).split("\n")
    assert "Minecraft is not answering (Minecraft is not answering on RCON): who is online, the time and the weather are unknown." in lines
    assert any(line.startswith("Running: galick-alex") for line in lines)


async def test_the_router_failing_says_why():
    b = Box()
    os.environ["VFG_FAKE_ROUTE_FAIL"] = "not configured: VFG_ROUTE_LOAD must be a number"
    block = await b.block()
    assert block.startswith("EXAMPLES for T7: nothing was picked, the router failed "
                            "(exit 1: vfg: not configured: VFG_ROUTE_LOAD must be a number).")


async def test_route_run_refuses_what_is_not_its_json():
    d = temp_dir()
    use_fake_vfg(d)
    os.environ["VFG_FAKE_ROUTE"] = os.path.join(d, "r.json")
    write(os.environ["VFG_FAKE_ROUTE"], '{"scripts": "no"}')
    with pytest.raises(route.RouteFailed):
        await route.run("x", "T1", 1)
    write(os.environ["VFG_FAKE_ROUTE"], "Traceback")
    with pytest.raises(route.RouteFailed, match="not JSON"):
        await route.run("x", None, 1)


# ---------------------------------------------------------------- in the guard, after the places

async def test_off_until_the_runner_turns_it_on():
    use_fake_vfg(temp_dir())
    assert await examples.examples("x", AGENT) == "x"


async def test_an_allowed_agent_call_goes_out_with_the_places_then_the_examples():
    b = Box()
    g = GuardContext(repo=b.dir, max_implementers=4, implementer_model="claude-haiku-5-5", main_thread_rule=True,
                     open_task_ids=lambda: ["T7"], active_implementers=lambda: 0, on_agent_allowed=lambda t, u: None)
    call = {"session_id": "s", "transcript_path": "t", "cwd": b.dir, "hook_event_name": "PreToolUse",
            "tool_name": "Agent", "tool_input": AGENT, "tool_use_id": "u1"}
    out = await make_guard(g)(call, "u1", {"signal": None})
    updated = out["hookSpecificOutput"]["updatedInput"]
    assert {k: v for k, v in updated.items() if k != "prompt"} == {k: v for k, v in AGENT.items() if k != "prompt"}
    assert updated["prompt"].startswith(JOB + "\n\nSAVED PLACES: none\n\nEXAMPLES, picked for T7 by Jev in 0.3 s.\n")
    assert whole("scripts/ice_beam/main.py") in updated["prompt"]
    assert brief.STEPS[-1] is examples.examples
