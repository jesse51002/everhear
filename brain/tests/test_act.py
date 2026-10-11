"""Running a decision: actions in order with no task, each copy named for its player, the one
retry of a refused command, the orchestrator's five answers carried out, and a reply that
never claims what failed."""

import asyncio
import json

import pytest
from vfg_llm import LlmError

from brain.act import BROKE, TOLD
from brain.vfg import VfgResult
from brain.world import Seen

from conftest import REPO_COPY, FakeOrchestrator, answer, cmd, decision, part, ran, rows

SEEN = Seen(places="SAVED PLACES: home 1 64 2 overworld (home_steve)", known={"home"})
LINE = "LINE#812 18:22:31 Styrkaeopteryx voice action_oriented 0.93 -8dB | make me a galick gun"
HEAD = "THE LINE #812, 18:22:31: Styrkaeopteryx said: make me a galick gun"
GUN = part("orchestrator", "a Galick Gun for Styrkaeopteryx: charge with right-click and fire a beam",
           said="make me a galick gun")
NEW_GUN = answer("new", name="galick_gun_styrk", description="A Galick Gun for Styrkaeopteryx: charge and fire.",
                 brief="a Galick Gun for Styrkaeopteryx", reply="on it, your galick gun's coming")
RAILGUN = {"action": "railgun", "values": {"player": "Steve", "cooldown": 5}, "live": ["cooldown"], "task": None}


def decided(k, answer: dict):
    return k.call.Decision.model_validate_json(json.dumps(answer))


async def run(k, d: dict, player: str = "Styrkaeopteryx", line: str = "make me a galick gun", **kw):
    await k.acts.run(decided(k, d), player=player, line=line, seen=SEEN, ctx=k.ctx, head=HEAD, ref="#812", **kw)
    await k.settle()


def opened(k, name: str = "galick_gun_styrk") -> dict:
    k.tasks.open(name, "Styrkaeopteryx", "make me a galick gun", "a Galick Gun for Styrkaeopteryx",
                 "A Galick Gun for Styrkaeopteryx: charge and fire.")
    return k.tasks.get(name)


# ------------------------------------------------------------------ actions, no task

async def test_an_action_line_opens_no_task_names_each_copy_for_its_player_and_says_with_no_task(kit):
    k = kit()
    d = decision(part("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickaxe")),
                 part("railgun", "a railgun for Alex", {"player": "Alex"}),
                 part("teleport", "teleport everyone home", {"player": "@a", "to": "place", "target": "home"}),
                 part("talk", said="thanks"), reply="pickaxe's yours, Alex has a railgun, home you all go")
    await run(k, d, player="Steve", line="pickaxe, a railgun for Alex, and everyone home")
    runs = k.vfg.of("action", "run")
    assert runs == [["action", "run", "command", 'commands=["give Steve diamond_pickaxe"]', "--for", "Steve"],
                    ["action", "run", "railgun", "player=Alex", "--for", "Alex"],
                    ["action", "run", "teleport", "player=@a", "to=place", "target=home", "--for", "Steve"]]
    assert k.vfg.calls.index(k.vfg.of("say")[0]) > k.vfg.calls.index(runs[-1])
    assert k.vfg.said() == [["pickaxe's yours, Alex has a railgun, home you all go"]]
    assert [f for f, _ in k.vfg.rows("action")] == ["action -", "action -", "action -"]
    assert k.vfg.rows("action")[0][1] == "command: give Steve a diamond pickaxe — ok: done"
    assert k.tasks.all() == [] and k.vfg.of("task") == [] and k.orch.calls == []


