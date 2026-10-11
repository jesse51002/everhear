"""The orchestrator call: what its cached opening holds and what it leaves out, its tail's blocks
and their order, TASKS and RUNNING NOW, and the answer's checks, which send a wrong answer back
to the model once."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from vfg_actions import check_edit, menu_line
from vfg_llm import LlmError

from brain import names
from brain.call import Call, library
from brain.orchestrator import (EDIT, FOLLOW_UP, NEW, PROMPT, REPLY, STOP, AnswerBase, Orchestrator, answer_type,
                                describe, running_lines, task_lines)

from conftest import NOW, FakeAsk, FakeRefs, catalog, rows

LINE = "LINE#812 18:29:58 Steve voice action_oriented 0.93 -8dB | make my railgun pierce blocks and give me steak"
HEAD = "THE LINE #812, 18:29:58: Steve said: make my railgun pierce blocks and give me steak"
TODAY = NOW.strftime("%Y-%m-%d")


def row(name: str, **more) -> dict:
    """A task's row, as the brain keeps it and `vfg task list --json` prints it."""
    base = {"name": name, "description": f"what {name} is", "player": "Alex", "status": "done",
            "line": "make me one", "brief": "make one",
            "made": {"scripts": [], "actions": [], "presets": [], "copies": []}, "variables": None,
            "session": None, "result": "", "created_at": "2026-10-10T18:20:00.000Z",
            "updated_at": "2026-10-10T18:21:00.000Z", "closed_at": None}
    return {**base, **more}


TASKS = [
    row("lava_moat_sam", player="Sam", status="open", description="A lava moat round Sam's base, 3 wide.",
        created_at="2026-10-10T18:25:00.000Z", updated_at="2026-10-10T18:26:10.500Z"),
    row("frost_bow_alex", description="A Frost Bow for Alex: right-click to shoot freezing arrows.",
        made={"scripts": ["frost_bow_alex"], "actions": ["frost_bow_alex"], "presets": [],
              "copies": ["frost_bow_alex"]},
        result="frost_bow_alex done: the bow is in Alex's hand", created_at="2026-10-09T23:59:00.000Z",
        updated_at="2026-10-09T23:59:30.000Z"),
]
COPIES = {
    "railgun_steve": {"action": "railgun", "script": "railgun", "values": {"player": "Steve", "cooldown": 5,
                                                                          "range": 100, "through_walls": True},
                      "live": ["cooldown", "range"], "task": None},
    "frost_bow_alex": {"action": "frost_bow_alex", "script": "frost_bow_alex",
                       "values": {"power": 3, "name": "Frost Bow"}, "live": ["power"], "task": "frost_bow_alex"},
    "summon_around": {"action": "summon_around", "values": {"mob": "zombie", "count": 20}, "task": "cast"},
}


def p(part: str, instruction: str, to: str = "orchestrator", drop: str = ""):
    return SimpleNamespace(part=part, instruction=instruction, to=to, drop=drop)


PARTS = [p("make my railgun pierce blocks", "make Steve's railgun pierce blocks"),
         p("and a frost bow for me", "a frost bow for Steve")]
OTHERS = [p("give me steak", "give Steve 64 steak", to="command"),
          p("and a diamond", "give Steve a diamond", to="command", drop="Steve's inventory is full"),
          p("you're the best", "", to="talk")]


def orch(ask=None) -> Orchestrator:
    return Orchestrator(catalog(), ask, owner="KrownJM", checkpoint=2)


def check(answer: dict, *, replying: bool = True, copies: dict | None = None):
    T = answer_type({t["name"] for t in TASKS}, set(library(catalog())) | names.RESERVED,
                    COPIES if copies is None else copies, catalog(), replying)
    return T.model_validate_json(json.dumps(answer))


def a(kind: str, **fields) -> dict:
    return {"kind": kind, **fields}


# ------------------------------------------------------------------ the opening and the cache

