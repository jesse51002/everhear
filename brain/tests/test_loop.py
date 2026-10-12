"""The brain: each row routed by its class, a context line no model is shown, Respond's hand-up and
the order it answers in, history, and the loop: one step when every action lands, a refusal read
and fixed in the next step, a read, a hand-off and its brief, a follow-up and its cancel, silence,
the step and time limits, a failed call, a report and what its task takes from it, the prompt's
cache marks, and one turn per player."""

import asyncio
import importlib.util
import json

import pytest
import vfg_actions
from pydantic import ValidationError
from vfg_llm import LlmError

from brain import config, loop
from brain.act import CANCEL
from brain.actions import variables_of
from brain.call import PROMPT
from brain.respond import Response, promises
from brain.tasks import Tasks
from brain.turns import turned_away_line
from brain.vfg import VfgResult

from conftest import STATE, act, cmd, follow_up, hand_off, made, ran, rows, step

ASK = "LINE#{seq} 18:29:58 Steve voice action_oriented 0.93 -8dB | give me a diamond pickaxe"
TALK = "LINE#{seq} 18:29:59 Steve voice conversational 0.88 - | haha thanks AI"
PICKAXE = act("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickaxe"))
GIVE = step(PICKAXE, reply="there you go")


def line(seq: int, who: str = "Steve", text: str = "give me a diamond pickaxe", cls: str = "action_oriented") -> str:
    return f"LINE#{seq} 18:29:58 {who} voice {cls} 0.9 - | {text}"


async def test_each_row_goes_where_its_class_says_and_a_read_marks_what_it_took(kit):
    k = kit(rows("GAME#1 18:29:40 join Alex | Alex joined the game", "AI#2 18:29:41 say - | hi"),
            Step=GIVE, Response={"reply": "anytime!", "hand_up": False, "reason": "thanks"})
    for text in ("GAME#3 18:29:50 death Alex | Alex tried to swim in lava", "SYS#0 18:29:51 | vfg watch: reconnected",
                 "LINE#4 18:29:52 Alex voice context 0.7 - | ouch, I'm by the lava", ASK.format(seq=5),
                 TALK.format(seq=6)):
        k.brain.feed(text)
        await asyncio.sleep(0)  # as `vfg watch` gives them: one line at a time
    await k.settle()
    assert [c["output"] for c in k.ask.calls] == ["Step", "Response"]  # Steve's second line waited its turn
    tail = k.ask.of("Step")[0]["cached"][-1][0]
    assert "Alex joined the game" in tail.split("NEW SINCE")[1] and "Alex tried to swim in lava" in tail
    for c in k.ask.calls:                  # the context line: in no prompt, cached or not, of any call
        assert "by the lava" not in c["prompt"] + "".join(text for text, _ in c["cached"])
    assert k.vfg.said() == [["there you go"], ["anytime!"]]
    # the context line marked as it came, nobody waiting; the line's read took the game events and the line
    assert k.vfg.of("context", "mark") == [["context", "mark", "4"], ["context", "mark", "1", "3", "5"],
                                           ["context", "mark", "6"]]
    # each call names its usage row, which structured() queues; the brain only sends the queue, per call
    assert [(c["component"], c["call_id"]) for c in k.ask.calls] == [("brain", "#5 s1"), ("respond", "#6")]
    assert k.vfg.of("usage", "add") == [] and k.vfg.of("usage") == [["usage", "flush"]] * 2
    assert k.ctx.unprocessed() == [] and k.ctx.last_seq == 6


async def test_respond_replies_or_hands_up_never_both_and_a_hand_up_is_the_brains_loop(kit):
    k = kit(Step=GIVE, Response={"reply": "sure thing!", "hand_up": True, "reason": "asks for rain"})
    k.brain.feed("LINE#7 18:29:59 Steve voice conversational 0.9 - | lol make it rain")
    await k.settle()
    assert [c["output"] for c in k.ask.calls] == ["Response", "Step"]
    assert "handed this line up to you: asks for rain" in k.ask.calls[1]["cached"][-1][0]
    assert k.vfg.said() == [["there you go"]]  # the brain's reply alone, never Respond's

    k = kit(Step=GIVE, Response=LlmError("no answer"))  # unsure, failing: handed up all the same
    k.brain.feed(TALK.format(seq=8))
    await k.settle()
    assert [c["output"] for c in k.ask.calls] == ["Response", "Step"]


async def test_respond_never_says_an_offer_or_a_promise_it_hands_the_line_up_instead(kit, capsys):
    k = kit(Step=GIVE, Response={"reason": "a question", "hand_up": False, "reply": "Want me to make it rain?"})
    k.brain.feed(TALK.format(seq=7))
    await k.settle()
    assert [c["output"] for c in k.ask.calls] == ["Response", "Step"]
    assert "handed this line up to you: a question · handed up in code: an offer" in k.ask.calls[1]["cached"][-1][0]
    assert k.vfg.said() == [["there you go"]]  # the brain's line, never the offer
    assert 'respond guard #7 an offer: "Want me to make it rain?"' in capsys.readouterr().out  # the log alone

    ack = "Got it, I'll ask you before giving Alex anything."  # a rule for later: said
    k = kit(Response={"reason": "a rule for later", "hand_up": False, "reply": ack})
    k.brain.feed(TALK.format(seq=8))
    await k.settle()
    assert [c["output"] for c in k.ask.calls] == ["Response"] and k.vfg.said() == [[ack]]