async def test_a_refused_command_is_retried_once_by_the_planner_with_its_reference(kit):
    k = kit(Planned_command={"action": cmd("give Steve minecraft:diamond_pickaxe 1"), "hand_on": "", "drop": "",
                             "say": "a pickaxe for Steve"})
    k.vfg.on("action", "run", answer=[ran(False, "Unknown item 'minecraft:diamond_pickax'"), ran(True, "Gave 1")])
    d = decision(part("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickax")),
                 reply="there you go")
    await run(k, d, player="Steve", line="diamond pickaxe pls")
    plan = k.ask.of("Planned_command")
    assert len(plan) == 1 and plan[0]["model"] == "plan-model"
    assert (plan[0]["component"], plan[0]["call_id"]) == ("planner", "#812 #1")  # its usage row's
    assert "IT FAILED: Unknown item" in plan[0]["prompt"] and "## give" in plan[0]["prompt"]  # its reference
    assert "Steve said: diamond pickaxe pls" in plan[0]["prompt"]
    assert k.vfg.of("action", "run")[1] == ["action", "run", "command",
                                            'commands=["give Steve minecraft:diamond_pickaxe 1"]', "--for", "Steve"]
    assert k.vfg.said() == [["there you go"]]
    assert k.vfg.of("usage") == [["usage", "flush"]]  # the planner's row, sent nobody waiting


async def test_a_failed_planner_call_counts_what_it_spent(kit, capsys):
    failed = LlmError("no answer from plan-model within 20 s")
    failed.cost, failed.ms = 0.0004, 20000
    k = kit(Planned_command=failed)
    k.vfg.on("action", "run", answer=ran(False, "Unknown item 'minecraft:diamond_pickax'"))
    d = decision(part("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickax")),
                 reply="there you go")
    await run(k, d, player="Steve", line="diamond pickaxe pls")
    assert "#812 retries: 1 planner call(s), 20000 ms, $0.000400\n" in capsys.readouterr().out
    assert k.vfg.of("usage") == [["usage", "flush"]]
    failed.cost_known = False  # cut off with an attempt in flight: what it spent is a floor
    await run(k, d, player="Steve", line="diamond pickaxe pls")
    assert "#812 retries: 1 planner call(s), 20000 ms, $0.000400+\n" in capsys.readouterr().out


async def test_the_planners_usage_flush_waits_for_the_one_running(kit):
    k = kit(Planned_command={"action": cmd("give Steve minecraft:diamond_pickaxe 1"), "hand_on": "", "drop": "",
                             "say": "a pickaxe for Steve"})
    k.vfg.on("action", "run", answer=[ran(False, "Unknown item 'minecraft:diamond_pickax'"), ran(True, "Gave 1")])
    down = asyncio.Event()

    async def flush(argv):
        await down.wait()
        return VfgResult(0, "", "")

    k.vfg.on("usage", "flush", answer=flush)
    k.later.flush_usage()  # a brain call's, still running
    await asyncio.sleep(0)
    d = decided(k, decision(part("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickax")),
                            reply="there you go"))
    await k.acts.run(d, player="Steve", line="diamond pickaxe pls", seen=SEEN, ctx=k.ctx, head=HEAD, ref="#812")
    assert len(k.ask.of("Planned_command")) == 1 and k.vfg.of("usage") == [["usage", "flush"]]
    down.set()
    await k.settle()
    assert k.vfg.of("usage") == [["usage", "flush"]] * 2  # the planner's row, in the one after it


async def test_a_failed_action_is_never_claimed_a_code_line_says_so_instead(kit):
    k = kit(Planned_command={"action": cmd("give Steve diamond_pickaxe"), "hand_on": "", "drop": "", "say": "x"})
    k.vfg.on("action", "run", "command", answer=ran(False, "Unknown item"))
    d = decision(part("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickax")),
                 part("stop", "stop Alex's railgun", {"copy": "railgun_alex"}),
                 part("command", "enchant Steve's helmet", drop="Steve has no helmet"),
                 reply="pickaxe given, railgun gone")
    await run(k, d, player="Steve", line="…")
    assert len(k.vfg.of("action", "run")) == 2  # tried, and retried once; the stop names no running copy
    [said] = k.vfg.said()
    assert said == ["couldn't give Steve a diamond pickaxe: Unknown item — couldn't stop Alex's railgun: "
                    "nothing called railgun_alex is running — couldn't enchant Steve's helmet: Steve has no helmet"]