def test_the_opening_is_built_once_and_holds_the_owner_and_the_brains_conversation_header():
    o = orch()
    opening = o.opening()
    assert o.opening() is opening
    assert "THE OWNER: KrownJM." in opening and "{owner}" not in opening
    header = Call(catalog(), None, owner="KrownJM").opening(FakeRefs()).rsplit("\n\n", 1)[1]
    assert header.startswith("THE CONVERSATION SO FAR") and opening.endswith("\n\n" + header)
    assert opening.startswith(PROMPT.read_text(encoding="utf-8").split("{owner}")[0])
    assert "not named (VFG_OWNER is not set)" in Orchestrator(catalog(), None).opening()


async def test_the_call_caches_the_conversation_exactly_as_the_brains_call_does():
    ctx = rows("LINE#801 18:20:01 Alex voice action_oriented 0.9 - | make me a frost bow",
               "AI#802 18:20:03 task frost_bow_alex | new: A Frost Bow for Alex",
               "LINE#803 18:21:30 Sam chat conversational 0.8 - | nice",
               "AI#804 18:22:00 result frost_bow_alex | frost_bow_alex done: the bow is in Alex's hand",
               "GAME#805 18:23:10 death Sam | Sam fell from a high place", LINE, processed=True)
    ask = FakeAsk(TaskAnswer=a(NEW, name="frost_bow_steve", description="A Frost Bow for Steve.",
                               brief="Make Steve a frost bow.", reply="on it"))
    o = orch(ask)
    got = await o.answer(ctx, head=HEAD, parts=PARTS, k=1, given=["follow_up frost_bow_alex"], others=OTHERS,
                         replying=True, tasks=TASKS, copies=COPIES)
    assert got.value.kind == NEW and got.value.name == "frost_bow_steve"  # never made unique here
    sent = ask.calls[0]
    assert sent["output"] == "TaskAnswer"
    assert sent["cached"] == ctx.cached(o.opening(), 2)
    brain = Call(catalog(), None, owner="KrownJM", checkpoint=2).cached(ctx, FakeRefs())
    assert sent["cached"][1:] == brain[1:] and sent["cached"][0] == (o.opening(), True)
    assert sum(m for _, m in sent["cached"][1:]) >= 1  # a checkpoint mark, as the brain's
    assert sent["prompt"] == o.tail(head=HEAD, parts=PARTS, k=1, given=["follow_up frost_bow_alex"],
                                    others=OTHERS, replying=True, tasks=TASKS, copies=COPIES)
    assert ctx.unprocessed() == []  # it never takes rows


def test_the_prompt_and_the_cached_blocks_hold_no_menu_no_actions_no_commands_and_no_world():
    cat = catalog()
    o = orch()
    ctx = rows(LINE)
    tail = o.tail(head=HEAD, parts=PARTS, k=0, given=[], others=OTHERS, replying=True, tasks=TASKS,
                  copies=COPIES)
    text = "".join(t for t, _ in ctx.cached(o.opening(), 2)) + tail
    for name in cat.names():
        assert menu_line(name, cat.get(name)) not in text
    for word in ("ACTION:", "MINECRAFT COMMANDS", "PLAYERS ONLINE", "THE MENU", "THE ACTIONS IN FULL",
                 "items, by slot", "hotbar", "SAVED PLACES", "INCANTATIONS", "TIME:", "NEW SINCE", "THE WORLD NOW"):
        assert word not in text, word


# ------------------------------------------------------------------ the tail

