"""The router: its answer's checks (each choice's fields, the loops and tasks of the moment, a
report's choices), what it drops and logs, the schema that never changes, its event, its prompt's
layout (the opening marked, the conversation, the tail in order) and the call it makes."""

import json

import pytest
from pydantic import ValidationError
from vfg_llm import LlmError

from brain.call import HandOff
from brain.context import parse
from brain.router import CHOICES, MARKS, PROMPT, Router, RouterEvent, route_type
from brain.world import Seen

from conftest import NOW, FakeAsk, FakeRefs, catalog, made, rows

LINE = "LINE#58 18:29:40 Alex voice to_ai 0.91 -9dB | can I get a grappling hook that pulls me up"
TYPED = "LINE#60 18:29:50 Sam chat to_ai 0.88 - | never mind the moat"
HOOK = {"words": "“can I get a grappling hook that pulls me up”",
        "picture": "a fishing rod named Grappling Hook; a right-click shoots a line up to 40 blocks and pulls Alex to "
                   "the block it catches, fast, with a reel sound",
        "assumed": "Alex flies there, never teleports; nothing happens aiming at open sky; no fall damage",
        "cast": "right-click with the Grappling Hook, a rod given to Alex",
        "done_when": ["Alex has the rod", "a right-click at a wall pulls Alex to it through the air"]}
TASKS = {"grappling_hook_alex", "lava_moat_sam"}
LOOPS = ["L2", "L4"]


def route(to: str, *, reason: str = "what it is about", loop: str = "", hand_offs: tuple[dict, ...] = (),
          follow_ups: tuple[dict, ...] = (), reply: str = "") -> dict:
    """One answer as the model writes it."""
    return {"reason": reason, "to": to, "loop": loop, "hand_offs": list(hand_offs), "follow_ups": list(follow_ups),
            "reply": reply}


def hook(**parts) -> dict:
    """A hand-off as the model writes it: Alex's grappling hook, `parts` changed."""
    return {"name": "grappling_hook_alex", "description": "A Grappling Hook for Alex: right-click to be pulled up.",
            **HOOK, **parts}


def follow_up(task: str, brief: str = "", description: str = "", cancel: bool = False) -> dict:
    return {"task": task, "brief": brief, "description": description, "cancel": cancel}


def check(answer: dict, *, loops=LOOPS, tasks=TASKS, report: bool = False, cat=None):
    return route_type(cat or catalog(), loops=loops, tasks=tasks, report=report).model_validate_json(json.dumps(answer))


# ------------------------------------------------------------------ the answer

def test_each_choice_with_its_own_field_passes():
    assert check(route("new_loop")).to == "new_loop"
    assert check(route("loop", loop="L4")).loop == "L4"
    placed = check(route("new_implementer", hand_offs=(hook(),)))
    assert isinstance(placed.hand_offs[0], HandOff) and placed.hand_offs[0].name == "grappling_hook_alex"
    followed = check(route("implementer", follow_ups=(follow_up("grappling_hook_alex", "reach twice as far"),)))
    assert followed.follow_ups[0].task == "grappling_hook_alex" and not followed.follow_ups[0].cancel
    cancelled = check(route("implementer", follow_ups=(follow_up("lava_moat_sam", cancel=True),)))
    assert cancelled.follow_ups[0].cancel and cancelled.follow_ups[0].brief == ""
    assert check(route("nowhere", reply="Still building your hook, nearly there!")).reply.startswith("Still")
    assert check(route("nowhere", reply="x" * 300)).reply == "x" * 300   # a long line goes out rather than ask again
    assert check(route("nowhere")).reply == check(route("nowhere", reply="  ")).reply == ""  # the agent's line goes
    # a line about two tasks' things: one follow-up a task
    both = check(route("implementer", follow_ups=(follow_up("grappling_hook_alex", "pull twice as fast"),
                                                  follow_up("lava_moat_sam", cancel=True))))
    assert [(f.task, f.cancel) for f in both.follow_ups] == [("grappling_hook_alex", False), ("lava_moat_sam", True)]
    # a report's: an implementer, a new one, a new loop, or nowhere with nothing more to say
    for answer in (route("new_loop"), route("new_implementer", hand_offs=(hook(),)), route("nowhere"),
                   route("implementer", follow_ups=(follow_up("lava_moat_sam", "the moat on the north side too"),))):
        assert check(answer, loops=[], report=True).to == answer["to"]
    assert check(route("nowhere", reply="Enjoy it!"), loops=[], report=True).reply == "Enjoy it!"


