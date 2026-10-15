"""The loops' registry: ids counted per run, the budget (10 for the first line, 10 more a line added,
never past 20), a line added to a thinking loop read next, an ended loop taken up again within 120 s
of its creation and gone after, deleted at the cap, WORK UNDER WAY in full for the router and brief
for the conversation agent, and the stop forgetting the saved ones. Pure: a clock moved by hand."""

from datetime import timedelta

from brain.call import Event, Prompt
from brain.context import parse
from brain.loops import DELETED, ENDED, THINKING, Loops

from conftest import NOW, Tick, line


def said(seq: int, text: str, who: str = "KrownJM", at: int = 0):
    """A line said to the AI, `at` seconds after 18:29:40."""
    t = NOW - timedelta(seconds=20) + timedelta(seconds=at)
    return parse(line(seq, who, text, hms=t.strftime("%H:%M:%S")), now=NOW)


def registry(tick: Tick | None = None) -> tuple[Loops, Tick]:
    tick = tick or Tick()
    return Loops(10, 20, 120.0, clock=tick, now=lambda: NOW - timedelta(seconds=19)), tick


def started(loops: Loops, seq: int = 58, text: str = "can you teleport me to Stirka", who: str = "KrownJM"):
    row = said(seq, text, who)
    return loops.new(Event("LINE", row, said="On it!"), who, row.text)


def ended(loops: Loops, loop, steps: int = 4, reply: str = "Teleporting you to Styrka!") -> None:
    """A run that ran `steps` steps and ended with `reply`, its prompt kept."""
    loop.prompt, loop.steps, loop.last_reply, loop.how = Prompt(head=[], tail=""), steps, reply, "reply"
    assert loops.settle(loop) is False


def test_a_new_loop_has_an_id_its_first_line_and_the_grant():
    loops, tick = registry()
    first, second = started(loops), started(loops, 59, "give me food", "Styrkaeopteryx")
    assert (first.id, second.id) == ("L1", "L2") and loops.get("L2") is second and loops.get(" L1 ") is first
    assert (first.state, first.limit, first.steps, [r.seq for r in first.lines]) == (THINKING, 10, 0, [58])
    assert first.created == tick.t and first.created_at == "18:29:41" and first.line == "can you teleport me to Stirka"
    assert loops.offered() == [first, second] and loops.busy() == {"KrownJM", "Styrkaeopteryx"}
    report = loops.new(Event("REPORT", task={"name": "galick_gun_styrk"}, left="the beam reaches 150"), "Styrk", "x")
    assert report.lines == [] and report.limit == 10 and report.words() == [
        "what galick_gun_styrk's report left: the beam reaches 150"]
    assert loops.add(report, said(90, "and make it blue", "Styrk", at=5)) == "read next"
    assert report.limit == 20                              # the report counts as its first line: the line gets its 10


def test_each_line_added_grants_ten_more_steps_never_past_twenty():
    loops, _ = registry()
    loop = started(loops)
    loop.steps = 3
    assert loops.add(loop, said(60, "teleport me to Stirkosaurus", at=10), said="Still on it.") == "read next"
    assert loop.limit == 20 and [a.row.seq for a in loop.pending] == [60] and loop.pending[0].said == "Still on it."
    assert loops.add(loop, said(63, "can you teleport me to Stirka?", at=32)) == "read next"
    assert loop.limit == 20 and [r.seq for r in loop.lines] == [58, 60, 63]  # the cap: the third line opens none
    loop.steps = 20                                     # its step in flight is the cap's: no step left to read a line
    assert loops.add(loop, said(64, "and again", at=40)) == "gone" and [r.seq for r in loop.lines] == [58, 60, 63]


def test_a_run_that_ends_with_a_line_pending_goes_on_else_it_is_saved_or_deleted_at_the_cap():
    loops, _ = registry()
    loop = started(loops)
    loop.prompt, loop.steps = Prompt(head=[], tail=""), 2
    loops.add(loop, said(60, "teleport me to Stirkosaurus", at=10))
    assert loops.settle(loop) is True and loop.state == THINKING  # one more step reads the line
    loop.pending.clear()
    assert loops.settle(loop) is False and loop.state == ENDED and loops.offered() == [loop]  # saved
    capped = started(loops, 70, "make me a flame aura")
    capped.prompt, capped.steps = Prompt(head=[], tail=""), 20
    assert loops.settle(capped) is False and capped.state == DELETED and loops.get(capped.id) is None
    broke = started(loops, 71, "a castle")                        # never got a prompt: nothing to take up
    assert loops.settle(broke) is False and broke.state == DELETED