def test_the_tail_holds_its_blocks_in_order():
    tail = orch().tail(head=HEAD, parts=PARTS + [p("and stop the zombies", "stop the zombies round Steve")], k=1,
                       given=["follow_up frost_bow_alex"], others=OTHERS, replying=False, tasks=TASKS,
                       copies=COPIES)
    order = ["TASKS (oldest first", "RUNNING NOW (each copy", HEAD,
             "THE BRAIN'S INSTRUCTION FOR THIS PART: a frost bow for Steve\n"
             "(the part of the line it covers: \"and a frost bow for me\")",
             "THE LINE'S OTHER PARTS FOR YOU:\n"
             "- \"make my railgun pierce blocks\": make Steve's railgun pierce blocks -> follow_up frost_bow_alex\n"
             "- \"and stop the zombies\": stop the zombies round Steve (asked after this one)",
             "THE LINE'S ACTION PARTS (the brain runs them; your reply is said once they are done):\n"
             "- command: give Steve 64 steak\n"
             "- command: give Steve a diamond — dropped, it cannot be done: Steve's inventory is full",
             "THE LINE'S TALK PARTS (only your reply answers them):\n- \"you're the best\"",
             "THE REPLY: not yours — leave reply empty, unless you answer reply only."]
    at = [tail.index(x) for x in order]
    assert at == sorted(at) and tail.startswith("\nTASKS (") and tail.endswith("reply only.\n")
    assert "a frost bow for Steve (asked after" not in tail  # the part itself is not among the others


def test_the_tail_says_none_and_the_reply_is_yours_on_a_lone_part():
    tail = orch().tail(head=HEAD, parts=PARTS[:1], k=0, given=[], others=[p("x", "y", to="orchestrator")],
                       replying=True, tasks=[], copies={})
    assert "TASKS: none yet\n\nRUNNING NOW: none\n\n" + HEAD in tail
    for title in ("THE LINE'S OTHER PARTS FOR YOU: none", "THE LINE'S TALK PARTS (only your reply answers them): none",
                  "THE LINE'S ACTION PARTS (the brain runs them; your reply is said once they are done): none"):
        assert title in tail
    assert tail.endswith("THE REPLY: yours — the line's one chat line, said once the action parts are done.\n")


# ------------------------------------------------------------------ TASKS and RUNNING NOW

def test_task_lines_show_each_task_oldest_first_with_what_it_made_its_copies_running_and_its_result():
    text = task_lines(TASKS, COPIES, today=TODAY)
    lines = text.splitlines()
    assert lines[0].startswith("TASKS (oldest first: name, for whom, status, when last written")
    assert lines[1] == ("- frost_bow_alex, for Alex, done, 2026-10-09 23:59:30: A Frost Bow for Alex: right-click "
                        "to shoot freezing arrows.")  # yesterday: its date shown
    assert lines[2] == ("  made: scripts frost_bow_alex; actions frost_bow_alex; copies frost_bow_alex; "
                        "running now: frost_bow_alex")
    assert lines[3] == "  last result: frost_bow_alex done: the bow is in Alex's hand"
    assert lines[4] == "- lava_moat_sam, for Sam, open, 18:26:10: A lava moat round Sam's base, 3 wide."
    assert lines[5] == "  made: nothing yet; running now: none" and len(lines) == 6  # no result yet: no line
    assert task_lines([], COPIES) == "TASKS: none yet"
    assert "  made: not known; running now: none" in task_lines([row("castle_bob", made=None)], {}, today=TODAY)


def test_the_tail_shows_times_against_its_own_clock():
    o = Orchestrator(catalog(), None, now=lambda: NOW)
    tail = o.tail(head=HEAD, parts=PARTS, k=0, given=[], others=[], replying=True, tasks=TASKS, copies={})
    assert "- lava_moat_sam, for Sam, open, 18:26:10: " in tail and ", done, 2026-10-09 23:59:30: " in tail