@pytest.mark.parametrize("reason, to", [
    ("Sam repeats the village ask L2 is on, so: loop L2", "loop"),
    ("Alex asks for a change; no copy runs, so: implementer grappling_hook_alex", "implementer"),
    ("arrows and the night so:new_loop", "new_loop"),
    ("a greeting to the AI, so: nowhere.", "nowhere"),
    ("Sam said so: dig it. Items now, so: a new loop", "new_loop"),     # no choice named after the last so:
    ("what it is about", "nowhere"),                                   # no so: at all: not checked
])
def test_a_reason_that_ends_with_the_choice_made_or_with_none_passes(reason, to):
    fields = {"loop": "L2"} if to == "loop" else {
        "follow_ups": (follow_up("grappling_hook_alex", "reach further"),)} if to == "implementer" else {}
    assert check(route(to, reason=reason, **fields)).to == to


@pytest.mark.parametrize("given, want", [("l4", "L4"), (" L4 ", "L4"), ("4", "L4")])
def test_a_loop_id_is_read_as_the_offered_one_it_means(given, want):
    assert check(route("loop", loop=given)).loop == want


WRONG = [
    (route("loop", loop="L3"), {}, "loop 'L3' is not under WORK UNDER WAY: name one that is (L2, L4), or choose "
                                   "new_loop"),
    (route("loop", loop=""), {}, "loop '' is not under WORK UNDER WAY"),
    (route("loop", loop="the teleport"), {}, "is not under WORK UNDER WAY"),
    (route("loop", loop="L2"), {"loops": []}, "no loop is under WORK UNDER WAY to take a line: choose new_loop"),
    (route("loop", loop="L2"), {"report": True}, "this is a report's hand-off, and no loop takes one: follow its "
                                                 "task up (implementer), start a new_loop, or nowhere"),
    (route("new_implementer"), {}, "new_implementer needs its hand-off: the name, the description and every part "
                                   "of the brief"),
    (route("new_implementer", hand_offs=(hook(), hook(name="jump_boots_alex"))), {},
     "new_implementer hands off one thing: for several new things, choose new_loop, which splits them"),
    (route("new_implementer", hand_offs=(hook(name="Grappling Hook"),)), {}, "hand-off name 'Grappling Hook': "
                                                                             "lowercase letters"),
    (route("new_implementer", hand_offs=(hook(name="railgun"),)), {}, "railgun is a library action's name"),
    (route("new_implementer", hand_offs=(hook(picture=" "),)), {}, "the hand-off grappling_hook_alex's brief has no "
                                                                    "picture: write every part"),
    (route("new_implementer", hand_offs=(hook(done_when=[]),)), {}, "has no done_when"),
    (route("implementer"), {}, "implementer needs its follow-up: the task's name as TASKS shows it, with its brief, "
                               "or cancel"),
    (route("implementer", follow_ups=(follow_up("lava_moat_sam", "wider"), follow_up("lava_moat_sam", cancel=True))),
     {}, "a task is followed up twice: one follow-up a task, saying everything"),
    (route("implementer", follow_ups=(follow_up("lava_moat_sam", "wider"), follow_up("lava_moat_steve", "deeper"))),
     {}, "task 'lava_moat_steve' is not under TASKS"),
    (route("implementer", follow_ups=(follow_up("lava_moat_sam", "the north side"),
                                      follow_up("grappling_hook_alex", "reach further"))), {"report": True},
     "a report's hand-off follows one task up: the one what is left belongs to"),
    (route("implementer", follow_ups=(follow_up("lava_moat_steve", "wider"),)), {},
     "task 'lava_moat_steve' is not under TASKS: follow up one that is, or hand it off as new"),
    (route("implementer", follow_ups=(follow_up("lava_moat_sam", description="A wider moat."),)), {},
     "the follow-up of lava_moat_sam has no brief"),
    (route("implementer", follow_ups=(follow_up("lava_moat_sam", "wider"),)), {"tasks": set()}, "is not under TASKS"),
    (route("new_loop", reason="a change to the hook grappling_hook_alex made, so: implementer grappling_hook_alex"),
     {}, "the reason ends choosing implementer, and to is new_loop: make them agree, the reason ending with the "
         "choice you mean"),
    (route("new_implementer", reason="the same ask, reworded, as the moat being dug. So: nowhere.",
           hand_offs=(hook(),)), {}, "the reason ends choosing nowhere, and to is new_implementer"),
    (route("nowhere", reply="x" * 301), {}, "the reply is 301 characters, past the 300 a chat line may hold"),
    (route("nowhere", reply="x" * 301), {"report": True}, "301 characters"),
    (route("upstairs"), {}, "Input should be 'new_loop', 'loop', 'new_implementer', 'implementer' or 'nowhere'"),
]