GUARD = {  # reply -> what promises() says, no task open (the reviewer's probes among them)
    "Sure thing, on it!": "a promise", "Aye, on it, captain!": "a promise", "Will do!": "a promise",
    "Consider it done.": "a promise", "Leave it to me!": "a promise", "I'm gonna make it rain!": "a promise",
    "I'm going to build that for you.": "a promise", "Okay, making it rain now!": "a promise",
    "I'll take care of it.": "a promise", "I'll make sure you get a netherite sword.": "a promise",
    "Aye, I'll be makin' it rain, matey!": "a promise", "One storm, coming right at ya!": "a promise",
    "Done! Enjoy the sword.": "a promise", "Sure thing, spawning some cows now.": "a promise",
    "I can do that.": "an offer", "Let me do that for you.": "an offer", "Want it stopped?": "an offer",
    "Yeah, I can add theme music. Want distinct tracks for each?": "an offer",
    "Put a sign on it and click on it.": "", "Aye aye! I could get used to this pirate life.": "",
    "Let me put it this way: smite only beats sharpness on undead.": "", "Will check with you first, promise.": "",
    "Want the recipe? Three glass, five obsidian and a nether star.": "",
    "You don't want me to spoil the ending, trust me.": "", "Good luck in the nether! Watch your back for ghasts.": "",
    "Hang on, you lost everything in lava? Rip.": "", "Night's coming up, find a bed soon.": "",
    "Grab some food on the way back.": "", "The sun will be up soon, hold out.": "",
    "I mean, that was a pretty good one, I'll give you that.": "", "Aye aye, Captain Riley.": "",
    # small talk and the AI's own talk: said, not handed up
    "I'm doing great, thanks for asking!": "", "We're doing great as a team.": "",
    "Getting dark now, better find shelter.": "", "Haha, I'm making no promises about the creepers.": "",
    "Done already? That was fast.": "", "Right away? Patience, my friend.": "",
    "Okay, I'll stop talking now.": "", "Sure, I'll keep quiet for a bit.": "", "Arr, I'll turn the chatter down.": "",
    "You're on the way to the nether, nice.": "", "Not right away, it takes a minute to learn.": "",
    "Steak, well-done!": "", "I can get why you'd think that.": "", "Your stuff is on the way back to spawn.": "",
    "Sure - on it.": "a promise", "Your sword's on the way.": "a promise", "I can get you one.": "an offer",
    # the brain's loop on the 10-11 replay: a yes, then the work; work promised for later
    "Got it, making sure it explodes right on impact.": "a promise",
    "Got it, making sure it reliably detonates right when it hits.": "a promise",
    "Yeah, 600ms latency feels noticeable in real-time play. We can look into smaller or streaming ASR models to "
    "trim that down.": "a promise",
    "Sure, adding a moat round your castle.": "a promise", "I'll dig into why it broke.": "a promise",
    "Yeah, doing great!": "", "Okay, getting dark out there, grab a bed.": "", "Got it, good to know.": "",
    "Fire Aspect's max is 2 on /enchant, but I can set 5": "an offer",
    # respond_bench's offers of a thing (7DZGBF), and live #45's banter about lava, which is not one
    "Glad you like it — the obsidian one's got your name on it, right?": "an offer",
    "That obsidian upgrade has your name on it.": "an offer",
    "It is, right? Don't get too comfy, lava's got your name on it next.": "",
}


def test_promises_catches_an_offer_or_a_promise_and_lets_talk_and_a_speakers_own_status_through():
    assert {reply: promises(reply, "TASKS: none yet") for reply in GUARD} == GUARD
    status = "castle's coming up, give it a sec"   # a status only while the speaker has a task open
    castle = {"name": "castle_steve", "description": "a castle", "player": "Steve", "status": "open",
              "line": "build me a castle", "created_at": "2026-10-08T18:22:31.000Z",
              "updated_at": "2026-10-08T18:22:31.000Z"}
    opened = Tasks(rows=[castle])                         # the new format: a named row, open
    closed = Tasks(rows=[{**castle, "status": "done", "closed_at": "2026-10-08T18:25:00.000Z"}])
    assert opened.show().endswith("\n- castle_steve for Steve, open: a castle")
    assert [bool(promises(status, tasks, who)) for tasks, who in (
        (opened.show(), "Steve"), (opened.show(["castle_steve"]), "Steve"), (opened.show(), "Alex"),
        (closed.show(), "Steve"),                         # all closed: a status is a promise
        ("TASKS: none yet", "Steve"))] == [False, False, True, True, True]


def test_the_brains_check_is_for_promises_alone_an_offer_is_a_question_it_may_ask():
    assert promises("Fire Aspect's max is 2 on /enchant, but I can set 5", offers=False) == ""
    assert promises("Want me to make it rain? Got it, making sure it rains", offers=False) == "a promise"


