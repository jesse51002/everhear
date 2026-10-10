"""One task end to end, with a fake structured(), a fake vfg and fake refs: every case in the
spec's Test section that is about a task."""

from __future__ import annotations

import asyncio
import json
from typing import Literal

import pytest
from pydantic import Field, ValidationError

from vfg_llm import LlmError

from conftest import CATALOG, Action, Harness, Teleport, bad, cmd, fail, ok, planned, select, tp
from dispatcher.action_planner import Final, final_type, planned_type


def args_of(argv: list[str]) -> dict:
    return json.loads(argv[argv.index("--args") + 1])


# ---------------------------------------------------------------- done

async def test_two_picks_both_done(h):
    h.llm.add("Selection", select(("command", "give Alex Speed II"),
                                  ("teleport", "teleport Alex to a village")))
    h.llm.add("Planned_command", planned(cmd("effect give Alex minecraft:speed 60 1"), say="Speed II for Alex"))
    h.llm.add("Planned_command", planned(cmd("effect give Alex minecraft:speed 60 1"), say="Speed II for Alex"))
    h.llm.add("Planned_teleport", planned(tp("Alex", "structure", "village"), say="taking Alex to a village"))
    h.vfg.on("action", "run", "command", answer=ok("Applied effect Speed to Alex"))
    h.vfg.on("action", "run", "teleport", answer=ok("teleported Alex to a village at 812 70 -340",
                                                    pos=[812, 70, -340]))

    tid = await h.run("give me speed two and take me to a village")

    assert h.vfg.replies() == [
        (tid, "progress", "Speed II for Alex — Applied effect Speed to Alex"),
        (tid, "done", "taking Alex to a village — teleported Alex to a village at 812 70 -340"),
    ]
    assert h.vfg.hands() == []
    assert h.state.tasks == {} and h.state.outbox == []
    assert [c.output for c in h.llm.calls] == ["Selection", "Planned_command", "Planned_command", "Planned_teleport"]


async def test_each_report_goes_out_as_its_action_finishes_and_the_last_as_done_with_facts(h):
    # (two adjacent command parts would be merged into one pick: a teleport comes second)
    h.llm.add("Selection", select(("command", "give Alex 64 cooked beef"), ("teleport", "teleport Alex to a village")))
    c = "give Alex minecraft:cooked_beef 64"
    h.llm.add("Planned_command", planned(cmd(c), say=f"running {c}"), planned(cmd(c), say=f"running {c}"))
    h.llm.add("Planned_teleport", planned(tp("Alex", "structure", "village"), say="to a village"))
    h.vfg.on("action", "run", "command", answer=ok("Gave 64 [Cooked Beef] to Alex"))
    h.vfg.on("action", "run", "teleport", answer=ok("teleported Alex to a village at 812 70 -340"))

    tid = await h.run("give me 64 steak and take me to a village")

    # the first report reached vfg orch reply before the second action started
    first_reply = next(i for i, a in enumerate(h.vfg.calls) if a[:2] == ["orch", "reply"])
    second_action = [i for i, a in enumerate(h.vfg.calls) if a[:2] == ["action", "run"]][1]
    assert first_reply < second_action
    assert h.vfg.replies() == [
        (tid, "progress", "running give Alex minecraft:cooked_beef 64 — Gave 64 [Cooked Beef] to Alex"),
        (tid, "done", "to a village — teleported Alex to a village at 812 70 -340"),
    ]


async def test_the_second_action_sees_the_first_ones_report(h):
    h.llm.add("Selection", select(("teleport", "teleport Alex to a village"),
                                  ("command", "tell Alex where the village is")))
    h.llm.add("Planned_teleport", planned(tp("Alex", "structure", "village"), say="to a village"))
    h.llm.add("Planned_command", planned(cmd("say hi"), say="telling"), planned(cmd("say hi"), say="telling"))
    h.vfg.on("action", "run", "teleport", answer=ok("teleported Alex to a village at 812 70 -340"))

    await h.run("take me to a village and tell me where it is")

    second = h.llm.prompts("Planned_command")[0]
    assert "EARLIER IN THIS TASK:\n- to a village — teleported Alex to a village at 812 70 -340" in second
    first = h.llm.prompts("Planned_teleport")[0]
    assert "EARLIER IN THIS TASK: none" in first


async def test_a_listing_is_its_own_pick_and_the_change_after_it_sees_it(h):
    h.llm.add("Selection", select(("command", "list Alex's inventory and armor"),
                                  ("command", "put Depth Strider III on Alex's boots")))
    look = cmd("data get entity Alex Inventory")
    put = cmd("item modify entity Alex armor.feet {type:\"minecraft:set_enchantments\",enchantments:{\"minecraft:depth_strider\":3},add:true}")
    h.llm.add("Planned_command", planned(look, say="listing"), planned(look, say="listing"),
              planned(put, say="boots"), planned(put, say="boots"))
    h.vfg.on("action", "run", "command", answer=ok('Alex has the following entity data: {feet: {count: 1, id: "minecraft:diamond_boots"}}'))

    await h.run("put depth strider on my boots")

    runs = [a for a in h.vfg.calls if a[:2] == ["action", "run"]]
    assert len(runs) == 2  # two picks, not one merged pick
    change = h.llm.prompts("Planned_command")[2]
    assert 'EARLIER IN THIS TASK:\n- listing — Alex has the following entity data: {feet: {count: 1, id: "minecraft:diamond_boots"}}' in change


async def test_a_tasks_actions_run_in_the_order_picked_the_second_after_the_first_finished(h):
    h.llm.add("Selection", select(("command", "make it night"), ("teleport", "teleport Alex to a village")))
    h.llm.add("Planned_command", planned(cmd("time set night"), say="night"), planned(cmd("time set night"), say="night"))
    h.llm.add("Planned_teleport", planned(tp("Alex", "structure", "village"), say="village"))

    async def slow(argv):
        await asyncio.sleep(0.05)
        return ok("done")

    h.vfg.on("action", "run", "command", answer=slow)
    h.vfg.on("action", "run", "teleport", answer=slow)
    await h.run("night, then a village")

    runs = [(kind, args_of(a).get("commands", [a[2]])[0]) for kind, a in h.vfg.events if a[:2] == ["action", "run"]]
    assert runs == [("start", "time set night"), ("end", "time set night"),
                    ("start", "teleport"), ("end", "teleport")]