@pytest.mark.parametrize("answer, moment, says", WRONG)
def test_each_rule_sends_a_wrong_answer_back_with_why(answer, moment, says):
    with pytest.raises(ValidationError) as e:
        check(answer, **moment)
    assert says in str(e.value)


def test_what_the_choice_does_not_use_is_dropped_and_logged_never_sent_back(capsys):
    got = check(route("new_loop", loop="L2", reply="On it!", hand_offs=(hook(),),
                      follow_ups=(follow_up("lava_moat_sam", "wider"),)))
    assert (got.loop, got.reply, got.hand_offs, got.follow_ups) == ("", "", [], [])
    out = capsys.readouterr().out
    assert ("router to new_loop: dropped, it would do nothing: loop 'L2'; hand-off grappling_hook_alex; "
            "follow-up of lava_moat_sam; reply 'On it!'") in out
    got = check(route("loop", loop="L2", reply="Still on it."))
    assert (got.loop, got.reply) == ("L2", "") and "router to loop: dropped, it would do nothing: reply" in \
        capsys.readouterr().out
    # a dropped field's own checks never run: a bad hand-off on a follow-up's answer is dropped, not sent back
    got = check(route("implementer", hand_offs=(hook(name="Bad Name"),),
                      follow_ups=(follow_up("lava_moat_sam", "wider"),)))
    assert got.hand_offs == [] and got.follow_ups[0].task == "lava_moat_sam"
    assert "hand-off Bad Name" in capsys.readouterr().out
    got = check(route("nowhere", reply="Still building it.", loop="L2", hand_offs=(hook(),)))
    assert (got.reply, got.loop, got.hand_offs) == ("Still building it.", "", [])
    assert "router to nowhere: dropped, it would do nothing: loop 'L2'; hand-off grappling_hook_alex" in \
        capsys.readouterr().out
    check(route("nowhere"), loops=[], report=True)               # nothing set that the choice ignores: no line
    check(route("new_loop"))
    assert capsys.readouterr().out == ""


def test_target_names_where_the_work_goes():
    assert check(route("loop", loop="l2")).target() == "L2"
    assert check(route("new_implementer", hand_offs=(hook(),))).target() == "grappling_hook_alex"
    assert check(route("implementer", follow_ups=(follow_up("lava_moat_sam", "wider"),))).target() == "lava_moat_sam"
    assert check(route("implementer", follow_ups=(follow_up("lava_moat_sam", cancel=True),))).target() == \
        "cancel lava_moat_sam"
    assert check(route("implementer", follow_ups=(follow_up("grappling_hook_alex", "faster"),
                                                  follow_up("lava_moat_sam", cancel=True)))).target() == \
        "grappling_hook_alex, cancel lava_moat_sam"
    assert check(route("new_loop")).target() == check(route("nowhere", reply="Soon!")).target() == ""