async def test_a_question_back_is_a_talk_part_that_runs_nothing(kit):
    k = kit()
    await run(k, decision(part("talk", said="make a patu"), reply="a path to where, the surface?"), player="Krown")
    assert k.vfg.of("action", "run") == [] and k.tasks.all() == []
    assert k.vfg.said() == [["a path to where, the surface?"]]


# ------------------------------------------------------------------ the orchestrator's answers

async def test_new_opens_the_task_writes_its_whole_row_and_starts_its_implementer_after_the_row_went_out(kit):
    k = kit(rows(LINE), orchestrator=[NEW_GUN])
    sent_first = []
    k.impl.on_start = lambda name: sent_first.append(bool(k.written(name)))
    await run(k, decision(GUN, reply="on it!"))
    [call] = k.orch.calls
    assert call["head"] == HEAD and call["k"] == 0 and call["replying"] and call["given"] == []
    assert call["parts"][0].instruction.startswith("a Galick Gun") and call["ctx"] is k.ctx
    assert k.impl.started == [("galick_gun_styrk", "a Galick Gun for Styrkaeopteryx")] and sent_first == [True]
    [row] = k.written("galick_gun_styrk")
    assert row == {"name": "galick_gun_styrk", "description": "A Galick Gun for Styrkaeopteryx: charge and fire.",
                   "player": "Styrkaeopteryx", "status": "open", "line": "make me a galick gun",
                   "brief": "a Galick Gun for Styrkaeopteryx",
                   "made": {"scripts": [], "actions": [], "presets": [], "copies": []}, "variables": None,
                   "session": None, "result": None, "created_at": row["created_at"],
                   "updated_at": row["created_at"], "closed_at": None}
    assert k.vfg.rows("task") == [("task galick_gun_styrk", "new: A Galick Gun for Styrkaeopteryx: charge and fire.")]
    assert k.vfg.said() == [["on it, your galick gun's coming", "--task", "galick_gun_styrk"]]  # never the brain's
    # its usage row is structured()'s, named by the line's ref; the brain only sends the queue
    assert k.orch.calls[0]["ref"] == "#812"
    assert k.vfg.of("usage", "add") == [] and k.vfg.of("usage") == [["usage", "flush"]]


@pytest.mark.parametrize("asked, got", [
    ("galick_gun_styrk", "galick_gun_styrk_2"),   # a task's name
    ("railgun", "railgun_2"),                     # an action's
    ("frost_bow_alex", "frost_bow_alex_2"),       # a script folder in made/<server>/scripts/
    ("ice_trail_alex", "ice_trail_alex_2"),       # a running copy's
    ("cast", "cast_2"),                           # the bridge's task for a cast's copies
])
async def test_a_new_tasks_name_is_made_free_with_a_number(kit, asked, got):
    (REPO_COPY / "made" / "main" / "scripts" / "frost_bow_alex").mkdir(parents=True, exist_ok=True)
    k = kit(orchestrator=[{**NEW_GUN, "name": asked}])
    k.running({"ice_trail_alex": {"action": "ice_trail", "values": {}, "task": None}})
    if asked == "galick_gun_styrk":
        opened(k)
    await run(k, decision(GUN, reply="on it!"))
    assert k.impl.started == [(got, "a Galick Gun for Styrkaeopteryx")]
    assert k.tasks.get(got)["status"] == "open" and k.vfg.said() == [["on it, your galick gun's coming", "--task", got]]