async def test_two_tasks_run_side_by_side(h):
    release = asyncio.Event()

    async def held(argv):
        await release.wait()
        return ok("teleported Alex to a village")

    async def answer_selection(prompt):
        if "village" in prompt.split("\nTASK: ")[1]:
            return select(("teleport", "teleport Alex to a village"))
        return select(("command", "give Steve bread"))

    h.llm.add("Selection", answer_selection, answer_selection)
    h.llm.add("Planned_teleport", planned(tp("Alex", "structure", "village"), say="village"))
    h.llm.add("Planned_command", planned(cmd("give Steve minecraft:bread"), say="bread"),
              planned(cmd("give Steve minecraft:bread"), say="bread"))
    h.vfg.on("action", "run", "teleport", answer=held)
    h.vfg.on("action", "run", "command", answer=ok("Gave 1 [Bread] to Steve"))

    slow = h.submit("take me to a village")
    fast = h.submit("give Steve bread")
    a, b = h.d.start(slow), h.d.start(fast)
    await asyncio.wait_for(b, 2)
    await h.reply.drain(1)
    assert (fast, "done", "bread — Gave 1 [Bread] to Steve") in h.vfg.replies()
    assert not a.done() and h.vfg.replies(slow) == []
    release.set()
    await asyncio.wait_for(a, 2)
    await h.settle()
    assert h.vfg.replies(slow) == [(slow, "done", "village — teleported Alex to a village")]


# ---------------------------------------------------------------- handed on

async def test_a_pick_and_an_orchestrator_part(h):
    h.llm.add("Selection", select(("command", "give Alex Speed I"), orchestrator="build Alex a castle"))
    h.llm.add("Planned_command", planned(cmd("effect give Alex minecraft:speed"), say="Speed for Alex"),
              planned(cmd("effect give Alex minecraft:speed"), say="Speed for Alex"))
    h.vfg.on("action", "run", "command", answer=ok("Applied effect Speed to Alex"))

    tid = await h.run("give me speed and build me a castle", "Alex is at 10 64 10")

    assert h.vfg.replies() == [
        (tid, "progress", "Speed for Alex — Applied effect Speed to Alex"),
        (tid, "progress", "handed on to the orchestrator: build Alex a castle"),
    ]
    [[line]] = h.vfg.hands()
    assert line["id"] == tid and line["at"] == "2026-10-09T12:00:00.000Z"
    assert line["task"] == "build Alex a castle"
    assert line["context"] == ("Alex is at 10 64 10\n\n"
                               "Dispatcher: done Speed for Alex — Applied effect Speed to Alex")
    assert h.state.batch == [] and h.state.tasks == {}


async def test_a_selection_with_nothing_in_it_is_handed_whole(h):
    h.llm.add("Selection", select())
    tid = await h.run("do something odd", "ctx")
    [[line]] = h.vfg.hands()
    assert line == {"id": tid, "at": "2026-10-09T12:00:00.000Z", "task": "do something odd", "context": "ctx"}
    assert h.vfg.actions() == []
    assert h.vfg.replies() == [(tid, "progress", "handed on to the orchestrator: do something odd")]


@pytest.mark.parametrize("task", ["Change to T3: make it taller", "Cancel T3: no tower", "cancel t12"])
async def test_earlier_work_is_handed_whole_with_no_model_call_and_no_places_fetch(h, task):
    tid = await h.run(task, "ctx")
    assert h.llm.calls == []
    assert h.vfg.calls[0][:2] != ["place", "list"] and not [a for a in h.vfg.calls if a[:2] == ["place", "list"]]
    [[line]] = h.vfg.hands()
    assert line == {"id": tid, "at": "2026-10-09T12:00:00.000Z", "task": task, "context": "ctx"}


@pytest.mark.parametrize("task", ["Fix the weather", "Undo the rain", "Build Steve a tower", "Give Alex a building kit"])
async def test_a_fix_an_undo_or_a_build_reaches_the_selector(h, task):
    h.llm.add("Selection", select(orchestrator=task))
    await h.run(task, "ctx")
    assert [c.output for c in h.llm.calls] == ["Selection"]  # the parts and the prompt decide, not a regex


def test_block_work_is_the_verb_build_or_a_block_change_never_a_building():
    from dispatcher.selector import BLOCK_WORK
    for yes in ("build a tower", "Steve builds a wall", "place blocks around Alex", "remove the blocks under him"):
        assert BLOCK_WORK.search(yes), yes
    for no in ("give Alex a building kit", "teleport Steve to the tallest building", "rebuild nothing"):
        assert not BLOCK_WORK.search(no), no


async def test_a_selection_naming_a_script_not_in_scripts_is_handed_whole(h):
    h.llm.add("Selection", select(("command", "give Alex bread"), ("run_script", "start the snow_storm script")))
    tid = await h.run("bread, and a snow storm", "ctx")
    assert [c.output for c in h.llm.calls] == ["Selection"]  # no planner call: nothing ran
    [[line]] = h.vfg.hands()
    assert line == {"id": tid, "at": "2026-10-09T12:00:00.000Z", "task": "bread, and a snow storm", "context": "ctx"}
    assert h.vfg.actions() == []


async def test_an_orchestrator_part_alone_hands_the_original_task(h):
    h.llm.add("Selection", select(orchestrator="build a tower"))
    await h.run("build me a small stone tower")
    [[line]] = h.vfg.hands()
    assert line["task"] == "build me a small stone tower" and line["context"] == ""


async def test_a_complexity_selector_call_that_fails_hands_the_whole_task(h):
    h.llm.add("Selection", LlmError("OpenRouter unreachable: ConnectError"))
    await h.run("give me speed", "ctx")
    [[line]] = h.vfg.hands()
    assert line["task"] == "give me speed"
    assert line["context"] == "ctx\n\nDispatcher: the complexity selector failed: OpenRouter unreachable: ConnectError"
    assert h.vfg.actions() == []


async def test_an_action_planner_that_hands_on_at_once(h):
    h.llm.add("Selection", select(("teleport", "teleport Alex somewhere cosy"),
                                  ("command", "give Alex a bed")))
    h.llm.add("Planned_teleport", planned(hand_on="find a cosy spot for Alex and take him there", say="handing on"))

    await h.run("take me somewhere cosy and give me a bed")

    assert h.vfg.actions() == []
    [[line]] = h.vfg.hands()
    assert line["task"] == "take me somewhere cosy and give me a bed"  # nothing was done: the original
    assert "not done teleport Alex somewhere cosy: the action planner handed it on: find a cosy spot" in line["context"]
    assert [c.output for c in h.llm.calls] == ["Selection", "Planned_teleport"]


async def test_an_action_planner_call_that_fails_is_a_hand_on(h):
    h.llm.add("Selection", select(("teleport", "teleport Alex to a village")))
    h.llm.add("Planned_teleport", LlmError("no answer within 30 s"))
    await h.run("take me to a village")
    [[line]] = h.vfg.hands()
    assert "the action planner failed: no answer within 30 s" in line["context"]