async def test_a_reply_that_promises_work_while_the_step_starts_nothing_is_sent_back(kit):
    k = kit(Step=step(reply="hey KrownJM, good to see you"))
    k.brain.feed(line(5, "KrownJM", "So that actually explodes on hit."))
    await k.settle()
    output = k.ask.of("Step")[0]["cls"]                     # the class the step's answer was checked against
    with pytest.raises(ValidationError, match="the reply promises work and this step starts nothing for it"):
        output.model_validate_json(json.dumps(step(reply="Got it, making sure it explodes right on impact.")))
    output.model_validate_json(json.dumps(step(end="handoff", reply="Got it, making sure it explodes right on impact.",
                                               follow_ups=(follow_up("hot_fireball_krown", "explode on hit"),))))
    k = kit(Step=step(reply="the gun's on; making it pierce now", status="done", record="x"))
    gun(k)
    k.impl.busy = ["galick_gun_styrk"]                      # a report told its follow-up already runs: a status
    await k.brain.on_report("galick_gun_styrk", "done", "Done: the gun is running.")
    await k.settle()
    assert k.vfg.said() == [["the gun's on; making it pierce now", "--task", "galick_gun_styrk"]]


def test_respond_decides_first_and_always_writes_its_reason():
    schema = Response.model_json_schema()               # what the model is shown, and writes in this order
    assert list(schema["properties"]) == ["reason", "hand_up", "reply"]
    assert schema["required"] == ["reason"] and schema["properties"]["hand_up"]["default"] is True


async def test_history_is_the_old_feed_at_the_start_and_a_line_on_the_watch_is_live_whatever_its_time(kit):
    k = kit(rows("LINE#8 18:29:50 Alex voice context 0.9 - | so hungry",
                 "LINE#9 18:20:00 Steve voice action_oriented 0.9 - | give me a diamond pickaxe"), Step=GIVE)
    k.brain.start = 9
    assert [r.seq for r in k.brain.catch_up()] == [9]      # ten minutes old as the brain starts: history
    await k.settle()                                      # and a context line unread, whatever its age, read
    assert k.ask.calls == [] and k.vfg.of("context", "mark") == [["context", "mark", "8", "9"]]
    # the box's clock 90 s ahead of the PC's: a line said now reads as yesterday's 18:31:30, and is live
    k.brain.feed("LINE#10 18:31:30 Steve voice action_oriented 0.9 - | give me a diamond pickaxe")
    await k.settle()
    assert len(k.ask.calls) == 1 and "LINE #10" in k.ask.calls[0]["cached"][-1][0].split("THE EVENT")[1]
    # after a drop, the backlog the watch catches up with is judged by its age, as a start's is
    k.brain.feed("SYS#0 18:30:00 | reconnected")
    k.brain.feed("LINE#11 18:21:00 Steve voice action_oriented 0.9 - | give me a diamond axe")
    await k.settle()
    assert len(k.ask.calls) == 1


# ------------------------------------------------------------------ the loop

async def test_an_ask_whose_actions_all_land_is_one_step_its_reply_said_and_one_record_written(kit, capsys):
    k = kit(Step=step(act("command", "give KrownJM a full set of netherite armor",
                          cmd("give KrownJM minecraft:netherite_helmet", "give KrownJM minecraft:netherite_boots")),
                      act("command", "give KrownJM 64 cobblestone", cmd("give KrownJM minecraft:cobblestone 64")),
                      reply="suited up, and 64 cobble", record="gave KrownJM netherite armor and 64 cobblestone"))
    k.brain.feed(line(5, "KrownJM", "give me netherite armor and 64 cobblestone"))
    await k.settle()
    assert len(k.ask.calls) == 1 and k.ask.calls[0]["call_id"] == "#5 s1"  # no second call
    assert len(k.vfg.of("action", "run")) == 2
    assert k.vfg.said() == [["suited up, and 64 cobble"]]
    assert k.vfg.rows() == [("action -", "gave KrownJM netherite armor and 64 cobblestone")]  # ONE record
    assert k.vfg.calls.index(k.vfg.of("say")[0]) > k.vfg.calls.index(k.vfg.of("action", "run")[-1])
    out = capsys.readouterr().out
    assert "brain #5 s1 7 ms $0.002000, 1000 in, 90% cached, 40 out | together: command" in out
    assert "loop #5 KrownJM: 1 step(s), $0.002000, 90% cached, reply at 0.0 s, 0.0 s, ended reply" in out


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
    assert second["prompt"] == "\nSTEP 2 of at most 10; 0 s of 120 used.\n"
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