async def test_follow_up_opens_the_task_again_and_resumes_its_implementer_with_its_session_and_description(kit):
    k = kit(orchestrator=[answer("follow_up", task="galick_gun_styrk", brief="make the beam pierce blocks",
                                 description="A Galick Gun for Styrkaeopteryx: a beam that pierces blocks.",
                                 reply="on it, it'll pierce blocks")])
    opened(k)
    k.tasks.set_session("galick_gun_styrk", "sess-1")
    k.tasks.close("galick_gun_styrk", "done", "galick_gun_styrk done: a Galick Gun")
    await run(k, decision(part("orchestrator", "make Styrkaeopteryx's galick gun pierce blocks"), reply="ok"),
              line="make it pierce blocks")
    assert k.impl.followed == [("galick_gun_styrk", "make the beam pierce blocks", "sess-1",
                                "A Galick Gun for Styrkaeopteryx: a beam that pierces blocks.")]
    t = k.tasks.get("galick_gun_styrk")
    assert (t["status"], t["closed_at"], t["brief"]) == ("open", None, "make the beam pierce blocks")
    assert k.written("galick_gun_styrk")[-1]["status"] == "open"
    assert k.vfg.rows("task") == [("task galick_gun_styrk", "follow-up: make the beam pierce blocks")]
    assert k.vfg.said() == [["on it, it'll pierce blocks", "--task", "galick_gun_styrk"]]


async def test_edit_runs_the_edit_action_with_no_task_and_writes_a_note(kit):
    k = kit(orchestrator=[answer("edit", copy="railgun_steve", values=[{"key": "cooldown", "value": "0"}],
                                 reply="cooldown's 0 now")])
    k.running({"railgun_steve": RAILGUN})
    await run(k, decision(part("orchestrator", "no cooldown on Steve's railgun"), reply="sure"), player="Steve")
    assert k.vfg.of("action", "run") == [["action", "run", "edit", "copy=railgun_steve", "cooldown=0"]]
    assert k.vfg.rows("note") == [("note -", "edited railgun_steve: cooldown=0")]
    assert k.orch.calls[0]["copies"] == {"railgun_steve": RAILGUN}  # running.json, read fresh
    assert k.vfg.said() == [["cooldown's 0 now"]]


async def test_stop_runs_stop_on_each_copy_writes_a_note_each_and_leaves_the_tasks_status(kit):
    k = kit(orchestrator=[answer("stop", copies=["railgun_steve", "galick_gun_styrk"], reply="both stopped")])
    opened(k)
    k.tasks.close("galick_gun_styrk", "done", "galick_gun_styrk done: a Galick Gun")
    k.running({"railgun_steve": RAILGUN, "galick_gun_styrk": {"action": "galick_gun_styrk", "values": {},
                                                               "task": "galick_gun_styrk"}})
    await run(k, decision(part("orchestrator", "stop Steve's railgun and Styrkaeopteryx's galick gun"), reply="ok"))
    assert k.vfg.of("action", "run") == [["action", "run", "stop", "copy=railgun_steve"],
                                         ["action", "run", "stop", "copy=galick_gun_styrk"]]
    assert k.vfg.rows("note") == [("note -", "stopped railgun_steve"),
                                  ("note galick_gun_styrk", "stopped galick_gun_styrk")]
    assert k.tasks.get("galick_gun_styrk")["status"] == "done" and k.vfg.said() == [["both stopped"]]


async def test_a_reply_only_starts_nothing_and_is_said_with_no_task(kit):
    k = kit(orchestrator=[answer("reply", reply="better how, more damage or no cooldown?")])
    await run(k, decision(part("orchestrator", "make Steve's gun better"), reply="ok"), player="Steve")
    assert k.impl.started == k.impl.followed == k.vfg.of("action", "run") == [] and k.tasks.all() == []
    assert k.vfg.said() == [["better how, more damage or no cooldown?"]]


