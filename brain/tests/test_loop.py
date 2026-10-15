"""The brain: every line to the AI through its player's front turn (the conversation agent, then the
router), a context line no model is shown, history, no hold — the agent's line at once, hand-off or
not, the router's own line after it — and the agent's choice (look or do) passed to the router, the
work placed (a new loop, a loop already on it, a new implementer, an implementer followed up or
cancelled, nowhere), loops side by side and holding no turn, a line added to a loop read in its
next step (one more after an ending), an ended loop taken up again within its window, the budget
and the cap; and the loop itself: one step when every action lands, a refusal read and fixed in
the next step, a read, a hand-off and its brief, a follow-up and its cancel, silence, the last
step, a failed call, the prompt's cache marks. Reports go to the conversation agent at once, and
what they leave to the router. One turn per player, the settle, and the stop."""

import asyncio
import importlib.util
import json

import pytest
import vfg_actions
from pydantic import ValidationError
from vfg_llm import LlmError

from brain import config, loop
from brain.act import CANCEL, WROTE
from brain.actions import variables_of
from brain.call import PROMPT, HandOff
from brain.context import LIVE
from brain.turns import turned_away_line
from brain.vfg import VfgResult

from conftest import (STATE, act, answer, cmd, follow_up, hand_off, line, made, ran, report_answer, route, rows,
                      step)

ASK = line(5)
PICKAXE = act("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickaxe"))
GIVE = step(PICKAXE, reply="there you go")
TELEPORT = act("command", "teleport KrownJM to Styrkaeopteryx", cmd("tp KrownJM Styrkaeopteryx"))


def calls(k, output: str = "") -> list[str]:
    """Each call's output and ref, in the order they began: `Response #5`, `Route #5`, `Step #5 s1`."""
    return [f"{c['output']} {c['call_id']}" for c in k.ask.calls if not output or c["output"] == output]


def events(k) -> list[str]:
    """Each brain step's loop, in the order the calls began: `#5`, `galick_gun_styrk`."""
    return [c["call_id"].rsplit(" ", 1)[0] for c in k.ask.of("Step")]