async def test_a_hand_off_opens_its_task_with_the_brief_starts_its_implementer_and_ends_the_turn(kit):
    k = kit(Step=step(end="handoff", reply="on it, a fireball on your words soon", hand_offs=(hand_off(),)))
    sent_first = []
    k.impl.on_start = lambda name: sent_first.append(bool(k.written(name)) and bool(k.vfg.said()))
    k.brain.feed(line(5, "KrownJM", "Can you make an incantation? That fires a fireball whenever I say Hot fireball"))
    await k.settle()
    [(name, brief)] = k.impl.started
    assert name == "hot_fireball_krown" and sent_first == [True]  # the row and the reply went out first
    assert brief.startswith("THE PLAYER'S WORDS\n“Can you make an incantation?")
    assert "\n\nDONE WHEN\n- saying it" in brief
    t = k.tasks.get(name)
    assert (t["brief"], t["player"], t["line"], t["status"]) == (brief, "KrownJM", "Can you make an incantation? "
                                                                 "That fires a fireball whenever I say Hot fireball",
                                                                 "open")
    assert k.vfg.rows("task") == [("task hot_fireball_krown", "new: Hot Fireball for KrownJM: say hot fireball.")]
    assert k.vfg.said() == [["on it, a fireball on your words soon", "--task", "hot_fireball_krown"]]
    assert k.vfg.rows("action") == []                     # a lone hand-off: its task row says it
    assert not k.brain.turns.busy("KrownJM") and k.impl.running() == [name]  # the turn ended; the implementer runs


async def test_a_follow_up_resumes_its_implementer_and_a_cancel_sends_the_cancel_words(kit):
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
    assert [text for _, text in k.vfg.rows("task")] == ["follow-up: make the beam pierce blocks",
                                                        "follow-up: never mind — put back what its last run built"]


async def test_a_silent_ending_says_nothing_and_still_leaves_its_record(kit):
    k = kit(Step=step(act("teleport", "teleport Steve home", {"player": "Steve", "to": "place", "target": "home"}),
                      act("command", "make it day", cmd("time set day")), order="in_turn", end="silent",
                      record="took Steve home and set the time to day"))
    k.brain.feed(line(5, text="home, then make it day"))
    await k.settle()
    assert [a[2] for a in k.vfg.of("action", "run")] == ["teleport", "command"]
    assert k.vfg.said() == [] and k.vfg.rows() == [("action -", "took Steve home and set the time to day")]


async def test_the_tenth_step_is_the_last_it_runs_nothing_and_must_end(kit, capsys):
    more = step(act("command", "give Steve bread", cmd("give Steve bread")), end="next")
    k = kit(Step=[more] * 9 + [step(reply="that took too long, sorry: you got 9 bread")])
    k.brain.feed(line(5, text="keep giving me bread"))
    await k.settle()
    calls = k.ask.of("Step")
    assert len(calls) == 10 and calls[-1]["call_id"] == "#5 s10"
    assert "THIS IS THE LAST STEP (this is step 10)" in calls[-1]["prompt"] and "LAST" not in calls[-2]["prompt"]
    try:
        calls[-1]["cls"].model_validate_json(json.dumps(more))
        raise AssertionError("the last step took an action")
    except ValidationError as e:
        assert "this is the last step" in str(e)
    calls[-2]["cls"].model_validate_json(json.dumps(more))  # the step before it could still run one
    assert k.vfg.said() == [["that took too long, sorry: you got 9 bread"]]
    assert k.vfg.rows() == [("action -", "; ".join(["give Steve bread — done"] * 9))]  # written in code
    assert "loop #5 Steve: 10 step(s)" in capsys.readouterr().out


async def test_a_step_that_starts_past_two_minutes_is_the_last(kit):
    async def slow(prompt):
        k.tick.t += 70                                     # each call takes 70 s, by the loop's clock
        return answers.pop(0)

    answers = [step(act("command", "give Steve bread", cmd("give Steve bread")), end="next")] * 2 + \
              [step(end="handoff", reply="handing the rest on", hand_offs=(hand_off("bread_rain_steve"),))]
    k = kit(Step=slow)
    k.brain.feed(line(5, text="rain bread on me"))
    await k.settle()
    trailers = [c["prompt"] for c in k.ask.of("Step")]
    assert trailers[1] == "\nSTEP 2 of at most 10; 70 s of 120 used.\n"
    assert trailers[2].startswith("\nSTEP 3 of at most 10; 140 s of 120 used.\nTHIS IS THE LAST STEP (120 s have gone)")
    assert [n for n, _ in k.impl.started] == ["bread_rain_steve"]