async def test_the_real_orchestrator_as_main_builds_it_opens_a_new_task_and_runs_an_edit_under_acts(kit):
    from brain.__main__ import orchestrator_of
    k = kit(rows(LINE))
    k.acts.orchestrator = orchestrator_of(k.cat, k.ask, owner="KrownJM", checkpoint=2)
    k.ask.answers["TaskAnswer"] = {**NEW_GUN, "name": " galick_gun_styrk "}
    await run(k, decision(GUN, reply="on it!"))
    [asked] = k.ask.of("TaskAnswer")
    assert HEAD in asked["prompt"] and "THE REPLY: yours" in asked["prompt"]
    assert k.impl.started == [("galick_gun_styrk", "a Galick Gun for Styrkaeopteryx")]
    assert k.vfg.said() == [["on it, your galick gun's coming", "--task", "galick_gun_styrk"]]

    k = kit()
    k.acts.orchestrator = orchestrator_of(k.cat, k.ask, owner="KrownJM", checkpoint=2)
    k.ask.answers["TaskAnswer"] = answer("edit", copy="railgun_steve", values=[{"key": "cooldown", "value": " 0 "}],
                                         reply="cooldown's 0 now")
    k.running({"railgun_steve": RAILGUN})
    await run(k, decision(part("orchestrator", "no cooldown on Steve's railgun"), reply="sure"), player="Steve")
    assert k.vfg.of("action", "run") == [["action", "run", "edit", "copy=railgun_steve", "cooldown=0"]]
    assert k.vfg.rows("note") == [("note -", "edited railgun_steve: cooldown=0")]
    assert k.vfg.said() == [["cooldown's 0 now"]]


# ------------------------------------------------------------------ the reply

async def test_the_orchestrators_reply_goes_out_once_the_action_parts_are_done(kit):
    gate = asyncio.Event()

    async def slow(argv):
        await gate.wait()
        return ran(True, "Gave 64 [Steak] to Steve")

    k = kit(orchestrator=[answer("follow_up", task="galick_gun_styrk", brief="pierce blocks", description="…",
                                 reply="steak's yours, and your gun will pierce blocks")])
    opened(k)
    k.vfg.on("action", "run", "command", answer=slow)
    d = decision(part("command", "give Steve 64 steak", cmd("give Steve cooked_beef 64")),
                 part("orchestrator", "make Steve's gun pierce blocks"), reply="steak given")
    running = asyncio.create_task(run(k, d, player="Steve"))
    for _ in range(20):
        await asyncio.sleep(0)
    assert k.impl.followed and k.vfg.said() == []   # the orchestrator ran beside the action; the reply waits
    assert [p.to for p in k.orch.calls[0]["others"]] == ["command"]
    gate.set()
    await running
    assert k.vfg.said() == [["steak's yours, and your gun will pierce blocks", "--task", "galick_gun_styrk"]]


async def test_the_brains_held_reply_is_said_when_the_first_call_fails_and_nothing_starts(kit):
    k = kit(orchestrator=[LlmError("timed out")])
    d = decision(part("command", "give Steve 64 steak", cmd("give Steve cooked_beef 64")),
                 part("orchestrator", "make Steve's gun pierce blocks"),
                 reply="64 steak's yours; say the gun part again?")
    await run(k, d, player="Steve")
    assert k.vfg.said() == [["64 steak's yours; say the gun part again?"]]
    assert k.impl.started == k.impl.followed == [] and k.tasks.all() == []


async def test_a_failed_action_or_edit_replaces_the_orchestrators_reply_with_a_code_line(kit):
    k = kit(orchestrator=[NEW_GUN])
    k.vfg.on("action", "run", "summon_around", answer=ran(False, "no such mob"))
    d = decision(part("summon_around", "summon 5 zombies round Steve", {"players": "Steve", "mob": "zombie",
                                                                         "count": 5}), GUN, reply="ok")
    await run(k, d)
    assert k.vfg.said() == [["couldn't summon 5 zombies round Steve: no such mob — on it: make me a galick gun",
                             "--task", "galick_gun_styrk"]]

    k = kit(orchestrator=[answer("edit", copy="railgun_steve", values=[{"key": "cooldown", "value": "0"}],
                                 reply="cooldown's 0 now")])
    k.running({"railgun_steve": RAILGUN})
    k.vfg.on("action", "run", "edit", answer=ran(False, "the copy did not answer"))
    await run(k, decision(part("orchestrator", "no cooldown on Steve's railgun"), reply="sure"), player="Steve")
    assert k.vfg.said() == [["couldn't change railgun_steve: the copy did not answer"]]
    assert k.vfg.rows("note") == [("note -", "couldn't edit railgun_steve: the copy did not answer")]

    k = kit(orchestrator=[answer("stop", copies=["railgun_steve"], reply="stopped")])  # gone by the time it runs
    k.running({})
    await run(k, decision(part("orchestrator", "stop Steve's railgun"), reply="ok"), player="Steve")
    assert k.vfg.said() == [["couldn't stop railgun_steve: nothing called railgun_steve is running"]]


