"""The one call: what each event puts in the prompt's tail, what stays in the cached opening,
and the Decision's checks, which send a wrong answer back to the model once."""

import json

import pytest
from pydantic import ValidationError

from brain.call import Call, Event, PartBase, decision_type
from brain.context import parse
from brain.world import Seen

from conftest import NOW, FakeAsk, FakeRefs, catalog, cmd, decision, made, part, rows

LINE = "LINE#40 18:29:58 Steve voice action_oriented 0.93 -8dB | give me a diamond pickaxe"


def check(answer: dict):
    return decision_type(catalog()).model_validate_json(json.dumps(answer))


def test_a_good_decision_passes_and_the_code_rules_send_a_wrong_one_back():
    d = check(decision(part("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickaxe")),
                       part("orchestrator", "a meteor staff for Steve that rains meteors where he looks"),
                       part("talk"), reply="there you go, and a staff is on its way"))
    assert [p.to for p in d.parts] == ["command", "orchestrator", "talk"]
    assert "task" not in d.parts[1].model_dump() and "task" not in type(d).model_json_schema()["$defs"]["Part"][
        "properties"]  # the brain never names a task
    assert d.parts[0].values() == {"commands": ["give Steve diamond_pickaxe"]}
    edit = check(decision(part("edit", "no cooldown on Alex's railgun", {"copy": "railgun_alex", "cooldown": 0}),
                          reply="no more cooldown"))
    assert edit.parts[0].values() == {"copy": "railgun_alex", "values": {"cooldown": 0}}
    assert check(decision(record="castle_steve done: castle up", status="done")).parts == []  # a report: no parts
    wrong = {
        "not on the menu": decision(part("castle_maker", "a castle", {"at": "home"}), reply="ok"),
        "else the orchestrator's: send that part to orchestrator": decision(
            part("command", "a wall", cmd("fill 1 1 1 5 5 5 stone")), reply="ok"),
        "has no action": decision(part("command", "give Steve bread"), reply="ok"),
        "has no instruction": decision(part("orchestrator", ""), reply="on it"),
        "orchestrator part 'staff' carries an action": decision(
            part("orchestrator", "a staff", cmd("give Steve stick"), said="staff"), reply="ok"),
        "an action that is, or orchestrator": decision(part("implementer", "a meteor staff"), reply="ok"),
        "carries an action": decision(part("talk", "", cmd("say hi")), reply="hi"),
        "no reply": decision(part("command", "give Steve bread", cmd("give Steve bread"))),
        "does nothing": decision(part("command", "nothing", cmd("give Steve bread")), reply="ok"),
        "moves nobody": decision(part("teleport", "teleport Steve to his current position",
                                      {"player": "Steve", "to": "spawn_point"}), reply="ok"),
        "range=far": decision(part("railgun", "a railgun for Steve", {"player": "Steve", "range": "far"}),
                             reply="ok"),
    }
    for says, answer in wrong.items():
        with pytest.raises(ValidationError, match=says):
            check(answer)


def test_the_opening_holds_the_rules_the_owner_and_the_library_and_the_tail_this_servers_own_actions():
    cat = made("glimmer_trail", '''
        from actions._types import Action, Players


        class GlimmerTrail(Action):
            """Leave a trail of ice behind a player."""

            script = "glimmer_trail"
            runs = "until_stopped"
            takes = ["player"]

            player: Players = "@a"
            """who leaves the trail"""
    ''')
    call, refs = Call(cat, None, owner="KrownJM"), FakeRefs()
    opening = call.opening(refs)
    assert "THE OWNER: KrownJM." in opening and "ACTION: railgun" in opening and "give — Gives" in opening
    assert ("- orchestrator — anything new no action does, a change or fix to something that exists, or "
            "stopping, cancelling or undoing something running\n") in opening and "implementer —" not in opening
    assert opening.endswith("THE CONVERSATION SO FAR (oldest first; each row: its time in UTC, who, and for a "
                            "player's line Jev's class):\n")
    assert "glimmer_trail" not in opening  # an implementer's action never changes the cached opening
    tail = call.tail(Event("LINE", parse(LINE, now=NOW)), Seen(), "TASKS: none yet", [])
    assert "glimmer_trail — Leave a trail of ice behind a player." in tail and "ACTION: glimmer_trail" in tail
    assert call.opening(refs) is opening  # built once while the actions and references stay
    assert "(not loaded yet" in call.opening(None)