async def test_one_that_fails_twice_goes_to_the_batch_with_the_picks_after_it(h):
    h.llm.add("Selection", select(("command", "give Alex Speed II"),
                                  ("teleport", "teleport Alex to a cherry grove"),
                                  ("command", "give Alex 5 cherry saplings")), )
    h.llm.add("Planned_command", planned(cmd("effect give Alex minecraft:speed 60 1"), say="speed"),
              planned(cmd("effect give Alex minecraft:speed 60 1"), say="speed"))
    h.llm.add("Planned_teleport",
              planned(tp("Alex", "biome", "cherry_grove"), say="to a cherry grove"),
              planned(tp("Alex", "biome", "#is_forest"), say="to a forest instead"))
    h.llm.add("Final_teleport", {"hand_on": "find a cherry grove by hand and take Alex there", "say": "handing on"})
    h.vfg.on("action", "run", "command", answer=ok("Applied effect Speed to Alex"))
    h.vfg.on("action", "run", "teleport", answer=bad("no safe spot within 4 rings of the cherry grove at 812 -340"))

    tid = await h.run("speed, then a cherry grove, then saplings")

    teleports = [a for a in h.vfg.actions() if a[2] == "teleport"]
    assert len(teleports) == 2  # one try and VFG_DISPATCH_RETRIES=1 retry
    assert [args_of(a)["target"] for a in teleports] == ["cherry_grove", "#is_forest"]
    assert len([a for a in h.vfg.actions() if a[2] == "command"]) == 1  # the saplings never ran
    final = h.llm.prompts("Final_teleport")[0]
    assert "IT FAILED: no safe spot within 4 rings" in final and "It cannot be tried again" in final
    [[line]] = h.vfg.hands()
    assert line["task"] == "teleport Alex to a cherry grove; then give Alex 5 cherry saplings"
    assert line["context"] == (
        "Dispatcher: done speed — Applied effect Speed to Alex; "
        "failed teleport Alex to a cherry grove because no safe spot within 4 rings of the cherry grove at 812 -340 "
        "(the action planner handed it on: find a cherry grove by hand and take Alex there)")
    assert h.vfg.replies() == [
        (tid, "progress", "speed — Applied effect Speed to Alex"),
        (tid, "progress", "handed on to the orchestrator: teleport Alex to a cherry grove; then give Alex 5 cherry saplings"),
    ]


async def test_an_action_that_fails_is_fixed_by_the_action_planner_and_runs(h):
    h.llm.add("Selection", select(("teleport", "teleport Alex to a cherry grove")))
    h.llm.add("Planned_teleport",
              planned(tp("Alex", "biome", "cherry_grove"), say="to a cherry grove"),
              planned(tp("Alex", "biome", "minecraft:cherry_grove"), say="to a cherry grove, again"))
    h.vfg.on("action", "run", "teleport", answer=[bad("no safe spot within 4 rings"),
                                                  ok("teleported Alex to a cherry grove at 812 90 -340")])

    tid = await h.run("take me to a cherry grove")

    retry = h.llm.prompts("Planned_teleport")[1]
    assert 'YOUR LAST ATTEMPT: {"player": "Alex", "to": "biome", "target": "cherry_grove"}' in retry
    assert "IT FAILED: no safe spot within 4 rings" in retry
    assert "Change the action, hand it on, or drop it." in retry
    assert h.vfg.replies() == [(tid, "done", "to a cherry grove, again — teleported Alex to a cherry grove at 812 90 -340")]
    assert h.vfg.hands() == []


# ---------------------------------------------------------------- dropped

async def test_a_drop_because_the_player_is_offline_is_told_at_once_and_the_next_pick_runs(h):
    h.llm.add("Selection", select(("teleport", "teleport Steve to a cherry grove"),
                                  ("command", "give Alex a cake")))
    h.llm.add("Planned_teleport", planned(tp("Steve", "biome", "cherry_grove"), say="Steve to a cherry grove"),
              planned(drop="Steve is not online", say="dropping"))
    h.llm.add("Planned_command", planned(cmd("give Alex minecraft:cake"), say="a cake for Alex"),
              planned(cmd("give Alex minecraft:cake"), say="a cake for Alex"))
    h.vfg.on("action", "run", "teleport", answer=bad("No player was found"))
    h.vfg.on("action", "run", "command", answer=ok("Gave 1 [Cake] to Alex"))

    tid = await h.run("send Steve to a cherry grove and give me a cake")

    told = next(i for i, a in enumerate(h.vfg.calls) if a[:2] == ["orch", "reply"])
    cake = next(i for i, a in enumerate(h.vfg.calls) if a[:3] == ["action", "run", "command"])
    assert told < cake
    assert h.vfg.replies() == [
        (tid, "progress", "dropped: teleport Steve to a cherry grove — Steve is not online"),
        (tid, "done", "a cake for Alex — Gave 1 [Cake] to Alex; dropped: teleport Steve to a cherry grove — Steve is not online"),
    ]
    assert h.vfg.hands() == []


async def test_a_handler_reporting_offline_players_drops_the_pick_at_once_without_the_planner(h):
    h.llm.add("Selection", select(("teleport", "teleport Bob to a village"), ("command", "give Alex a cake")))
    h.llm.add("Planned_teleport", planned(tp("Bob", "structure", "village"), say="Bob to a village"))
    h.llm.add("Planned_command", planned(cmd("give Alex minecraft:cake"), say="a cake"),
              planned(cmd("give Alex minecraft:cake"), say="a cake"))
    h.vfg.on("action", "run", "teleport", answer=bad("Bob is not online", offline=["Bob"]))
    h.vfg.on("action", "run", "command", answer=ok("Gave 1 [Cake] to Alex"))

    tid = await h.run("teleport Bob to a village and give me a cake")

    assert [c.output for c in h.llm.calls].count("Planned_teleport") == 1  # no planner call after the run
    assert [a[2] for a in h.vfg.actions()] == ["teleport", "command"]  # run once, then the next pick
    assert h.vfg.replies() == [
        (tid, "progress", "dropped: teleport Bob to a village — Bob not online"),
        (tid, "done", "a cake — Gave 1 [Cake] to Alex; dropped: teleport Bob to a village — Bob not online"),
    ]
    assert h.vfg.hands() == []


class SummonAround(Action):
    """Summon a number of one mob round each player."""
    action: Literal["summon_around"]
    players: str = Field(description="round whom")
    mob: str = Field(description="the mob")


async def test_offline_players_alone_end_a_task_failed_naming_each_and_a_plain_refusal_still_goes_back(tmp_path):
    h = Harness(tmp_path, catalog={**CATALOG, "summon_around": SummonAround})
    h.llm.add("Selection", select(("summon_around", "summon zombies round Bob and Eve")),
              select(("teleport", "teleport Alex to a village")))
    h.llm.add("Planned_summon_around", planned({"action": "summon_around", "players": "@a", "mob": "zombie"},
                                               say="zombies"))
    h.vfg.on("action", "run", "summon_around", answer=bad("Bob and Eve are not online", offline=["Bob", "Eve"]))
    tid = await h.run("zombies round Bob and Eve")
    assert h.vfg.replies(tid) == [(tid, "failed", "dropped: summon zombies round Bob and Eve — Bob and Eve not online")]
    assert [c.output for c in h.llm.calls] == ["Selection", "Planned_summon_around"]

    # a refusal without `offline` still goes back to the planner
    h.llm.add("Planned_teleport", planned(tp("Alex", "structure", "village"), say="village"),
              planned(drop="no safe ground near any village", say="no"))
    h.vfg.on("action", "run", "teleport", answer=bad("no safe spot within 4 rings"))
    tid2 = await h.run("take me to a village")
    assert len(h.llm.prompts("Planned_teleport")) == 2
    assert h.vfg.replies(tid2) == [(tid2, "failed", "dropped: teleport Alex to a village — no safe ground near any village")]


