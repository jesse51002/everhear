"""One task end to end, with a fake structured(), a fake vfg and fake refs: every case in the
spec's Test section that is about a task."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError
from vfg_actions import planner_model
from vfg_llm import LlmError

from conftest import Harness, args_of, bad, catalog, cmd, fail, made, ok, planned, select, tp
from dispatcher.action_planner import Final, final_type, planned_type
from dispatcher.executor import GRACE_S


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


@pytest.mark.parametrize("task", ["Fix the weather", "fix: the tower fell over", "  FIX T3's castle"])
async def test_a_fix_is_handed_whole_with_no_model_call_and_no_places_fetch(h, task):
    tid = await h.run(task, "ctx")
    assert h.llm.calls == []
    assert not [a for a in h.vfg.calls if a[:2] == ["place", "list"]]
    [[line]] = h.vfg.hands()
    assert line == {"id": tid, "at": "2026-10-09T12:00:00.000Z", "task": task, "context": "ctx"}


@pytest.mark.parametrize("task", ["Change to T3: make it taller", "Cancel T3: no tower", "Undo the rain",
                                  "Build Steve a tower", "Give Alex a building kit"])
async def test_a_change_a_cancel_an_undo_or_a_build_reaches_the_selector(h, task):
    h.llm.add("Selection", select(orchestrator=task))
    await h.run(task, "ctx")
    assert [c.output for c in h.llm.calls] == ["Selection"]  # the parts and the prompt decide, not a regex


async def test_a_build_an_action_does_goes_straight_to_the_game_at_a_saved_place(tmp_path):
    h = Harness(tmp_path, cat=catalog())  # the library's castle action
    h.vfg.on("place", "list", answer=ok_lines("home 10 64 10 overworld (T2)"))
    h.llm.add("Selection", select(("castle", "a castle at the saved place home")))
    h.llm.add("Planned_castle", planned({"castle_at": "Home", "castle_size": 31}, say="a castle at home"))
    h.vfg.on("action", "run", "castle", answer=ok("built a castle at home, 10 64 10", copy="castle", action="castle"))

    tid = await h.run("build a castle at home")

    assert h.vfg.hands() == []  # no regex hands a build on any more
    [run] = h.vfg.actions()
    assert args_of(run) == {"castle_at": "home", "castle_size": "31"}  # the saved place's name, checked
    assert h.vfg.timeouts[h.vfg.calls.index(run)] == 120 + GRACE_S  # its timeout_s
    assert h.vfg.replies(tid) == [(tid, "done", "a castle at home — built a castle at home, 10 64 10")]


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


async def test_offline_players_alone_end_a_task_failed_naming_each_and_a_plain_refusal_still_goes_back(h):
    h.llm.add("Selection", select(("summon_around", "summon zombies round Bob and Eve")),
              select(("teleport", "teleport Alex to a village")))
    h.llm.add("Planned_summon_around", planned({"players": "Bob,Eve", "mob": "zombie"}, say="zombies"))
    h.vfg.on("action", "run", "summon_around", answer=bad("Bob and Eve are not online", offline=["Bob", "Eve"]))
    tid = await h.run("zombies round Bob and Eve")
    assert h.vfg.replies(tid) == [(tid, "failed", "dropped: summon zombies round Bob and Eve — Bob and Eve not online")]
    assert [c.output for c in h.llm.calls] == ["Selection", "Planned_summon_around"]
    assert args_of(h.vfg.actions()[0]) == {"players": "Bob,Eve", "mob": "zombie"}

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
    Out = planned_type("teleport", planner_model(h.catalog.get("teleport")))
    with pytest.raises(ValidationError, match="exactly one of action, hand_on and drop"):
        Out.model_validate(planned(tp("Alex", "coords", "1 2"), drop="no", say="x"))
    with pytest.raises(ValidationError, match="exactly one"):
        Out.model_validate(planned(hand_on="a", drop="b", say="x"))
    with pytest.raises(ValidationError, match="exactly one"):
        Out.model_validate(planned(say="nothing set"))
    with pytest.raises(ValidationError, match="exactly one of hand_on and drop"):
        final_type("teleport").model_validate({"hand_on": "a", "drop": "b", "say": "x"})
    assert issubclass(final_type("teleport"), Final)

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
    assert args_of(run) == {"commands": draft} and run[-2:] == ["--task", tid]
    assert h.vfg.timeouts[h.vfg.calls.index(run)] == 20 + GRACE_S  # VFG_DISPATCH_ACTION_TIMEOUT_S, and vfg's grace
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
    listed = draft.split("MINECRAFT COMMANDS (name — what it does):\n", 1)[1]
    assert "\ngive — Gives an item to a player." in "\n" + listed
    assert "setblock" not in listed and "\nop — " not in "\n" + listed


def test_the_hidden_commands_come_from_actions_types():
    from dispatcher import actions
    catalog()  # actions/_types.py is loaded with the actions
    hidden = actions.hidden_commands()
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
    assert run[:3] == ["action", "run", "teleport"] and run[-2:] == ["--task", "T1"]
    assert args_of(run) == {"player": "Alex", "to": "place", "target": "castle"}
    assert h.vfg.timeouts[h.vfg.calls.index(run)] == 60 + GRACE_S  # the class's own timeout_s
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

async def test_the_selector_sees_every_action_every_running_copy_and_the_saved_places(h):
    h.running(galick_gun_2={"action": "railgun", "task": "T12",
                            "values": {"player": "Alex", "cooldown": 0, "range": 100, "item": "Railgun"}})
    h.vfg.on("place", "list", answer=ok_lines("castle 197 68 251 overworld (T23)"))
    h.llm.add("Selection", select(("teleport", "teleport Alex to a village")))
    h.llm.add("Planned_teleport", planned(tp("Alex", "structure", "village"), say="village"))
    await h.run("take me to a village", "Alex asked")
    sel = h.llm.calls[0]
    assert sel.system is None  # the rules and the menu open the one message, the task closes it
    rules, task = sel.prompt.split("\n\nTASK: ")
    # the menu: every action exactly once, in the catalog's order, as its name and its docstring's
    # first line, and nothing more of any docstring; then every running copy
    menu = rules[rules.index("Your actions:\n") + len("Your actions:\n"):].split("\nRUNNING NOW", 1)[0].splitlines()
    assert [row.split(" — ", 1)[0] for row in menu] == ["- " + n for n in h.catalog.names()]
    assert set(h.catalog.names()) >= {"command", "teleport", "summon_around", "save_place", "stop", "edit",
                                      "railgun", "gatling_gun"}
    assert "- teleport — Move a player, or everyone, to a biome, a structure, a saved place, coordinates, far " \
           "away, their spawn point or where they last died, landing on safe ground." in menu
    assert "To another player it is one `tp`" not in rules and "Up to five commands" not in rules
    assert "run_script" not in rules and "stop_script" not in rules
    assert rules.endswith("\nRUNNING NOW (each: the copy's name: its action, the values that differ from the "
                          "action's defaults, the task that started it):\n"
                          "- galick_gun_2: railgun, player=Alex cooldown=0, started by T12")
    assert task == "take me to a village\nCONTEXT: Alex asked\nSAVED PLACES: castle 197 68 251 overworld (T23)\n"

    plan = h.llm.calls[1]
    assert plan.prompt.startswith("ACTION: teleport\nMove a player")
    assert "To another player it is one `tp`, which the command action does" in plan.prompt
    assert "\nIt runs once and reports what it did.\n" in plan.prompt
    assert ("\nVALUES (fill each one; one the instruction does not give keeps its default):\n- player (player names "
            "separated by commas, or @a, @r, @p; default \"@a\"): who moves") in plan.prompt
    assert "- dimension (one of overworld, the_nether, the_end; default \"overworld\")" in plan.prompt
    assert "INSTRUCTION: teleport Alex to a village\nCONTEXT: Alex asked\n" in plan.prompt
    assert "RUNNING NOW" not in plan.prompt and "galick_gun" not in plan.prompt  # only stop and edit see the copies
    assert "\nTASK:" not in plan.prompt and "take me to" not in plan.prompt  # its instruction, not the task
    assert "Copy player names exactly." in plan.system


async def test_with_nothing_running_the_menu_says_so(h):
    h.llm.add("Selection", select())
    await h.run("anything")
    assert "\nRUNNING NOW: none\n\nTASK: anything\n" in h.llm.prompts("Selection")[0]


async def test_the_planner_sees_and_fills_only_the_values_the_action_takes(h):
    h.llm.add("Selection", select(("gatling_gun", "give Steve a gatling gun that only hurts mobs")))
    h.llm.add("Planned_gatling_gun", planned({"player": "Steve", "damage_amount": 30, "damage_what": "mobs"},
                                             say="a gatling gun for Steve"))
    h.vfg.on("action", "run", "gatling_gun", answer=ok("running as gatling_gun", copy="gatling_gun",
                                                       action="gatling_gun", running=True))
    tid = await h.run("give Steve a gatling gun for mobs")
    prompt = h.llm.prompts("Planned_gatling_gun")[0]
    values = prompt.split("VALUES (fill each one", 1)[1].split("\n\nINSTRUCTION:", 1)[0]
    assert [x.split(" (", 1)[0] for x in values.splitlines()[1:]] == ["- player", "- damage_amount", "- damage_what"]
    assert "cooldown" not in values and "particle" not in values  # fixed, or not taken: an edit reaches them
    assert "It runs until stopped: it keeps going after it reports" in prompt
    Out = next(o for o in h.llm.outputs if o.__name__ == "Planned_gatling_gun")
    inner = Out.model_json_schema()["$defs"]["GatlingGun"]
    assert list(inner["properties"]) == ["player", "damage_amount", "damage_what"]
    assert inner["required"] == ["player", "damage_amount", "damage_what"]  # every value written
    [run] = h.vfg.actions()
    assert run == ["action", "run", "gatling_gun", "player=Steve", "damage_amount=30", "damage_what=mobs",
                   "--task", tid]
    assert h.vfg.replies(tid) == [(tid, "done", "a gatling gun for Steve — running as gatling_gun")]


async def test_a_number_out_of_range_is_clamped_and_the_report_says_so(h):
    h.llm.add("Selection", select(("summon_around", "summon 500 zombies round Steve")))
    h.llm.add("Planned_summon_around", planned({"players": "Steve", "mob": "zombie", "count": 500}, say="zombies"))
    h.vfg.on("action", "run", "summon_around", answer=ok("summoned 100 zombies round Steve at 197 68 251"))
    tid = await h.run("500 zombies round Steve")
    assert args_of(h.vfg.actions()[0])["count"] == "100"  # clamped before it is sent: vfg has nothing to clamp
    assert h.vfg.replies(tid) == [(tid, "done", "zombies — summoned 100 zombies round Steve at 197 68 251 "
                                                "(count=500 is over its max, clamped to 100)")]


async def test_an_until_stopped_copy_is_named_in_its_report(h):
    h.llm.add("Selection", select(("railgun", "give Alex a railgun")))
    h.llm.add("Planned_railgun", planned({"player": "Alex"}, say="a railgun for Alex"))
    h.vfg.on("action", "run", "railgun", answer=ok("Alex has a railgun", copy="railgun_2", action="railgun",
                                                   running=True))
    tid = await h.run("a railgun for me too")
    assert h.vfg.replies(tid) == [(tid, "done", "a railgun for Alex — Alex has a railgun (running as railgun_2)")]


async def test_a_once_action_past_its_timeout_is_done_still_running_and_never_run_again(h):
    h.llm.add("Selection", select(("summon_around", "summon 100 zombies round Steve")))
    h.llm.add("Planned_summon_around", planned({"players": "Steve", "mob": "zombie", "count": 100}, say="zombies"))
    h.vfg.on("action", "run", "summon_around", answer=ok("still running as summon_around", copy="summon_around",
                                                         action="summon_around", running=True))
    tid = await h.run("100 zombies round Steve")
    assert len(h.vfg.actions()) == 1 and h.vfg.hands() == []
    assert h.vfg.replies(tid) == [(tid, "done", "zombies — still running as summon_around")]


async def test_a_value_the_check_refuses_goes_back_to_the_planner_before_anything_runs(tmp_path):
    cat = catalog()
    made(cat, "beam_up", BEAM_UP)
    h = Harness(tmp_path, cat=cat)
    h.vfg.on("place", "list", answer=ok_lines("home 1 64 2 overworld (T3)"))
    h.llm.add("Selection", select(("beam_up", "beam Alex up to the castle")))
    h.llm.add("Planned_beam_up", planned({"player": "Alex", "to": "the big stone castle"}, say="beaming Alex up"),
              planned({"player": "Alex", "to": "home"}, say="beaming Alex home"))
    tid = await h.run("beam me up to the castle")
    retry = h.llm.prompts("Planned_beam_up")[1]
    # a name that is no player's must be a saved place (one that could be a player's is EC2's to find)
    assert ("IT FAILED: to=the big stone castle: 'the big stone castle' is not a saved place (saved: home), x y z, "
            "or a player's name") in retry  # in the words the model wrote
    assert [args_of(a) for a in h.vfg.actions()] == [{"player": "Alex", "to": "home"}]  # the refused one never ran
    assert h.vfg.replies(tid)[-1] == (tid, "done", "beaming Alex home — done")


BEAM_UP = '''
    from actions._types import Action, Place, Player


    class BeamUp(Action):
        """Beam a player up to a place in a column of light."""

        script = "beam_up"
        runs = "once"
        takes = ["player", "to"]

        player: Player = "Steve"
        """who is beamed up"""
        to: Place = ""
        """where to: a saved place's name, x y z, or a player's name"""
'''


# ---------------------------------------------------------------- a new action, with no restart

ZAP = '''
    from actions._types import Action, Command, Field, Players


    class Zap(Action):
        """Zap a player with lightning, then run a command or two after it."""

        script = "zap"
        runs = "once"
        takes = ["player", "then"]

        player: Players = "@a"
        """who is zapped"""
        then: list[Command] = Field([], max_length=2)
        """commands to run after the zap, no slash"""
'''


async def test_an_action_made_between_two_asks_is_on_the_menu_at_the_next_with_no_restart(tmp_path):
    cat = catalog()
    h = Harness(tmp_path, cat=cat)
    h.llm.add("Selection", select(), select(("zap", "zap Steve")))
    await h.run("zap Steve")  # nothing does it yet: the orchestrator's
    first = h.llm.prompts("Selection")[0]
    assert "\n- zap — " not in first

    made(cat, "zap", ZAP)  # an implementer saves its action
    h.llm.add("Planned_zap", planned({"player": "Steve", "then": ["say zapped"]}, say="zapping Steve"),
              planned({"player": "Steve", "then": ["say zapped"]}, say="zapping Steve"))
    h.vfg.on("action", "run", "zap", answer=ok("zapped Steve at 1 64 2", copy="zap", action="zap"))
    tid = await h.run("zap Steve again")

    second = h.llm.prompts("Selection")[1]
    assert "\n- zap — Zap a player with lightning, then run a command or two after it.\n" in second
    [run] = h.vfg.actions()
    assert args_of(run) == {"player": "Steve", "then": ["say zapped"]}
    assert h.vfg.replies(tid) == [(tid, "done", "zapping Steve — zapped Steve at 1 64 2")]

    # changed: its new line is on the menu at the next ask; gone: it is off it
    path = made(cat, "zap", ZAP.replace("Zap a player with lightning", "Strike a player with lightning"))
    import os
    os.utime(path, ns=(path.stat().st_mtime_ns + 10**9, path.stat().st_mtime_ns + 10**9))
    h.llm.add("Selection", select())
    await h.run("strike Steve")
    assert "\n- zap — Strike a player with lightning" in h.llm.prompts("Selection")[2]
    path.unlink()
    h.llm.add("Selection", select())
    await h.run("strike Steve")
    assert "\n- zap — " not in h.llm.prompts("Selection")[3]


async def test_the_command_steps_run_on_any_actions_command_field(tmp_path):
    cat = catalog()
    made(cat, "zap", ZAP)
    h = Harness(tmp_path, cat=cat)
    h.llm.add("Selection", select(("zap", "zap Steve and give him a cake")))
    h.llm.add("Planned_zap", planned({"player": "Steve", "then": ["give Steve cake"]}, say="zap"),          # draft
              planned({"player": "Steve", "then": ["give Steve minecraft:cake"]}, say="zap and a cake"))  # approve
    tid = await h.run("zap Steve, then a cake")
    draft, approve = h.llm.prompts("Planned_zap")
    assert "MINECRAFT COMMANDS (name — what it does):\ngive — Gives an item to a player." in draft
    assert "YOUR DRAFT:\n1. give Steve cake" in approve and "## give\ngive <targets>" in approve
    assert h.refs.referenced == [({"give"}, set())]
    assert args_of(h.vfg.actions()[0]) == {"player": "Steve", "then": ["give Steve minecraft:cake"]}
    assert h.vfg.replies(tid)[0][1] == "done"


async def test_an_empty_command_list_is_refused_and_asked_for_again(h):
    h.llm.add("Selection", select(("command", "give Alex bread")))
    h.llm.add("Planned_command", planned(cmd(), say="nothing"), planned(cmd("give Alex minecraft:bread"), say="bread"),
              planned(cmd("give Alex minecraft:bread"), say="bread"))
    await h.run("bread")
    assert any("commands is empty" in c.prompt for c in h.llm.calls if c.prompt.startswith("REPAIR"))
    assert args_of(h.vfg.actions()[0]) == {"commands": ["give Alex minecraft:bread"]}


# ---------------------------------------------------------------- stop and edit

async def test_stop_by_a_copys_name_needs_no_model_call(h):
    h.running(galick_gun={"action": "railgun", "task": "T3", "values": {"player": "Steve"}},
              galick_gun_2={"action": "railgun", "task": "T5", "values": {"player": "Alex"}})
    h.llm.add("Selection", select(("stop", "stop galick_gun_2")))
    h.vfg.on("action", "run", "stop", answer=ok("stopped galick_gun_2; removed 3 things tagged vfg_galick_gun_2",
                                                copy="stop", action="stop"))
    tid = await h.run("stop Alex's gun")
    assert [c.output for c in h.llm.calls] == ["Selection"]
    assert h.vfg.actions() == [["action", "run", "stop", "copy=galick_gun_2", "--task", tid]]
    assert h.vfg.replies(tid) == [(tid, "done", "stopping galick_gun_2 — stopped galick_gun_2; removed 3 things "
                                                "tagged vfg_galick_gun_2")]


async def test_stop_with_no_copy_named_is_picked_from_every_copys_line(h):
    h.running(galick_gun={"action": "railgun", "task": "T3", "values": {"player": "Steve"}},
              galick_gun_2={"action": "railgun", "task": "T5", "values": {"player": "Alex"}})
    h.llm.add("Selection", select(("stop", "stop Steve's railgun")))
    h.llm.add("Planned_stop", planned({"copy": "galick_gun"}, say="stopping Steve's railgun"))
    tid = await h.run("stop Steve's gun")
    prompt = h.llm.prompts("Planned_stop")[0]
    assert prompt.startswith("ACTION: stop\nStop a running copy")
    assert "- galick_gun: railgun, player=Steve, started by T3\n- galick_gun_2: railgun, player=Alex, started by T5" \
        in prompt
    Out = next(o for o in h.llm.outputs if o.__name__ == "Planned_stop")
    assert Out.model_json_schema()["$defs"]["Copy"]["properties"]["copy"]["enum"] == ["galick_gun", "galick_gun_2"]
    assert h.vfg.actions() == [["action", "run", "stop", "copy=galick_gun", "--task", tid]]


async def test_stop_or_edit_with_nothing_running_is_dropped(h):
    h.llm.add("Selection", select(("stop", "stop the rain")), select(("edit", "make the rain heavier")))
    tid = await h.run("stop the rain")
    tid2 = await h.run("more rain")
    assert [c.output for c in h.llm.calls] == ["Selection", "Selection"] and h.vfg.actions() == []
    assert h.vfg.replies(tid) == [(tid, "failed", "dropped: stop the rain — nothing is running")]
    assert h.vfg.replies(tid2) == [(tid2, "failed", "dropped: make the rain heavier — nothing is running")]


async def test_an_edit_sees_the_named_copy_in_full_and_changes_only_what_it_sets(h):
    h.running(galick_gun={"action": "railgun", "task": "T3", "values": {"player": "Steve", "cooldown": 2}},
              galick_gun_2={"action": "railgun", "task": "T5",
                            "values": {"player": "Alex", "cooldown": 2, "range": 100}})
    h.llm.add("Selection", select(("edit", "set galick_gun_2's cooldown to 0")))
    h.llm.add("Planned_edit", planned({"cooldown": 0}, say="no cooldown on Alex's railgun"))
    h.vfg.on("action", "run", "edit", answer=ok("galick_gun_2: cooldown 0, live", copy="edit", action="edit"))
    tid = await h.run("no cooldown on Alex's gun")

    assert [c.output for c in h.llm.calls] == ["Selection", "Planned_edit"]  # the copy was named: one call
    prompt = h.llm.prompts("Planned_edit")[0]
    assert prompt.startswith("ACTION: edit\nChange values on a running copy")
    assert "- galick_gun: railgun, player=Steve, started by T3" in prompt  # every copy's line
    assert "THE COPY TO CHANGE: galick_gun_2, a copy of railgun, started by T5." in prompt
    assert "railgun — Give a player a railgun" in prompt
    assert "* cooldown (a number 0..60; live; now 0): seconds between shots" not in prompt
    assert "* cooldown (a number 0..60; live; now 2): seconds between shots" in prompt  # its value now
    assert "  particle (a particle id, such as flame; live; now \"end_rod\")" in prompt  # every value, not just takes
    Out = next(o for o in h.llm.outputs if o.__name__ == "Planned_edit")
    schema = Out.model_json_schema()
    assert schema["required"] == ["action", "hand_on", "drop", "say"]
    assert not schema["$defs"]["RailgunEdit"].get("required")  # a value left out is not changed
    assert set(schema["$defs"]["RailgunEdit"]["properties"]) >= {"cooldown", "particle", "fire", "player"}
    assert h.vfg.actions() == [["action", "run", "edit", "copy=galick_gun_2", "cooldown=0", "--task", tid]]
    assert h.vfg.replies(tid) == [(tid, "done", "no cooldown on Alex's railgun — galick_gun_2: cooldown 0, live")]


async def test_an_edit_with_several_copies_and_none_named_picks_the_copy_then_the_values(h):
    h.running(galick_gun={"action": "railgun", "task": "T3", "values": {"player": "Steve"}},
              rain={"action": "gatling_gun", "task": "T4", "values": {"player": "Alex"}})
    h.llm.add("Selection", select(("edit", "make Alex's gun hurt only mobs")))
    h.llm.add("Planned_edit_copy", planned({"copy": "rain"}, say="Alex's gun"))
    h.llm.add("Planned_edit", planned({"damage_what": "mobs", "player": None}, say="Alex's gun hurts only mobs"))
    tid = await h.run("Alex's gun: only mobs")
    first, second = h.llm.prompts("Planned_edit_copy")[0], h.llm.prompts("Planned_edit")[0]
    assert "- galick_gun: railgun, player=Steve" in first and "- rain: gatling_gun, player=Alex" in first
    assert "THE COPY TO CHANGE" not in first
    assert "THE COPY TO CHANGE: rain, a copy of gatling_gun, started by T4." in second
    assert "  fire (one of click, hold; now \"hold\")" in second  # a fixed value an edit may still change
    assert h.vfg.actions() == [["action", "run", "edit", "copy=rain", "damage_what=mobs", "--task", tid]]


async def test_an_edit_with_one_copy_running_goes_straight_to_its_values(h):
    h.running(railgun={"action": "railgun", "task": "T3", "values": {"player": "Steve"}})
    h.llm.add("Selection", select(("edit", "make the railgun reach further")))
    h.llm.add("Planned_edit", planned({"range": 1000}, say="a longer reach"))
    h.vfg.on("action", "run", "edit", answer=ok("railgun: range 300, live"))
    tid = await h.run("further!")
    assert [c.output for c in h.llm.calls] == ["Selection", "Planned_edit"]
    assert args_of(h.vfg.actions()[0]) == {"copy": "railgun", "range": "300"}
    assert h.vfg.replies(tid) == [(tid, "done", "a longer reach — railgun: range 300, live "
                                                "(range=1000 is over its max, clamped to 300)")]


async def test_an_edit_that_sets_nothing_is_refused_and_a_change_no_value_covers_is_handed_on(h):
    h.running(railgun={"action": "railgun", "task": "T3", "values": {"player": "Steve"}})
    h.llm.add("Selection", select(("edit", "make the railgun shoot fireballs")))
    h.llm.add("Planned_edit", planned({"range": None}, say="x"),  # nothing set: refused, asked again
              planned(hand_on="make the railgun shoot fireballs", say="that is new code"))
    await h.run("fireballs from the railgun")
    assert any("no value is set" in c.prompt for c in h.llm.calls if c.prompt.startswith("REPAIR"))
    assert h.vfg.actions() == []
    [[line]] = h.vfg.hands()
    assert "the action planner handed it on: make the railgun shoot fireballs" in line["context"]


async def test_an_edit_vfg_refuses_goes_back_to_the_planner_with_the_same_copy(h):
    h.running(railgun={"action": "railgun", "task": "T3", "values": {"player": "Steve"}})
    h.llm.add("Selection", select(("edit", "give the railgun to Alex too")))
    h.llm.add("Planned_edit", planned({"player": "Steve,Alex"}, say="Alex too"),
              planned(drop="the railgun has stopped", say="gone"))
    h.vfg.on("action", "run", "edit", answer=bad("railgun is not running", error="railgun is not running"))
    tid = await h.run("Alex too")
    retry = h.llm.prompts("Planned_edit")[1]
    assert 'YOUR LAST ATTEMPT: {"copy": "railgun", "player": "Steve,Alex"}' in retry
    assert "IT FAILED: railgun is not running" in retry and "THE COPY TO CHANGE: railgun" in retry
    assert h.vfg.replies(tid) == [(tid, "failed", "dropped: give the railgun to Alex too — the railgun has stopped")]


async def test_an_edit_of_a_copy_whose_action_is_gone_is_handed_on(h):
    h.running(zap={"action": "zap", "task": "T3", "values": {"player": "Steve"}})
    h.llm.add("Selection", select(("edit", "zap Alex too")))
    await h.run("zap Alex too")
    assert [c.output for c in h.llm.calls] == ["Selection"] and h.vfg.actions() == []
    [[line]] = h.vfg.hands()
    assert "the copy zap's action zap is not loaded" in line["context"]


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
    (2, "vfg: teleport has no value speed"),
    (3, "vfg: mc.py is not deployed on EC2 — vfg up"),
    (4, "vfg: cannot reach EC2 (ec2-user@1.2.3.4): ssh exited 255"),
    (5, "vfg: Minecraft is not answering RCON"),
    (1, "vfg: no actions/teleport.py in ~/vfg"),  # no JSON line: it cannot be run either
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


async def test_a_duration_no_value_covers_is_handed_on_not_bent_into_another_value(h):
    h.llm.add("Selection", select(("railgun", "give Steve a railgun for 30 seconds")))
    h.llm.add("Planned_railgun", planned(hand_on="give Steve a railgun and take it away after 30 seconds",
                                         say="the railgun has no duration: the orchestrator times it"))
    tid = await h.run("give Steve a railgun for 30 seconds")
    call = next(c for c in h.llm.calls if c.output == "Planned_railgun")
    assert ("Use a value only for what its name, type and description mean. When the instruction asks for "
            "something no value covers, such as a duration the action has no value for, hand it on rather than "
            "bend another value to fit.") in call.system
    assert "INSTRUCTION: give Steve a railgun for 30 seconds\n" in call.prompt  # the unit kept
    assert h.vfg.actions() == []  # never cooldown=30
    [[line]] = h.vfg.hands()
    assert line["task"] == "give Steve a railgun for 30 seconds"
    assert "take it away after 30 seconds" in line["context"]
    assert h.vfg.replies(tid)[-1][2].startswith("handed on to the orchestrator")


def test_the_selector_keeps_units_with_numbers():
    from dispatcher.selector import PROMPT
    assert 'Keep each number\'s unit with it: "for 30 seconds", never a bare "30".' in PROMPT.read_text()