async def test_each_event_puts_its_own_tail_after_the_cached_conversation():
    ctx = rows("LINE#37 18:29:49 Alex voice context 0.8 - | I'm by the lava pool",
               "GAME#38 18:29:50 death Alex | Alex tried to swim in lava", LINE)
    ask = FakeAsk(Decision=decision())
    call = Call(catalog(), ask, owner="KrownJM")
    new = ctx.take_unprocessed()
    seen = Seen(players="PLAYERS ONLINE:\n- Steve at 1 2 3 in overworld: Steve carries and wears nothing")

    line = parse(LINE, now=NOW)
    await call.decide(Event("LINE", line, handed_up="it asks for an item"), ctx, seen, "TASKS: none yet",
                      new[:2], FakeRefs())
    tail, cached = ask.calls[0]["prompt"], ask.calls[0]["cached"]
    assert cached[0][1] and "THE CONVERSATION SO FAR" in cached[0][0]
    assert cached[1] == ("18:29:50 game, death: Alex tried to swim in lava\n"
                         "18:29:58 Steve (action_oriented): give me a diamond pickaxe\n", False)
    assert (tail.index("THE WORLD NOW") < tail.index("TASKS: none yet") < tail.index("NEW SINCE")
            < tail.index("THE EVENT"))
    assert "Steve carries and wears nothing" in tail
    assert "NEW SINCE THE LAST READ (rows no call had read; they end the conversation above):\n" \
           "18:29:50 game, death: Alex tried to swim in lava\n\n" in tail  # the game event; never the context line
    assert tail.endswith("LINE #40, 18:29:58: Steve said: give me a diamond pickaxe\n"
                         "(Respond, the cheap mode, handed this line up to you: it asks for an item)\n")

    task = {"name": "ice_trail_steve", "player": "Steve", "line": "make me an ice trail",
            "brief": "an ice trail behind Steve", "description": "An ice trail that follows Steve as he walks."}
    report = Event("REPORT", task=task, status="done", report="Done: the ice trail is on.")
    await call.decide(report, ctx, seen, "TASKS (…):\n- ice_trail_steve …", new[1:2], FakeRefs())
    tail = ask.calls[1]["prompt"]
    assert tail.endswith("THE EVENT:\nREPORT: the implementer of ice_trail_steve ended, done.\n"
                         "ice_trail_steve was for Steve, who said: make me an ice trail\n"
                         "WHAT IT IS: An ice trail that follows Steve as he walks.\n"
                         "ITS LATEST BRIEF: an ice trail behind Steve\nITS REPORT:\nDone: the ice trail is on.\n")
    assert "Done: the ice trail is on." in tail and "lava\n\nTHE EVENT:\nREPORT" in tail  # a report reads NEW too
    assert not any("the ice trail is on" in text for text, _ in ask.calls[1]["cached"])  # never in the context
    await call.decide(Event("REPORT", task=task, status="done", report="Done."), ctx, seen, "", new[:1], FakeRefs())
    assert "had read; they end the conversation above):\nnothing\n" in ask.calls[2]["prompt"]  # a context line alone
    for c in ask.calls:
        assert "lava pool" not in c["prompt"] + "".join(text for text, _ in c["cached"])
    assert ask.calls[0]["cached"][0] == ask.calls[1]["cached"][0]  # one opening for every event
    following = Event("REPORT", task=task, status="done", report="Done.", following=True).show()
    assert following.endswith("\nA FOLLOW-UP to ice_trail_steve is already under way, so the task stays open: say "
                              "how this step went, never that it is all done.")


def test_the_heads_the_orchestrator_is_given():
    line = Event("LINE", parse(LINE, now=NOW))
    assert line.head() == "THE LINE #40, 18:29:58: Steve said: give me a diamond pickaxe"
    task = {"name": "ice_trail_steve", "player": "Steve", "line": "make me an ice trail", "brief": "…"}
    report = Event("REPORT", task=task, status="failed", report="Failed:\n" + "x" * 900)
    head = report.head()
    assert head.startswith("THE REPORT: the implementer of ice_trail_steve ended, failed. ice_trail_steve was "
                           "for Steve, who said: make me an ice trail\nITS REPORT: Failed: xxx")
    assert len(head.split("ITS REPORT: ")[1]) == 600 and head.endswith("…")


def test_values_keys_are_gathered_into_the_values_an_incantation_binds():
    p = PartBase.model_validate(part("incantation_create", "juicy steak gives Steve 64 steak", {
        "player": "Steve", "phrase": "juicy steak", "action": "command",
        "values.commands": ["give Steve minecraft:cooked_beef 64"]}))
    assert p.values() == {"player": "Steve", "phrase": "juicy steak", "action": "command",
                          "values": {"commands": ["give Steve minecraft:cooked_beef 64"]}}


def test_an_edit_keeps_its_flat_keys_beside_its_values_keys():
    p = PartBase.model_validate(part("edit", "no cooldown and a longer railgun for Alex", {
        "copy": "railgun_2", "values.cooldown": 0, "length": 80}))
    assert p.values() == {"copy": "railgun_2", "values": {"cooldown": 0, "length": 80}}
    p = PartBase.model_validate(part("edit", "a longer railgun for Alex", {"copy": "railgun_2", "length": 80}))
    assert p.values() == {"copy": "railgun_2", "values": {"length": 80}}