def test_the_planner_prompt_says_how_to_write_an_effect_and_that_offline_is_a_drop():
    from dispatcher.action_planner import PROMPT
    text = PROMPT.read_text()
    assert "`effect give <player> <effect> <seconds> <amplifier>`: always write both." in text
    assert "No duration asked → `infinite`" in text
    assert "no level asked → 0 (level I); Speed II → 1" in text
    assert '"give Steve speed" is `effect give Steve minecraft:speed infinite 0`' in text
    assert "infinite 1`" not in text  # the old example biased plain speed to Speed II
    assert "- A failure saying a player is not online is a drop." in text
    assert "never drop for it before a failure says so" in text


async def test_a_task_whose_every_pick_is_dropped_ends_failed_with_the_reasons(h):
    h.llm.add("Selection", select(("teleport", "teleport Bob to a village"), ("command", "give Bob bread")))
    h.llm.add("Planned_teleport", planned(drop="Bob is not online", say="no"))
    h.llm.add("Planned_command", planned(drop="Bob is not online, so he cannot get bread", say="no"))

    tid = await h.run("teleport Bob to a village and give him bread")

    assert h.vfg.replies() == [
        (tid, "progress", "dropped: teleport Bob to a village — Bob is not online"),
        (tid, "failed", "dropped: teleport Bob to a village — Bob is not online; "
                        "give Bob bread — Bob is not online, so he cannot get bread"),
    ]
    assert h.vfg.actions() == [] and h.vfg.hands() == []


async def test_a_last_pick_dropped_after_one_done_ends_done_naming_the_drop(h):
    h.llm.add("Selection", select(("command", "give Alex bread"), ("teleport", "teleport Bob to Alex")))
    h.llm.add("Planned_command", planned(cmd("give Alex minecraft:bread"), say="bread"),
              planned(cmd("give Alex minecraft:bread"), say="bread"))
    h.llm.add("Planned_teleport", planned(drop="Bob is not online", say="no"))
    tid = await h.run("bread for me, Bob to me")
    assert h.vfg.replies()[-1] == (tid, "done", "done: give Alex bread; dropped: teleport Bob to Alex — Bob is not online")


# ---------------------------------------------------------------- refused answers

async def test_an_answer_setting_more_than_one_of_action_hand_on_and_drop_is_refused(h):
    Out = planned_type(Teleport)
    with pytest.raises(ValidationError, match="exactly one of action, hand_on and drop"):
        Out.model_validate(planned(tp("Alex", "coords", "1 2"), drop="no", say="x"))
    with pytest.raises(ValidationError, match="exactly one"):
        Out.model_validate(planned(hand_on="a", drop="b", say="x"))
    with pytest.raises(ValidationError, match="exactly one"):
        Out.model_validate(planned(say="nothing set"))
    with pytest.raises(ValidationError, match="exactly one of hand_on and drop"):
        final_type(Teleport).model_validate({"hand_on": "a", "drop": "b", "say": "x"})
    assert issubclass(final_type(Teleport), Final)

    # through the flow: refused twice (the answer and its repair) is a hand-on, and nothing runs
    h.llm.add("Selection", select(("teleport", "teleport Alex to 1 2")))
    h.llm.add("Planned_teleport", planned(tp("Alex", "coords", "1 2"), drop="no", say="x"),
              planned(tp("Alex", "coords", "1 2"), hand_on="also", say="x"))
    await h.run("take me to 1 2")
    assert h.vfg.actions() == []
    [[line]] = h.vfg.hands()
    assert "the action planner failed" in line["context"]


# ---------------------------------------------------------------- command

async def test_a_command_draft_gets_the_reference_of_each_command_it_uses_and_is_approved_as_it_is(h):
    draft = ["execute as @a at @s run give @s minecraft:bread 3", "effect give @a minecraft:speed 30 1"]
    h.llm.add("Selection", select(("command", "give everyone 3 bread and Speed II")))
    h.llm.add("Planned_command", planned(cmd(*draft), say="bread and speed for everyone"),
              planned(cmd(*draft), say="bread and speed for everyone"))
    h.vfg.on("action", "run", "command", answer=ok("Gave 3 [Bread] to Alex; Applied effect Speed to 2 targets"))

    tid = await h.run("give everyone 3 bread and speed two")

    assert h.refs.referenced == [({"execute", "give", "effect"}, {"as", "at", "run"})]
    draft_prompt, approve_prompt = h.llm.prompts("Planned_command")
    assert "MINECRAFT COMMANDS (name — what it does):\ngive — Gives an item to a player." in draft_prompt
    assert "REFERENCES" not in draft_prompt
    assert "YOUR DRAFT:\n1. execute as @a at @s run give @s minecraft:bread 3\n2. effect give" in approve_prompt
    assert "## give\ngive <targets>" in approve_prompt and "execute subcommands: as at run" in approve_prompt
    assert "MINECRAFT COMMANDS" not in approve_prompt
    [run] = h.vfg.actions()
    assert args_of(run) == {"commands": draft} and run[-2:] == ["--timeout", "20"]
    assert h.vfg.replies() == [(tid, "done", "bread and speed for everyone — Gave 3 [Bread] to Alex; "
                                             "Applied effect Speed to 2 targets")]
    assert len(h.llm.calls) == 3  # selector, draft, approve


async def test_a_command_draft_changed_at_approval_runs_changed_and_a_new_command_is_checked_again(h):
    h.llm.add("Selection", select(("command", "put a diamond sword in Alex's hand")))
    h.llm.add("Planned_command",
              planned(cmd("give Alex diamond_sword"), say="a sword"),                                # draft
              planned(cmd("item replace entity Alex weapon.mainhand with diamond_sword"), say="a sword"),  # approve: changed, new command
              planned(cmd("item replace entity Alex weapon.mainhand with minecraft:diamond_sword"), say="a sword in hand"))  # recheck 1
    await h.run("put a diamond sword in my hand")

    assert h.refs.referenced == [({"give"}, set()), ({"item"}, set())]
    assert h.llm.prompts("Planned_command")[2].count("YOUR DRAFT:\n1. item replace") == 1
    [run] = h.vfg.actions()
    assert args_of(run)["commands"] == ["item replace entity Alex weapon.mainhand with minecraft:diamond_sword"]
    assert h.vfg.replies()[0][1] == "done"


