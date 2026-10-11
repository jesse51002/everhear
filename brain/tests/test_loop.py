"""The loop: each row routed by its class, a context line no model is shown, Respond's
hand-up, history, two lines at once, a report and what its task takes from it, and no batch."""

import asyncio
import importlib.util

import vfg_actions
from vfg_llm import LlmError

from brain import config, loop
from brain.act import BROKE
from brain.actions import variables_of
from brain.call import PROMPT

from conftest import answer, cmd, decision, made, part, rows

ASK = "LINE#{seq} 18:29:58 Steve voice action_oriented 0.93 -8dB | give me a diamond pickaxe"
TALK = "LINE#{seq} 18:29:59 Steve voice conversational 0.88 - | haha thanks AI"
GIVE = decision(part("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickaxe")),
                reply="there you go")


async def test_each_row_goes_where_its_class_says_and_a_read_marks_what_it_took(kit):
    k = kit(rows("GAME#1 18:29:40 join Alex | Alex joined the game", "AI#2 18:29:41 say - | hi"),
            Decision=GIVE, Response={"reply": "anytime!", "hand_up": False, "reason": "thanks"})
    for line in ("GAME#3 18:29:50 death Alex | Alex tried to swim in lava", "SYS#0 18:29:51 | vfg watch: reconnected",
                 "LINE#4 18:29:52 Alex voice context 0.7 - | ouch, I'm by the lava", ASK.format(seq=5),
                 TALK.format(seq=6)):
        k.brain.feed(line)
        await asyncio.sleep(0)  # as `vfg watch` gives them: one line at a time
    await k.settle()
    assert sorted(c["output"] for c in k.ask.calls) == ["Decision", "Response"]
    tail = k.ask.of("Decision")[0]["prompt"].split("NEW SINCE")[1]
    assert "Alex joined the game" in tail and "Alex tried to swim in lava" in tail  # every game event since
    for c in k.ask.calls:                  # the context line: in no prompt, cached or not, of any call
        assert "by the lava" not in c["prompt"] + "".join(text for text, _ in c["cached"])
    assert sorted(k.vfg.said()) == [["anytime!"], ["there you go"]]
    # the context line marked as it came, nobody waiting; the line's read took the game events and the line
    assert k.vfg.of("context", "mark") == [["context", "mark", "4"], ["context", "mark", "1", "3", "5"],
                                           ["context", "mark", "6"]]
    usage = k.vfg.of("usage", "add")
    assert ["usage", "add", "--component", "brain", "--model", "brain-model", "--in", "1000", "--out", "40",
            "--cost", "0.002000", "--cache-read", "900", "--ref", "#5"] in usage and any("respond" in u for u in usage)
    assert k.ctx.unprocessed() == [] and k.ctx.last_seq == 6


async def test_respond_replies_or_hands_up_never_both(kit):
    k = kit(Decision=GIVE, Response={"reply": "sure thing!", "hand_up": True, "reason": "asks for rain"})
    k.brain.feed("LINE#7 18:29:59 Steve voice conversational 0.9 - | lol make it rain")
    await k.settle()
    assert [c["output"] for c in k.ask.calls] == ["Response", "Decision"]
    assert "handed this line up to you: asks for rain" in k.ask.calls[1]["prompt"]
    assert k.vfg.said() == [["there you go"]]  # the brain's reply alone, never Respond's

    k = kit(Decision=GIVE, Response=LlmError("no answer"))  # unsure, failing: handed up all the same
    k.brain.feed(TALK.format(seq=8))
    await k.settle()
    assert [c["output"] for c in k.ask.calls] == ["Response", "Decision"]


