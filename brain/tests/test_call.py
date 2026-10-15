"""The step: its checks, which send a wrong answer back to the model once, the schema that never
changes, the brief rendered from its parts, what stays in the cached opening, and what each event
puts in the prompt's tail."""

import functools
import json

import pytest
from pydantic import ValidationError

from brain.call import ActBase, Call, Event, HandOff, Prompt, step_type
from brain.context import parse
from brain.respond import promises
from brain.world import Seen

from conftest import NOW, FakeRefs, act, catalog, cmd, follow_up, hand_off, rows, start, step

LINE = "LINE#40 18:29:58 Steve voice action_oriented 0.93 -8dB | give me a diamond pickaxe"


def check(answer: dict, **moment):
    return step_type(catalog(), **moment).model_validate_json(json.dumps(answer))


def test_good_steps_pass():
    s = check(step(act("engine_start", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickaxe")),
                   act("engine_give", "a fire staff for Alex", {"player": "Alex", "item": "fire_staff"}),
                   reply="there you go"))
    assert [a.to for a in s.actions] == ["engine_start", "engine_give"] and s.order == "together"
    assert s.end == "reply"
    assert s.actions[0].values() == {"scenario": "command", "args": {"commands": ["give Steve diamond_pickaxe"]}}
    assert check(step(act("engine_start", "give Steve bread", cmd("give Steve bread")), end="silent")).reply == ""
    assert check(step(act("engine_start", "give Steve bread", cmd("give Steve bread")), end="next")).end == "next"
    assert check(step(end="next", read=("Alex",))).read_items == ["Alex"]
    assert check(step(reply="a path to where, the surface?")).actions == []  # a question back: no tool
    handed = check(step(end="handoff", reply="on it", hand_offs=(hand_off(),)))
    assert handed.hand_offs[0].name == "hot_fireball_krown"
    cancelled = check(step(end="handoff", follow_ups=(follow_up("hot_fireball_krown", cancel=True),)))
    assert cancelled.follow_ups[0].cancel and cancelled.follow_ups[0].brief == ""
    giant = check(step(act("engine_apply", "Alex giant for a minute", {"player": "Alex", "mark": "giant", "seconds": 60,
                                                                         "args.factor": 3}), reply="huge"))
    assert giant.actions[0].values() == {"player": "Alex", "mark": "giant", "seconds": 60, "args": {"factor": 3}}
    assert check(step(record="castle_steve done: castle up", status="done", reply="castle's up"),
                 report=True).status == "done"
    # the last step: an ending with no action and no read; a follow-up of a task there is
    assert check(step(reply="couldn't do the rest, sorry"), last=True).end == "reply"
    assert check(step(end="handoff", follow_ups=(follow_up("castle_steve", "a moat", "A castle with a moat."),)),
                 last=True, tasks={"castle_steve"}).follow_ups[0].task == "castle_steve"


BREAD = act("engine_start", "give Steve bread", cmd("give Steve bread"))
WRONG = [
    (step(act("castle_maker", "a castle", {"at": "home"}), reply="ok"), {}, "castle_maker is not on the menu"),
    (step(act("engine_start", "", cmd("give Steve bread")), reply="ok"), {}, "has no instruction"),
    (step(act("engine_start", "nothing", cmd("give Steve bread")), reply="ok"), {}, "does nothing"),
    (step(act("engine_start", "teleport Steve to his current position", start("teleport", players="Steve")),
          reply="ok"), {}, "moves nobody"),
    (step({"to": "engine_start", "instruction": "give Steve bread", "action": [
        {"key": "scenario", "value": "command"}, {"key": "scenario", "value": "x"}]}, reply="ok"), {},
     "sets a field twice"),
    (step(act("engine_apply", "Steve giant for ever", {"player": "Steve", "mark": "giant", "seconds": "forever"}),
          reply="ok"), {}, "seconds=forever"),
    (step(act("engine_grant", "fireball for everyone", {"player": "@a", "ability": "fireball"}), reply="ok"), {},
     "player=@a: should be one player's exact name"),
    (step(end="next"), {}, "end next runs or reads something"),
    (step(end="next", status="done", reply="there"), {"report": True}, "end next runs or reads something"),
    (step(BREAD), {}, "end reply needs the reply"),
    (step(end="reply", status="done"), {"report": True}, "end reply needs the reply"),  # the pilot's answer
    (step(end="handoff", reply="on it"), {}, "end handoff needs a hand-off or a follow-up"),
    (step(end="handoff", reply="On it, making the explosion bigger too.", status="done"), {},
     "end handoff needs a hand-off or a follow-up"),                       # the pilot's #97, its status dropped
    (step(BREAD, end="next", hand_offs=(hand_off(),)), {}, "hand_offs and follow_ups end the loop"),
    (step(end="handoff", hand_offs=(hand_off("Hot Fireball"),)), {}, "lowercase letters"),
    (step(end="handoff", hand_offs=(hand_off("cast"),)), {}, "cast is never a task's name"),
    (step(end="handoff", hand_offs=(hand_off("engine_grant"),)), {}, "engine_grant is an action's name"),
    (step(end="handoff", hand_offs=(hand_off(assumed=" ", cast=""),)), {}, "has no assumed, cast"),
    (step(end="handoff", hand_offs=(hand_off(done_when=[" "]),)), {}, "has no done_when"),
    (step(end="handoff", hand_offs=(hand_off(), hand_off())), {}, "two hand-offs share a name"),
    (step(end="handoff", follow_ups=(follow_up("a_b", cancel=True), follow_up("a_b", cancel=True))), {},
     "followed up twice"),
    (step(end="handoff", follow_ups=(follow_up("a_b", description="A bigger thing."),)), {}, "has no brief"),
    (step(end="handoff", follow_ups=(follow_up(" ", cancel=True),)), {}, "names no task"),
    (step(reply="x" * 301), {}, "301 characters"),
    (step(BREAD, reply="ok"), {"last": True}, "this is the last step: run no action"),
    (step(end="next", read=("Alex",)), {"last": True}, "this is the last step"),
    (step(reply="here", read=("Alex",)), {"last": True}, "this is the last step"),
    (step(BREAD, end="silent"), {"last": True}, "this is the last step"),
    (step(end="handoff", follow_ups=(follow_up("castle_bob", "a moat", "A castle."),)),
     {"last": True, "tasks": {"castle_steve"}}, "'castle_bob' is not under TASKS"),
]


@pytest.mark.parametrize("answer, moment, says", WRONG)
def test_each_rule_sends_a_wrong_step_back_with_why(answer, moment, says):
    with pytest.raises(ValidationError, match=says):
        check(answer, **moment)


def test_what_would_do_nothing_is_dropped_and_logged_never_sent_back(capsys):
    bread = check(step(BREAD, end="next", reply="there", record="gave bread", status="done"), report=True)
    assert (bread.end, bread.reply, bread.record, bread.status) == ("next", "", "", "")
    assert "end next: dropped, it would do nothing: status 'done'; reply 'there'; record 'gave bread'" in \
        capsys.readouterr().out
    assert check(step(reply="ok", status="done")).status == ""          # a status outside a report's loop
    silent = check(step(BREAD, end="silent", reply="there", record="gave bread"))
    assert (silent.reply, silent.record) == ("", "gave bread")
    read = check(step(reply="here", read=("Alex",)))                     # a read comes back only to a next step
    assert (read.read_items, read.reply) == ([], "here")
    # a hand-off or a follow-up under a reply or silent ending: carried out, as a hand-off's
    handed = check(step(reply="on it", hand_offs=(hand_off(),)))
    assert (handed.end, handed.reply, handed.hand_offs[0].name) == ("handoff", "on it", "hot_fireball_krown")
    assert check(step(end="silent", follow_ups=(follow_up("a_b", cancel=True),))).end == "handoff"
    # a follow-up with no new description keeps the one it had (act.py): no need to send it back
    assert check(step(end="handoff", follow_ups=(follow_up("a_b", brief="bigger"),))).follow_ups[0].brief == "bigger"
    assert "end handoff: dropped, it would do nothing: end reply (it hands off: end handoff)" in capsys.readouterr().out
    assert check(step(reply="ok", status="done"), report=True).status == "done"  # a report's: kept


PROMISE = functools.partial(promises, offers=False)


@pytest.mark.parametrize("reply", [
    "Got it, making sure it explodes right on impact.",                      # #99 on the 10-11 replay
    "Yeah, 600ms latency feels noticeable in real-time play. We can look into smaller or streaming ASR models to "
    "trim that down.",                                                       # #239
    "on it, your castle's coming up",
])
def test_a_reply_that_promises_with_nothing_started_is_sent_back(reply):
    with pytest.raises(ValidationError, match="promises work and this step starts nothing"):
        check(step(reply=reply), promise=PROMISE)
    assert check(step(BREAD, reply=reply), promise=PROMISE).reply == reply  # actions: the reply is about them
    assert check(step(end="handoff", reply=reply, hand_offs=(hand_off(),)), promise=PROMISE).end == "handoff"


@pytest.mark.parametrize("reply", ["hey KrownJM, good to see you!",
                                   "Fire Aspect's max is 2 on /enchant, but I can set 5",
                                   "can't do that one, sorry", "a path to where, the surface?"])
def test_talk_an_answer_an_offer_or_a_refusal_with_nothing_started_passes(reply):
    assert check(step(reply=reply), promise=PROMISE).reply == reply


def test_a_follow_up_of_a_task_that_is_not_there_is_the_tools_to_refuse_until_the_last_step():
    s = check(step(end="handoff", follow_ups=(follow_up("castle_bob", "a moat", "A castle with a moat."),)))
    assert s.follow_ups[0].task == "castle_bob"  # act.py refuses it, and the next step reads why


def test_the_schema_is_the_same_for_every_moment():
    one = step_type(catalog())
    two = step_type(catalog(), last=True, report=True, tasks={"castle_steve"})
    assert one.__name__ == two.__name__ == "Step"
    assert one.model_json_schema() == two.model_json_schema()
    schema = one.model_json_schema()
    # every property of the step and of all it holds is required: Gemini leaves out what it may
    objects = [schema, *schema["$defs"].values()]
    assert {d.get("title") for d in objects} == {"Step", "Act", "Value", "HandOff", "FollowUp"}
    for d in objects:
        assert d["required"] == list(d["properties"]), d["title"]
    assert schema["properties"]["status"]["default"] == ""   # defaults kept, for a model that writes them
    assert list(schema["properties"]) == ["order", "actions", "read_items", "read_configs", "hand_offs", "follow_ups",
                                          "end", "reply", "record", "status"]
    assert list(schema["$defs"]["HandOff"]["properties"]) == ["name", "description", "words", "picture", "assumed",
                                                              "cast", "done_when"]


def test_the_brief_is_rendered_from_its_parts_under_headings():
    h = HandOff.model_validate(hand_off(done_when=["saying it fires one", "  it explodes\n  there "]))
    assert h.brief() == (
        "THE PLAYER'S WORDS\n“Can you make an incantation? That fires a fireball whenever I say Hot fireball”\n\n"
        "THE WHOLE PICTURE\na fireball from KrownJM's eyes, where they look, fast and straight, trailing flame\n\n"
        "WHAT THE PLAYER TAKES FOR GRANTED\nit stops at the first mob, player or block it meets, explodes there, "
        "breaks blocks and sets fire\n\n"
        "HOW IT IS CAST, AND FOR WHOM\nthe incantation “hot fireball”, for KrownJM\n\n"
        "DONE WHEN\n- saying it fires one\n- it explodes there")


def test_args_keys_are_gathered_into_the_args_a_start_or_a_bind_sets():
    a = ActBase.model_validate(act("engine_bind", "Alex says castle here to build one where Alex stands", {
        "player": "Alex", "phrase": "castle here", "scenario": "castle", "args.at": "caster", "args.size": 31}))
    assert a.values() == {"player": "Alex", "phrase": "castle here", "scenario": "castle",
                          "args": {"at": "caster", "size": 31}}


def test_args_given_as_json_text_are_read_as_the_object_never_sent_back():
    """The 10-11 replay's event 18: an object field written as one string of JSON."""
    cat = catalog()
    given = act("engine_start", "a horde of 30 round Steve", {
        "scenario": "horde", "args": "{\"count\": 30, \"players\": \"Steve\"}", "args.mob": "zombie"})
    s = step_type(cat).model_validate_json(json.dumps(step(given, reply="here they come")))
    assert s.actions[0].values(cat.get("engine_start")) == {
        "scenario": "horde", "args": {"count": 30, "players": "Steve", "mob": "zombie"}}
    bad = act("engine_start", "x", {"scenario": "horde", "args": "{not json"})
    with pytest.raises(ValidationError, match="args is an object"):  # text that is no JSON object: sent back
        step_type(cat).model_validate_json(json.dumps(step(bad, reply="done")))
    phrase = act("engine_bind", "x", {"player": "Steve", "phrase": "{\"a\": 1}", "ability": "fireball"})
    s = step_type(cat).model_validate_json(json.dumps(step(phrase, reply="done")))
    assert s.actions[0].values(cat.get("engine_bind"))["phrase"] == '{"a": 1}'   # a text field keeps its text


def test_the_opening_holds_the_rules_the_owner_and_the_menu_and_the_tail_the_engine():
    call, refs = Call(catalog(), None, owner="KrownJM"), FakeRefs()
    opening = call.opening(refs)
    assert "THE OWNER: KrownJM." in opening and "ACTION: engine_start" in opening and "give — Gives" in opening
    assert "grappling hook" in opening and "FINISH IN ONE STEP" in opening and "THE ENGINE\n" in opening
    # fireball, fire_staff, giant and meteor are the engine's own examples now (the spec's names)
    for asked in ("High Step", "nuke", "Speed Dash", "netherite", "cobblestone"):
        assert asked.lower() not in opening.lower(), asked  # the replay's asks never in the prompt it tests
    assert "orchestrator" not in opening.lower() and "{owner}" not in opening
    rules = call.rules                    # brain.md as the model reads it: its authors' TODO lines left out
    assert "TODO" not in opening and not any(w in rules for w in ("copies", "copy", "@a", "scripts", " script"))
    assert opening.endswith("THE CONVERSATION SO FAR (oldest first; each row: its time in UTC, who, and for a "
                            "player's line Jev's class):\n")
    seen = Seen(engine="ENGINE (every class …):\nSCENARIOS:\n- glimmer_trail: Leave a trail of ice.")
    tail = call.tail(Event("LINE", parse(LINE, now=NOW)), seen, "TASKS: none yet", [])
    assert "- glimmer_trail: Leave a trail of ice." in tail
    assert "glimmer_trail" not in opening         # the classes are the tail's: a reload never changes the opening
    assert call.opening(refs) is opening  # built once while the actions and references stay
    assert "(not loaded yet" in call.opening(None)


def test_each_event_puts_its_own_tail_after_the_conversation():
    ctx = rows("LINE#37 18:29:49 Alex voice context 0.8 - | I'm by the lava pool",
               "GAME#38 18:29:50 death Alex | Alex tried to swim in lava", LINE)
    call = Call(catalog(), None, owner="KrownJM")
    new = ctx.take_unprocessed()
    seen = Seen(players="PLAYERS ONLINE:\n- Steve at 1 2 3 in overworld: Steve carries and wears nothing")
    line = Event("LINE", parse(LINE, now=NOW), handed_up="it asks for an item")
    p = call.prompt(line, ctx, seen, "TASKS: none yet", new[:2], FakeRefs())
    assert p.head[0][1] and "THE CONVERSATION SO FAR" in p.head[0][0]
    assert p.head[1] == ("18:29:50 game, death: Alex tried to swim in lava\n"
                         "18:29:58 Steve (action_oriented): give me a diamond pickaxe\n", False)
    tail = p.tail
    assert (tail.index("THE WORLD NOW") < tail.index("TASKS: none yet") < tail.index("NEW SINCE")
            < tail.index("THE EVENT"))
    assert "NEW SINCE THE LAST READ (rows no call had read; they end the conversation above):\n" \
           "18:29:50 game, death: Alex tried to swim in lava\n\n" in tail  # the game event; never the context line
    assert tail.endswith("LINE #40, 18:29:58: Steve said: give me a diamond pickaxe\n"
                         "(Respond, the cheap mode, handed this line up to you: it asks for an item)\n")
    assert "lava pool" not in tail + "".join(text for text, _ in p.cached())

    task = {"name": "ice_trail_steve", "player": "Steve", "line": "make me an ice trail",
            "brief": "THE PLAYER'S WORDS\nmake me an ice trail", "description": "An ice trail that follows Steve."}
    report = Event("REPORT", task=task, status="done", report="Done: the ice trail is on.")
    tail = call.tail(report, seen, "TASKS (…)", new[1:2])
    assert tail.endswith("THE EVENT:\nREPORT: the implementer of ice_trail_steve ended, done.\n"
                         "ice_trail_steve was for Steve, who said: make me an ice trail\n"
                         "WHAT IT IS: An ice trail that follows Steve.\n"
                         "ITS LATEST BRIEF:\nTHE PLAYER'S WORDS\nmake me an ice trail\n"
                         "ITS REPORT:\nDone: the ice trail is on.\n")
    following = Event("REPORT", task=task, status="done", report="Done.", following=True).show()
    assert following.endswith("\nA FOLLOW-UP to ice_trail_steve is already under way, so the task stays open: say "
                              "how this run went, never that it is all done.")
    assert (line.ref(), report.ref()) == ("#40", "ice_trail_steve")


def test_a_steps_prompt_marks_its_last_block_and_keeps_every_earlier_one_as_it_was():
    p = Prompt(head=[("OPENING", True), ("block 1\n", False), ("block 2\n", True)], tail="TAIL")
    assert p.cached() == [("OPENING", True), ("block 1\n", False), ("block 2\n", True), ("TAIL", True)]
    p.steps += ["STEP 1", "STEP 2"]
    assert p.cached() == [("OPENING", True), ("block 1\n", False), ("block 2\n", True), ("TAIL", False),
                          ("STEP 1", False), ("STEP 2", True)]


def test_the_trailer_says_the_step_the_time_and_on_the_last_step_why():
    assert Call.trailer(2, 10, 14.4, 120, False) == "\nSTEP 2 of at most 10; 14 s of 120 used.\n"
    assert "THIS IS THE LAST STEP (this is step 10): run no action" in Call.trailer(10, 10, 30, 120, True)
    assert "THIS IS THE LAST STEP (120 s have gone)" in Call.trailer(4, 10, 121, 120, True)
    # the last step's reply is in the player's terms: never the steps, the time or the limits it hit (live #72, #78)
    notice = Call.trailer(10, 10, 30, 120, True).split("THIS IS THE LAST STEP", 1)[1]
    assert "in the player's terms" in notice and "never mentions steps, time, limits or how you work" in notice
    assert "why" not in notice.split(":", 1)[1]