async def test_a_command_draft_that_keeps_adding_commands_is_a_failure(tmp_path):
    h = Harness(tmp_path, retries=0)
    h.llm.add("Selection", select(("command", "make Alex glow")))
    h.llm.add("Planned_command",
              planned(cmd("effect give Alex glowing"), say="glow"),                      # draft
              planned(cmd("give Alex glowstone"), say="glow"),                           # approve: new
              planned(cmd("item replace entity Alex armor.head with glowstone"), say="glow"),  # recheck 1: new
              planned(cmd("data merge entity Alex {Glowing:1b}"), say="glow"))           # recheck 2: still new
    h.llm.add("Final_command", {"hand_on": "make Alex glow", "say": "handing on"})
    await h.run("make me glow")
    assert h.vfg.actions() == []
    assert [c.output for c in h.llm.calls].count("Planned_command") == 4
    final = h.llm.prompts("Final_command")[0]
    assert "IT FAILED: the draft kept changing to commands never checked: data" in final
    [[line]] = h.vfg.hands()
    assert "kept changing to commands never checked" in line["context"]


async def test_a_minecraft_error_goes_back_to_the_action_planner(h):
    h.llm.add("Selection", select(("command", "give Alex 64 steak")))
    h.llm.add("Planned_command",
              planned(cmd("give Alex minecraft:steak 64"), say="steak"),
              planned(cmd("give Alex minecraft:steak 64"), say="steak"),
              planned(cmd("give Alex minecraft:cooked_beef 64"), say="64 steak for Alex"))  # the retry
    h.vfg.on("action", "run", "command", answer=[
        bad("Unknown item 'minecraft:steak'", replies=["Unknown item 'minecraft:steak'"],
            failed="give Alex minecraft:steak 64"),
        ok("Gave 64 [Cooked Beef] to Alex")])

    tid = await h.run("give me 64 steak")

    retry = h.llm.prompts("Planned_command")[2]
    assert "YOUR LAST ATTEMPT: \n1. give Alex minecraft:steak 64" in retry
    assert "IT FAILED: Unknown item 'minecraft:steak'" in retry
    assert "## give" in retry  # the references already sent, in view for the retry
    assert len(h.refs.referenced) == 2 and h.refs.referenced[1] == ({"give"}, set())
    assert len(h.llm.calls) == 4  # no new command in the retry: no second approval
    assert h.vfg.replies() == [(tid, "done", "64 steak for Alex — Gave 64 [Cooked Beef] to Alex")]


async def test_a_command_retry_never_repeats_the_commands_that_already_took_effect(h):
    h.llm.add("Selection", select(("command", "give Alex 64 rockets and Speed II")))
    h.llm.add("Planned_command",
              planned(cmd("give Alex minecraft:firework_rocket 64", "effect give Alex speed infinite 1"), say="rockets"),
              planned(cmd("give Alex minecraft:firework_rocket 64", "effect give Alex speed infinite 1"), say="rockets"),
              # the retry repeats the give: the code drops it
              planned(cmd("give Alex minecraft:firework_rocket 64", "effect give Alex minecraft:speed infinite 1"),
                      say="rockets and Speed II for Alex"))
    h.vfg.on("action", "run", "command", answer=[
        bad("Gave 64 [Firework Rocket] to Alex; Unknown effect 'speed'",
            replies=["Gave 64 [Firework Rocket] to Alex", "Unknown effect 'speed'"],
            failed="effect give Alex speed infinite 1", ran=["give Alex minecraft:firework_rocket 64"]),
        ok("Applied effect Speed to Alex", replies=["Applied effect Speed to Alex"], failed=None,
           ran=["effect give Alex minecraft:speed infinite 1"])])

    tid = await h.run("64 rockets and speed two")

    retry = h.llm.prompts("Planned_command")[2]
    assert ("THESE ALREADY RAN AND TOOK EFFECT:\n1. give Alex minecraft:firework_rocket 64\n"
            "Do NOT repeat them; write only what is still to do.") in retry
    assert [args_of(a)["commands"] for a in h.vfg.actions()] == [
        ["give Alex minecraft:firework_rocket 64", "effect give Alex speed infinite 1"],
        ["effect give Alex minecraft:speed infinite 1"]]  # the rockets went once
    assert h.vfg.replies() == [(tid, "done", "rockets and Speed II for Alex — Gave 64 [Firework Rocket] to Alex; "
                                             "Applied effect Speed to Alex")]


async def test_a_retry_that_only_repeats_what_took_effect_runs_nothing(h):
    h.llm.add("Selection", select(("command", "give Alex rockets")))
    h.llm.add("Planned_command",
              planned(cmd("give Alex minecraft:firework_rocket 64", "say hi"), say="rockets"),
              planned(cmd("give Alex minecraft:firework_rocket 64", "say hi"), say="rockets"),
              planned(cmd("give Alex minecraft:firework_rocket 64"), say="rockets for Alex"))
    h.vfg.on("action", "run", "command", answer=bad(
        "Gave 64 [Firework Rocket] to Alex; Unknown or incomplete command",
        replies=["Gave 64 [Firework Rocket] to Alex", "Unknown or incomplete command"], failed="say hi",
        ran=["give Alex minecraft:firework_rocket 64"]))
    tid = await h.run("rockets")
    assert len(h.vfg.actions()) == 1
    assert h.vfg.replies() == [(tid, "done", "rockets for Alex — Gave 64 [Firework Rocket] to Alex")]


async def test_a_partial_run_is_never_retried_and_its_hand_on_names_what_ran(h):
    h.llm.add("Selection", select(("command", "give Alex rockets and make it day")))
    h.llm.add("Planned_command", planned(cmd("give Alex minecraft:firework_rocket 64", "time set day"), say="x"),
              planned(cmd("give Alex minecraft:firework_rocket 64", "time set day"), say="x"))
    h.vfg.on("action", "run", "command", answer=bad(
        "Gave 64 [Firework Rocket] to Alex; RCON dropped while 'time set day' was sent: it may or may not have happened",
        replies=["Gave 64 [Firework Rocket] to Alex"], failed="time set day",
        ran=["give Alex minecraft:firework_rocket 64"], partial=True))

    tid = await h.run("rockets and day")

    assert len(h.vfg.actions()) == 1
    assert [c.output for c in h.llm.calls] == ["Selection", "Planned_command", "Planned_command"]  # no retry call
    [[line]] = h.vfg.hands()
    assert line["context"].startswith("Dispatcher: give Alex rockets and make it day: it may have partly happened (")
    assert "; these ran and took effect: give Alex minecraft:firework_rocket 64 — check before redoing it" in line["context"]