def test_the_schema_is_the_same_for_every_catalog_loops_tasks_and_event_and_every_field_is_required():
    other = made("lava_moat_sam", '''
        from actions._types import Action, Players


        class LavaMoatSam(Action):
            """Dig a lava moat round a player's base."""

            script = "lava_moat_sam"
            runs = "once"
            takes = ["player"]

            player: Players = "@a"
            """whose base"""
    ''')
    one = route_type(catalog(), loops=LOOPS, tasks=TASKS)
    two = route_type(other, loops=[], tasks=set(), report=True)
    three = route_type(catalog(), loops=["L9"], tasks=["x_y"])
    assert one.__name__ == two.__name__ == three.__name__ == "Route"
    schema = one.model_json_schema()
    assert schema == two.model_json_schema() == three.model_json_schema()
    assert list(schema["properties"]) == ["reason", "to", "loop", "hand_offs", "follow_ups", "reply"]  # decide first
    assert schema["required"] == list(schema["properties"])
    for name in ("HandOff", "FollowUp"):
        part = schema["$defs"][name]
        assert part["required"] == list(part["properties"]), name
    assert schema["properties"]["to"]["enum"] == list(CHOICES)


# ------------------------------------------------------------------ the event

def test_a_lines_event_shows_the_line_and_the_conversation_agents_answer():
    row = parse(LINE, now=NOW)
    event = RouterEvent("LINE", row, said="On it!", reason="a new item: a grappling hook")
    assert event.ref() == "#58"
    assert event.show() == ("THE LINE #58, 18:29:40: Alex said: can I get a grappling hook that pulls me up\n"
                            "(the conversation agent already answered the player: “On it!”)\n"
                            "(the conversation agent handed it off: a new item: a grappling hook)")
    silent = RouterEvent("LINE", parse(TYPED, now=NOW))
    assert silent.show() == ("THE LINE #60, 18:29:50: Sam typed: never mind the moat\n"
                             "(the conversation agent said nothing)")


def test_a_reports_event_shows_the_task_its_report_and_what_is_left():
    task = {"name": "lava_moat_sam", "player": "Sam", "line": "dig me a lava moat", "description": "A lava moat "
            "round Sam's base.", "brief": "THE PLAYER'S WORDS\n“dig me a lava moat”"}
    event = RouterEvent("REPORT", task=task, said="Your moat's in, the north side's coming!", status="done",
                        report="DONE WHEN\n- the moat is round the base: yes\n- the north side: missing\n",
                        left="the north side of the moat")
    assert event.ref() == "lava_moat_sam"
    shown = event.show()
    assert shown.startswith("THE REPORT: the implementer of lava_moat_sam ended; the conversation agent read the "
                            "report, told the player and wrote the task done. It handed off what is left.\n"
                            "lava_moat_sam was for Sam, who said: dig me a lava moat\n"
                            "WHAT IT IS: A lava moat round Sam's base.\nITS LATEST BRIEF:\n")
    assert "ITS REPORT:\nDONE WHEN\n- the moat is round the base: yes\n- the north side: missing\n(the conversation " \
           "agent already answered the player: “Your moat's in, the north side's coming!”)" in shown
    assert shown.endswith("WHAT IS LEFT, as the conversation agent read it: the north side of the moat")
    assert RouterEvent("REPORT", task={"name": "x" * 70}).ref() == "x" * 56
    assert RouterEvent("REPORT").ref() == "report"


# ------------------------------------------------------------------ the prompt and the call

def test_the_opening_holds_the_rules_the_owner_the_library_and_the_commands_and_is_built_once():
    router, refs = Router(catalog(), None, owner="KrownJM", hidden=frozenset({"fill"})), FakeRefs()
    opening = router.opening(refs)
    assert opening.startswith("You are the router of the AI")
    assert "THE OWNER: KrownJM, who runs the server." in opening and "{owner}" not in opening
    assert "THE FIVE CHOICES" in opening and "grappling hook" in opening
    assert "THE MENU (each action the brain can run" in opening and "- railgun — Give a player a railgun" in opening
    assert "THE ACTIONS IN FULL:\nACTION: " in opening and "ACTION: railgun" in opening and "ACTION: command" in opening
    assert "MINECRAFT COMMANDS (the brain's command action runs them; name — what it does):\ngive — Gives" in opening
    assert opening.endswith("THE CONVERSATION SO FAR (oldest first; each row: its time in UTC, who, and for a "
                            "player's line Jev's class):\n")
    assert router.opening(refs) is opening                     # built once while the library and refs stay
    assert router.opening(FakeRefs()) is not opening           # new references, a new opening
    assert "(not loaded yet: the brain writes commands from what it knows of 26.3)" in router.opening(None)
    assert "not named (VFG_OWNER is not set)" in Router(catalog(), None).opening(refs)

    excluded = []

    class Refs(FakeRefs):
        def menu(self, exclude=()):
            excluded.append(exclude)
            return "give — Gives an item to a player."
    router.opening(Refs())
    assert excluded == [frozenset({"fill"})]                   # the commands the brain never sees, left out