async def test_a_question_back_is_still_asked_after_the_code_line_when_an_action_fails(kit):
    k = kit(orchestrator=[answer("reply", reply="better how, more damage or no cooldown?"),
                          answer("reply", reply="which staff, the ice one or the fire one?")])
    k.vfg.on("action", "run", "summon_around", answer=ran(False, "no such mob"))
    d = decision(part("summon_around", "summon 5 zombies round Steve", {"players": "Steve", "mob": "zombie",
                                                                         "count": 5}),
                 part("orchestrator", "make Steve's gun better", said="make my gun better"),
                 part("orchestrator", "make Steve's staff stronger", said="and my staff stronger"), reply="ok")
    await run(k, d, player="Steve")
    assert k.impl.started == k.impl.followed == [] and k.tasks.all() == []
    assert k.vfg.said() == [["couldn't summon 5 zombies round Steve: no such mob"],
                            ["better how, more damage or no cooldown?"],
                            ["which staff, the ice one or the fire one?"]]


async def test_two_orchestrator_parts_one_after_another_one_reply_and_a_later_reply_only_as_a_line_of_its_own(kit):
    k = kit(orchestrator=[NEW_GUN, answer("reply", reply="which staff, the ice one or the fire one?"),
                          LlmError("no answer")])
    d = decision(GUN, part("orchestrator", "make Styrkaeopteryx's staff stronger", said="and my staff stronger"),
                 part("orchestrator", "a moat round Styrkaeopteryx's castle", said="and a moat round my castle"),
                 reply="on it")
    await run(k, d)
    assert [c["k"] for c in k.orch.calls] == [0, 1, 2] and [c["replying"] for c in k.orch.calls] == [True, False, False]
    assert k.orch.calls[1]["given"] == ["new galick_gun_styrk"]
    assert k.orch.calls[2]["given"] == ["new galick_gun_styrk", "reply only"]
    assert k.vfg.said() == [["on it, your galick gun's coming", "--task", "galick_gun_styrk"],
                            ["which staff, the ice one or the fire one?"],
                            ['sorry, I lost part of that — say "and a moat round my castle" again?']]


async def test_a_later_call_that_breaks_never_costs_the_line_the_first_calls_reply(kit):
    class Breaks:
        """Call 0 answers reply only; call 1 raises before it awaits anything, as a bug in building
        its prompt would."""
        def __init__(self):
            self.inner = FakeOrchestrator(answer("reply", reply="which gun, the railgun or the galick gun?"))

        async def answer(self, ctx, **kw):
            if kw["k"] == 1:
                raise KeyError("values")
            return await self.inner.answer(ctx, **kw)

    k = kit()
    k.acts.orchestrator = Breaks()
    d = decision(part("orchestrator", "make Steve's gun stronger"), part("orchestrator", "a moat round the castle"),
                 reply="say that again?")
    with pytest.raises(KeyError) as broke:
        await k.acts.run(decided(k, d), player="Steve", line="…", seen=SEEN, ctx=k.ctx, head=HEAD, ref="#812")
    await k.settle()
    assert k.vfg.said() == [["which gun, the railgun or the galick gun?"],
                            [BROKE]]
    assert TOLD in broke.value.__notes__  # said once: the loop never says an error line of its own for it