async def test_a_hand_on_after_a_refusal_names_the_commands_that_took_effect(tmp_path):
    h = Harness(tmp_path, retries=0)
    h.llm.add("Selection", select(("command", "give Alex rockets and a trident of storms")))
    h.llm.add("Planned_command", planned(cmd("give Alex minecraft:firework_rocket 64", "give Alex storm_trident"), say="x"),
              planned(cmd("give Alex minecraft:firework_rocket 64", "give Alex storm_trident"), say="x"))
    h.llm.add("Final_command", {"hand_on": "make Alex a trident of storms", "say": "handing on"})
    h.vfg.on("action", "run", "command", answer=bad(
        "Gave 64 [Firework Rocket] to Alex; Unknown item 'minecraft:storm_trident'",
        replies=["Gave 64 [Firework Rocket] to Alex", "Unknown item 'minecraft:storm_trident'"],
        failed="give Alex storm_trident", ran=["give Alex minecraft:firework_rocket 64"]))
    await h.run("rockets and a storm trident")
    [[line]] = h.vfg.hands()
    assert line["context"].endswith("these already ran and took effect, do not redo them: "
                                    "give Alex minecraft:firework_rocket 64")


async def test_a_command_refused_by_its_validator_is_handed_on(h):
    h.llm.add("Selection", select(("command", "put a stone block under Alex")))
    h.llm.add("Planned_command", planned(cmd("setblock ~ ~-1 ~ stone"), say="a block"),
              planned(cmd("execute at Alex run fill ~ ~-1 ~ ~ ~-1 ~ stone"), say="a block"))
    await h.run("put a stone block under me")
    assert h.vfg.actions() == []
    repair = [c.prompt for c in h.llm.calls if c.prompt.startswith("REPAIR")]
    assert repair and "places or removes blocks" in repair[0]
    [[line]] = h.vfg.hands()
    assert line["task"] == "put a stone block under me"
    assert "the action planner failed" in line["context"]


async def test_command_hands_on_when_the_references_are_not_loaded(tmp_path):
    h = Harness(tmp_path, refs=None)
    h.llm.add("Selection", select(("command", "give Alex bread"), ("teleport", "teleport Alex to spawn")))
    await h.run("bread and home")
    assert [c.output for c in h.llm.calls] == ["Selection"]
    [[line]] = h.vfg.hands()
    assert line["task"] == "bread and home"
    assert "the command references are not loaded" in line["context"]


async def test_the_drafting_model_never_sees_block_or_owner_commands(h):
    h.llm.add("Selection", select(("command", "give Alex bread")))
    h.llm.add("Planned_command", planned(cmd("give Alex minecraft:bread"), say="bread"),
              planned(cmd("give Alex minecraft:bread"), say="bread"))
    await h.run("bread")
    draft = h.llm.prompts("Planned_command")[0]
    assert "\ngive — Gives an item to a player." in draft
    assert "setblock" not in draft and "\nop — " not in draft


def test_the_hidden_commands_come_from_actions_types():
    from dispatcher import actions, config
    hidden = actions.hidden_commands(config.REPO)
    if not hidden:
        pytest.skip("actions/_types.py has no BLOCK_COMMANDS yet")
    assert {"setblock", "fill", "clone", "place", "fillbiome", "stop", "op", "ban", "whitelist", "kick"} <= hidden
    assert "give" not in hidden


# ---------------------------------------------------------------- saved places

async def test_a_saved_place_is_in_every_prompt_and_teleport_reaches_it(h):
    h.vfg.on("place", "list", answer=ok_lines("castle 197 68 251 overworld (T23)", "home -368 70 912 overworld (T41)"))
    h.llm.add("Selection", select(("teleport", "teleport Alex to the saved place castle")))
    h.llm.add("Planned_teleport", planned(tp("Alex", "place", "castle"), say="to the castle"))
    h.vfg.on("action", "run", "teleport", answer=ok("teleported Alex to castle at 197 68 251"))

    await h.run("take me to the castle")

    line = "SAVED PLACES: castle 197 68 251 overworld (T23); home -368 70 912 overworld (T41)"
    assert all(line in p for p in h.llm.prompts())
    [run] = h.vfg.actions()
    assert args_of(run) == {"player": "Alex", "to": "place", "target": "castle"}
    assert run[-2:] == ["--timeout", "40"]  # the class's own timeout_s
    assert h.vfg.calls[0] == ["place", "list"]  # read once, when the task starts
    assert [a for a in h.vfg.calls if a[:2] == ["place", "list"]] == [["place", "list"]]


async def test_the_saved_places_line_is_in_both_prompts_and_every_command_step(h):
    h.llm.add("Selection", select(("command", "give Alex bread"), ("teleport", "teleport Alex to spawn")))
    h.llm.add("Planned_command", planned(cmd("give Alex minecraft:bread"), say="bread"),
              planned(cmd("give Alex minecraft:bread"), say="bread"))
    h.llm.add("Planned_teleport", planned(tp("Alex", "coords", "0 0"), say="spawn"))
    await h.run("bread and spawn")
    prompts = h.llm.prompts()
    assert len(prompts) == 4
    assert all("\nSAVED PLACES: none\n" in p for p in prompts)


async def test_a_failed_place_list_says_so_rather_than_none(h):
    h.vfg.on("place", "list", answer=fail())
    h.llm.add("Selection", select())
    await h.run("anything")
    assert "SAVED PLACES: unknown (the list could not be read)" in h.llm.prompts("Selection")[0]


def ok_lines(*lines: str):
    from dispatcher.vfg import VfgResult
    return VfgResult(0, "".join(x + "\n" for x in lines), "")


# ---------------------------------------------------------------- prompts

async def test_the_selector_sees_the_menu_and_the_planner_one_action(h):
    h.llm.add("Selection", select(("teleport", "teleport Alex to a village")))
    h.llm.add("Planned_teleport", planned(tp("Alex", "structure", "village"), say="village"))
    await h.run("take me to a village", "Alex asked")
    sel = h.llm.calls[0]
    assert sel.system is None  # the rules and the menu open the one message, the task closes it
    rules, task = sel.prompt.split("\n\nTASK: ")
    rules += "\n"
    # the menu: every action exactly once, in the catalog's order, as its name and its docstring's
    # first line (selector.py may add to a line), and nothing more of any docstring
    lines = ["\n- command: Run vanilla commands, in order: anything that takes effect at once — items, effects, "
             "time, weather. Never places or removes blocks.",
             "\n- teleport: Move a player, or everyone, to a biome, a structure, a saved place or coordinates, "
             "landing on safe ground.",
             "\n- run_script: Start one of the scripts in scripts/, by name, with its arguments."]
    assert [rules.count(x) for x in lines] == [1, 1, 1]
    menu = rules[rules.index(lines[0]) + 1:].splitlines()  # the menu closes the rules
    assert [row.split(":", 1)[0] for row in menu] == ["- command", "- teleport", "- run_script"]
    assert all(("\n" + row).startswith(x) for row, x in zip(menu, lines))
    assert "Up to five commands" not in rules and "To another player" not in rules
    assert "SAVED PLACES" not in rules and "TASK:" not in rules  # those are the task's, after them
    assert "with its arguments. The only scripts: tnt_rain, zeus.\n" in rules  # the scripts there are, by name only
    assert "To another player it is one tp" not in rules  # the menu is the first line only
    assert task == "take me to a village\nCONTEXT: Alex asked\nSAVED PLACES: none\n"
    plan = h.llm.calls[1]
    assert plan.prompt.startswith("ACTION: teleport\nMove a player")
    assert "To another player it is one tp, which command does." in plan.prompt
    assert "- player (required): who moves; text matching" in plan.prompt
    assert "- target: a biome or structure id, a saved place's name, or 'x z'; text; default \"\"" in plan.prompt
    assert "INSTRUCTION: teleport Alex to a village\nCONTEXT: Alex asked\n" in plan.prompt
    assert "command" not in plan.prompt.split("OPTIONS:")[1].split("INSTRUCTION:")[0]
    assert "\nTASK:" not in plan.prompt and "take me to" not in plan.prompt  # its instruction, not the task
    assert "Copy player names exactly." in plan.system


