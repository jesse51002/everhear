"""The one call: what each event puts in the prompt's tail, what stays in the cached opening,
and the Decision's checks, which send a wrong answer back to the model once."""

import json

import pytest
from pydantic import ValidationError

from brain.call import Call, Event, decision_type
from brain.context import parse
from brain.world import Seen

from conftest import NOW, FakeAsk, FakeRefs, catalog, cmd, decision, made, part, rows

LINE = "LINE#40 18:29:58 Steve voice action_oriented 0.93 -8dB | give me a diamond pickaxe"


def check(answer: dict):
    return decision_type(catalog()).model_validate_json(json.dumps(answer))


def test_a_good_decision_passes_and_the_code_rules_send_a_wrong_one_back():
    d = check(decision(part("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickaxe")),
                       part("implementer", "a meteor staff for Steve that rains meteors where he looks"),
                       part("talk"), reply="there you go, and a staff is on its way"))
    assert [p.to for p in d.parts] == ["command", "implementer", "talk"]
    assert d.parts[0].values() == {"commands": ["give Steve diamond_pickaxe"]}
    edit = check(decision(part("edit", "no cooldown on Alex's railgun", {"copy": "railgun_2", "cooldown": 0}),
                          reply="no more cooldown"))
    assert edit.parts[0].values() == {"copy": "railgun_2", "values": {"cooldown": 0}}
    assert check(decision(record="T3 done: castle up", status="done")).parts == []  # a report: no parts, no reply
    wrong = {
        "not on the menu": decision(part("castle_maker", "a castle", {"at": "home"}), reply="ok"),
        "an implementer's": decision(part("command", "a wall", cmd("fill 1 1 1 5 5 5 stone")), reply="ok"),
        "has no action": decision(part("command", "give Steve bread"), reply="ok"),
        "has no brief": decision(part("implementer", ""), reply="on it"),
        "carries an action": decision(part("talk", "", cmd("say hi")), reply="hi"),
        "no reply": decision(part("command", "give Steve bread", cmd("give Steve bread"))),
        "does nothing": decision(part("command", "nothing", cmd("give Steve bread")), reply="ok"),
        "moves nobody": decision(part("teleport", "teleport Steve to his current position",
                                      {"player": "Steve", "to": "spawn_point"}), reply="ok"),
        "not a task id": decision(part("implementer", "make it bigger", task="the castle"), reply="ok"),
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
    assert "glimmer_trail" not in opening  # an implementer's action never changes the cached opening
    tail = call.tail(Event("LINE", parse(LINE, now=NOW)), Seen(), "OPEN TASKS:\nnone", [])
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
    await call.decide(Event("LINE", line, handed_up="it asks for an item"), ctx, seen, "OPEN TASKS:\nnone",
                      new[:2], FakeRefs())
    tail, cached = ask.calls[0]["prompt"], ask.calls[0]["cached"]
    assert cached[0][1] and "THE CONVERSATION SO FAR" in cached[0][0]
    assert cached[1] == ("18:29:50 game, death: Alex tried to swim in lava\n"
                         "18:29:58 Steve (action_oriented): give me a diamond pickaxe\n", False)
    assert tail.index("THE WORLD NOW") < tail.index("OPEN TASKS") < tail.index("NEW SINCE") < tail.index("THE EVENT")
    assert "Steve carries and wears nothing" in tail
    assert "NEW SINCE THE LAST READ (rows no call had read; they end the conversation above):\n" \
           "18:29:50 game, death: Alex tried to swim in lava\n\n" in tail  # the game event; never the context line
    assert tail.endswith("LINE #40, 18:29:58: Steve said: give me a diamond pickaxe\n"
                         "(Respond, the cheap mode, handed this line up to you: it asks for an item)\n")

    task = {"id": "T7", "player": "Steve", "line": "make me an ice trail", "task": "an ice trail behind Steve"}
    await call.decide(Event("REPORT", task=task, status="done", report="Done: the ice trail is on."), ctx,
                      seen, "OPEN TASKS:\n- T7 …", new[1:2], FakeRefs())
    tail = ask.calls[1]["prompt"]
    assert "REPORT: the implementer of T7 ended, done." in tail and "ITS BRIEF: an ice trail behind Steve" in tail
    assert "Done: the ice trail is on." in tail and "lava\n\nTHE EVENT:\nREPORT" in tail  # a report reads NEW too
    assert not any("the ice trail is on" in text for text, _ in ask.calls[1]["cached"])  # never in the context
    await call.decide(Event("REPORT", task=task, status="done", report="Done."), ctx, seen, "", new[:1], FakeRefs())
    assert "had read; they end the conversation above):\nnothing\n" in ask.calls[2]["prompt"]  # a context line alone
    for c in ask.calls:
        assert "lava pool" not in c["prompt"] + "".join(text for text, _ in c["cached"])
    assert ask.calls[0]["cached"][0] == ask.calls[1]["cached"][0]  # one opening for every event