def test_the_rules_hold_no_example_from_the_benches_lines():
    rules = PROMPT.read_text(encoding="utf-8").lower()
    for asked in ("stirka", "styrka", "krown", "fireball", "fire ball", "blizzard", "flame aura", "fire aura",
                  "ice aura", "freeze", "steak", "galick", "hollow purple", "gatling", "spirit bomb", "saiyan",
                  "wither", "sniper", "infinity", "death gaze", "ice staff", "nuke", "castle", "trident", "meteor",
                  "where are you", "orchestrator", "respond", "music stopped", "darkness", "any incantations",
                  "aura of ice", "ice one"):
        assert asked not in rules, asked
    assert "{owner}" in rules and "new_loop" in rules and "nowhere" in rules
    # the replay's fixes: the reason ends in the choice; an edit only of a copy that runs; a stop is no
    # cancel; a line that asks for nothing goes nowhere; one follow-up a task
    for rule in ('"… so: implementer lava_moat_sam"', "an edit is only for a copy running now lists",
                 "never a cancel", "does the line ask for anything?", "one follow-up for each task",
                 "never a second new task", "never by a word the line shares with a task's name",
                 "promises work nobody will now do", "always write your own reply",
                 # the second round: a broken thing, a clarification and a look ask; a stop only of what runs;
                 # never a second worker beside an implementer
                 "a fix, from that thing's task", "a clarification of an ask under way",
                 "a question only a look at the game answers", "a stop is only of a copy running now lists",
                 "while an implementer is on it", "two workers on one thing undo each other",
                 "steve echoes sam's ask",
                 # the last round: the busy task takes only a change to its own thing, its phrases included; a
                 # complaint is a fix; the reason names the copy it rests on
                 "a line that asks that thing to change", "a phrase the task bound", "a new thing asked for while "
                 "another task builds is new", "never a reply that it is fixed", "running now lists none, so:",
                 "a new thing at the same base, not a change to the moat"):
        assert rule in rules, rule


async def test_a_line_is_routed_with_its_prompt_laid_out_for_the_cache():
    ctx = rows("LINE#50 18:27:10 Sam voice to_ai 0.80 -9dB | hey AI",
               "AI#51 18:27:12 say - | Hey Sam!",
               "LINE#58 18:29:40 Alex voice to_ai 0.91 -9dB | can I get a grappling hook that pulls me up")
    answer = route("new_implementer", reason="a new item; no task or loop is on it", hand_offs=(hook(),),
                   reply="On it!")
    ask = FakeAsk(Route=answer)
    router = Router(catalog(), ask, owner="KrownJM")
    refs = FakeRefs()
    event = RouterEvent("LINE", ctx.rows[-1], said="On it!", reason="a new item")
    seen = Seen(players="PLAYERS ONLINE: Alex, Sam", running="RUNNING NOW: none")
    got = await router.route(event, ctx, seen, "TASKS: none yet", "WORK UNDER WAY: no brain loops",
                             loops=[], tasks=set(), refs=refs)
    assert got.value.to == "new_implementer" and got.value.reply == "" and got.value.target() == "grappling_hook_alex"
    [call] = ask.calls
    assert (call["output"], call["component"], call["call_id"]) == ("Route", "router", "#58")
    assert call["cached"] == ctx.cached(router.opening(refs), 5, marks=MARKS)
    assert call["cached"][0] == (router.opening(refs), True)               # the opening, marked
    assert "".join(text for text, _ in call["cached"][1:]).splitlines() == [
        "18:27:10 Sam (to_ai): hey AI", "18:27:12 AI: Hey Sam!",
        "18:29:40 Alex (to_ai): can I get a grappling hook that pulls me up"]
    prompt = call["prompt"]
    order = ["THIS SERVER'S OWN ACTIONS: none yet", "THE WORLD NOW:\nPLAYERS ONLINE: Alex, Sam", "TASKS: none yet",
             "WORK UNDER WAY: no brain loops", "THE LINE #58, 18:29:40: Alex said: can I get a grappling hook"]
    at = [prompt.index(part) for part in order]
    assert at == sorted(at), at
    assert prompt.endswith("(the conversation agent handed it off: a new item)\n")
    assert "THE CONVERSATION SO FAR" not in prompt and "You are the router" not in prompt  # all in the cached part