async def test_history_is_the_old_feed_at_the_start_and_a_line_on_the_watch_is_live_whatever_its_time(kit):
    k = kit(rows("LINE#8 18:29:50 Alex voice context 0.9 - | so hungry",
                 "LINE#9 18:20:00 Steve voice action_oriented 0.9 - | give me a diamond pickaxe"), Decision=GIVE)
    k.brain.start = 9
    assert [r.seq for r in k.brain.catch_up()] == [9]      # ten minutes old as the brain starts: history
    await k.settle()                                      # and a context line unread, whatever its age, read
    assert k.ask.calls == [] and k.vfg.of("context", "mark") == [["context", "mark", "8", "9"]]
    # the box's clock 90 s ahead of the PC's: a line said now reads as yesterday's 18:31:30, and is live
    k.brain.feed("LINE#10 18:31:30 Steve voice action_oriented 0.9 - | give me a diamond pickaxe")
    await k.settle()
    assert len(k.ask.calls) == 1 and "LINE #10" in k.ask.calls[0]["prompt"].split("THE EVENT")[1]
    # after a drop, the backlog the watch catches up with is judged by its age, as a start's is
    k.brain.feed("SYS#0 18:30:00 | reconnected")
    k.brain.feed("LINE#11 18:21:00 Steve voice action_oriented 0.9 - | give me a diamond axe")
    await k.settle()
    assert len(k.ask.calls) == 1


async def test_two_lines_at_once_neither_waits_for_the_other(kit):
    both = asyncio.Event()
    started = []

    async def slow(prompt):
        started.append(prompt)
        if len(started) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 1)  # the first call ends only once the second has begun
        return GIVE

    k = kit(Decision=slow)
    k.brain.feed(ASK.format(seq=10))
    k.brain.feed("LINE#11 18:29:58 Alex voice action_oriented 0.9 - | give me a diamond pickaxe too")
    await k.settle()
    assert len(started) == 2 and len(k.vfg.said()) == 2
    assert sorted(a[-1] for a in k.vfg.of("action", "run")) == ["Alex", "Steve"]  # each one's own copy, --for
    assert k.tasks.all() == []


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
    """vfg_actions.variables(), which lands with the orchestrator's builder: each field's name."""
    def variables(cls):
        if seen is not None:
            seen.append(cls.__name__)
        return [{"name": k, "fixed": False} for k in cls.model_fields if k in cls.takes]

    monkeypatch.setattr(vfg_actions, "variables", variables, raising=False)


def gun(k, brief: str = "a Galick Gun for Styrkaeopteryx") -> str:
    k.tasks.open("galick_gun_styrk", "Styrkaeopteryx", "make me a galick gun", brief,
                 "A Galick Gun for Styrkaeopteryx: charge and fire.")
    return "galick_gun_styrk"


async def test_a_report_takes_its_description_made_and_variables_and_closes_with_one_record_and_one_line(
        kit, monkeypatch):
    fake_variables(monkeypatch)
    k = kit(rows("GAME#1 18:29:50 death Steve | Steve fell from a high place"), cat=made("galick_gun_styrk", GUN),
            Decision=decision(reply="your galick gun's ready: hold right-click", status="done",
                              record="galick_gun_styrk done: a Galick Gun, hold right-click to fire"))
    name = gun(k)
    k.impl.session = lambda name: "sess-1"
    await k.brain.on_report(name, "done", "Done: galick_gun_styrk is running as galick_gun_styrk.",
                            "A Galick Gun for Styrkaeopteryx: hold right-click 1.5 s, a 100-block beam.", MADE)
    await k.settle()
    prompt = k.ask.calls[0]["prompt"]
    assert "REPORT: the implementer of galick_gun_styrk ended, done." in prompt
    assert "WHAT IT IS: A Galick Gun for Styrkaeopteryx: hold right-click 1.5 s, a 100-block beam." in prompt
    assert "ITS REPORT:\nDone: galick_gun_styrk is running" in prompt
    assert "Steve fell from a high place" in prompt.split("NEW SINCE")[1]  # a report reads NEW
    assert k.vfg.of("context", "mark") == [["context", "mark", "1"]]
    t = k.tasks.get(name)
    assert (t["status"], t["result"], t["session"]) == ("done", "galick_gun_styrk done: a Galick Gun, hold "
                                                                "right-click to fire", "sess-1")
    assert t["description"] == "A Galick Gun for Styrkaeopteryx: hold right-click 1.5 s, a 100-block beam."
    assert t["made"] == MADE and t["closed_at"]
    assert t["variables"] == [{"name": "player", "fixed": False}, {"name": "cooldown", "fixed": False}]
    assert k.written(name)[-1] == t                       # every change written through, the last one whole
    assert k.vfg.rows("result") == [("result galick_gun_styrk", t["result"])]
    assert k.vfg.said() == [["your galick gun's ready: hold right-click", "--task", "galick_gun_styrk"]]

    k = kit(Decision=decision(reply="ready", status="done", record="galick_gun_styrk done"))
    name = gun(k)
    await k.brain.on_report(name, "done", "Done.", "", None)  # no DESCRIPTION line: the orchestrator's stays
    await k.settle()
    assert k.tasks.get(name)["description"] == "A Galick Gun for Styrkaeopteryx: charge and fire."