async def test_a_call_that_fails_mid_loop_tells_the_player_and_records_what_ran(kit, capsys):
    failed = LlmError("google/gemini-3.8-flash twice failed to match Step:\n- actions.0: Value error, the "
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
    assert "brain #7 s2 FAILED (9400 ms $0.004000, 18000 in, 0% cached, 900 out): google/" in out
    assert "loop #7 Steve: 2 step(s), $0.006000, 5% cached" in out and "ended failed" in out
    assert k.vfg.of("usage") == [["usage", "flush"]] * 2  # each step's row, sent, the failed one's too
    assert loop.errored("Alex", LlmError("no answer from m within 30 s")) == \
        "Alex: that errored out — no answer from m within 30 s"   # a timeout keeps its short reason
    assert loop.errored("Alex", LlmError("m twice failed to match Step:\n- (the answer): Value error, the reply "
                                         "promises work and this step starts nothing for it")) == \
        "Alex: sorry, I got tangled up on that one — say it again?"
    long = loop.errored("Alex", LlmError("x" * 300), ["give Alex 64 rockets " * 10])
    assert len(long) == 200 and long.startswith("Alex: done: give Alex 64 rockets") and "then that errored out" in long
    cut = LlmError("no answer from m within 30 s")  # an attempt in flight when it timed out: billed, cost unknown
    cut.cost, cut.cost_known, cut.ms = 0.0012, False, 30000
    assert loop.spent(cut) == "30000 ms $0.001200+, 0 in, 0 out"


async def test_each_steps_prompt_is_the_next_steps_cached_prefix_at_most_four_marks(kit):
    convo = rows(*(f"LINE#{i} 18:{i:02d}:00 Alex voice conversational 0.9 - | line {i}" for i in range(1, 25)),
                 processed=True)
    more = step(act("command", "give Steve bread", cmd("give Steve bread")), end="next")
    k = kit(convo, Step=[more, more, step(reply="three loaves")])
    k.brain.feed(line(30, text="bread, bread, bread"))
    await k.settle()
    calls = k.ask.of("Step")
    for c in calls:
        marks = [i for i, (_, m) in enumerate(c["cached"]) if m]
        assert len(marks) <= 4 and marks[0] == 0 and marks[-1] == len(c["cached"]) - 1  # the opening; the end
        assert len(marks) == 4                                # two conversation checkpoints between them
    for before, after in zip(calls, calls[1:]):
        texts = [t for t, _ in before["cached"]]
        assert [t for t, _ in after["cached"]][:len(texts)] == texts  # step n's whole prompt but its trailer
        assert len(after["cached"]) == len(texts) + 1                 # and one block more: step n's results
    assert [c["prompt"] for c in calls] == [f"\nSTEP {n} of at most 10; 0 s of 120 used.\n" for n in (1, 2, 3)]
    assert calls[0]["cached"][-1][0].startswith("\nTHIS SERVER'S OWN ACTIONS")  # step 1's end mark: the tail


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


def gun(k, line: str = "make me a galick gun") -> str:
    k.tasks.open("galick_gun_styrk", "Styrkaeopteryx", line, "THE PLAYER'S WORDS\nmake me a galick gun",
                 "A Galick Gun for Styrkaeopteryx: charge and fire.")
    return "galick_gun_styrk"


async def test_a_report_takes_its_description_made_and_variables_and_closes_with_one_record_and_one_line(
        kit, monkeypatch):
    fake_variables(monkeypatch)
    k = kit(rows("GAME#1 18:29:50 death Steve | Steve fell from a high place"), cat=made("galick_gun_styrk", GUN),
            Step=step(reply="your galick gun's ready: hold right-click", status="done",
                      record="galick_gun_styrk done: a Galick Gun, hold right-click to fire"))
    name = gun(k)
    k.impl.session = lambda name: "sess-1"
    await k.brain.on_report(name, "done", "Done: galick_gun_styrk is running as galick_gun_styrk.",
                            "A Galick Gun for Styrkaeopteryx: hold right-click 1.5 s, a 100-block beam.", MADE)
    await k.settle()
    [c] = k.ask.calls
    tail = c["cached"][-1][0]
    assert c["call_id"] == "galick_gun_styrk s1"
    assert "REPORT: the implementer of galick_gun_styrk ended, done." in tail
    assert "WHAT IT IS: A Galick Gun for Styrkaeopteryx: hold right-click 1.5 s, a 100-block beam." in tail
    assert "ITS REPORT:\nDone: galick_gun_styrk is running" in tail
    assert "Steve fell from a high place" in tail.split("NEW SINCE")[1]  # a report reads NEW
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

    k = kit(Step=step(reply="ready", status="done", record="galick_gun_styrk done"))
    name = gun(k)
    await k.brain.on_report(name, "done", "Done.", "", None)  # no DESCRIPTION line: the one it had stays
    await k.settle()
    assert k.tasks.get(name)["description"] == "A Galick Gun for Styrkaeopteryx: charge and fire."


async def test_a_report_that_left_something_undone_follows_its_task_up_and_the_task_stays_open(kit):
    k = kit(Step=step(end="handoff", reply="your galick gun's up; making it reach further", status="done",
                      record="galick_gun_styrk done: a 100-block beam; 150 blocks asked, a follow-up on it",
                      follow_ups=(follow_up("galick_gun_styrk", "the beam reaches 100 blocks; make it reach 150",
                                            "A Galick Gun for Styrkaeopteryx: a 150-block beam."),)))
    name = gun(k)
    await k.brain.on_report(name, "done", "Done: the beam reaches 100 blocks. DONE WHEN: 150 blocks — not landed.")
    await k.settle()
    assert k.impl.followed == [("galick_gun_styrk", "the beam reaches 100 blocks; make it reach 150", None,
                                "A Galick Gun for Styrkaeopteryx: a 150-block beam.")]
    t = k.tasks.get(name)
    assert (t["status"], t["closed_at"]) == ("open", None)  # its follow-up runs
    assert t["result"] == "galick_gun_styrk done: a 100-block beam; 150 blocks asked, a follow-up on it"
    assert k.vfg.rows() == [("task galick_gun_styrk", "follow-up: the beam reaches 100 blocks; make it reach 150"),
                            ("result galick_gun_styrk", t["result"])]
    assert k.vfg.said() == [["your galick gun's up; making it reach further", "--task", "galick_gun_styrk"]]


async def test_a_report_while_a_follow_up_to_its_task_runs_keeps_the_task_open(kit):
    k = kit(Step=step(reply="the gun's on; making it pierce now", status="done",
                      record="galick_gun_styrk: a Galick Gun, piercing next"))
    name = gun(k)
    k.impl.busy = [name]  # the follow-up queued behind this run has started
    await k.brain.on_report(name, "done", "Done: the gun is running.")
    await k.settle()
    assert "A FOLLOW-UP to galick_gun_styrk is already under way" in k.ask.calls[0]["cached"][-1][0]
    t = k.tasks.get(name)
    assert (t["status"], t["result"], t["closed_at"]) == ("open", "galick_gun_styrk: a Galick Gun, piercing next", None)
    assert k.vfg.rows("result") == [("result galick_gun_styrk", "galick_gun_styrk: a Galick Gun, piercing next")]


async def test_a_report_gets_a_record_and_a_chat_line_written_in_code_its_line_never_its_description(kit):
    k = kit(Step=step(reply=""))                          # no reply, no record: invalid, so the call fails …
    k.ask.answers["Step"] = LlmError("no answer")         # … as a failed call does
    k.tasks.open("ice_trail_steve", "Steve", "make me an ice trail", "THE PLAYER'S WORDS\n…",
                 "Ice Trail for Steve: SECRET DETAILS")
    await k.brain.on_report("ice_trail_steve", "failed", "Failed: vfg script check said line 40 …")
    await k.settle()
    k.ask.answers["Step"] = step(reply="", status="done", end="silent")  # silent: a report's line still goes out
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

    async def blind(**kw):
        raise RuntimeError("no state")

    k = kit(Step=GIVE)
    k.vfg.on("action", "run", answer=broken)
    k.brain.feed(ASK.format(seq=5))                       # the line's action: its run raises
    await k.settle()
    name = gun(k)
    k.world.seen = blind                                  # the report: the world cannot be read
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


async def test_a_changed_action_file_refreshes_its_tasks_variables_at_any_call_respond_included(kit, monkeypatch):
    seen = []
    fake_variables(monkeypatch, seen)
    k = kit(cat=made("galick_gun_styrk", GUN), Response={"reply": "anytime!", "hand_up": False, "reason": "thanks"})
    name = gun(k)
    k.tasks.merge_made(name, MADE)
    monkeypatch.setattr(loop.actions, "refresh", lambda catalog: ["galick_gun_styrk"])
    k.brain.feed(TALK.format(seq=1))
    await k.settle()
    assert seen == ["GalickGunStyrk"] and k.tasks.get(name)["variables"][1]["name"] == "cooldown"
    assert [c["output"] for c in k.ask.calls] == ["Response"]
    assert "galick_gun_styrk for Styrkaeopteryx, open: A Galick Gun" in k.ask.calls[0]["prompt"]  # TASKS, Respond's


async def test_the_brains_tasks_and_running_now_are_in_full(kit):
    k = kit(Step=GIVE)
    gun(k)
    k.running({"railgun_steve": {"action": "railgun", "values": {"player": "Steve", "cooldown": 5},
                                 "live": ["cooldown"], "task": None}})
    k.brain.feed(ASK.format(seq=5))
    await k.settle()
    tail = k.ask.calls[0]["cached"][-1][0]
    assert "- galick_gun_styrk, for Styrkaeopteryx, open, 18:30:00: A Galick Gun for Styrkaeopteryx" in tail
    assert "  made: nothing yet; running now: none" in tail
    assert "- railgun_steve: railgun, the brain started it\n  live: " in tail and "cooldown=5 (0..60)" in tail


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
            yield ASK.format(seq=3)
        await asyncio.Event().wait()

    following = asyncio.create_task(k.brain.follow(watch))
    for _ in range(200):
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
    k.brain.feed(ASK.format(seq=3))
    await k.settle()
    assert "Alex tried to swim in lava" in k.ask.calls[0]["cached"][-1][0].split("NEW SINCE")[1]


async def test_respond_sees_each_players_place_and_state_but_no_items(kit):
    k = kit(Response={"reply": "anytime!", "hand_up": False, "reason": "thanks"})
    await k.world.fresh_state()                           # Respond never waits: the last state there is
    k.brain.feed(TALK.format(seq=1))
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
    for _ in range(100):  # the first call's flush, waiting on the box
        if running[0]:
            break
        await asyncio.sleep(0)
    for seq in range(6, 10):                              # four more players, each their own turn
        k.brain.feed(line(seq, f"P{seq}"))
    while k.brain.inflight:
        await asyncio.gather(*k.brain.inflight)
    assert len(k.ask.calls) == 5 and k.vfg.of("usage") == [["usage", "flush"]] and running[0] == 1
    down.set()
    await k.settle()
    assert k.vfg.of("usage") == [["usage", "flush"]] * 2 and most[0] == 1  # one more for the four, after it
    k.brain.feed(line(10, "P10"))  # nothing running: a call's own flush, at once
    await k.settle()
    assert len(k.vfg.of("usage")) == 3 and most[0] == 1


async def test_a_line_whose_event_breaks_tells_the_player_it_errored_out(kit):
    k = kit(Step=GIVE)

    async def broken(*a, **kw):
        raise RuntimeError("the world broke")
    k.brain.world.seen = broken
    k.brain.feed("LINE#7 18:29:59 Steve voice action_oriented 0.9 - | give me a diamond pickaxe")
    await k.settle()
    assert k.vfg.said() == [["Steve: that errored out — the world broke"]]


# ------------------------------------------------------------------ one turn per player

class Held:
    """Step answers held until the test lets each go: `held.go(i)` lets the i-th call answer."""

    def __init__(self, answer: dict):
        self.answer = answer
        self.gates: list[asyncio.Event] = []
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> dict:
        gate = asyncio.Event()
        self.gates.append(gate)
        await gate.wait()
        return self.answer

    async def go(self, i: int) -> None:
        for _ in range(50):
            if len(self.gates) > i:
                break
            await asyncio.sleep(0)
        self.gates[i].set()
        for _ in range(20):
            await asyncio.sleep(0)


async def turns(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


def events(k) -> list[str]:
    """Each step's event, in the order the calls began: `#5`, `galick_gun_styrk`."""
    return [c["call_id"].rsplit(" ", 1)[0] for c in k.ask.of("Step")]


async def test_a_players_lines_take_turns_while_another_players_run_at_once(kit):
    held = Held(GIVE)
    k = kit(Step=held)
    assert k.brain.feed(line(5)) == "started"
    assert k.brain.feed(line(6, text="and a shield")) == "waiting"
    assert k.brain.feed(line(7, "Alex", "make it day")) == "started"
    await turns()
    assert events(k) == ["#5", "#7"] and k.brain.turns.waiting("Steve") == ["and a shield"]
    await held.go(1)                                      # Alex's ends: Steve's second still waits for his first
    assert events(k) == ["#5", "#7"]
    await held.go(0)
    await held.go(2)
    await k.settle()
    assert events(k) == ["#5", "#7", "#6"] and not k.brain.turns.busy("Steve")


async def test_three_lines_wait_and_a_fourth_is_turned_away_by_a_private_message_naming_them(kit):
    held = Held(GIVE)
    k = kit(Step=held)
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
    assert events(k) == ["#5", "#6", "#7", "#8"]


async def test_a_report_goes_ahead_of_the_players_waiting_lines(kit):
    held = Held(step(reply="ok", status="done", record="x"))
    k = kit(Step=held)
    name = gun(k)
    k.brain.feed(line(5, "Styrkaeopteryx", "build me a castle"))
    k.brain.feed(line(6, "Styrkaeopteryx", "and a moat"))
    await turns()
    await k.brain.on_report(name, "done", "Done: the gun is running.")
    for i in range(3):
        await held.go(i)
    await k.settle()
    assert events(k) == ["#5", "galick_gun_styrk", "#6"]


async def test_a_hand_up_keeps_the_turn_and_a_hand_off_frees_it_at_once(kit):
    held = Held(step(end="handoff", reply="on it", hand_offs=(hand_off(),)))
    k = kit(Step=held, Response={"reason": "asks for a fireball", "hand_up": True, "reply": ""})
    k.brain.feed(line(5, "KrownJM", "a fireball on my words?", cls="conversational"))
    await turns()
    assert [c["output"] for c in k.ask.calls] == ["Response", "Step"]  # handed up: the brain's loop, same turn
    assert k.brain.feed(line(6, "KrownJM", "and a shield")) == "waiting"  # the turn is still Respond's line's
    await held.go(0)                                       # the hand-off: the implementer starts, the turn ends
    assert k.impl.started and events(k) == ["#5", "#6"]    # the next line runs while the implementer still works
    await held.go(1)
    await k.settle()


async def test_a_turn_ends_once_the_rows_it_wrote_come_back_on_the_watch(kit):
    k = kit(Step=step(PICKAXE, reply="there you go", record="gave Steve a diamond pickaxe"))
    k.brain.settle_s = 30.0                                 # never reached: the rows come back
    k.brain.feed(line(5))
    k.brain.feed(line(6, text="and a shield"))
    await turns(40)
    assert events(k) == ["#5"] and k.vfg.said() == [["there you go"]]  # #5 said and recorded; #6 still waits
    k.brain.feed("AI#7 18:30:01 say - | there you go")
    await turns()
    assert events(k) == ["#5"]                              # the record is not back yet
    k.brain.feed("AI#8 18:30:01 action - | gave Steve a diamond pickaxe")
    await turns(40)
    assert events(k) == ["#5", "#6"]


async def test_a_stop_drops_what_waits_and_a_report_dropped_closes_its_task_in_code(kit):
    held = Held(GIVE)
    k = kit(Step=held)
    name = gun(k)
    k.brain.feed(line(5, "Styrkaeopteryx", "build me a castle"))
    k.brain.feed(line(6, "Styrkaeopteryx", "and a moat"))
    await turns()
    await k.brain.on_report(name, "done", "Done: the gun is running.", "A Galick Gun.", MADE)
    stopping = asyncio.create_task(k.brain.stop(1.0))
    await turns()
    t = k.tasks.get(name)                                   # closed in code, on the implementer's status
    assert (t["status"], t["result"], t["made"]) == ("done", "galick_gun_styrk done: A Galick Gun.", MADE)
    await held.go(0)                                        # the loop in flight finishes in the grace
    await stopping
    await k.settle()
    assert events(k) == ["#5"] and k.brain.feed(line(9)) is None  # the moat line dropped; nothing more starts
    assert k.vfg.rows("result") == [("result galick_gun_styrk", "galick_gun_styrk done: A Galick Gun.")]



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


async def test_the_loops_clock_starts_with_the_turn_so_respond_counts_against_the_two_minutes(kit):
    async def slow(prompt):
        k.tick.t += 130                                    # Respond took 130 s of the player's wait
        return {"reason": "asks for a fireball", "hand_up": True, "reply": ""}

    k = kit(Response=slow, Step=step(end="handoff", reply="handing it on", hand_offs=(hand_off(),)))
    k.brain.feed(line(5, "KrownJM", "a fireball on my words?", cls="conversational"))
    await k.settle()
    assert k.ask.of("Step")[0]["prompt"].startswith("\nSTEP 1 of at most 10; 130 s of 120 used.\nTHIS IS THE LAST")


async def test_a_hand_off_that_would_not_open_says_what_landed_in_the_same_step(kit):
    k = kit(Step=step(PICKAXE, end="handoff", reply="pickaxe's yours, and a hook coming up",
                      hand_offs=(hand_off(words="a grappling hook"),)))
    k.acts.open_task = lambda h, **kw: None
    k.brain.feed(line(5, text="a pickaxe and a grappling hook"))
    await k.settle()
    assert k.vfg.said() == [["done: give Steve a diamond pickaxe — but I couldn't start “a grappling hook” — say it "
                             "again?"]]


async def test_a_hand_off_whose_task_would_not_open_is_never_promised(kit, capsys):
    k = kit(Step=step(end="handoff", reply="on it, a fireball on your words soon", hand_offs=(hand_off(),)))
    k.acts.open_task = lambda h, **kw: None
    k.brain.feed(line(5, "KrownJM", "a hot fireball incantation"))
    await k.settle()
    assert k.impl.started == []
    words = "“Can you make an incantation? That fires a fireball whenever…”"  # the player's words, cut short
    assert k.vfg.said() == [[f"sorry, I couldn't start {words} — say it again?"]]
    assert k.vfg.rows() == [("action -", f"could not start {words}")]
    assert "1 hand-off(s) did not open" in capsys.readouterr().out


async def test_a_say_or_a_row_that_failed_keeps_no_turn_waiting_for_it(kit):
    k = kit(Step=step(PICKAXE, reply="there you go", record="gave Steve a diamond pickaxe"))
    k.brain.settle_s = 30.0                                 # never waited out: nothing will come back
    k.vfg.on("say", answer=VfgResult(4, "", "vfg: cannot reach EC2"))
    k.vfg.on("feed", "post", answer=VfgResult(4, "", "vfg: cannot reach EC2"))
    k.brain.feed(line(5))
    k.brain.feed(line(6, text="and a shield"))
    await turns(60)
    assert events(k) == ["#5", "#6"]


async def test_a_line_dropped_at_a_stop_is_marked_read_and_logged(kit, capsys):
    held = Held(GIVE)
    k = kit(Step=held)
    k.brain.feed(line(5))
    await turns()                                           # #5's loop has read the feed: #6 comes after
    k.brain.feed(line(6, text="and a shield"))
    stopping = asyncio.create_task(k.brain.stop(1.0))
    await turns()
    await held.go(0)
    await stopping
    await k.settle()
    assert ["context", "mark", "6"] in k.vfg.calls and k.ctx.rows[-1].processed and events(k) == ["#5"]
    assert "#6 Steve dropped at the stop, marked read: 'and a shield'" in capsys.readouterr().out


async def test_a_line_or_a_report_that_waited_for_its_turn_logs_how_long(kit, capsys):
    held = Held(step(reply="ok", status="done", record="x"))
    k = kit(Step=held)
    name = gun(k)
    k.brain.feed(line(5, "Styrkaeopteryx", "build me a castle"))
    k.brain.feed(line(6, "Styrkaeopteryx", "and a moat"))
    await turns()
    await k.brain.on_report(name, "done", "Done: the gun is running.")
    k.tick.t += 4.2                                          # the first loop holds the turn 4.2 s
    for i in range(3):
        await held.go(i)
    await k.settle()
    out = capsys.readouterr().out
    assert "turn report galick_gun_styrk waited 4.2 s for their turn" in out
    assert "turn #6 Styrkaeopteryx waited 4.2 s for their turn" in out
    assert "turn #5 " not in out.split("waits for their turn")[0]  # the first took its turn at once: no wait logged


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