async def test_a_reports_hand_off_is_routed_under_the_tasks_name_and_never_to_a_loop():
    task = {"name": "lava_moat_sam", "player": "Sam", "line": "dig me a lava moat", "description": "A lava moat."}
    event = RouterEvent("REPORT", task=task, said="Moat's in!", status="done", report="the north side: missing",
                        left="the north side")
    ask = FakeAsk(Route=[route("loop", loop="L2"),
                         route("implementer", follow_ups=(follow_up("lava_moat_sam", "dig the north side too"),))])
    router = Router(catalog(), ask)
    with pytest.raises(ValidationError, match="no loop takes one"):  # structured() would send it back once
        await router.route(event, rows(), Seen(), "TASKS: …", "WORK UNDER WAY: …", loops=["L2"], tasks=TASKS,
                           refs=None)
    got = await router.route(event, rows(), Seen(), "TASKS: …", "WORK UNDER WAY: …", loops=["L2"], tasks=TASKS,
                             refs=None)
    assert got.value.target() == "lava_moat_sam"
    assert [c["call_id"] for c in ask.calls] == ["lava_moat_sam", "lava_moat_sam"]
    assert ask.calls[-1]["prompt"].rstrip("\n").endswith("WHAT IS LEFT, as the conversation agent read it: the north "
                                                         "side")


async def test_the_tail_shows_this_servers_own_actions_in_full_after_the_conversation():
    cat = made("glimmer_path", '''
        from actions._types import Action, Players


        class GlimmerPath(Action):
            """Leave a glowing path behind a player."""

            script = "glimmer_path"
            runs = "until_stopped"
            takes = ["player"]

            player: Players = "@a"
            """who leaves the path"""
    ''')
    ask = FakeAsk(Route=route("new_loop"))
    router = Router(cat, ask)
    await router.route(RouterEvent("LINE", parse(LINE, now=NOW)), rows(), Seen(), "TASKS: none yet",
                       "WORK UNDER WAY: no brain loops", loops=[], tasks=(), refs=FakeRefs())
    prompt = ask.calls[0]["prompt"]
    assert prompt.startswith("\nTHIS SERVER'S OWN ACTIONS (made by implementers; on the menu like the rest):\n"
                             "- glimmer_path — Leave a glowing path behind a player.")
    assert "ACTION: glimmer_path" in prompt and "glimmer_path" not in router.opening(FakeRefs())


async def test_a_failed_call_is_the_callers_to_handle():
    ask = FakeAsk(Route=LlmError("no answer from deepseek/deepseek-v4.1-flash within 30 s"))
    with pytest.raises(LlmError):
        await Router(catalog(), ask).route(RouterEvent("LINE", parse(LINE, now=NOW)), rows(), Seen(), "", "",
                                           loops=[], tasks=(), refs=None)