async def test_a_reports_part_goes_to_the_orchestrator_writing_no_reply_and_the_brains_reply_is_said(kit):
    k = kit(Decision=decision(part("orchestrator", "make Styrkaeopteryx's galick gun reach 150 blocks, as he asked"),
                              reply="your galick gun's up; making it reach further", status="done",
                              record="galick_gun_styrk done: a Galick Gun, 100 blocks"),
            orchestrator=[answer("follow_up", task="galick_gun_styrk", brief="make the beam reach 150 blocks",
                                 description="A Galick Gun for Styrkaeopteryx: a 150-block beam.", reply="x")])
    name = gun(k)
    await k.brain.on_report(name, "done", "Done: the beam reaches 100 blocks.", "", None)
    await k.settle()
    [call] = k.orch.calls
    assert not call["replying"] and call["head"].startswith("THE REPORT: the implementer of galick_gun_styrk ended")
    assert k.impl.followed == [("galick_gun_styrk", "make the beam reach 150 blocks", None,
                                "A Galick Gun for Styrkaeopteryx: a 150-block beam.")]
    assert k.vfg.said() == [["your galick gun's up; making it reach further", "--task", "galick_gun_styrk"]]
    assert k.tasks.get(name)["status"] == "open"          # the follow-up runs
    assert any(a[-2:] == ["--ref", "galick_gun_styrk"] for a in k.vfg.of("usage", "add"))


async def test_a_report_while_a_follow_up_to_its_task_runs_keeps_the_task_open(kit):
    k = kit(Decision=decision(reply="the gun's on; making it pierce now", status="done",
                              record="galick_gun_styrk: a Galick Gun, piercing next"))
    name = gun(k)
    k.impl.busy = [name]  # the follow-up queued behind this run has started
    await k.brain.on_report(name, "done", "Done: the gun is running.")
    await k.settle()
    assert "A FOLLOW-UP to galick_gun_styrk is already under way" in k.ask.calls[0]["prompt"]
    t = k.tasks.get(name)
    assert (t["status"], t["result"], t["closed_at"]) == ("open", "galick_gun_styrk: a Galick Gun, piercing next", None)
    assert k.vfg.rows("result") == [("result galick_gun_styrk", "galick_gun_styrk: a Galick Gun, piercing next")]
    assert "galick_gun_styrk for Styrkaeopteryx, an implementer is on it: A Galick Gun" in k.brain.open_tasks()


async def test_a_report_while_the_brain_stops_closes_its_task_in_code(kit):
    gate = asyncio.Event()

    async def held(prompt):
        await gate.wait()
        return GIVE

    k = kit(Decision=held)
    name = gun(k, "a castle at spawn, 40 by 40")
    k.brain.feed(ASK.format(seq=5))                       # a call in flight as the stop begins
    stopping = asyncio.create_task(k.brain.stop(1.0))
    await asyncio.sleep(0)
    await k.brain.on_report(name, "done", "Done: the castle is up at spawn, with a moat I added.", "A castle.",
                            MADE)                         # meanwhile
    gate.set()
    await stopping
    await k.settle()
    assert len(k.ask.calls) == 1                          # the line's call; the report's needs no model
    t = k.tasks.get(name)
    assert (t["status"], t["result"]) == ("done", "galick_gun_styrk done: a castle at spawn, 40 by 40")
    assert (t["description"], t["made"]) == ("A castle.", MADE)
    assert k.vfg.rows("result") == [("result galick_gun_styrk", "galick_gun_styrk done: a castle at spawn, 40 by 40")]