async def test_run_script_sees_each_scripts_values_and_an_old_ones_usage(h):
    h.llm.add("Selection", select(("run_script", "start zeus for Alex")))
    h.llm.add("Planned_run_script", planned({"action": "run_script", "name": "zeus", "args": ["player=Alex"]}, say="zeus"))
    h.vfg.on("script", "run", answer=ok_lines("started zeus (T1); stop: vfg script stop zeus"))
    tid = await h.run("make my tridents call lightning")
    prompt = h.llm.prompts("Planned_run_script")[0]
    assert ("\nTHE SCRIPT THE INSTRUCTION NAMES (what it does, then every value it takes as key (type, default, "
            "limits or options), set as key=value with the key exactly as listed; for one written the old way, "
            "its usage and how it ends):\n- zeus: Zeus: every trident a player throws calls lightning where it "
            "lands.\n  player (player, default @a)\n  strikes (int, default 1, 1..10)\n"
            "\nOTHER SCRIPTS: tnt_rain\n\nINSTRUCTION:") in prompt
    assert "Usage: tnt_rain" not in prompt and "Drops TNT" not in prompt
    assert h.vfg.actions() == [["script", "run", "zeus", "player=Alex"]]
    assert h.vfg.replies() == [(tid, "done", "zeus — started zeus (T1); stop: vfg script stop zeus")]


async def test_run_script_names_with_spaces_and_an_old_scripts_usage_and_no_name_at_all(h):
    from conftest import RunScript

    h.llm.add("Selection", select(("run_script", "start tnt rain on Steve")))
    h.llm.add("Planned_run_script",
              planned({"action": "run_script", "name": "tnt_rain", "args": ["every_s=5"]}, say="tnt rain"))
    h.vfg.on("script", "run", answer=ok_lines("started tnt_rain (T1)"))
    await h.run("rain tnt on Steve")
    [named] = h.llm.prompts("Planned_run_script")
    assert ("its usage and how it ends):\n- tnt_rain: Drops TNT round players.\n  Usage: tnt_rain [every_s=20]\n"
            "  Ends: when stopped\n\nOTHER SCRIPTS: zeus\n\nINSTRUCTION:") in named
    assert "player (player" not in named and "Zeus: every trident" not in named
    # no script named (the selector hands such a task on before the planner, but the planner
    # holds on its own): what each does, one line each, and no value at all
    unnamed = h.d.planner.describe(RunScript, "start the lightning thing for Alex")
    assert unnamed.endswith("\nSCRIPTS (each: what it does):\n- zeus: Zeus: every trident a player throws calls "
                            "lightning where it lands.\n- tnt_rain: Drops TNT round players.\n")
    assert "THE SCRIPT THE INSTRUCTION NAMES" not in unnamed and "(player, default" not in unnamed


async def test_stop_script_sees_the_scripts_too(tmp_path):

    from pydantic import Field

    from conftest import CATALOG, Action

    class StopScript(Action):
        """Stop a running script, by name."""
        action: Literal["stop_script"]
        name: Literal["zeus", "tnt_rain"] = Field(description="the script")

        def vfg_argv(self, task: str) -> list[str]:
            return ["script", "stop", self.name]

    h = Harness(tmp_path, catalog={**CATALOG, "stop_script": StopScript})
    h.llm.add("Selection", select(("stop_script", "stop zeus")))
    h.llm.add("Planned_stop_script", planned({"action": "stop_script", "name": "zeus"}, say="stopping zeus"))
    await h.run("stop the lightning")
    prompt = h.llm.prompts("Planned_stop_script")[0]
    assert ("\nSCRIPTS (each: what it does):\n- zeus: Zeus: every trident a player throws calls lightning where "
            "it lands.\n- tnt_rain: Drops TNT round players.\n") in prompt
    assert "player (player" not in prompt and "Usage:" not in prompt  # stopping takes no values
    assert h.vfg.actions() == [] and ["script", "stop", "zeus"] in h.vfg.calls


async def test_a_timeout_is_never_retried_and_is_handed_on_saying_it_may_have_happened(h):
    h.llm.add("Selection", select(("command", "give Alex 64 steak"), ("teleport", "teleport Alex to a village")))
    h.llm.add("Planned_command", planned(cmd("give Alex minecraft:cooked_beef 64"), say="steak"),
              planned(cmd("give Alex minecraft:cooked_beef 64"), say="steak"))
    h.vfg.on("action", "run", "command", answer=fail(124, "vfg: timed out: command did not finish within 20 s"))

    tid = await h.run("give me 64 steak and make it day")

    assert len(h.vfg.actions()) == 1  # run once: a retry might give the steak twice
    assert [c.output for c in h.llm.calls] == ["Selection", "Planned_command", "Planned_command"]  # no planner call after
    [[line]] = h.vfg.hands()
    assert line["task"] == "give me 64 steak and make it day"
    assert line["context"] == ("Dispatcher: give Alex 64 steak: timed out after 20 s; "
                               "it may or may not have happened — check before redoing it")
    assert h.vfg.replies() == [(tid, "progress", "handed on to the orchestrator: give me 64 steak and make it day")]