def test_an_ended_loop_is_taken_up_within_120_s_of_its_creation_and_gone_after():
    loops, tick = registry()
    loop = started(loops)
    tick.t += 30
    ended(loops, loop)
    tick.t += 89                                          # 119 s after its creation
    assert loops.offered() == [loop]
    assert loops.add(loop, said(63, "can you teleport me to Stirka?", at=61)) == "taken up"
    assert loop.state == THINKING and loop.limit == 20 and [a.row.seq for a in loop.pending] == [63]
    late = started(loops, 80, "give me food", "Styrkaeopteryx")
    ended(loops, late, steps=2)
    tick.t += 120                                         # 120 s from its creation, however recently it ended
    assert loops.offered() == [loop]                      # the thinking one is offered however long it runs
    assert late.state == DELETED and loops.get(late.id) is None
    assert loops.add(late, said(81, "more food", "Styrkaeopteryx", at=200)) == "gone"


def test_an_ended_loop_at_the_cap_or_past_its_window_can_take_no_line():
    loops, tick = registry()
    loop = started(loops)
    ended(loops, loop, steps=19)
    assert loops.offered() == [loop]                      # one step left under the cap
    tick.t += 121
    assert loops.add(loop, said(60, "again", at=130)) == "gone" and loop.state == DELETED and loops.all() == []


def test_work_under_way_in_full_for_the_router():
    loops, tick = registry()
    assert loops.show(brief=False) == "WORK UNDER WAY: no brain loops"
    loop = started(loops)
    loop.steps = 2
    loops.add(loop, said(60, "teleport me to Stirkosaurus", at=10), said="Still on it.")
    food = started(loops, 34, "give me food", "Styrkaeopteryx")
    tick.t += 40
    ended(loops, food, steps=1, reply="Here's a stack of steak!")
    food.record = "gave Styrkaeopteryx 64 steak"
    tick.t += 12
    shown = loops.show(brief=False)
    assert shown.startswith("WORK UNDER WAY (every brain loop a line can be added to, every player's, oldest first; "
                            "implementers are under TASKS):\n")
    assert ("- L1, for KrownJM, thinking, step 2 of 20, started 18:29:41 (52 s ago); lines: #58 “can you teleport me "
            "to Stirka” 18:29:40, #60 “teleport me to Stirkosaurus” 18:29:50 (added, not read yet)") in shown
    assert ("- L2, for Styrkaeopteryx, ended, saved: can take a line for 68 s more; 1 of 10 steps used, started "
            "18:29:41 (52 s ago); lines: #34 “give me food” 18:29:40; did: gave Styrkaeopteryx 64 steak; last reply: "
            "“Here's a stack of steak!”") in shown


def test_work_under_way_brief_for_the_conversation_agent():
    loops, tick = registry()
    assert loops.show(brief=True) == "WORK UNDER WAY: nothing right now"
    loop = started(loops)
    loops.add(loop, said(60, "teleport me to Stirkosaurus", at=10))
    food = started(loops, 34, "give me food", "Styrkaeopteryx")
    ended(loops, food, steps=1, reply="Here's a stack of steak!")
    tick.t += 12
    assert loops.show(brief=True) == (
        "WORK UNDER WAY (what is being done for the players now, or was a moment ago):\n"
        "- KrownJM: “can you teleport me to Stirka”, then “teleport me to Stirkosaurus” — being done now (12 s)\n"
        "- Styrkaeopteryx: “give me food” — done a moment ago: “Here's a stack of steak!”")
    food.how, food.last_reply = "failed", "Styrkaeopteryx: that errored out — no answer"
    assert "— failed a moment ago: “Styrkaeopteryx: that errored out — no answer”" in loops.show(brief=True)
    food.how = "handoff"
    assert "“give me food” — passed on to be built a moment ago" in loops.show(brief=True)


def test_the_stop_forgets_the_saved_loops_and_a_loop_still_thinking_is_deleted_as_it_ends():
    loops, _ = registry()
    thinking, saved = started(loops), started(loops, 34, "give me food", "Styrkaeopteryx")
    ended(loops, saved)
    assert loops.close() == 1 and saved.state == DELETED and loops.all() == [thinking]
    assert loops.add(thinking, said(60, "again", at=10)) == "gone"
    thinking.prompt = Prompt(head=[], tail="")
    assert loops.settle(thinking) is False and thinking.state == DELETED and loops.all() == []