async def test_a_task_whose_implementer_never_started_is_failed_by_name(kit):
    async def broken(name, brief):
        raise RuntimeError("no CLI")

    k = kit(orchestrator=[NEW_GUN])
    k.impl.start = broken
    await run(k, decision(GUN, reply="on it!"))
    t = k.tasks.get("galick_gun_styrk")
    assert (t["status"], t["result"]) == ("failed", "galick_gun_styrk failed: the implementer could not start: "
                                                    "RuntimeError: no CLI")
    assert k.vfg.rows("result") == [("result galick_gun_styrk", t["result"])]

    hold = asyncio.Event()

    async def held(name, brief):
        await hold.wait()

    k = kit(orchestrator=[NEW_GUN])
    k.impl.start = held
    running = asyncio.create_task(k.acts.run(decided(k, decision(GUN, reply="on it!")), player="Styrkaeopteryx",
                                             line="make me a galick gun", seen=SEEN, ctx=k.ctx, head=HEAD, ref="#812"))
    for _ in range(20):
        await asyncio.sleep(0)
    running.cancel()                                  # the brain stops before the start is done
    await asyncio.gather(running, return_exceptions=True)
    await k.settle()
    assert k.tasks.get("galick_gun_styrk")["result"] == ("galick_gun_styrk failed: cut off, the brain stopped "
                                                         "before its implementer started")


async def test_a_follow_up_cut_off_before_its_implementer_got_it_is_failed_by_name(kit):
    k = kit(orchestrator=[answer("follow_up", task="galick_gun_styrk", brief="make the beam pierce blocks",
                                 description="…", reply="on it, it'll pierce blocks")])
    opened(k)
    k.tasks.close("galick_gun_styrk", "done", "galick_gun_styrk done: a Galick Gun")
    posted = k.acts.post

    def post(kind, task, text):  # the brain stops right after the follow-up's row: the cancel
        posted(kind, task, text)  # lands on the await before the implementer is handed the brief
        if kind == "task":
            asyncio.current_task().cancel()

    k.acts.post = post
    running = asyncio.create_task(k.acts.run(decided(k, decision(part("orchestrator", "make it pierce blocks"),
                                                                 reply="ok")),
                                             player="Styrkaeopteryx", line="make it pierce blocks", seen=SEEN,
                                             ctx=k.ctx, head=HEAD, ref="#812"))
    await asyncio.gather(running, return_exceptions=True)
    await k.settle()
    assert k.impl.followed == []
    t = k.tasks.get("galick_gun_styrk")
    record = "galick_gun_styrk failed: cut off, the brain stopped before its implementer got the follow-up"
    assert (t["status"], t["result"]) == ("failed", record)
    assert k.written("galick_gun_styrk")[-1]["status"] == "failed"
    assert k.vfg.rows("result") == [("result galick_gun_styrk", record)]


async def test_a_reports_orchestrator_part_writes_no_reply_and_the_brains_is_said(kit):
    k = kit(orchestrator=[answer("follow_up", task="galick_gun_styrk", brief="fix the beam's range",
                                 description="…", reply="never said")])
    opened(k)
    k.tasks.close("galick_gun_styrk", "done", "galick_gun_styrk done: a Galick Gun")
    await k.acts.run(decided(k, decision(part("orchestrator", "make the beam reach 150 blocks as asked"),
                                         reply="your galick gun's ready; making it reach further")),
                     player="Styrkaeopteryx", line="make me a galick gun", seen=SEEN, ctx=k.ctx,
                     head="THE REPORT: …", ref="galick_gun_styrk", report=True)
    await k.settle()
    assert [c["replying"] for c in k.orch.calls] == [False] and k.orch.calls[0]["head"] == "THE REPORT: …"
    assert k.vfg.said() == [["your galick gun's ready; making it reach further", "--task", "galick_gun_styrk"]]
    assert k.impl.followed[0][:2] == ("galick_gun_styrk", "fix the beam's range")
    assert [c["ref"] for c in k.orch.calls] == ["galick_gun_styrk"] and k.vfg.of("usage", "add") == []