def test_running_lines_show_each_value_now_its_limits_or_choices_live_or_restart_and_who_started_it():
    text = running_lines(COPIES, catalog())
    lines = text.splitlines()
    assert lines[0].startswith("RUNNING NOW (each copy: its action, who started it; then every value")
    i = lines.index("- railgun_steve: railgun, the brain started it")
    live, restart = lines[i + 1], lines[i + 2]
    assert live.startswith("  live: ") and "cooldown=5 (0..60)" in live and "range=100 (4..300)" in live
    assert "damage_what=all (all|mobs|hostile|players)" in live and "through_walls=true" in live
    assert ("damage_type=magic (player_attack|magic|lightning_bolt|explosion|freeze|on_fire|sonic_boom|arrow|…+"
            in live)  # choices capped at 8
    assert restart == "  restart: player=Steve item=Railgun"  # not live: a restart; the default when not set
    assert "fire=" not in text  # a fixed value is left out
    # An action not loaded: running.json's values and live list.
    assert "- frost_bow_alex: frost_bow_alex, started by task frost_bow_alex\n  live: power=3\n" \
           "  restart: name=\"Frost Bow\"" in text
    assert "- summon_around: summon_around, an incantation started it" in text
    assert "count=20 (1..100)" in text and "mob=zombie" in text
    assert running_lines({}, catalog()) == "RUNNING NOW: none"
    long = {"c": {"action": "command", "values": {"commands": ["x" * 80]}, "task": None}}
    shown = running_lines(long, catalog()).split("commands=", 1)[1].split()[0]
    assert len(shown) == 40 and shown.endswith("…")


# ------------------------------------------------------------------ the answer's checks

def test_good_answers_of_each_kind_pass():
    assert check(a(NEW, name="frost_bow_steve", description="A Frost Bow for Steve.", brief="Make one.",
                   reply="on it")).kind == NEW
    assert check(a(FOLLOW_UP, task="frost_bow_alex", description="A Frost Bow, now with fire.",
                   brief="Add fire.", reply="on it")).task == "frost_bow_alex"
    e = check(a(EDIT, copy="railgun_steve", values=[{"key": "cooldown", "value": "0"},
                                                   {"key": "through_walls", "value": "false"}], reply="done"))
    assert e.edits() == {"cooldown": "0", "through_walls": "false"}
    assert check(a(STOP, copies=["railgun_steve", "frost_bow_alex"], reply="stopped")).copies == \
        ["railgun_steve", "frost_bow_alex"]
    assert check(a(REPLY, reply="better how?"), replying=False).reply == "better how?"
    assert check(a(NEW, name="frost_bow_steve", description="d", brief="b"), replying=False).reply == ""
    # An edit of a copy whose action is not loaded is checked for its copy and values alone.
    assert check(a(EDIT, copy="frost_bow_alex", values=[{"key": "power", "value": "9"}], reply="ok")).copy