@pytest.mark.parametrize("code,err", [
    (2, "vfg: --args: player must be a name"),
    (3, "vfg: mc.py is not deployed on EC2 — vfg up"),
    (4, "vfg: cannot reach EC2 (ec2-user@1.2.3.4): ssh exited 255"),
    (5, "vfg: Minecraft is not answering RCON"),
    (1, "vfg: no actions/teleport/ in ~/vfg"),  # from vfg action run: cannot be run either
])
async def test_an_action_that_cannot_be_run_at_all_is_handed_on_at_once_with_the_reason(h, code, err):
    h.llm.add("Selection", select(("command", "give Alex bread"), ("teleport", "teleport Alex to a village")))
    h.llm.add("Planned_command", planned(cmd("give Alex minecraft:bread"), say="bread"),
              planned(cmd("give Alex minecraft:bread"), say="bread"))
    h.llm.add("Planned_teleport", planned(tp("Alex", "structure", "village"), say="village"))
    h.vfg.on("action", "run", "command", answer=ok("Gave 1 [Bread] to Alex"))
    h.vfg.on("action", "run", "teleport", answer=fail(code, err))

    tid = await h.run("bread, then a village")

    assert [a[2] for a in h.vfg.actions()] == ["command", "teleport"]
    assert [c.output for c in h.llm.calls].count("Planned_teleport") == 1  # no planner call after the failure
    [[line]] = h.vfg.hands()
    assert line["task"] == "teleport Alex to a village"
    assert line["context"] == (f"Dispatcher: done bread — Gave 1 [Bread] to Alex; "
                               f"not done teleport Alex to a village: could not run it: {err}")
    assert h.vfg.replies()[-1] == (tid, "progress", "handed on to the orchestrator: teleport Alex to a village")


async def test_a_refusal_from_a_command_with_no_json_goes_back_to_the_planner(h):
    h.llm.add("Selection", select(("run_script", "start tnt rain")))
    h.llm.add("Planned_run_script",
              planned({"action": "run_script", "name": "tnt_rain", "args": ["every_s=0"]}, say="tnt rain"),
              planned({"action": "run_script", "name": "tnt_rain", "args": ["every_s=5"]}, say="tnt rain every 5 s"))
    h.vfg.on("script", "run", answer=[fail(1, "vfg: tnt_rain: every_s must be at least 1"),
                                      ok_lines("started tnt_rain (T1)")])
    tid = await h.run("rain tnt on us")
    assert "IT FAILED: vfg: tnt_rain: every_s must be at least 1" in h.llm.prompts("Planned_run_script")[1]
    assert h.vfg.actions() == [["script", "run", "tnt_rain", "every_s=0"], ["script", "run", "tnt_rain", "every_s=5"]]
    assert h.vfg.replies() == [(tid, "done", "tnt rain every 5 s — started tnt_rain (T1)")]
    assert h.vfg.hands() == []


async def test_a_command_task_with_the_real_refs_from_the_saved_commands_json(tmp_path):
    """The planner against refs.Refs (not the fake), built from the refs agent's fixture."""
    import gzip
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "refs"
    if not (fixture / "commands.json.gz").exists():
        pytest.skip("no commands.json fixture yet")
    from dispatcher.refs import Refs, wiki_text

    with gzip.open(fixture / "commands.json.gz") as f:
        tree = json.load(f)
    wiki, first = {}, {}
    for page in fixture.glob("wiki/*.wiki"):
        wiki[page.stem], first[page.stem] = wiki_text(page.read_text().split("\n", 1)[1], page.stem)
    h = Harness(tmp_path, refs=Refs("26.3", tree, wiki=wiki, first=first))
    draft = ["execute as @a at @s run give @s minecraft:bread 3"]
    h.llm.add("Selection", select(("command", "give everyone 3 bread")))
    h.llm.add("Planned_command", planned(cmd(*draft), say="bread for everyone"),
              planned(cmd(*draft), say="bread for everyone"))
    tid = await h.run("give everyone 3 bread")

    draft_prompt, approve_prompt = h.llm.prompts("Planned_command")
    menu = draft_prompt.split("MINECRAFT COMMANDS (name — what it does):\n", 1)[1].split("\n\n", 1)[0]
    shown = {row.split(" — ", 1)[0] for row in menu.splitlines()}
    assert {"give", "effect", "execute", "summon", "tp"} <= shown
    assert not shown & {"setblock", "fill", "clone", "place", "fillbiome", "op", "deop", "ban", "stop",
                        "whitelist", "kick"}
    assert "## give\nSyntax on this server:\ngive <targets> <item> [<count>]" in approve_prompt
    assert "## execute\nSyntax on this server:\nexecute (as|at) <targets> ...\nexecute run <command>" in approve_prompt
    assert "(if|unless) block" not in approve_prompt.split("## execute", 1)[1].split("Wiki", 1)[0]
    assert h.vfg.replies()[0][:2] == (tid, "done")


def test_the_selector_prompt_asks_for_player_names_in_the_orchestrator_part():
    from dispatcher.selector import PROMPT
    assert 'Write an orchestrator part with the players\' names, never "his", "her" or "their"' in PROMPT.read_text()


def test_the_selector_prompt_never_adds_a_level_amount_or_duration():
    from dispatcher.selector import PROMPT
    text = PROMPT.read_text()
    assert "Copy levels, amounts and durations only when the task says them; never add one." in text
    assert "Speed II" not in text  # its own examples once taught it to turn "speed" into Speed II


def test_only_an_action_instruction_is_capped_at_160_characters():
    from dispatcher.selector import selection_type
    Selection = selection_type(["command", "teleport"])
    long_task = "build a castle with four towers, a moat, a drawbridge and banners on every tower " * 7  # ~560
    long_part = "the castle with four towers and a moat and a drawbridge and banners on each tower, " * 3  # ~250
    ok = Selection.model_validate({"parts": [
        {"part": long_part, "to": "orchestrator", "instruction": long_task},
        {"part": "speed", "to": "command", "instruction": "give Steve speed"}]})
    assert len(ok.parts[0].instruction) > 500 and len(ok.parts[0].part) > 200
    with pytest.raises(ValidationError, match="at most 160"):
        Selection.model_validate({"parts": [{"part": "speed", "to": "command", "instruction": "give Steve speed " * 12}]})


async def test_a_duration_no_script_value_covers_is_handed_on_not_bent_into_another_value(h):
    h.llm.add("Selection", select(("run_script", "start tnt_rain on Steve for 30 seconds")))
    h.llm.add("Planned_run_script", planned(hand_on="run tnt_rain on Steve and stop it after 30 seconds",
                                            say="tnt_rain has no duration: the orchestrator times it"))
    tid = await h.run("start tnt_rain on Steve for 30 seconds")
    call = next(c for c in h.llm.calls if c.output == "Planned_run_script")
    assert ("Use a script's value only for what its name and type mean. When the instruction asks for something "
            "no value covers, such as a duration the script has no value for, hand it on rather than bend another "
            "value to fit.") in call.system
    assert "INSTRUCTION: start tnt_rain on Steve for 30 seconds\n" in call.prompt  # the unit kept
    assert h.vfg.actions() == []  # never `every_s=30`, a 30 s interval that never ends
    [[line]] = h.vfg.hands()
    assert line["task"] == "start tnt_rain on Steve for 30 seconds"
    assert "run tnt_rain on Steve and stop it after 30 seconds" in line["context"]
    assert h.vfg.replies(tid)[-1][2].startswith("handed on to the orchestrator")


def test_the_selector_keeps_units_with_numbers():
    from dispatcher.selector import PROMPT
    assert 'Keep each number\'s unit with it: "for 30 seconds", never a bare "30".' in PROMPT.read_text()