async def test_a_report_gets_a_record_and_a_chat_line_written_in_code_never_its_description(kit):
    k = kit(Decision=decision())                          # an answer with no record and no reply
    k.tasks.open("ice_trail_steve", "Steve", "make me an ice trail", "an ice trail behind Steve",
                         "Ice Trail for Steve: SECRET DETAILS")
    await k.brain.on_report("ice_trail_steve", "failed", "Failed: vfg script check said line 40 …")
    await k.settle()
    k.ask.answers["Decision"] = LlmError("no answer")     # and a call that fails
    k.tasks.open("ice_beam_alex", "Alex", "an ice beam", "", "Ice Beam for Alex: SECRET DETAILS")  # no brief
    await k.brain.on_report("ice_beam_alex", "done", "Done: ice_beam_alex running as ice_beam_alex; I also …")
    await k.settle()
    assert k.vfg.rows("result") == [("result ice_trail_steve", "ice_trail_steve failed: an ice trail behind Steve"),
                                    ("result ice_beam_alex", "ice_beam_alex done")]  # never the implementer's words
    assert k.vfg.said() == [["sorry, that one failed: an ice trail behind Steve", "--task", "ice_trail_steve"],
                            ["done: an ice beam", "--task", "ice_beam_alex"]]  # the player's own words
    assert "SECRET" not in repr(k.vfg.said())


async def test_an_unexpected_error_never_leaves_a_task_open_and_the_player_hears_so(kit):
    async def broken(argv):
        raise RuntimeError("the runner broke")

    async def blind():
        raise RuntimeError("no state")

    k = kit(Decision=GIVE)
    k.vfg.on("action", "run", answer=broken)
    k.brain.feed(ASK.format(seq=5))                       # the line's action: its run raises
    await k.settle()
    name = gun(k)
    k.world.seen = blind                                  # the report: the world cannot be read
    await k.brain.on_report(name, "done", "Done: the gun is running.")
    await k.settle()
    t = k.tasks.get(name)
    assert (t["status"], t["result"]) == ("done", "galick_gun_styrk done: a Galick Gun for Styrkaeopteryx")
    assert k.vfg.said() == [[BROKE], ["done: a Galick Gun for Styrkaeopteryx", "--task", "galick_gun_styrk"]]


def test_a_tasks_variables_are_its_actions_as_vfg_actions_reads_them(monkeypatch):
    fake_variables(monkeypatch)
    cat = made("galick_gun_styrk", GUN)
    assert variables_of(cat, "galick_gun_styrk") == [{"name": "player", "fixed": False},
                                                     {"name": "cooldown", "fixed": False}]
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


async def test_after_the_feed_restarts_the_context_is_read_again_and_the_new_feed_heard(kit, monkeypatch):
    monkeypatch.setattr(loop, "WATCH_AGAIN_S", 0.001)
    k = kit(rows("LINE#500 18:20:00 Alex voice context 0.9 - | on the old box", processed=True), Decision=GIVE)
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
    assert not hasattr(config.load({"VFG_BRAIN_BATCH_S": "60"}), "batch_s")
    assert "batch" not in PROMPT.read_text().lower()
    k = kit(Decision=GIVE)
    k.brain.feed("GAME#1 18:29:50 death Alex | Alex tried to swim in lava")
    k.brain.feed("LINE#2 18:29:52 Alex voice context 0.7 - | ouch")
    await k.settle()
    assert k.ask.calls == [] and not hasattr(k.brain, "timer") and not hasattr(k.brain, "batch")
    assert [r.seq for r in k.ctx.unprocessed()] == [1]    # the game event waits for the next call; the context line not
    k.brain.feed(ASK.format(seq=3))
    await k.settle()
    assert "Alex tried to swim in lava" in k.ask.calls[0]["prompt"].split("NEW SINCE")[1]


async def test_respond_sees_each_players_place_and_state_but_no_items(kit):
    k = kit(Response={"reply": "anytime!", "hand_up": False, "reason": "thanks"})
    await k.world.fresh_state()                           # Respond never waits: the last state there is
    k.brain.feed(TALK.format(seq=1))
    await k.settle()
    prompt = k.ask.of("Response")[0]["prompt"]
    assert ("- Steve at 10 64 -20 in overworld: 14.5/20 health, 18/20 food, armor 12, level 5, survival; "
            "effects: speed II 0:30, night vision ∞\n") in prompt
    assert "items, by slot" not in prompt