@pytest.mark.parametrize("answer,replying,says", [
    (a(NEW, name="x_y", description="d", brief="b"), True, "the reply is empty"),
    (a(REPLY), False, "the reply is empty"),
    (a(NEW, name="x_y", description="d", brief="b", reply="x" * 301), True, "301 characters"),
    (a(NEW, name="frost_bow", description="d", brief="b", copy="railgun_steve"), False, "for an edit alone"),
    (a(NEW, name="frost_bow", description="d", brief="b", values=[{"key": "a", "value": "1"}]), False,
     "for an edit alone"),
    (a(FOLLOW_UP, task="frost_bow_alex", description="d", brief="b", copies=["railgun_steve"]), False,
     "for a stop alone"),
    (a(NEW, name="railgun", description="d", brief="b"), False,
     "railgun is a library action's name: add whose it is, as frost_bow_alex"),
    (a(NEW, name="cast", description="d", brief="b"), False, "the bridge's task for an incantation"),
    (a(NEW, name="Frost Bow", description="d", brief="b"), False, "lowercase letters"),
    (a(NEW, name="x" * 33, description="d", brief="b"), False, "2 to 32 characters"),
    (a(NEW, name="frost_bow_steve", description="", brief="b"), False, "the description is empty"),
    (a(NEW, name="frost_bow_steve", description="d", brief=" "), False, "the brief is empty"),
    (a(NEW, name="frost_bow_steve", task="frost_bow_alex", description="d", brief="b"), False, "leave task empty"),
    (a(FOLLOW_UP, task="castle_bob", description="d", brief="b"), False, "'castle_bob' is not on TASKS"),
    (a(FOLLOW_UP, task="frost_bow_alex", description="", brief="b"), False, "the description is empty"),
    (a(FOLLOW_UP, task="frost_bow_alex", description="d", brief=""), False, "the brief is empty"),
    (a(FOLLOW_UP, task="frost_bow_alex", name="frost_bow_2", description="d", brief="b"), False,
     "keeps its task's name"),
    (a(EDIT, copy="railgun_alex", values=[{"key": "cooldown", "value": "0"}]), False, "not a copy under RUNNING NOW"),
    (a(EDIT, copy="railgun_steve"), False, "changes no value"),
    (a(EDIT, copy="railgun_steve", values=[{"key": " ", "value": "0"}]), False, "changes no value"),
    (a(EDIT, copy="railgun_steve", values=[{"key": "colour", "value": "red"}]), False, "railgun has no value colour"),
    (a(EDIT, copy="railgun_steve", values=[{"key": "range", "value": "far"}]), False,
     "the edit of railgun_steve: range=far"),
    (a(EDIT, copy="railgun_steve", task="frost_bow_alex", values=[{"key": "cooldown", "value": "0"}]), False,
     "an edit changes no task"),
    (a(EDIT, copy="railgun_steve", copies=["railgun_steve"], values=[{"key": "cooldown", "value": "0"}]), False,
     "for a stop alone"),
    (a(STOP), False, "a stop names no copy"),
    (a(STOP, copies=["railgun_alex"]), False, "railgun_alex: not running"),
    (a(REPLY, reply="ok", task="frost_bow_alex"), False, "a reply only starts nothing"),
    (a(REPLY, reply="ok", brief="b"), False, "a reply only starts nothing"),
    (a(REPLY, reply="ok", copies=["railgun_steve"]), False, "for a stop alone"),
    (a("cancel", reply="ok"), False, "kind"),
])
def test_each_rule_sends_a_wrong_answer_back_with_why(answer, replying, says):
    with pytest.raises(ValidationError, match=says):
        check(answer, replying=replying)


def test_a_follow_up_may_repeat_its_tasks_name_and_the_schema_is_the_same_for_every_input():
    assert check(a(FOLLOW_UP, task="frost_bow_alex", name="frost_bow_alex", description="d", brief="b"),
                 replying=False).kind == FOLLOW_UP
    one = answer_type(set(), set(), {}, None, False)
    two = answer_type({"frost_bow_alex"}, {"railgun"}, COPIES, catalog(), True)
    assert one.__name__ == two.__name__ == "TaskAnswer"
    assert one.model_json_schema() == two.model_json_schema()
    schema = one.model_json_schema()
    assert schema["title"] == "TaskAnswer" and list(schema["properties"])[-1] == "reply"
    assert list(schema["properties"]) == ["kind", "task", "name", "description", "brief", "copy", "values",
                                          "copies", "reply"]


def test_edits_keep_text_for_the_field_to_read_and_decode_a_json_list():
    cls = catalog().get("railgun")
    v = AnswerBase(kind=EDIT, copy="railgun_steve", values=[
        {"key": "range", "value": "30"}, {"key": "through_walls", "value": "true"},
        {"key": " damage_spare ", "value": '["Steve","Alex"]'}, {"key": "item", "value": " Big Gun "}])
    got = v.edits()
    assert got == {"range": "30", "through_walls": "true", "damage_spare": ["Steve", "Alex"], "item": "Big Gun"}
    e = check_edit(cls, COPIES["railgun_steve"]["values"], got)  # an int, a bool and a list field
    assert e.values["range"] == 30 and e.values["through_walls"] is True
    assert e.values["damage_spare"] == ["Steve", "Alex"] and set(e.changed) == {"range", "damage_spare", "item"}
    assert AnswerBase(kind=EDIT, values=[{"key": "cooldown", "value": "0"}]).edits() == {"cooldown": "0"}
    assert check_edit(cls, {}, {"cooldown": "0"}).values["cooldown"] == 0
    with pytest.raises(ValidationError, match="through_walls is true or false, not maybe"):
        check(a(EDIT, copy="railgun_steve", values=[{"key": "through_walls", "value": "maybe"}]), replying=False)