def test_a_nowhere_reply_that_promises_or_claims_a_thing_done_is_sent_back_and_a_status_passes():
    for reply in ("I'll fix it right now!", "Yep, it's fixed.", "Done, enjoy!"):
        with pytest.raises(ValidationError, match="nowhere starts nothing, so its reply may neither promise"):
            check(route("nowhere", reply=reply))
    for reply in ("Still building it, nearly there.", "Still on it.", "It's coming, hang tight."):
        assert check(route("nowhere", reply=reply)).reply == reply
    # a report's nowhere follows the agent's line, and may say nothing is left after all
    assert check(route("nowhere", reply="You're all set."), loops=[], report=True).reply == "You're all set."
    with pytest.raises(ValidationError, match="neither promise work"):
        check(route("nowhere", reply="I'll fix the rest now."), loops=[], report=True)


def test_the_router_and_the_brain_build_their_openings_alike():
    from brain.call import Call
    call, router, refs = Call(catalog(), None, owner="KrownJM"), Router(catalog(), None, owner="KrownJM"), FakeRefs()
    brains, routers = call.opening(refs), router.opening(refs)
    for text in (brains, routers):        # the same blocks, in the same order, each with its own headings
        assert text.index("THE MENU (each action") < text.index("THE ACTIONS IN FULL:\n") < \
            text.index("MINECRAFT COMMANDS (") < text.index("THE CONVERSATION SO FAR (")
    assert brains.split("THE ACTIONS IN FULL:")[1].split("MINECRAFT")[0] == \
        routers.split("THE ACTIONS IN FULL:")[1].split("MINECRAFT")[0]
    assert router.own_actions() == call.own_actions() == "THIS SERVER'S OWN ACTIONS: none yet"
    assert router.opening(refs) is routers                 # built once while the library and the references stay
    assert "(not loaded yet: the brain writes commands" in router.opening(None)


def test_a_lines_nowhere_with_no_reply_behind_a_promise_is_sent_back():
    """A fresh brain's first line, "give me 64 steak", went nowhere behind the agent's "On it, chef!"."""
    for said in ("On it, chef!", "Coming right up!"):
        with pytest.raises(ValidationError, match="the player was told it's on its way: place the work"):
            route_type(catalog(), loops=[], tasks=TASKS, said=said).model_validate_json(json.dumps(route("nowhere")))
    ok = route_type(catalog(), loops=[], tasks=TASKS, said="On it, chef!")
    assert ok.model_validate_json(json.dumps(route("nowhere", reply="That one was for Steve!"))).to == "nowhere"
    assert ok.model_validate_json(json.dumps(route("new_loop"))).to == "new_loop"
    # a status behind work under way, talk, or a report's nowhere: no reply needed
    busy = route_type(catalog(), loops=[], tasks=TASKS, said="Still on it.", under_way=True)
    assert busy.model_validate_json(json.dumps(route("nowhere"))).reply == ""
    with pytest.raises(ValidationError, match="told it's on its way"):
        route_type(catalog(), loops=[], tasks=TASKS, said="Still on it.").model_validate_json(
            json.dumps(route("nowhere")))                     # nothing under way: "still on it" promises
    talk = route_type(catalog(), loops=[], tasks=TASKS, said="Ha, nice one!")
    assert talk.model_validate_json(json.dumps(route("nowhere"))).to == "nowhere"
    report = route_type(catalog(), loops=[], tasks=TASKS, report=True, said="On it!")
    assert report.model_validate_json(json.dumps(route("nowhere"))).to == "nowhere"
    assert "A line it hands you is never already placed: you are the one who places it" in PROMPT.read_text()


async def test_the_router_checks_its_answer_against_the_agents_line_and_the_speakers_work():
    ask = FakeAsk(Route=route("nowhere"))
    router, ctx = Router(catalog(), ask, owner="KrownJM"), rows(LINE)
    event = RouterEvent("LINE", ctx.rows[-1], said="On it, chef!")
    with pytest.raises(ValidationError, match="told it's on its way"):
        await router.route(event, ctx, Seen(), "TASKS: none yet", "WORK UNDER WAY: no brain loops", loops=[],
                           tasks=[], refs=None)
    event.said = "Still on it."
    got = await router.route(event, ctx, Seen(), "TASKS: none yet", "WORK UNDER WAY: no brain loops", loops=[],
                             tasks=[], refs=None, busy=True)
    assert got.value.to == "nowhere"
