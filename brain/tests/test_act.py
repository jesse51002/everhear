"""Running a decision: actions in order under one task, the one retry of a refused command,
implementers started and followed up, and a reply that never claims what failed."""

import json

from brain.world import Seen

from conftest import cmd, decision, part, ran

SEEN = Seen(places="SAVED PLACES: home 1 64 2 overworld (T3)", known={"home"})


def decided(k, answer: dict):
    return k.call.Decision.model_validate_json(json.dumps(answer))


async def test_actions_run_in_order_under_one_task_and_the_reply_comes_after_them(kit):
    k = kit()
    d = decided(k, decision(part("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickaxe")),
                            part("teleport", "teleport Steve home",
                                 {"player": "Steve", "to": "place", "target": "home"}),
                            part("talk", said="thanks"), reply="pickaxe's yours, and home you go"))
    await k.acts.run(d, player="Steve", line="pickaxe and take me home", seen=SEEN)
    await k.settle()
    runs = k.vfg.of("action", "run")
    assert runs == [["action", "run", "command", 'commands=["give Steve diamond_pickaxe"]', "--task", "T1"],
                    ["action", "run", "teleport", "player=Steve", "to=place", "target=home", "--task", "T1"]]
    assert k.vfg.calls.index(k.vfg.of("say")[0]) > k.vfg.calls.index(runs[1])
    assert k.vfg.said() == [["pickaxe's yours, and home you go", "--task", "T1"]]
    assert [f for f, _ in k.vfg.rows("action")] == ["action T1", "action T1"]
    assert k.vfg.rows("action")[0][1] == "command: give Steve a diamond pickaxe — ok: done"
    t = k.tasks.get("T1")
    assert (t["player"], t["line"], t["status"]) == ("Steve", "pickaxe and take me home", "done")


async def test_a_refused_command_is_retried_once_by_the_planner_with_its_reference(kit):
    k = kit(Planned_command={"action": cmd("give Steve minecraft:diamond_pickaxe 1"), "hand_on": "", "drop": "",
                             "say": "a pickaxe for Steve"})
    k.vfg.on("action", "run", answer=[ran(False, "Unknown item 'minecraft:diamond_pickax'"), ran(True, "Gave 1")])
    d = decided(k, decision(part("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickax")),
                            reply="there you go"))
    await k.acts.run(d, player="Steve", line="diamond pickaxe pls", seen=SEEN)
    plan = k.ask.of("Planned_command")
    assert len(plan) == 1 and plan[0]["model"] == "plan-model"
    assert "IT FAILED: Unknown item" in plan[0]["prompt"] and "## give" in plan[0]["prompt"]  # its reference
    assert k.vfg.of("action", "run")[1][3] == 'commands=["give Steve minecraft:diamond_pickaxe 1"]'
    assert k.vfg.said() == [["there you go", "--task", "T1"]]


async def test_a_failed_action_is_never_claimed_a_code_line_says_so_instead(kit):
    k = kit(Planned_command={"action": cmd("give Steve diamond_pickaxe"), "hand_on": "", "drop": "", "say": "x"})
    k.vfg.on("action", "run", "command", answer=ran(False, "Unknown item"))
    d = decided(k, decision(part("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickax")),
                            part("stop", "stop Alex's railgun", {"copy": "railgun_2"}),
                            part("command", "enchant Steve's helmet", drop="Steve has no helmet"),
                            reply="pickaxe given, railgun gone"))
    await k.acts.run(d, player="Steve", line="…", seen=SEEN)
    assert len(k.vfg.of("action", "run")) == 2  # tried, and retried once; the stop names no running copy
    [said] = k.vfg.said()
    assert said[0] == ("couldn't give Steve a diamond pickaxe: Unknown item — couldn't stop Alex's railgun: "
                       "nothing called railgun_2 is running — couldn't enchant Steve's helmet: Steve has no helmet")
    assert k.tasks.get("T1")["status"] == "failed"


async def test_implementers_start_and_follow_up_and_the_reply_goes_out_at_once(kit):
    k = kit()
    k.tasks.open("Alex", "make me an ice trail", "an ice trail behind Alex")  # T1, from earlier
    d = decided(k, decision(part("implementer", "a meteor staff for Steve that rains meteors where he looks"),
                            part("implementer", "make the trail twice as wide", task="T1"),
                            reply="on it: staff coming, trail getting wider"))
    await k.acts.run(d, player="Steve", line="meteor staff, and widen Alex's trail", seen=SEEN)
    await k.settle()
    assert k.impl.started == [("T2", "a meteor staff for Steve that rains meteors where he looks")]
    assert k.impl.followed == [("T1", "make the trail twice as wide", "an ice trail behind Alex")]  # and its brief
    assert k.vfg.said() == [["on it: staff coming, trail getting wider", "--task", "T2"]]
    assert sorted(k.vfg.rows("task")) == [("task T1", "T1 follow-up: make the trail twice as wide"),
                                          ("task T2", "T2: a meteor staff for Steve that rains meteors where he looks")]
    saved = json.loads(k.tasks.path.read_text())
    assert saved["next"] == 3 and saved["tasks"]["T2"]["status"] == "open" and saved["tasks"]["T2"]["player"] == "Steve"
    assert not k.vfg.of("action")


async def test_a_clarify_part_runs_nothing_opens_no_task_and_its_question_goes_out_at_once(kit):
    k = kit()
    d = decided(k, decision(part("clarify", said="make a patu"), reply="a path to where, the surface?"))
    await k.acts.run(d, player="Krown", line="can you make a patu, please?", seen=SEEN)
    await k.settle()
    assert k.vfg.of("action", "run") == [] and k.tasks.tasks == {}
    assert k.vfg.said() == [["a path to where, the surface?"]]