# ------------------------------------------------------------------ the answer, normalised

async def test_the_reply_is_emptied_when_it_is_not_this_calls_and_every_string_is_stripped():
    ask = FakeAsk(TaskAnswer=a(FOLLOW_UP, task=" frost_bow_alex ", name="frost_bow_alex",
                               description=" A Frost Bow, now on fire. ", brief=" Add fire. ", reply=" on it "))
    got = await orch(ask).answer(rows(LINE), head=HEAD, parts=PARTS, k=1, given=["new railgun_pierce_steve"],
                                 others=[], replying=False, tasks=TASKS, copies=COPIES)
    v = got.value
    assert (v.kind, v.task, v.name, v.description, v.brief, v.reply) == \
        (FOLLOW_UP, "frost_bow_alex", "", "A Frost Bow, now on fire.", "Add fire.", "")
    ask = FakeAsk(TaskAnswer=a(REPLY, reply=" more damage, or no cooldown? "))
    got = await orch(ask).answer(rows(LINE), head=HEAD, parts=PARTS, k=1, given=["failed"], others=[],
                                 replying=False, tasks=TASKS, copies=COPIES)
    assert got.value.reply == "more damage, or no cooldown?"  # a reply only keeps its reply
    ask = FakeAsk(TaskAnswer=a(STOP, copies=[" railgun_steve ", ""], reply="stopped"))
    got = await orch(ask).answer(rows(LINE), head=HEAD, parts=PARTS, k=0, given=[], others=[], replying=True,
                                 tasks=TASKS, copies=COPIES)
    assert got.value.copies == ["railgun_steve"] and got.value.reply == "stopped"


async def test_a_failed_call_raises_llm_error_and_the_answer_is_checked_against_the_tasks_given():
    ask = FakeAsk(TaskAnswer=LlmError("no answer within 15 s"))
    with pytest.raises(LlmError):
        await orch(ask).answer(rows(LINE), head=HEAD, parts=PARTS, k=0, given=[], others=[], replying=True,
                               tasks=TASKS, copies=COPIES)
    ask = FakeAsk(TaskAnswer=a(FOLLOW_UP, task="frost_bow_alex", description="d", brief="b"))
    with pytest.raises(ValidationError, match="not on TASKS"):
        await orch(ask).answer(rows(LINE), head=HEAD, parts=PARTS, k=1, given=["failed"], others=[],
                               replying=False, tasks=[], copies={})
    ask = FakeAsk(TaskAnswer=a(NEW, name="railgun", description="d", brief="b"))
    with pytest.raises(ValidationError, match="library action's name"):
        await orch(ask).answer(rows(LINE), head=HEAD, parts=PARTS, k=1, given=["failed"], others=[],
                               replying=False, tasks=[], copies={})


def test_describe_gives_one_log_line_per_kind():
    assert describe(AnswerBase(kind=NEW, name="frost_bow_steve", brief="Make one.", reply="on it")) == \
        "new frost_bow_steve: Make one. || reply: on it"
    assert describe(AnswerBase(kind=FOLLOW_UP, task="frost_bow_alex", brief="Add fire.")) == \
        "follow_up frost_bow_alex: Add fire."
    assert describe(AnswerBase(kind=EDIT, copy="railgun_steve", values=[{"key": "cooldown", "value": "0"}])) == \
        "edit railgun_steve: cooldown=0"
    assert describe(AnswerBase(kind=STOP, copies=["a_b", "c_d"])) == "stop a_b, c_d"
    assert describe(AnswerBase(kind=REPLY, reply="huh?")) == "reply only || reply: huh?"