async def turns(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def until(check, n: int = 200) -> None:
    """Yield until `check()` holds; nothing sleeps."""
    for _ in range(n):
        if check():
            return
        await asyncio.sleep(0)
    raise AssertionError("never happened")


class Held:
    """Answers held until the test lets each go: `held.go(i)` lets the i-th call answer; `answer` is
    one answer for every call, or a list of them in order."""

    def __init__(self, answer):
        self.answers = answer if isinstance(answer, list) else None
        self.answer = answer
        self.gates: list[asyncio.Event] = []
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> dict:
        gate = asyncio.Event()
        self.gates.append(gate)
        self.prompts.append(prompt)
        i = len(self.gates) - 1
        await gate.wait()
        return self.answers[i] if self.answers is not None else self.answer

    async def go(self, i: int) -> None:
        await until(lambda: len(self.gates) > i)
        self.gates[i].set()
        await turns()


# ------------------------------------------------------------------ the front turn

async def test_every_line_to_the_ai_goes_to_the_conversation_agent_whatever_its_live_class(kit):
    k = kit(Response=answer("haha, anytime!", choice="reply", reason="thanks"))
    for seq, cls in enumerate(LIVE, 5):
        k.brain.feed(line(seq, "Steve", "haha thanks AI", cls=cls))
        await k.settle()
    k.brain.feed("LINE#20 18:29:59 Alex voice context 0.7 - | ouch, I'm by the lava")  # a context line: nobody's
    await k.settle()
    assert calls(k) == [f"Response #{seq}" for seq, _ in enumerate(LIVE, 5)]
    assert k.vfg.said() == [["haha, anytime!"]] * len(LIVE)


async def test_each_row_goes_where_its_class_says_and_a_read_marks_what_it_took(kit):
    k = kit(rows("GAME#1 18:29:40 join Alex | Alex joined the game", "AI#2 18:29:41 say - | hi"),
            Step=GIVE, Response=[answer(), answer("anytime!", choice="reply", reason="thanks")])
    for text in ("GAME#3 18:29:50 death Alex | Alex tried to swim in lava", "SYS#0 18:29:51 | vfg watch: reconnected",
                 "LINE#4 18:29:52 Alex voice context 0.7 - | ouch, I'm by the lava", ASK,
                 line(6, text="haha thanks AI")):
        k.brain.feed(text)
        await asyncio.sleep(0)  # as `vfg watch` gives them: one line at a time
    await k.settle()
    assert calls(k, "Response") == ["Response #5", "Response #6"] and calls(k, "Route") == ["Route #5"]
    assert calls(k, "Step") == ["Step #5 s1"]
    for c in (k.ask.of("Response")[0]["prompt"], k.ask.of("Step")[0]["cached"][-1][0]):  # the agent's NEW, the loop's
        assert "Alex joined the game" in c.split("NEW SINCE")[1] and "Alex tried to swim in lava" in c
    for c in k.ask.calls:                  # the context line: in no prompt, cached or not, of any call
        assert "by the lava" not in c["prompt"] + "".join(text for text, _ in c["cached"])
    assert sorted(k.vfg.said()) == [["anytime!"], ["there you go"]]
    # the context line marked as it came, nobody waiting; each front turn's read took what no call had read
    assert k.vfg.of("context", "mark") == [["context", "mark", "4"], ["context", "mark", "1", "3", "5"],
                                           ["context", "mark", "6"]]
    # each call names its usage row, which structured() queues; the brain only sends the queue
    assert sorted((c["component"], c["call_id"]) for c in k.ask.calls) == [
        ("brain", "#5 s1"), ("respond", "#5"), ("respond", "#6"), ("router", "#5")]
    assert k.vfg.of("usage", "add") == [] and {tuple(c) for c in k.vfg.of("usage")} == {("usage", "flush")}
    assert k.ctx.unprocessed() == [] and k.ctx.last_seq == 6


async def test_the_agent_replies_says_nothing_or_hands_off_and_only_a_hand_off_calls_the_router(kit, capsys):
    k = kit(Response=[answer("haha, anytime!", choice="reply", reason="thanks"), answer("", choice="quiet"),
                      answer("On it!")], Step=GIVE)
    for seq, words in ((5, "haha thanks AI"), (6, "ok cool"), (7, "give me a diamond pickaxe")):
        k.brain.feed(line(seq, text=words))
        await k.settle()
    assert calls(k) == ["Response #5", "Response #6", "Response #7", "Route #7", "Step #7 s1"]
    assert k.vfg.said() == [["haha, anytime!"], ["On it!"], ["there you go"]]  # silence says nothing
    out = capsys.readouterr().out
    assert "front #5 Steve: agent reply 7 ms; said agent" in out
    assert "front #6 Steve: agent silent 7 ms; said nothing" in out
    assert "front #7 Steve: agent handoff 7 ms; router 7 ms: new_loop L1; said agent" in out
    tail = k.ask.of("Step")[0]["cached"][-1][0]                   # the loop's event: the agent's line, the router's why
    assert tail.endswith("LINE #7, 18:29:58: Steve said: give me a diamond pickaxe\n(the conversation agent already "
                         "answered the player: “On it!”; your reply follows it — never repeat it)\n"
                         "(the router: what it is about)\n")


async def test_the_agents_line_goes_out_at_once_even_with_a_hand_off_the_routers_line_after(kit):
    """There is no hold now (VFG_ROUTER_REASONING no longer changes what is said, config.py): the
    agent's line goes out the moment it answers, hand-off or not, and the router's own line, when
    it has one, goes out after it — never in its place, so it is never said twice."""
    seen_at_route: list = []

    async def routed(prompt):
        seen_at_route.append(list(k.vfg.said()))                 # the agent's line already went out
        return answers.pop(0)

    answers = [route(), route("nowhere", reply="Still building your aura.")]
    k = kit(Response=[answer("On it!"), answer("Still on it.")], Route=routed, Step=step(PICKAXE, end="silent"))
    k.brain.feed(line(5))
    await k.settle()
    assert seen_at_route == [[["On it!"]]]                       # out before the router was even asked
    k.brain.feed(line(6, text="is my aura done yet, hurry up"))
    await k.settle()
    assert seen_at_route == [[["On it!"]], [["On it!"], ["Still on it."]]]
    assert k.vfg.said() == [["On it!"], ["Still on it."], ["Still building your aura."]]  # the router's line after
    assert calls(k, "Step") == ["Step #5 s1"]                    # nowhere starts nothing


async def test_the_agents_choice_look_or_do_is_passed_to_the_router(kit):
    k = kit(Response=answer("Checking what you've got bound.", choice="look", reason="only a look tells"),
            Route=route())
    k.brain.feed(line(5, text="is my railgun still running"))
    await k.settle()
    assert ("(the conversation agent passed it on to be looked up: only a look tells)" in
            k.ask.of("Route")[0]["prompt"])

    k = kit(Response=answer("On it!", choice="do", reason="something to build"), Route=route())
    k.brain.feed(line(6, text="build me a castle"))
    await k.settle()
    assert "(the conversation agent passed it on to be done: something to build)" in k.ask.of("Route")[0]["prompt"]


async def test_the_router_failing_starts_a_new_loop_and_the_agent_failing_hands_off(kit, capsys):
    k = kit(Response=LlmError("no answer from the agent within 20 s"), Route=LlmError("no answer within 30 s"),
            Step=GIVE)
    k.brain.feed(ASK)
    await k.settle()
    assert calls(k) == ["Response #5", "Route #5", "Step #5 s1"] and k.vfg.said() == [["there you go"]]
    out = capsys.readouterr().out
    assert "respond #5 FAILED (" in out and "): handed off" in out and "router #5 FAILED (" in out
    assert "front #5 Steve: agent failed 0 ms; router FAILED 0 ms, so new_loop L1; said nothing" in out
    assert k.ask.of("Step")[0]["cached"][-1][0].endswith("(the conversation agent said nothing)\n")  # no router's why


async def test_the_turn_ends_once_the_router_placed_the_line_so_the_next_is_answered_while_the_loop_thinks(kit):
    held = Held(step(TELEPORT, reply="Teleporting you to Styrka!"))
    k = kit(Step=held, Response=[answer("On it!"), answer("Stirka's on her way to you? No, you to her, hang on.",
                                                          choice="reply", reason="a question about work under way")])
    k.brain.feed(line(5, "KrownJM", "can you teleport me to Stirka"))
    await until(lambda: held.gates)
    assert not k.brain.turns.busy("KrownJM")                     # the loop thinks; the turn is free
    assert k.brain.feed(line(6, "KrownJM", "wait, which way?")) == "started"
    await until(lambda: len(k.vfg.said()) == 2)
    assert k.vfg.said() == [["On it!"], ["Stirka's on her way to you? No, you to her, hang on."]]
    assert "KrownJM: “can you teleport me to Stirka” — being done now" in k.ask.of("Response")[1]["prompt"]
    await held.go(0)
    await k.settle()
    assert k.vfg.said()[-1] == ["Teleporting you to Styrka!"]


async def test_two_loops_of_one_player_run_side_by_side(kit):
    held = Held([step(PICKAXE, reply="there you go"), step(act("command", "make it day", cmd("time set day")),
                                                             end="silent")])
    k = kit(Step=held)
    k.brain.feed(line(5))
    await until(lambda: held.gates)
    k.brain.feed(line(6, text="and make it day"))
    await until(lambda: len(held.gates) == 2)                    # both steps in flight at once
    assert [x.id for x in k.brain.loops.offered()] == ["L1", "L2"] and k.brain.loops.busy() == {"Steve"}
    await held.go(1)                                             # the second ends first: nothing waits on the first
    await held.go(0)
    await k.settle()
    assert events(k) == ["#5", "#6"] and k.vfg.said() == [["there you go"]]
    assert [a[2] for a in k.vfg.of("action", "run")] == ["command", "command"]


# ------------------------------------------------------------------ a line added to a loop

async def test_a_line_added_to_a_thinking_loop_is_read_in_its_next_step_with_the_rows_since(kit):
    held = Held([step(TELEPORT, end="next"), step(reply="you're with Styrka now")])
    k = kit(Step=held, Response=[answer("On it!"), answer("Still on it.")],
            Route=[route(), route("loop", loop="L1", reason="the same teleport as L1")])
    k.brain.feed(line(5, "KrownJM", "can you teleport me to Stirka"))
    await until(lambda: held.gates)
    k.brain.feed("GAME#6 18:30:00 death Alex | Alex fell from a high place")
    k.brain.feed(line(7, "KrownJM", "teleport me to Stirkosaurus", hms="18:30:01"))
    await until(lambda: k.brain.loops.get("L1").pending)
    assert k.brain.loops.get("L1").limit == 20                   # 10 more for the line added
    await held.go(0)
    await held.go(1)
    await k.settle()
    second = k.ask.of("Step")[1]
    block = second["cached"][-1][0]
    assert block == ("LINE ADDED TO THIS LOOP: LINE #7, 18:30:01: KrownJM said: teleport me to Stirkosaurus\n"
                     "(the conversation agent answered: “Still on it.”)\n(the router: the same teleport as L1)\n"
                     "ROWS SINCE THIS LOOP LAST READ:\n18:30:00 game, death: Alex fell from a high place\n\n")
    assert second["cached"][-2][0].startswith("THE LOOP SO FAR (") and "YOU ENDED IT next" in second["cached"][-2][0]
    assert second["prompt"] == "\nSTEP 2 of at most 20.\n" and calls(k, "Step") == ["Step #5 s1", "Step #5 s2"]
    assert k.vfg.said() == [["On it!"], ["Still on it."], ["you're with Styrka now"]]


async def test_a_line_added_while_a_step_ends_the_loop_gets_one_more_step_the_ended_one_shown_as_sent(kit, capsys):
    async def first(prompt):                                     # step 1: the line comes in while it runs
        k.brain.feed(line(7, "KrownJM", "teleport me to Stirkosaurus", hms="18:30:01"))
        await until(lambda: k.brain.loops.get("L1").pending)
        return step(TELEPORT, reply="Teleporting you to Styrka!", record="teleported KrownJM to Styrkaeopteryx")

    k = kit(Step=[first, step(end="silent")], Response=[answer("On it!"), answer("Still on it.")],
            Route=[route(), route("loop", loop="L1")])
    k.brain.feed(line(5, "KrownJM", "can you teleport me to Stirka"))
    await k.settle()
    assert calls(k, "Step") == ["Step #5 s1", "Step #5 s2"]
    blocks = [t for t, _ in k.ask.of("Step")[1]["cached"]][-2:]
    assert blocks[0].endswith("ENDED reply AND SENT: reply “Teleporting you to Styrka!”, record “teleported KrownJM "
                              "to Styrkaeopteryx”.\n\n")
    assert blocks[1].startswith("LINE ADDED TO THIS LOOP: LINE #7")
    assert len(k.vfg.of("action", "run")) == 1                   # one teleport; the repeat needed nothing more
    assert k.vfg.rows() == [("action -", "teleported KrownJM to Styrkaeopteryx")]  # recorded once
    assert k.vfg.said() == [["On it!"], ["Still on it."], ["Teleporting you to Styrka!"]]
    assert "loop #5 KrownJM: 2 step(s)" in capsys.readouterr().out and k.brain.loops.get("L1").state == "ended"


async def test_an_ended_loop_is_taken_up_within_120_s_of_its_creation_from_its_saved_prompt(kit, capsys):
    k = kit(Step=[step(TELEPORT, reply="Teleporting you to Styrka!"), step(end="silent")],
            Response=answer("On it!"), Route=[route(), route("loop", loop="L1")])
    k.brain.feed(line(5, "KrownJM", "can you teleport me to Stirka"))
    await k.settle()
    first = k.ask.of("Step")[0]
    k.tick.t += 61                                               # the night's third ask: 61 s after the first
    k.brain.feed(line(9, "KrownJM", "can you teleport me to Stirka?", hms="18:30:59"))
    await k.settle()
    second = k.ask.of("Step")[1]
    assert second["call_id"] == "#5 s2" and second["prompt"] == "\nSTEP 2 of at most 20.\n"
    before = [t for t, _ in first["cached"]]
    assert [t for t, _ in second["cached"]][:len(before)] == before   # the same prefix: read from the cache
    assert second["cached"][-2][0].endswith("ENDED reply AND SENT: reply “Teleporting you to Styrka!”.\n\n")
    assert second["cached"][-1][0].startswith("LINE ADDED TO THIS LOOP: LINE #9")
    assert "- L1, for KrownJM, ended, saved: can take a line for 59 s more; 1 of 10 steps used" in \
        k.ask.of("Route")[1]["prompt"]                           # the router saw it, ended and offered
    assert "L1 saved, 2 of 20 steps, lines #5, #9" in capsys.readouterr().out


async def test_an_ended_loop_past_its_window_takes_no_line_the_line_starts_a_new_loop(kit, capsys):
    async def slow(prompt):                                      # the router answers past L1's window
        k.tick.t += 121
        return route("loop", loop="L1")

    k = kit(Step=[step(TELEPORT, reply="Teleporting you to Styrka!"), step(end="silent")], Response=answer(),
            Route=[route(), slow])
    k.brain.feed(line(5, "KrownJM", "can you teleport me to Stirka"))
    await k.settle()
    k.brain.feed(line(9, "KrownJM", "can you teleport me to Stirka?"))
    await k.settle()
    assert calls(k, "Step") == ["Step #5 s1", "Step #9 s1"] and k.brain.loops.get("L1") is None
    out = capsys.readouterr().out
    assert "#9 -> L1: it can no longer take a line, so a new loop" in out
    assert "front #9 KrownJM: agent handoff 7 ms; router 7 ms: loop L1, so new_loop L2; said nothing" in out
    assert "STEP 1 of at most 10." in k.ask.of("Step")[1]["prompt"]


async def test_ten_steps_a_line_up_to_twenty_and_at_twenty_the_loop_ends_and_is_deleted(kit, capsys):
    bread = step(act("command", "give Steve bread", cmd("give Steve bread")), end="next")

    async def first(prompt):
        k.brain.feed(line(7, text="and keep it coming", hms="18:30:01"))
        await until(lambda: k.brain.loops.get("L1").pending)
        return bread

    k = kit(Step=[first] + [bread] * 18 + [step(reply="that's a lot of bread: 19 loaves")],
            Response=answer(), Route=[route(), route("loop", loop="L1")])
    k.brain.feed(line(5, text="keep giving me bread"))
    await k.settle()
    trailers = [c["prompt"] for c in k.ask.of("Step")]
    assert len(trailers) == 20 and trailers[1] == "\nSTEP 2 of at most 20.\n"
    assert trailers[-1].startswith("\nSTEP 20 of at most 20.\nTHIS IS THE LAST STEP (this is step 20)")
    assert k.vfg.said() == [["that's a lot of bread: 19 loaves"]]
    assert k.brain.loops.get("L1") is None and k.brain.loops.offered() == []  # deleted: it never takes a line again
    assert "ended reply; L1 deleted, 20 of 20 steps, lines #5, #7" in capsys.readouterr().out


# ------------------------------------------------------------------ the router's other choices

async def test_a_new_implementer_starts_on_the_routers_brief_the_agents_line_already_went_out(kit, capsys):
    hook = hand_off("grappling_hook_alex", "A grappling hook for Alex: right-click to be pulled.",
                    words="“can I get a grappling hook?”")
    k = kit(Response=answer("On it!"), Route=route("new_implementer", hand_offs=(hook,)))
    k.brain.feed(line(5, "Alex", "can I get a grappling hook?"))
    await k.settle()
    [(name, brief)] = k.impl.started
    assert name == "grappling_hook_alex" and brief == HandOff.model_validate(hook).brief()
    t = k.tasks.get(name)
    assert (t["player"], t["line"], t["status"], t["brief"]) == ("Alex", "can I get a grappling hook?", "open", brief)
    assert k.vfg.rows("task") == [("task grappling_hook_alex", "new: A grappling hook for Alex: right-click to be "
                                                               "pulled.")]
    # said at once, before the task is even opened: never tagged with a task it did not yet know
    assert k.vfg.said() == [["On it!"]] and calls(k, "Step") == []
    assert "router 7 ms: new_implementer grappling_hook_alex; said agent" in capsys.readouterr().out


async def test_a_task_that_would_not_open_gets_codes_apology_after_the_agents_line(kit):
    """The agent's "On it!" goes out at once, before the router even runs; a task that then does
    not open is never promised either — code's apology follows it, correcting rather than
    replacing it (there is no hold to hold it back for)."""
    k = kit(Response=answer("On it!"), Route=route("new_implementer", hand_offs=(hand_off(),)))
    k.acts.open_task = lambda h, **kw: None
    k.brain.feed(line(5, "KrownJM", "a hot fireball incantation"))
    await k.settle()
    assert k.impl.started == []
    assert k.vfg.said() == [["On it!"],
                            ["sorry, I couldn't start “Can you make an incantation? That fires a fireball "
                             "whenever…” — say it again?"]]


async def test_the_router_follows_an_implementer_up_and_cancels_it(kit, capsys):
    k = kit(Response=[answer("On it!"), answer("Okay, putting it back.")],
            Route=[route("implementer", follow_ups=(follow_up("galick_gun_styrk", "make the beam pierce blocks",
                                                              "A Galick Gun that pierces."),)),
                   route("implementer", follow_ups=(follow_up("galick_gun_styrk", cancel=True),))])
    k.tasks.open("galick_gun_styrk", "Styrkaeopteryx", "a galick gun", "the brief", "A Galick Gun for Styrk.")
    k.tasks.set_session("galick_gun_styrk", "sess-1")
    k.tasks.close("galick_gun_styrk", "done", "galick_gun_styrk done")
    k.brain.feed(line(5, "Styrkaeopteryx", "make it pierce blocks"))
    await k.settle()
    k.brain.feed(line(6, "Styrkaeopteryx", "never mind, put it back"))
    await k.settle()
    assert k.impl.followed == [("galick_gun_styrk", "make the beam pierce blocks", "sess-1",
                                "A Galick Gun that pierces."),
                               ("galick_gun_styrk", CANCEL, "sess-1", "A Galick Gun that pierces.")]
    assert k.tasks.get("galick_gun_styrk")["status"] == "open" and calls(k, "Step") == []
    assert [text for _, text in k.vfg.rows("task")] == ["follow-up: make the beam pierce blocks",
                                                        "follow-up: never mind — put back what its last run built"]
    assert k.vfg.said() == [["On it!"], ["Okay, putting it back."]]  # said at once, before either follow-up is sent
    assert "#6 -> galick_gun_styrk's implementer, a cancel" in capsys.readouterr().out


async def test_a_line_about_two_tasks_things_follows_each_up_and_a_gone_one_is_dropped(kit, capsys):
    async def gone(prompt):  # the boots' task goes while the router answers: it was under TASKS when asked
        k.tasks.tasks.pop("rocket_boots_alex")
        return route("implementer", follow_ups=(follow_up("grappling_hook_alex", "reach twice as far"),
                                                follow_up("rocket_boots_alex", "land softly")))

    k = kit(Response=[answer("On it!"), answer("On it!")],
            Route=[route("implementer", reason="the hook and the boots, so: implementer",
                         follow_ups=(follow_up("grappling_hook_alex", "pull twice as fast"),
                                     follow_up("rocket_boots_alex", "launch twice as fast"))),
                   gone])
    for name in ("grappling_hook_alex", "rocket_boots_alex"):
        k.tasks.open(name, "Alex", "the ask", "the brief", f"{name} for Alex.")
        k.tasks.close(name, "done", f"{name} done")
    k.brain.feed(line(5, "Alex", "make my hook and my boots both twice as fast"))
    await k.settle()
    assert [(name, text) for name, text, _, _ in k.impl.followed] == [("grappling_hook_alex", "pull twice as fast"),
                                                                      ("rocket_boots_alex", "launch twice as fast")]
    assert {k.tasks.get(n)["status"] for n in ("grappling_hook_alex", "rocket_boots_alex")} == {"open"}
    assert [(task, text) for task, text in k.vfg.rows("task")] == [
        ("task grappling_hook_alex", "follow-up: pull twice as fast"),
        ("task rocket_boots_alex", "follow-up: launch twice as fast")]
    assert k.vfg.said() == [["On it!"]]  # said once, at once, before either follow-up is sent
    out = capsys.readouterr().out
    assert "router 7 ms: implementer grappling_hook_alex, rocket_boots_alex; said agent" in out
    assert calls(k, "Step") == []

    k.brain.feed(line(6, "Alex", "and the hook reaches further, the boots land softly"))
    await k.settle()
    assert [(name, text) for name, text, _, _ in k.impl.followed][2:] == [("grappling_hook_alex",
                                                                          "reach twice as far")]
    out = capsys.readouterr().out
    assert "#6 -> rocket_boots_alex: no such task now, its follow-up dropped" in out
    assert "router 7 ms: implementer grappling_hook_alex; said agent" in out and calls(k, "Step") == []


# ------------------------------------------------------------------ history and the loop

async def test_history_is_the_old_feed_at_the_start_and_a_line_on_the_watch_is_live_whatever_its_time(kit):
    k = kit(rows("LINE#8 18:29:50 Alex voice context 0.9 - | so hungry",
                 line(9, hms="18:20:00")), Step=GIVE)
    k.brain.start = 9
    assert [r.seq for r in k.brain.catch_up()] == [9]      # ten minutes old as the brain starts: history
    await k.settle()                                      # and a context line unread, whatever its age, read
    assert k.ask.calls == [] and k.vfg.of("context", "mark") == [["context", "mark", "8", "9"]]
    # the box's clock 90 s ahead of the PC's: a line said now reads as yesterday's 18:31:30, and is live
    k.brain.feed(line(10, hms="18:31:30"))
    await k.settle()
    assert calls(k, "Step") == ["Step #10 s1"] and "LINE #10" in k.ask.of("Step")[0]["cached"][-1][0]
    # after a drop, the backlog the watch catches up with is judged by its age, as a start's is
    k.brain.feed("SYS#0 18:30:00 | reconnected")
    k.brain.feed(line(11, text="give me a diamond axe", hms="18:21:00"))
    await k.settle()
    assert len(k.ask.of("Step")) == 1 and calls(k, "Response") == ["Response #10"]


async def test_an_ask_whose_actions_all_land_is_one_step_its_reply_said_and_one_record_written(kit, capsys):
    k = kit(Step=step(act("command", "give KrownJM a full set of netherite armor",
                          cmd("give KrownJM minecraft:netherite_helmet", "give KrownJM minecraft:netherite_boots")),
                      act("command", "give KrownJM 64 cobblestone", cmd("give KrownJM minecraft:cobblestone 64")),
                      reply="suited up, and 64 cobble", record="gave KrownJM netherite armor and 64 cobblestone"))
    k.brain.feed(line(5, "KrownJM", "give me netherite armor and 64 cobblestone"))
    await k.settle()
    assert calls(k, "Step") == ["Step #5 s1"]  # no second call
    assert len(k.vfg.of("action", "run")) == 2
    assert k.vfg.said() == [["suited up, and 64 cobble"]]
    assert k.vfg.rows() == [("action -", "gave KrownJM netherite armor and 64 cobblestone")]  # ONE record
    assert k.vfg.calls.index(k.vfg.of("say")[0]) > k.vfg.calls.index(k.vfg.of("action", "run")[-1])
    out = capsys.readouterr().out
    assert "brain #5 s1 7 ms $0.002000, 1000 in, 90% cached, 40 out | together: command" in out
    assert ("loop #5 KrownJM: 1 step(s), $0.002000, 90% cached, reply at 0.0 s, 0.0 s, ended reply; L1 saved, "
            "1 of 10 steps, lines #5") in out


async def test_a_refused_command_comes_back_with_its_error_and_reference_and_the_next_step_fixes_it(kit):
    fixed = ('item modify entity KrownJM hotbar.0 {type:"minecraft:set_enchantments",'
             'enchantments:{"minecraft:fire_aspect":5},add:true}')
    k = kit(Step=[step(act("command", "Fire Aspect V on KrownJM's axe", cmd("enchant KrownJM minecraft:fire_aspect 5")),
                       reply="your axe burns now", record="gave KrownJM's axe Fire Aspect 5"),
                  step(act("command", "Fire Aspect V on KrownJM's axe by item modifier", cmd(fixed)),
                       reply="your axe burns now", record="gave KrownJM's axe Fire Aspect 5 by item modifier; /enchant "
                                                          "refuses a level over the maximum")])
    k.vfg.on("action", "run", answer=[ran(False, "Fire Aspect V is higher than the maximum level of II"),
                                      ran(True, "Modified 1 item")])
    k.brain.feed(line(5, "KrownJM", "fire aspect 5 on my axe"))
    await k.settle()
    first, second = k.ask.of("Step")
    back = second["cached"][-1][0]                       # step 1, its result, its reference: the marked end
    assert second["cached"][-1][1] and back.startswith("THE LOOP SO FAR (")
    assert "REFUSED: Fire Aspect V is higher than the maximum level of II" in back
    assert "## enchant\nSyntax on this server:" in back
    assert "NOT SENT: you meant to end reply, reply 'your axe burns now'" in back
    assert [c["call_id"] for c in (first, second)] == ["#5 s1", "#5 s2"]
    assert second["prompt"] == "\nSTEP 2 of at most 10.\n"
    assert k.vfg.said() == [["your axe burns now"]]     # once: step 1's ending never went out
    assert k.vfg.rows() == [("action -", "gave KrownJM's axe Fire Aspect 5 by item modifier; /enchant refuses a "
                                         "level over the maximum")]


async def test_a_refused_follow_up_comes_back_as_a_result_and_the_next_step_names_the_right_task(kit):
    k = kit(Step=[step(end="handoff", reply="making it bigger",
                       follow_ups=(follow_up("fireball_krown", "make the blast twice as big",
                                             "A Hot Fireball for KrownJM, twice the blast."),)),
                  step(end="handoff", reply="making it bigger",
                       follow_ups=(follow_up("hot_fireball_krown", "make the blast twice as big",
                                             "A Hot Fireball for KrownJM, twice the blast."),))])
    k.tasks.open("hot_fireball_krown", "KrownJM", "hot fireball", "the brief", "A Hot Fireball for KrownJM.")
    k.brain.feed(line(5, "KrownJM", "make my fireball bigger"))
    await k.settle()
    back = k.ask.of("Step")[1]["cached"][-1][0]
    assert "- follow up fireball_krown\n  REFUSED: there is no task fireball_krown under TASKS" in back
    assert k.impl.followed == [("hot_fireball_krown", "make the blast twice as big", None,
                                "A Hot Fireball for KrownJM, twice the blast.")]
    assert k.vfg.said() == [["making it bigger", "--task", "hot_fireball_krown"]]


async def test_read_items_comes_back_in_the_next_step_which_then_acts_on_it(kit):
    alex = {**STATE["online"][0], "name": "Alex",
            "items": ["Alex has the following entity data: [{Slot: 2b, id: \"minecraft:bow\", count: 1}]",
                      "Alex has the following entity data: 0"]}
    k = kit(Step=[step(end="next", read=("Alex",)),
                  step(act("command", "Power V on Alex's bow", cmd(
                      'item modify entity Alex hotbar.2 {type:"minecraft:set_enchantments",'
                      'enchantments:{"minecraft:power":5},add:true}')), reply="Alex's bow has Power V",
                       record="put Power V on Alex's bow, hotbar.2")])
    k.vfg.on("state", answer=VfgResult(0, json.dumps({**STATE, "online": [STATE["online"][0], alex]}), ""))
    k.brain.feed(line(5, text="put power 5 on Alex's bow, Alex said yes"))
    await k.settle()
    first, second = k.ask.of("Step")
    assert "Steve's items, by slot" in first["cached"][-1][0] and "Alex's items" not in first["cached"][-1][0]
    assert "- read Alex's items\n  READ: Alex's items, by slot: hotbar.2 bow" in second["cached"][-1][0]
    assert k.vfg.said() == [["Alex's bow has Power V"]] and len(k.vfg.of("action", "run")) == 1


async def test_a_brains_hand_off_opens_its_task_with_the_brief_and_starts_its_implementer(kit):
    k = kit(Step=step(end="handoff", hand_offs=(hand_off(),)), Response=answer("On it!"))
    sent_first = []
    k.impl.on_start = lambda name: sent_first.append(bool(k.written(name)))
    k.brain.feed(line(5, "KrownJM", "Can you make an incantation? That fires a fireball whenever I say Hot fireball"))
    await k.settle()
    [(name, brief)] = k.impl.started
    assert name == "hot_fireball_krown" and sent_first == [True]  # the row went out first
    assert brief.startswith("THE PLAYER'S WORDS\n“Can you make an incantation?")
    assert "\n\nDONE WHEN\n- saying it" in brief
    t = k.tasks.get(name)
    assert (t["brief"], t["player"], t["line"], t["status"]) == (brief, "KrownJM", "Can you make an incantation? "
                                                                 "That fires a fireball whenever I say Hot fireball",
                                                                 "open")
    assert k.vfg.rows("task") == [("task hot_fireball_krown", "new: Hot Fireball for KrownJM: say hot fireball.")]
    assert k.vfg.said() == [["On it!"]]                  # the agent's line; the brain wrote none after it
    assert k.vfg.rows("action") == []                     # a lone hand-off: its task row says it
    assert k.impl.running() == [name]


async def test_a_brains_follow_up_resumes_its_implementer_and_a_cancel_sends_the_cancel_words(kit):
    k = kit(Step=[step(end="handoff", reply="making it pierce", follow_ups=(
                      follow_up("galick_gun_styrk", "make the beam pierce blocks", "A Galick Gun that pierces."),)),
                  step(end="handoff", reply="ok, putting it back", follow_ups=(
                      follow_up("galick_gun_styrk", cancel=True),))])
    k.tasks.open("galick_gun_styrk", "Styrkaeopteryx", "a galick gun", "the brief", "A Galick Gun for Styrk.")
    k.tasks.set_session("galick_gun_styrk", "sess-1")
    k.tasks.close("galick_gun_styrk", "done", "galick_gun_styrk done")
    k.brain.feed(line(5, "Styrkaeopteryx", "make it pierce blocks"))
    await k.settle()
    k.brain.feed(line(6, "Styrkaeopteryx", "never mind, put it back"))
    await k.settle()
    assert k.impl.followed == [("galick_gun_styrk", "make the beam pierce blocks", "sess-1",
                                "A Galick Gun that pierces."),
                               ("galick_gun_styrk", CANCEL, "sess-1", "A Galick Gun that pierces.")]
    t = k.tasks.get("galick_gun_styrk")
    assert (t["status"], t["brief"], t["description"]) == ("open", CANCEL, "A Galick Gun that pierces.")


async def test_a_silent_ending_says_nothing_and_still_leaves_its_record(kit):
    k = kit(Step=step(act("teleport", "teleport Steve home", {"player": "Steve", "to": "place", "target": "home"}),
                      act("command", "make it day", cmd("time set day")), order="in_turn", end="silent",
                      record="took Steve home and set the time to day"))
    k.brain.feed(line(5, text="home, then make it day"))
    await k.settle()
    assert [a[2] for a in k.vfg.of("action", "run")] == ["teleport", "command"]
    assert k.vfg.said() == [] and k.vfg.rows() == [("action -", "took Steve home and set the time to day")]


async def test_the_tenth_step_of_a_one_line_loop_is_its_last_it_runs_nothing_and_must_end(kit, capsys):
    more = step(act("command", "give Steve bread", cmd("give Steve bread")), end="next")
    # the last step's answer writes the action once more: dropped, never sent back, its reply kept (live #115)
    k = kit(Step=[more] * 9 + [step(act("command", "give Steve bread", cmd("give Steve bread")),
                                    reply="that took too long, sorry: you got 9 bread")])
    k.brain.feed(line(5, text="keep giving me bread"))
    await k.settle()
    steps = k.ask.of("Step")
    assert len(steps) == 10 and steps[-1]["call_id"] == "#5 s10"
    assert "THIS IS THE LAST STEP (this is step 10)" in steps[-1]["prompt"] and "LAST" not in steps[-2]["prompt"]
    with pytest.raises(ValidationError, match="this is the last step"):
        steps[-1]["cls"].model_validate_json(json.dumps(more))  # `next` with no reply to end on: sent back
    assert len(k.vfg.of("action", "run")) == 9
    steps[-2]["cls"].model_validate_json(json.dumps(more))  # the step before it could still run one
    assert k.vfg.said() == [["that took too long, sorry: you got 9 bread"]]
    assert k.vfg.rows() == [("action -", "; ".join(["give Steve bread — done"] * 9))]  # written in code
    out = capsys.readouterr().out
    assert "loop #5 Steve: 10 step(s)" in out and "L1 saved, 10 of 10 steps" in out  # saved: a line adds 10 more


async def test_a_step_past_45_s_lands_a_loop_has_no_time_limit(kit):
    async def slow(prompt):
        k.tick.t += 50                                      # each call takes 50 s, past the old 45 s timeout
        return answers.pop(0)

    answers = [step(act("command", "give Steve bread", cmd("give Steve bread")), end="next")] * 2 + \
              [step(reply="there, three loaves")]
    k = kit(Step=slow)
    k.brain.feed(line(5, text="rain bread on me"))
    await k.settle()
    assert [c["prompt"] for c in k.ask.of("Step")] == [f"\nSTEP {n} of at most 10.\n" for n in (1, 2, 3)]
    assert k.vfg.said() == [["there, three loaves"]]
    assert config.load({}).timeout_s == 120.0              # its step's timeout, at low
    assert config.load({"VFG_BRAIN_REASONING": "off"}).timeout_s == 60.0  # with no reasoning


async def test_a_call_that_fails_mid_loop_tells_the_player_and_records_what_ran(kit, capsys):
    failed = LlmError("deepseek/deepseek-v4.1-flash twice failed to match Step:\n- actions.0: Value error, the "
                      "incantation_create action 'juicy steak': incantation_create has no value commands; "
                      "its values: player, phrase, action, values, cooldown_s")
    failed.cost, failed.ms, failed.prompt_tokens, failed.cached_tokens, failed.completion_tokens = \
        0.004, 9400, 18000, 0, 900
    k = kit(Step=[step(act("command", "give Steve 64 steak", cmd("give Steve minecraft:cooked_beef 64")), end="next"),
                  failed])
    k.brain.feed(line(7, text="make juicy steak give me 64 steak"))
    await k.settle()
    # an answer that failed its checks twice: said plainly, never the check's words (the log keeps them)
    assert k.vfg.said() == [["Steve: done: give Steve 64 steak — then I got tangled up on the rest, say it again?"]]
    assert k.vfg.rows() == [("action -", "give Steve 64 steak — done")]
    out = capsys.readouterr().out
    assert "brain #7 s2 FAILED (9400 ms $0.004000, 18000 in, 0% cached, 900 out): deepseek/" in out
    assert "loop #7 Steve: 2 step(s), $0.006000, 5% cached" in out and "ended failed; L1 saved" in out
    assert {tuple(c) for c in k.vfg.of("usage")} == {("usage", "flush")}  # each call's row sent, the failed one's too
    assert loop.errored("Alex", LlmError("no answer from m within 30 s")) == \
        "Alex: that errored out — no answer from m within 30 s"   # a timeout keeps its short reason
    assert loop.errored("Alex", LlmError("m twice failed to match Step:\n- (the answer): Value error, the reply "
                                         "promises work and this step starts nothing for it")) == \
        "Alex: sorry, I got tangled up on that one — say it again?"
    assert loop.errored("", LlmError("no answer")) == "that errored out — no answer"  # a report's task with no player
    long = loop.errored("Alex", LlmError("x" * 300), ["give Alex 64 rockets " * 10])
    assert len(long) == 200 and long.startswith("Alex: done: give Alex 64 rockets") and "then that errored out" in long
    cut = LlmError("no answer from m within 30 s")  # an attempt in flight when it timed out: billed, cost unknown
    cut.cost, cut.cost_known, cut.ms = 0.0012, False, 30000
    assert loop.spent(cut) == "30000 ms $0.001200+, 0 in, 0 out"


async def test_a_line_added_while_a_call_fails_is_still_read_in_one_more_step(kit):
    async def first(prompt):
        k.brain.feed(line(7, text="and a shield too", hms="18:30:01"))
        await until(lambda: k.brain.loops.get("L1").pending)
        raise LlmError("no answer from deepseek within 60 s")

    k = kit(Step=[first, step(act("command", "give Steve a shield", cmd("give Steve shield")), reply="shield's yours")],
            Response=answer(), Route=[route(), route("loop", loop="L1")])
    k.brain.feed(line(5, text="give me a diamond pickaxe"))
    await k.settle()
    second = k.ask.of("Step")[1]
    assert second["cached"][-2][0] == ("THE LOOP SO FAR (your earlier steps for this event: what each ran and read, "
                                       "what it meant to end with, and what each tool came to; nothing of them "
                                       "reached the players unless it says so):\n\nSTEP 1:\n- your answer to it "
                                       "failed (no answer from deepseek within 60 s), so nothing of it ran\n"
                                       "SENT INSTEAD: “Steve: that errored out — no answer from deepseek within "
                                       "60 s”\n\n")
    assert second["cached"][-1][0].startswith("LINE ADDED TO THIS LOOP: LINE #7")
    assert k.vfg.said()[-1] == ["shield's yours"]


async def test_each_steps_prompt_is_the_next_steps_cached_prefix_at_most_four_marks(kit):
    convo = rows(*(line(i, "Alex", f"line {i}", hms=f"18:{i:02d}:00") for i in range(1, 25)), processed=True)
    more = step(act("command", "give Steve bread", cmd("give Steve bread")), end="next")
    k = kit(convo, Step=[more, more, step(reply="three loaves")])
    k.brain.feed(line(30, text="bread, bread, bread"))
    await k.settle()
    steps = k.ask.of("Step")
    for c in steps:
        marks = [i for i, (_, m) in enumerate(c["cached"]) if m]
        assert len(marks) <= 4 and marks[0] == 0 and marks[-1] == len(c["cached"]) - 1  # the opening; the end
        assert len(marks) == 4                                # two conversation checkpoints between them
    for before, after in zip(steps, steps[1:]):
        texts = [t for t, _ in before["cached"]]
        assert [t for t, _ in after["cached"]][:len(texts)] == texts  # step n's whole prompt but its trailer
        assert len(after["cached"]) == len(texts) + 1                 # and one block more: step n's results
    assert [c["prompt"] for c in steps] == [f"\nSTEP {n} of at most 10.\n" for n in (1, 2, 3)]
    assert steps[0]["cached"][-1][0].startswith("\nTHIS SERVER'S OWN ACTIONS")  # step 1's end mark: the tail


async def test_a_reply_written_up_front_never_claims_a_change_that_changed_nothing(kit):
    k = kit(Step=[step(act("edit", "no cooldown on Steve's railgun", {"copy": "railgun_steve", "values.cooldown": 0}),
                       reply="fixed: no more cooldown"),
                  step(reply="your railgun had no cooldown already", record="Steve's railgun: cooldown already 0")])
    k.running({"railgun_steve": {"action": "railgun", "values": {"player": "Steve", "cooldown": 0},
                                 "live": ["cooldown"], "task": None}})
    k.vfg.on("action", "run", "edit", answer=ran(True, "railgun_steve: nothing changed", copy="railgun_steve",
                                                 changed=[]))
    k.brain.feed(line(5, text="take the cooldown off my railgun"))
    await k.settle()
    assert "- edit: no cooldown on Steve's railgun copy=railgun_steve cooldown=0\n  CHANGED NOTHING: " \
           "railgun_steve: nothing changed" in k.ask.of("Step")[1]["cached"][-1][0]
    assert k.vfg.said() == [["your railgun had no cooldown already"]]  # never "fixed"


async def test_a_reply_that_promises_work_while_the_step_starts_nothing_is_sent_back(kit):
    k = kit(Step=step(reply="hey KrownJM, good to see you"))
    k.brain.feed(line(5, "KrownJM", "So that actually explodes on hit."))
    await k.settle()
    output = k.ask.of("Step")[0]["cls"]                     # the class the step's answer was checked against
    with pytest.raises(ValidationError, match="the reply promises work and this step starts nothing for it"):
        output.model_validate_json(json.dumps(step(reply="Got it, making sure it explodes right on impact.")))
    output.model_validate_json(json.dumps(step(end="handoff", reply="Got it, making sure it explodes right on impact.",
                                               follow_ups=(follow_up("hot_fireball_krown", "explode on hit"),))))


async def test_a_hand_off_that_would_not_open_says_what_landed_in_the_same_step(kit):
    k = kit(Step=step(PICKAXE, end="handoff", reply="pickaxe's yours, and a hook coming up",
                      hand_offs=(hand_off(words="a grappling hook"),)))
    k.acts.open_task = lambda h, **kw: None
    k.brain.feed(line(5, text="a pickaxe and a grappling hook"))
    await k.settle()
    assert k.vfg.said() == [["done: give Steve a diamond pickaxe — but I couldn't start “a grappling hook” — say it "
                             "again?"]]


async def test_a_brains_hand_off_whose_task_would_not_open_is_never_promised(kit, capsys):
    k = kit(Step=step(end="handoff", reply="on it, a fireball on your words soon", hand_offs=(hand_off(),)))
    k.acts.open_task = lambda h, **kw: None
    k.brain.feed(line(5, "KrownJM", "a hot fireball incantation"))
    await k.settle()
    assert k.impl.started == []
    words = "“Can you make an incantation? That fires a fireball whenever…”"  # the player's words, cut short
    assert k.vfg.said() == [[f"sorry, I couldn't start {words} — say it again?"]]
    assert k.vfg.rows() == [("action -", f"could not start {words}")]
    assert "1 hand-off(s) did not open" in capsys.readouterr().out


async def test_something_unexpected_mid_loop_still_records_what_earlier_steps_did(kit):
    k = kit(Step=[step(PICKAXE, end="next"), step(act("command", "give Steve bread", cmd("give Steve bread")),
                                                  reply="there")])
    runs = k.acts.run

    async def breaks(s, **kw):
        if kw["n"] == 2:
            raise RuntimeError("the runner broke")
        return await runs(s, **kw)

    k.acts.run = breaks
    k.brain.feed(line(5))
    await k.settle()
    assert k.vfg.rows() == [("action -", "give Steve a diamond pickaxe — done")]  # step 1's, then it raised on
    # what landed, then the error, as a failed call says it; said once (the spawn guard says nothing more)
    assert k.vfg.said() == [["Steve: done: give Steve a diamond pickaxe — then that errored out: the runner broke"]]
    assert k.brain.loops.all() == []                         # deleted: a broken loop takes no line


async def test_a_line_whose_loop_breaks_tells_the_player_it_errored_out(kit):
    k = kit(Step=GIVE)

    async def broken(*a, **kw):
        raise RuntimeError("the world broke")
    k.brain.world.seen = broken
    k.brain.feed(line(7))
    await k.settle()
    assert k.vfg.said() == [["Steve: that errored out — the world broke"]] and k.brain.loops.all() == []


# ------------------------------------------------------------------ reports

GUN = '''
    from actions._types import Action, Field, Live, Players


    class GalickGunStyrk(Action):
        """Give a player a Galick Gun: hold right-click to charge, release to fire a beam."""

        script = "galick_gun_styrk"
        runs = "until_stopped"
        takes = ["player", "cooldown"]

        player: Players = "@a"
        """who gets one"""
        cooldown: Live[float] = Field(5, ge=0, le=60)
        """seconds between shots"""
'''
MADE = {"scripts": ["galick_gun_styrk"], "actions": ["galick_gun_styrk"], "presets": [],
        "copies": ["galick_gun_styrk"]}


def fake_variables(monkeypatch, seen: list | None = None):
    """vfg_actions.variables() as the tasks read them: each field's name."""
    def variables(cls):
        if seen is not None:
            seen.append(cls.__name__)
        return [{"name": k, "fixed": False, "default": None, "live": False} for k in cls.model_fields
                if k in cls.takes]

    monkeypatch.setattr(vfg_actions, "variables", variables, raising=False)


def gun(k, words: str = "make me a galick gun") -> str:
    k.tasks.open("galick_gun_styrk", "Styrkaeopteryx", words, "THE PLAYER'S WORDS\nmake me a galick gun",
                 "A Galick Gun for Styrkaeopteryx: charge and fire.")
    return "galick_gun_styrk"


async def test_a_report_takes_its_description_made_and_variables_and_closes_with_one_record_and_one_line(
        kit, monkeypatch):
    fake_variables(monkeypatch)
    k = kit(rows("GAME#1 18:29:50 death Steve | Steve fell from a high place"), cat=made("galick_gun_styrk", GUN),
            ReportAnswer=report_answer("your galick gun's ready: hold right-click",
                                       record="galick_gun_styrk done: a Galick Gun, hold right-click to fire"))
    name = gun(k)
    k.impl.session = lambda name: "sess-1"
    await k.brain.on_report(name, "done", "Done: galick_gun_styrk is running as galick_gun_styrk.",
                            "A Galick Gun for Styrkaeopteryx: hold right-click 1.5 s, a 100-block beam.", MADE)
    await k.settle()
    [c] = k.ask.calls                                     # the conversation agent alone: nothing left to route
    assert (c["output"], c["component"], c["call_id"]) == ("ReportAnswer", "respond", "galick_gun_styrk")
    assert "WHAT IT IS: A Galick Gun for Styrkaeopteryx: hold right-click 1.5 s, a 100-block beam." in c["prompt"]
    assert "ITS REPORT:\nDone: galick_gun_styrk is running" in c["prompt"]
    assert "Steve fell from a high place" in c["prompt"].split("NEW SINCE")[1]  # a report reads NEW
    assert k.vfg.of("context", "mark") == [["context", "mark", "1"]]
    t = k.tasks.get(name)
    assert (t["status"], t["result"], t["session"]) == ("done", "galick_gun_styrk done: a Galick Gun, hold "
                                                                "right-click to fire", "sess-1")
    assert t["description"] == "A Galick Gun for Styrkaeopteryx: hold right-click 1.5 s, a 100-block beam."
    assert t["made"] == MADE and t["closed_at"]
    assert [v["name"] for v in t["variables"]] == ["player", "cooldown"]
    assert k.written(name)[-1] == t                       # every change written through, the last one whole
    assert k.vfg.rows() == [("result galick_gun_styrk", t["result"])]  # ONE record: no AI action row
    assert k.vfg.said() == [["your galick gun's ready: hold right-click", "--task", "galick_gun_styrk"]]

    k = kit(ReportAnswer=report_answer("ready", record="galick_gun_styrk done"))
    name = gun(k)
    await k.brain.on_report(name, "done", "Done.", "", None)  # no DESCRIPTION line: the one it had stays
    await k.settle()
    assert k.tasks.get(name)["description"] == "A Galick Gun for Styrkaeopteryx: charge and fire."


async def test_a_report_goes_to_the_conversation_agent_at_once_while_the_players_turn_is_busy(kit):
    held = Held(answer("On it!"))
    k = kit(Response=held, Step=GIVE, ReportAnswer=report_answer("your galick gun's ready", record="galick_gun_styrk "
                                                                                                   "done: a gun"))
    name = gun(k)
    k.brain.feed(line(5, "Styrkaeopteryx", "build me a castle"))
    await until(lambda: held.gates)
    assert k.brain.turns.busy("Styrkaeopteryx")
    await k.brain.on_report(name, "done", "Done: the gun is running.")
    await until(lambda: k.tasks.get(name)["status"] == "done")
    assert k.vfg.said() == [["your galick gun's ready", "--task", "galick_gun_styrk"]]
    assert k.brain.turns.busy("Styrkaeopteryx") and len(held.gates) == 1  # the player's turn still holds
    await held.go(0)
    await k.settle()


async def test_a_reports_hand_off_goes_to_the_router_which_follows_the_task_up_and_it_stays_open(kit):
    k = kit(ReportAnswer=report_answer("your galick gun's up; making it reach further",
                                       record="galick_gun_styrk done: a 100-block beam; 150 blocks asked",
                                       hand_off=True, left="the beam reaches 100 blocks, not the 150 asked"),
            Route=route("implementer", follow_ups=(follow_up("galick_gun_styrk", "the beam reaches 100 blocks; make it "
                                                            "reach 150", "A Galick Gun: a 150-block beam."),)))
    name = gun(k)
    await k.brain.on_report(name, "done", "Done: the beam reaches 100 blocks. DONE WHEN: 150 blocks — not landed.")
    await k.settle()
    assert calls(k) == ["ReportAnswer galick_gun_styrk", "Route galick_gun_styrk"]
    assert "WHAT IS LEFT, as the conversation agent read it: the beam reaches 100 blocks, not the 150 asked" in \
        k.ask.of("Route")[0]["prompt"]
    assert k.impl.followed == [("galick_gun_styrk", "the beam reaches 100 blocks; make it reach 150", None,
                                "A Galick Gun: a 150-block beam.")]
    t = k.tasks.get(name)
    assert (t["status"], t["closed_at"]) == ("open", None)  # its follow-up runs
    assert t["result"] == "galick_gun_styrk done: a 100-block beam; 150 blocks asked"
    assert k.vfg.rows() == [("result galick_gun_styrk", t["result"]),
                            ("task galick_gun_styrk", "follow-up: the beam reaches 100 blocks; make it reach 150")]
    assert k.vfg.said() == [["your galick gun's up; making it reach further", "--task", "galick_gun_styrk"]]


async def test_the_router_failing_on_a_report_follows_the_task_up_with_what_is_left(kit, capsys):
    k = kit(ReportAnswer=report_answer("your moat's in; the north side's coming", hand_off=True,
                                       record="moat_steve done: the moat, all but the north side",
                                       left="the north side of the moat is missing"),
            Route=LlmError("no answer within 30 s"))
    k.tasks.open("moat_steve", "Steve", "a moat round my castle", "the brief", "A moat round Steve's castle.")
    await k.brain.on_report("moat_steve", "done", "Done: the moat. DONE WHEN: the north side — missing.")
    await k.settle()
    assert k.impl.followed == [("moat_steve", "the north side of the moat is missing", None,
                                "A moat round Steve's castle.")]
    assert k.tasks.get("moat_steve")["status"] == "open"
    assert "router moat_steve FAILED (" in capsys.readouterr().out


async def test_a_reports_leftover_sent_to_a_new_loop_is_done_with_actions_and_the_task_closed_by_the_agent(kit):
    k = kit(ReportAnswer=report_answer("your galick gun's up; I'll fix the cooldown", hand_off=True,
                                       record="galick_gun_styrk done: a gun, its cooldown 5 s, 2 asked",
                                       left="a 2 s cooldown"),
            Route=route(reason="an edit of its running copy does it"),
            Step=step(act("edit", "a 2 s cooldown on Styrkaeopteryx's galick gun",
                          {"copy": "galick_gun_styrk", "values.cooldown": 2}),
                      reply="and the cooldown's 2 s now", record="galick_gun_styrk's cooldown set to 2 s"))
    name = gun(k)
    k.running({"galick_gun_styrk": {"action": "railgun", "values": {"player": "Styrkaeopteryx", "cooldown": 5},
                                    "live": ["cooldown"], "task": name}})
    await k.brain.on_report(name, "done", "Done: the gun is running, cooldown 5 s. DONE WHEN: a 2 s cooldown — not "
                                          "landed.")
    await k.settle()
    [s] = k.ask.of("Step")
    assert s["call_id"] == "galick_gun_styrk s1"
    assert ("WHAT IS LEFT: a 2 s cooldown\n(the router: an edit of its running copy does it)\n") in s["cached"][-1][0]
    assert "never close the task or write its result again" in s["cached"][-1][0]
    t = k.tasks.get(name)
    assert (t["status"], t["result"]) == ("done", "galick_gun_styrk done: a gun, its cooldown 5 s, 2 asked")
    assert k.vfg.rows() == [("result galick_gun_styrk", t["result"]),
                            ("action -", "galick_gun_styrk's cooldown set to 2 s")]  # the loop's own record
    assert k.vfg.said() == [["your galick gun's up; I'll fix the cooldown", "--task", "galick_gun_styrk"],
                            ["and the cooldown's 2 s now", "--task", "galick_gun_styrk"]]


async def test_a_reports_leftover_placed_nowhere_closes_the_task_with_the_routers_line(kit):
    k = kit(ReportAnswer=report_answer("your castle's up", hand_off=True, record="castle_steve done: a castle",
                                       left="the flag"),
            Route=route("nowhere", reply="The flag was never asked for, so you're all set."))
    k.tasks.open("castle_steve", "Steve", "build me a castle", "the brief", "A castle for Steve.")
    await k.brain.on_report("castle_steve", "done", "Done: the castle. A flag would be nice.")
    await k.settle()
    t = k.tasks.get("castle_steve")
    assert (t["status"], t["result"]) == ("done", "castle_steve done: a castle") and k.impl.followed == []
    assert k.vfg.said() == [["your castle's up", "--task", "castle_steve"],
                            ["The flag was never asked for, so you're all set.", "--task", "castle_steve"]]


async def test_a_report_while_a_follow_up_to_its_task_runs_keeps_the_task_open(kit):
    k = kit(ReportAnswer=report_answer("the gun's on; making it pierce now",
                                       record="galick_gun_styrk: a Galick Gun, piercing next"))
    name = gun(k)
    k.impl.busy = [name]  # the follow-up queued behind this run has started
    await k.brain.on_report(name, "done", "Done: the gun is running.")
    await k.settle()
    assert "A FOLLOW-UP to galick_gun_styrk is already under way" in k.ask.calls[0]["prompt"]
    t = k.tasks.get(name)
    assert (t["status"], t["result"], t["closed_at"]) == ("open", "galick_gun_styrk: a Galick Gun, piercing next", None)
    assert k.vfg.rows("result") == [("result galick_gun_styrk", "galick_gun_styrk: a Galick Gun, piercing next")]


async def test_a_report_gets_a_record_and_a_chat_line_written_in_code_its_line_never_its_description(kit):
    k = kit(ReportAnswer=LlmError("no answer"))           # the agent failing: closed in code
    k.tasks.open("ice_trail_steve", "Steve", "make me an ice trail", "THE PLAYER'S WORDS\n…",
                 "Ice Trail for Steve: SECRET DETAILS")
    await k.brain.on_report("ice_trail_steve", "failed", "Failed: vfg script check said line 40 …")
    await k.settle()
    k.ask.answers["ReportAnswer"] = report_answer("", record="")  # an empty line and record: written in code
    k.tasks.open("ice_beam_alex", "Alex", "an ice beam", "", "")
    await k.brain.on_report("ice_beam_alex", "done", "Done: ice_beam_alex running as ice_beam_alex; I also …")
    await k.settle()
    assert k.vfg.rows("result") == [("result ice_trail_steve", "ice_trail_steve failed: Ice Trail for Steve: SECRET "
                                                               "DETAILS"),
                                    ("result ice_beam_alex", "ice_beam_alex done")]  # never the implementer's words
    assert k.vfg.said() == [["sorry, that one failed: make me an ice trail", "--task", "ice_trail_steve"],
                            ["done: an ice beam", "--task", "ice_beam_alex"]]  # the player's own words
    assert "SECRET" not in repr(k.vfg.said())


async def test_an_unexpected_error_never_leaves_a_task_open_and_the_player_hears_so(kit):
    async def broken(argv):
        raise RuntimeError("the runner broke")

    async def blind():
        raise RuntimeError("no state")

    k = kit(Step=GIVE, ReportAnswer=report_answer("ready", record="x"))
    k.vfg.on("action", "run", answer=broken)
    k.brain.feed(ASK)                                     # the line's action: its run raises
    await k.settle()
    name = gun(k)
    k.world.glance = blind                                # the report: the world cannot be read
    await k.brain.on_report(name, "done", "Done: the gun is running.")
    await k.settle()
    t = k.tasks.get(name)
    assert (t["status"], t["result"]) == ("done", "galick_gun_styrk done: A Galick Gun for Styrkaeopteryx: charge "
                                                  "and fire.")
    assert k.vfg.rows("result") == [("result galick_gun_styrk", t["result"])]  # recorded once
    assert k.vfg.said() == [["Steve: that errored out — the runner broke"],
                            ["done: make me a galick gun", "--task", "galick_gun_styrk"]]


def test_a_tasks_variables_are_its_actions_as_vfg_actions_reads_them(monkeypatch):
    fake_variables(monkeypatch)
    cat = made("galick_gun_styrk", GUN)
    assert [v["name"] for v in variables_of(cat, "galick_gun_styrk")] == ["player", "cooldown"]
    assert variables_of(cat, "no_such_action") is None


async def test_a_changed_action_file_refreshes_its_tasks_variables_at_any_line(kit, monkeypatch):
    seen = []
    fake_variables(monkeypatch, seen)
    k = kit(cat=made("galick_gun_styrk", GUN), Response=answer("anytime!", choice="reply", reason="thanks"))
    name = gun(k)
    k.tasks.merge_made(name, MADE)
    monkeypatch.setattr(loop.actions, "refresh", lambda catalog: ["galick_gun_styrk"])
    k.brain.feed(line(1, text="haha thanks AI"))
    await k.settle()
    assert seen == ["GalickGunStyrk"] and k.tasks.get(name)["variables"][1]["name"] == "cooldown"
    assert calls(k) == ["Response #1"]
    assert "galick_gun_styrk for Styrkaeopteryx, open: A Galick Gun" in k.ask.calls[0]["prompt"]  # TASKS, the agent's


async def test_the_brains_and_the_routers_tasks_and_running_now_are_in_full(kit):
    k = kit(Step=GIVE)
    gun(k)
    k.running({"railgun_steve": {"action": "railgun", "values": {"player": "Steve", "cooldown": 5},
                                 "live": ["cooldown"], "task": None}})
    k.brain.feed(ASK)
    await k.settle()
    for tail in (k.ask.of("Step")[0]["cached"][-1][0], k.ask.of("Route")[0]["prompt"]):
        assert "- galick_gun_styrk, for Styrkaeopteryx, open, 18:30:00: A Galick Gun for Styrkaeopteryx" in tail
        assert "  made: nothing yet; running now: none" in tail
        assert "- railgun_steve: railgun, the brain started it\n  live: " in tail and "cooldown=5 (0..60)" in tail
    assert "WORK UNDER WAY: no brain loops" in k.ask.of("Route")[0]["prompt"]


# ------------------------------------------------------------------ the watch

async def test_after_the_feed_restarts_the_context_is_read_again_and_the_new_feed_heard(kit, monkeypatch):
    monkeypatch.setattr(loop, "WATCH_AGAIN_S", 0.001)
    k = kit(rows("LINE#500 18:20:00 Alex voice context 0.9 - | on the old box", processed=True), Step=GIVE)
    k.brain.start = 500
    gun(k)                                                # in memory; the new box has none of it
    fresh = rows("SYS#1 18:29:00 | a new box", "GAME#2 18:29:30 join Steve | Steve joined the game")
    castle = {"name": "castle_steve", "description": "A castle.", "player": "Steve", "status": "done",
              "created_at": "2026-10-10T18:00:00.000Z", "updated_at": "2026-10-10T18:05:00.000Z"}

    async def load():
        return fresh, 2, [castle]

    k.brain.load, seen = load, []

    async def watch(after: int):
        seen.append(after)
        if after == 500:
            yield "SYS#0 18:29:40 | vfg watch: feed restarted — cursor reset"
            yield "SYS#1 18:29:00 | a new box"
        else:
            yield line(3)
        await asyncio.Event().wait()

    following = asyncio.create_task(k.brain.follow(watch))
    for _ in range(400):
        if k.vfg.said():
            break
        await asyncio.sleep(0.001)
    following.cancel()
    await k.settle()
    assert seen == [500, 2] and [r.seq for r in k.brain.ctx.rows] == [1, 2, 3]
    assert k.vfg.said() == [["there you go"]]
    assert [t["name"] for t in k.tasks.all()] == ["castle_steve", "galick_gun_styrk"]  # the read's added
    assert len(k.written("galick_gun_styrk")) == 2 and k.written("castle_steve") == []  # the memory's written back


async def test_there_is_no_batch_whatever_waits_unread_waits_for_the_next_call(kit):
    assert importlib.util.find_spec("brain.timer") is None
    assert importlib.util.find_spec("brain.orchestrator") is None and importlib.util.find_spec("brain.action_planner") \
        is None
    assert not hasattr(config.load({"VFG_BRAIN_BATCH_S": "60"}), "batch_s")
    assert "batch" not in PROMPT.read_text().lower()
    k = kit(Step=GIVE)
    k.brain.feed("GAME#1 18:29:50 death Alex | Alex tried to swim in lava")
    k.brain.feed("LINE#2 18:29:52 Alex voice context 0.7 - | ouch")
    await k.settle()
    assert k.ask.calls == [] and not hasattr(k.brain, "timer") and not hasattr(k.brain, "batch")
    assert [r.seq for r in k.ctx.unprocessed()] == [1]    # the game event waits for the next call; the context line not
    k.brain.feed(line(3))
    await k.settle()
    assert "Alex tried to swim in lava" in k.ask.of("Step")[0]["cached"][-1][0].split("NEW SINCE")[1]


async def test_the_agent_sees_each_players_place_and_state_but_no_items(kit):
    k = kit(Response=answer("anytime!", choice="reply", reason="thanks"))
    await k.world.fresh_state()                           # the agent never waits: the last state there is
    k.brain.feed(line(1, text="haha thanks AI"))
    await k.settle()
    prompt = k.ask.of("Response")[0]["prompt"]
    assert ("- Steve at 10 64 -20 in overworld: 14.5/20 health, 18/20 food, armor 12, level 5, survival; "
            "effects: speed II 0:30, night vision ∞\n") in prompt
    assert "items, by slot" not in prompt


async def test_a_burst_of_calls_sends_the_usage_queue_once_more_never_two_flushes_at_once(kit):
    """The box down: each `vfg usage flush` is an SSH attempt that waits out its timeout. One runs at a
    time; the calls that end meanwhile ask for one more, which sends every row they queued, once."""
    k = kit(Step=GIVE)
    down, running, most = asyncio.Event(), [0], [0]

    async def flush(argv):
        running[0] += 1
        most[0] = max(most[0], running[0])
        await down.wait()
        running[0] -= 1
        return VfgResult(4, "", "vfg: cannot reach EC2")

    k.vfg.on("usage", "flush", answer=flush)
    k.brain.feed(line(5, "P5"))
    await until(lambda: running[0])                       # the first call's flush, waiting on the box
    for seq in range(6, 10):                              # four more players, each their own turn
        k.brain.feed(line(seq, f"P{seq}"))
    while k.brain.inflight:
        await asyncio.gather(*k.brain.inflight)
    assert len(k.ask.of("Step")) == 5 and k.vfg.of("usage") == [["usage", "flush"]] and running[0] == 1
    down.set()
    await k.settle()
    assert k.vfg.of("usage") == [["usage", "flush"]] * 2 and most[0] == 1  # one more for the fourteen, after it
    k.brain.feed(line(10, "P10"))  # nothing running: a call's own flush, at once
    await k.settle()
    assert len(k.vfg.of("usage")) >= 3 and most[0] == 1


# ------------------------------------------------------------------ one turn per player

async def test_a_players_lines_take_turns_while_another_players_run_at_once(kit):
    held = Held(answer("haha", choice="reply"))
    k = kit(Response=held)
    assert k.brain.feed(line(5)) == "started"
    assert k.brain.feed(line(6, text="and a shield")) == "waiting"
    assert k.brain.feed(line(7, "Alex", "make it day")) == "started"
    await turns()
    assert calls(k) == ["Response #5", "Response #7"] and k.brain.turns.waiting("Steve") == ["and a shield"]
    await held.go(1)                                      # Alex's ends: Steve's second still waits for his first
    assert calls(k) == ["Response #5", "Response #7"]
    await held.go(0)
    await held.go(2)
    await k.settle()
    assert calls(k) == ["Response #5", "Response #7", "Response #6"] and not k.brain.turns.busy("Steve")


async def test_three_lines_wait_and_a_fourth_is_turned_away_by_a_private_message_naming_them(kit):
    held = Held(answer("haha", choice="reply"))
    k = kit(Response=held)
    k.brain.feed(line(5, text="build me a castle"))
    for seq, words in ((6, "a meteor staff"), (7, "and a shield"), (8, "and a bow")):
        assert k.brain.feed(line(seq, text=words)) == "waiting"
    assert k.brain.feed(line(9, text="and a horse")) == "turned away"
    await turns()
    text = turned_away_line(["a meteor staff", "and a shield", "and a bow"])
    assert text.startswith("Still on: “a meteor staff”, “and a shield”, “and a bow”. I can't take any more")
    assert k.vfg.said() == [[text, "--to", "Steve"]]       # to Steve alone
    assert ["context", "mark", "9"] in k.vfg.calls and k.ctx.rows[-1].processed  # dropped: never answered
    for i in range(4):
        await held.go(i)
    await k.settle()
    assert calls(k) == ["Response #5", "Response #6", "Response #7", "Response #8"]


async def test_a_turn_ends_once_the_rows_it_wrote_come_back_and_a_loops_rows_hold_no_turn(kit):
    k = kit(Response=answer("On it!"), Step=step(PICKAXE, reply="there you go", record="gave Steve a diamond pickaxe"))
    k.brain.settle_s = 30.0                                 # never reached: the rows come back
    says, wrote = k.acts.say, {}

    async def say(text, task="", **kw):                     # whose turn's rows each line is noted among
        wrote[text] = WROTE.get()
        await says(text, task, **kw)

    k.acts.say = say
    k.brain.feed(line(5))
    k.brain.feed(line(6, text="and a shield"))
    await until(lambda: k.vfg.of("feed", "post"))          # the loop's record went out, and #6 still waits
    assert wrote["On it!"] is not None and wrote["there you go"] is None  # the turn's, then the loop's: nobody's
    assert calls(k, "Response") == ["Response #5"] and ["On it!"] in k.vfg.said()
    k.brain.feed("AI#7 18:30:01 say - | On it!")            # the front's one row back: its turn ends
    await turns(40)
    assert calls(k, "Response") == ["Response #5", "Response #6"]  # never waiting on the loop's reply or record
    assert all(key[1].startswith("onit") for key, _ in k.acts.echoes.waiting)  # the loop's rows: nobody waits


async def test_a_say_or_a_row_that_failed_keeps_no_turn_waiting_for_it(kit):
    k = kit(Response=answer("On it!"), Step=GIVE)
    k.brain.settle_s = 30.0                                 # never waited out: nothing will come back
    k.vfg.on("say", answer=VfgResult(4, "", "vfg: cannot reach EC2"))
    k.vfg.on("feed", "post", answer=VfgResult(4, "", "vfg: cannot reach EC2"))
    k.brain.feed(line(5))
    k.brain.feed(line(6, text="and a shield"))
    await turns(60)
    assert calls(k, "Response") == ["Response #5", "Response #6"]


async def test_a_line_or_its_wait_for_its_turn_is_logged(kit, capsys):
    held = Held(answer("haha", choice="reply"))
    k = kit(Response=held)
    k.brain.feed(line(5, "Styrkaeopteryx", "build me a castle"))
    k.brain.feed(line(6, "Styrkaeopteryx", "and a moat"))
    await turns()
    k.tick.t += 4.2                                          # the first turn holds 4.2 s
    for i in range(2):
        await held.go(i)
    await k.settle()
    out = capsys.readouterr().out
    assert "turn #6 Styrkaeopteryx waited 4.2 s for their turn" in out
    assert "turn #5 " not in out.split("waits for their turn")[0]  # the first took its turn at once: no wait logged


# ------------------------------------------------------------------ the stop

async def test_a_stop_drops_what_waits_forgets_the_saved_loops_and_a_report_meanwhile_closes_in_code(kit, capsys):
    held = Held(answer("haha", choice="reply"))
    k = kit(Response=[answer(), held], Step=GIVE)
    k.brain.feed(line(5))                                   # a loop that ends and is saved
    await k.settle()
    assert [x.state for x in k.brain.loops.all()] == ["ended"]
    k.ask.answers["Response"] = held
    k.brain.feed(line(6, text="lol"))
    await until(lambda: held.gates)
    k.brain.feed(line(7, text="and a shield"))              # waits for #6's turn
    stopping = asyncio.create_task(k.brain.stop(1.0))
    await turns()
    name = gun(k)
    await k.brain.on_report(name, "done", "Done: the gun is running.", "A Galick Gun.", MADE)
    t = k.tasks.get(name)                                   # closed in code, on the implementer's status
    assert (t["status"], t["result"], t["made"]) == ("done", "galick_gun_styrk done: A Galick Gun.", MADE)
    await held.go(0)                                        # the turn in flight finishes in the grace
    await stopping
    await k.settle()
    assert calls(k, "Response") == ["Response #5", "Response #6"] and k.brain.feed(line(9)) is None
    assert k.brain.loops.all() == [] and ["context", "mark", "7"] in k.vfg.calls
    out = capsys.readouterr().out
    assert "forgot 1 saved loop(s)" in out and "#7 Steve dropped at the stop, marked read: 'and a shield'" in out
    assert k.vfg.rows("result") == [("result galick_gun_styrk", "galick_gun_styrk done: A Galick Gun.")]


async def test_a_loop_a_front_turn_starts_during_the_stops_grace_is_waited_for_too(kit):
    held = Held(route())
    k = kit(Route=held, Step=GIVE)
    k.brain.feed(line(5))
    await until(lambda: held.gates)                         # the router is answering as the stop begins
    stopping = asyncio.create_task(k.brain.stop(1.0))
    await turns()
    await held.go(0)                                        # the turn places the line: a loop starts in the grace
    await stopping
    assert calls(k, "Step") == ["Step #5 s1"] and k.vfg.said() == [["there you go"]]
    assert k.brain.inflight == set() and k.brain.loops.all() == []  # waited for, then deleted: nothing saved


async def test_with_nothing_landed_a_step_may_not_end_silent_and_once_something_landed_it_may(kit):
    """Live run 4's helmet: step 1 refused, step 2 silent, and the player heard only "On it!"."""
    helmet = act("command", "Protection IV on Steve's helmet", cmd(
        'item modify entity Steve armor.head {type:"minecraft:set_enchantments",'
        'enchantments:{"minecraft:protection":4},add:true}'))
    k = kit(Response=answer("On it!"), Step=[step(helmet, reply="your helmet's tougher now"),
                                             step(reply="you've no helmet on, so nothing to enchant")])
    k.vfg.on("action", "run", answer=ran(False, "No item in slot armor.head"))
    k.brain.feed(line(5, text="protection 4 on my helmet"))
    await k.settle()
    with pytest.raises(ValidationError, match="nothing ran: silent leaves the player with nothing"):
        k.ask.of("Step")[1]["cls"].model_validate_json(json.dumps(step(end="silent")))
    with pytest.raises(ValidationError, match="nothing ran: the reply says it is done"):
        k.ask.of("Step")[1]["cls"].model_validate_json(json.dumps(step(reply="There you go, your helmet is tougher.")))
    assert k.vfg.said()[-1] == ["you've no helmet on, so nothing to enchant"]

    k = kit(Step=[step(PICKAXE, end="next"), step(end="silent")])  # step 1 landed: silence is fine after it
    k.brain.feed(line(6))
    await k.settle()
    assert calls(k, "Step") == ["Step #6 s1", "Step #6 s2"] and k.vfg.said() == []


async def test_the_agents_promise_never_goes_out_behind_a_nowhere_that_says_nothing(kit):
    """The post-review live run: a fresh brain's first line, "give me 64 steak", was routed nowhere with no
    reply, the agent's held "On it, chef!" went out, and nothing was done."""
    held = Held(step(act("command", "give Steve 64 steak", cmd("give Steve minecraft:cooked_beef 64")),
                     reply="steak's yours"))
    k = kit(Response=[answer("On it, chef!"), answer("Still on it.")], Step=held, Route=[route(), route("nowhere")])
    k.brain.feed(line(5, text="give me 64 steak"))
    await until(lambda: held.gates)
    first = k.ask.of("Route")[0]["cls"]
    with pytest.raises(ValidationError, match="the player was told it's on its way: place the work"):
        first.model_validate_json(json.dumps(route("nowhere")))
    k.brain.feed(line(6, text="is it done yet?"))             # its loop thinks: "Still on it." is a status now
    await until(lambda: len(k.vfg.said()) == 2)               # placed nowhere, behind the agent's status
    await held.go(0)
    await k.settle()
    assert k.vfg.said() == [["On it, chef!"], ["Still on it."], ["steak's yours"]]


async def test_the_routers_nowhere_line_is_dropped_when_the_agent_already_said_it(kit, capsys):
    """The live run's #93: the agent said "Still on it, Steve: your boomerang axe, throw-and-return with
    fire on the hit." while its implementer built it, and the router, sending the line nowhere, said its
    own "Still building your boomerang axe — fire and all.": the same thing twice. Now: a status for a
    player with work under way (respond.promises(), `busy` read as front() already reads it) is never a
    promise, so the router's own line is said only when the agent said nothing, or its line promised work
    the router is not placing; otherwise it only repeats the agent's, and is dropped, logged."""
    held = Held(step(PICKAXE, reply="there you go"))
    k = kit(Response=[answer("On it!"), answer("Still on it, Steve: your boomerang axe, throw-and-return with "
                                               "fire on the hit.")],
            Step=held, Route=[route(), route("nowhere", reply="Still building your boomerang axe — fire and all.")])
    k.brain.feed(line(5, text="make me a boomerang axe"))
    await until(lambda: held.gates)                       # its loop thinks: Steve has work under way
    assert k.brain.loops.busy() == {"Steve"}
    k.brain.feed(line(6, text="hey can you make me that boomerang axe"))
    await until(lambda: len(k.ask.of("Route")) == 1)       # the router answered; let the rest of the turn run
    await turns()
    assert k.vfg.said() == [["On it!"], ["Still on it, Steve: your boomerang axe, throw-and-return with fire on "
                                         "the hit."]]      # the router's duplicate dropped, never said
    out = capsys.readouterr().out
    assert ("router #6 nowhere: its line dropped, the agent's already said it: 'Still on it, Steve: your "
            "boomerang axe, throw-and-return with fire on the hit.'") in out
    await held.go(0)
    await k.settle()
    assert k.vfg.said()[-1] == ["there you go"]


async def test_the_routers_nowhere_line_is_kept_when_it_is_the_only_one_or_a_promise(kit):
    """The router's own line to nowhere is kept whole, unmoved, when the agent said nothing (its reply
    was dropped or empty) or its line promised work while the player had none under way: dropping it
    there would leave the router's correction unsaid, or the player's "still on it" unanswered."""
    k = kit(Response=answer("", choice="do", reason="said to Steve, not the AI"),
            Route=route("nowhere", reply="Ha, that one's for Steve!"))
    k.brain.feed(line(5, text="haha nice one Steve"))
    await k.settle()
    assert k.vfg.said() == [["Ha, that one's for Steve!"]]      # the agent said nothing: its own line stands
    # "Still on it." with nobody busy reads as a promise (respond_bench.md): the router's own line, correcting
    # it, is kept beside it.
    k = kit(Response=answer("Still on it."), Route=route("nowhere", reply="Nothing's building — say it again?"))
    k.brain.feed(line(6, text="hows my thing coming along"))
    await k.settle()
    assert k.vfg.said() == [["Still on it."], ["Nothing's building — say it again?"]]
