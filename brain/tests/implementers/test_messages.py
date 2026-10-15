"""Messages, "never mind" and the limits, at the runner's edge, on the fake harness: a follow-up into a
live run, parts of one line, a cancel live, after the run and before it began, code's put-back,
the warning near the turn cap and the time limit, and a run stopped at its limit, reported from
what the guard saw."""
import asyncio
import json
import os

import pytest

from brain.implementers import examples, runner
from brain.implementers.config import ImplementerConfig
from brain.implementers.runner import CANCEL, LIMIT, MESSAGE, Implementers, cancel_prompt, follow_up_prompt

from .fakes import (FakeHarness, FakeVfg, bash, done, failed_turns, ok, request, result_of, said, session,
                    tool)

BRIEF = "Give Steve an ice beam that freezes what it hits."


@pytest.fixture
def cfg(tmp_path) -> ImplementerConfig:
    return ImplementerConfig.load({"VFG_REPO": str(tmp_path), "VFG_HOME": str(tmp_path / "vfg-home")})


@pytest.fixture(autouse=True)
def _examples(monkeypatch):
    async def build(cfg, job, task, run, **kw):
        return "EXAMPLES for %s." % task

    monkeypatch.setattr(examples, "build", build)


class Run:
    def __init__(self, cfg, vfg=None):
        self.cfg = cfg
        self.harness = FakeHarness()
        self.vfg = vfg or (FakeVfg().on("prefix", answer=ok("THE KIT\n"))
                           .on("engine", "places", answer=ok('{"ok": true, "places": []}')))
        self.reports: list[tuple[str, str, str]] = []
        self.on_report = None

        async def on_report(task, status, text, description, made):
            self.reports.append((task, status, text))
            if self.on_report is not None:
                await self.on_report(task, status, text)

        self.imps = Implementers(cfg, on_report, harness=self.harness, vfg=self.vfg)

    async def settle(self):
        while True:
            busy = [t.worker for t in self.imps._tasks.values() if t.worker] + list(self.imps._background)
            if not busy:
                return
            await asyncio.wait(busy)

    async def until_live(self, name):
        for _ in range(200):
            t = self.imps._tasks.get(name)
            if t is not None and t.run is not None:
                return t.run
            await asyncio.sleep(0)
        raise AssertionError("the run never went live")

    def file(self, rel) -> str:
        return os.path.join(self.cfg.repo, rel)

    async def settle_one(self, name):
        w = self.imps._tasks[name].worker
        if w is not None:
            await asyncio.wait({w})


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def effects(r):
    """What allowed calls do on disk: a Write or an Edit writes its file (the new text as given)."""

    def effect(name, inp):
        if name in ("Write", "Edit"):
            write(r.file(inp["file_path"]), inp.get("content") or inp.get("new_string") or "")

    return effect


async def test_three_parts_go_into_the_live_run_in_order_and_make_no_job(cfg):
    r = Run(cfg)
    gate = asyncio.Event()
    r.harness.add(session("s1"), done("Done: fire, faster, louder."), gate=gate)
    await r.imps.start("fireball_steve", BRIEF)
    await r.until_live("fireball_steve")
    for part in ("a fireball", "faster", "and louder"):
        await r.imps.follow_up("fireball_steve", part)
    t = r.imps._tasks["fireball_steve"]
    assert r.harness.runs[0].sent == [MESSAGE + "a fireball", MESSAGE + "faster", MESSAGE + "and louder"]
    assert not t.jobs and r.imps.running() == ["fireball_steve"]
    gate.set()
    await r.settle()
    assert r.reports == [("fireball_steve", "done", "Done: fire, faster, louder.")] and len(r.harness.runs) == 1


async def test_parts_that_come_while_a_run_reports_make_one_resumed_run(cfg):
    r = Run(cfg)
    r.harness.add(session("s1"), done("Done: first."))
    r.harness.add(done("Done: both parts."))

    async def on_report(task, status, text):
        if text == "Done: first.":  # the run has ended: these queue, in one job
            await r.imps.follow_up(task, "make it bigger")
            await r.imps.follow_up(task, "and red")

    r.on_report = on_report
    await r.imps.start("castle_steve", BRIEF)
    await r.settle()
    assert [run.kind for run in r.harness.runs] == ["start", "resume"]
    assert r.harness.runs[1].prompt == follow_up_prompt("castle_steve", "make it bigger\nand red")
    assert r.reports[-1] == ("castle_steve", "done", "Done: both parts.")


async def test_a_cancel_goes_into_the_live_run_as_it_is_and_code_puts_back_what_is_not_back(cfg):
    r = Run(cfg)
    write(r.file("made/main/content/ice_beam_steve.py"), "v1")
    gate = asyncio.Event()
    r.harness.add(session("s1"),
                  tool("Edit", file_path="made/main/content/ice_beam_steve.py", old_string="v1", new_string="v2"),
                  tool("Write", file_path="made/main/content/ice_beam_steve_ring.py", content="x"),
                  tool("Write", file_path="made/main/tests/ice_beam_steve.py", content="x"),
                  done("Cancelled: I put the frost ring back; the beam I could not."), gate=gate, effect=effects(r))
    await r.imps.start("ice_beam_steve", BRIEF)
    run = await r.until_live("ice_beam_steve")
    for _ in range(20):
        await asyncio.sleep(0)
    await r.imps.follow_up("ice_beam_steve", CANCEL)
    assert run.sent == [CANCEL]  # verbatim, not as a MESSAGE
    gate.set()
    await r.settle()
    [(task, status, report)] = r.reports
    assert status == "done"  # Cancelled is done (Brain_Loop's tasks table: open, done, failed)
    assert report.startswith("Cancelled: I put the frost ring back; the beam I could not.\nCode put back: ")
    assert report.endswith("Code put back: restored made/main/content/ice_beam_steve.py; removed "
                           "made/main/content/ice_beam_steve_ring.py; removed made/main/tests/ice_beam_steve.py; "
                           "reloaded the engine.")
    assert open(r.file("made/main/content/ice_beam_steve.py")).read() == "v1"
    assert not os.path.exists(r.file("made/main/content/ice_beam_steve_ring.py"))
    assert r.vfg.made("engine", "reload") == [["engine", "reload"]]


async def test_a_cancel_after_the_run_resumes_with_its_words_and_puts_back_that_runs_changes(cfg):
    r = Run(cfg)
    r.harness.add(session("s1"), tool("Write", file_path="made/main/content/ice_beam_steve.py", content="copied"),
                  done(), effect=effects(r))
    r.harness.add(tool("Edit", file_path="made/main/content/ice_beam_steve.py", old_string="copied",
                       new_string="v2"), done("Done: bigger."), effect=effects(r))
    r.harness.add(done("Cancelled: back as it was."))
    await r.imps.start("ice_beam_steve", BRIEF)
    await r.settle()
    await r.imps.follow_up("ice_beam_steve", "bigger")
    await r.settle()
    await r.imps.follow_up("ice_beam_steve", CANCEL)
    await r.settle()
    cancel = r.harness.runs[2]
    assert (cancel.kind, cancel.resumed, cancel.prompt) == ("resume", "s1", cancel_prompt("ice_beam_steve", CANCEL))
    assert cancel.prompt == CANCEL + "\nEnd with your report, its last line DESCRIPTION: what ice_beam_steve is now."
    # the run it cancels is the follow-up: its edit goes, and the class the first run made stays
    assert open(r.file("made/main/content/ice_beam_steve.py")).read() == "copied"
    assert r.reports[-1] == ("ice_beam_steve", "done", "Cancelled: back as it was.\n"
                             "Code put back: restored made/main/content/ice_beam_steve.py; reloaded the engine.")


async def test_a_cancel_before_a_new_task_began_starts_nothing(cfg):
    r = Run(cfg)
    await r.imps.start("wall_alex", BRIEF)
    await r.imps.follow_up("wall_alex", CANCEL)  # while its brief is being made
    await r.settle()
    assert r.harness.runs == []
    assert r.reports == [("wall_alex", "done", "Cancelled: it had not started; nothing was made.")]
    # a task with nothing anywhere: said so, nothing resumed
    await r.imps.follow_up("zoo_alex", CANCEL)
    await r.settle()
    assert r.reports[-1] == ("zoo_alex", "done", "Cancelled: it had no session to resume.\n"
                             "Code put back: nothing; there was no run of it here to put back.")
    assert r.harness.runs == []


async def test_the_warning_near_the_turn_cap_then_the_runs_own_report_and_code_puts_back(cfg):
    cfg.max_turns = 8
    r = Run(cfg)
    reqs = [request(1000, 0, 0, 10)] * 3
    r.harness.add(session("s1"), *reqs, tool("Write", file_path="made/main/content/speed_dash.py", content="x"),
                  done("Failed: tried a launch; no command moves a player."), effect=effects(r))
    await r.imps.start("speed_dash", BRIEF)
    await r.settle()
    assert r.harness.runs[0].sent == [LIMIT]  # at 3 of 8 turns: 5 before the cap
    assert LIMIT == ("LIMIT: you are about to run out of turns and time. Stop building now. Put everything back the "
                     "way it was before this run, then report Failed: what you tried, why it did not work, and what might.")
    assert r.reports == [("speed_dash", "failed", "Failed: tried a launch; no command moves a player.\n"
                          "Code put back: removed made/main/content/speed_dash.py; reloaded the engine.")]


async def test_a_run_past_its_turn_cap_is_stopped_put_back_and_reported_from_what_the_guard_saw(cfg):
    cfg.max_turns = 6
    r = Run(cfg)
    write(r.file("made/main/content/speed_dash.py"), "v1")
    edit = tool("Edit", file_path="made/main/content/speed_dash.py", old_string="v1", new_string="v9")
    cmd = bash("vfg engine reload speed_dash dash_two")
    r.harness.add(session("s1"), edit, tool("Write", file_path="made/main/content/dash_two.py", content="x"), cmd,
                  result_of(cmd, "speed_dash: Traceback: no such attribute velocity"), *[request(100, 0, 0, 1)] * 7,
                  done("never seen"), gate=asyncio.Event(), effect=effects(r))
    await r.imps.start("speed_dash", BRIEF)
    await r.settle()
    run = r.harness.runs[0]
    assert run.sent == [LIMIT] and run.stopped and run.closed
    [(_, status, report)] = r.reports
    assert status == "failed"
    assert report.split("\n") == [
        "Failed: stopped at its limit (its 6 turns); put back to the last working version.",
        "It had made: content speed_dash, dash_two.",
        "It had changed: made/main/content/dash_two.py, made/main/content/speed_dash.py.",
        "Its last commands: `Edit made/main/content/speed_dash.py`; `Write made/main/content/dash_two.py`; "
        "`vfg engine reload speed_dash dash_two` gave the error: speed_dash: Traceback: no such attribute velocity.",
        "Code put back: removed made/main/content/dash_two.py; restored made/main/content/speed_dash.py; "
        "reloaded the engine."]
    assert open(r.file("made/main/content/speed_dash.py")).read() == "v1"


async def test_a_warned_runs_status_from_the_first_line_that_says_it(cfg):
    cfg.max_turns = 6
    r = Run(cfg)
    r.harness.add(session("s1"), request(1, 0, 0, 1), done("Everything is back.\n\nFailed: no command moves a player."))
    r.harness.add(request(1, 0, 0, 1), done("I stopped as told."))
    await r.imps.start("speed_dash", BRIEF)
    await r.settle()
    await r.imps.follow_up("speed_dash", "try again")
    await r.settle()
    assert [s for _, s, _ in r.reports] == ["failed", "failed"]  # its Failed: line; then warned, and saying neither
    assert runner.first_word("Built it.\nDone: all of it.") == "done" and runner.first_word("Built it.") is None


async def test_the_clis_own_turn_cap_is_a_stop_at_the_limit_too(cfg):
    r = Run(cfg)
    r.harness.add(session("s1"), failed_turns())
    await r.imps.start("high_step", BRIEF)
    await r.settle()
    assert r.reports[0][1:] == ("failed", "Failed: stopped at its limit (its 100 turns); put back to the last working "
                                "version.\nCode put back: nothing; the run had changed no made file, binding or grant.")


async def test_the_warning_near_the_time_limit_and_a_run_that_reports_after_it(cfg):
    cfg.time_limit_s = 0.4  # warned at 0.3 s (a quarter of it before the end, as it is under 4 minutes)
    r = Run(cfg)
    gate = asyncio.Event()
    r.harness.add(session("s1"), done("Failed: ran out of time; it needs a velocity command."), gate=gate,
                  on_send=lambda run, text: gate.set() if text == LIMIT else None)
    await r.imps.start("speed_dash", BRIEF)
    await r.settle()
    assert r.harness.runs[0].sent == [LIMIT] and not r.harness.runs[0].stopped
    assert r.reports == [("speed_dash", "failed", "Failed: ran out of time; it needs a velocity command.\n"
                          "Code put back: nothing; the run had changed no made file, binding or grant.")]


async def test_bindings_and_grants_are_put_back_through_vfg_after_a_cancel(cfg):
    r = Run(cfg)
    bindings = [{"id": "steve.fire_ball", "player": "Steve", "phrase": "fire ball", "ability": "fireball"}]
    grants = []
    r.vfg.on("engine", "incantations", answer=lambda argv: ok(json.dumps({"ok": True, "incantations": bindings})))
    r.vfg.on("engine", "instances", answer=lambda argv: ok(json.dumps({"ok": True, "instances": [
        {"instance": "ability:%s#1" % a, "kind": "ability", "id": a, "user": u} for u, a in grants]})))

    def effect(name, inp):  # what the engine has after the calls
        bindings[:] = [{"id": "steve.fire_ball", "player": "Steve", "phrase": "fire ball", "ability": "beam_steve"}]
        grants[:] = [("Steve", "beam_steve")]

    gate = asyncio.Event()
    r.harness.add(session("s1"), bash("vfg engine grant Steve beam_steve && vfg engine bind Steve 'fire ball' "
                                      "beam_steve --by beam_steve"), done("Cancelled: as asked."),
                  gate=gate, effect=effect)
    await r.imps.start("beam_steve", BRIEF)
    await r.until_live("beam_steve")
    for _ in range(20):
        await asyncio.sleep(0)
    await r.imps.follow_up("beam_steve", CANCEL)
    gate.set()
    await r.settle()
    assert r.vfg.made("engine", "bind") == [["engine", "bind", "Steve", "fire ball", "fireball", "--aliases", "[]"]]
    assert r.vfg.made("engine", "revoke") == [["engine", "revoke", "Steve", "beam_steve"]]
    assert r.reports[-1][2].endswith("Code put back: bound steve.fire_ball again as it was; revoked beam_steve from "
                                     "Steve.")


# ---------------------------------------------------------------- the review's fixes

async def test_a_warned_run_that_reports_done_keeps_its_work(cfg):
    cfg.max_turns = 8
    r = Run(cfg)
    r.harness.add(session("s1"), *[request(1000, 0, 0, 10)] * 3, tool("Write", file_path="made/main/content/hot.py",
                                                                    content="x"),
                  done("Done: built it just in time."), effect=effects(r))
    await r.imps.start("speed_dash", BRIEF)
    await r.settle()
    assert r.harness.runs[0].sent == [LIMIT]
    assert r.reports == [("speed_dash", "done", "Done: built it just in time.")]  # no put-back line
    assert os.path.exists(r.file("made/main/content/hot.py"))


async def test_a_message_in_a_run_stopped_at_its_limit_goes_to_a_resume(cfg):
    cfg.time_limit_s = 0.3
    r = Run(cfg)
    r.harness.add(session("s1"), done("never"), gate=asyncio.Event(), deaf=True)  # reaches its time limit
    r.harness.add(done("Done: blue."))
    await r.imps.start("beam_steve", BRIEF)
    await r.until_live("beam_steve")
    await r.imps.follow_up("beam_steve", "make it blue")
    await r.settle()
    assert [(run.kind, run.prompt) for run in r.harness.runs[1:]] == [
        ("resume", follow_up_prompt("beam_steve", "make it blue"))]
    assert [s for _, s, _ in r.reports] == ["failed", "done"]


async def test_a_message_in_a_run_that_crashed_goes_to_a_resume(cfg):
    r = Run(cfg)
    gate = asyncio.Event()
    r.harness.add(session("s1"), said("on it"), said("still on it"), gate=gate, breaks=RuntimeError("the CLI went away"))
    r.harness.add(done("Done: blue."))
    await r.imps.start("beam_steve", BRIEF)
    await r.until_live("beam_steve")
    await r.imps.follow_up("beam_steve", "make it blue")
    gate.set()
    await r.settle()
    assert r.harness.runs[1].prompt == follow_up_prompt("beam_steve", "make it blue")
    assert r.reports == [("beam_steve", "failed", "Failed: it crashed: RuntimeError: the CLI went away."),
                         ("beam_steve", "done", "Done: blue.")]


async def test_a_message_the_harness_ended_without_reading_goes_to_a_resume(cfg):
    r = Run(cfg)
    gate = asyncio.Event()
    unread = done("Done: built.")
    unread.unread = [MESSAGE + "and loud"]
    r.harness.add(session("s1"), unread, gate=gate)
    r.harness.add(done("Done: loud."))
    await r.imps.start("beam_steve", BRIEF)
    await r.until_live("beam_steve")
    await r.imps.follow_up("beam_steve", "make it blue")  # read
    await r.imps.follow_up("beam_steve", "and loud")  # not read
    gate.set()
    await r.settle()
    assert r.harness.runs[1].prompt == follow_up_prompt("beam_steve", "and loud")
    assert [t for _, _, t in r.reports] == ["Done: built.", "Done: loud."]


async def test_usage_is_written_at_shutdown_and_after_a_crash(cfg):
    r = Run(cfg)
    r.harness.add(session("s1"), request(5000, 0, 0, 10), done(), gate=asyncio.Event(), deaf=True)
    r.harness.add(session("s2"), request(7000, 0, 0, 10), breaks=RuntimeError("gone"))
    await r.imps.start("closing", BRIEF)  # the first script: it never ends, and is stopped by close()
    await r.imps.start("crashing", BRIEF)
    for _ in range(50):
        await asyncio.sleep(0)
    await r.settle_one("crashing")
    await r.imps.close(0.05)
    ins = sorted(a[a.index("--in") + 1] for a in r.vfg.made("usage"))
    assert ins == ["5000", "7000"]


async def test_a_cancel_after_a_restart_puts_back_from_the_snapshot_kept_on_disk(cfg):
    r = Run(cfg)
    write(r.file("made/main/content/ice_beam_steve.py"), "v1")
    r.harness.add(session("s1"), tool("Edit", file_path="made/main/content/ice_beam_steve.py", old_string="v1",
                                      new_string="v2"), done("Done: bigger."), effect=effects(r))
    await r.imps.start("ice_beam_steve", BRIEF)
    await r.settle()
    kept = os.path.join(cfg.brain_dir, "implementer-snapshots", "ice_beam_steve.json")
    assert os.path.exists(kept) and oct(os.stat(kept).st_mode & 0o777) == "0o600"
    again = Run(cfg)  # the brain restarted: nothing in memory
    again.harness.add(done("Cancelled: as asked."))
    await again.imps.follow_up("ice_beam_steve", CANCEL, session="s1")
    await again.settle()
    assert open(r.file("made/main/content/ice_beam_steve.py")).read() == "v1"
    assert again.reports[-1][2].endswith("Code put back: restored made/main/content/ice_beam_steve.py; reloaded the "
                                         "engine.")
    again.harness.add(done("Done: bigger."))
    await again.imps.follow_up("ice_beam_steve", "bigger again")  # a real run: the old snapshot goes,
    await again.settle()  # and this run's is kept in its place
    with open(kept) as f:
        assert json.load(f)["files"] == {}


SUMMARY = ("<analysis>\nLet me work through the conversation chronologically.\n</analysis>\n<summary>\n1. Primary "
           "request: a speed dash.\n</summary>")


async def test_a_run_that_ends_on_a_summary_is_resumed_once_for_its_report(cfg):
    r = Run(cfg)
    r.harness.add(session("s1"), said("Building the dash."), done(SUMMARY))  # a summary in place of its report
    r.harness.add(done("Done: Steve dashes 15 blocks forward when he says speed dash.\nDESCRIPTION: A speed dash."))
    await r.imps.start("speed_dash_steve", BRIEF)
    await r.settle()
    first, recovery = r.harness.runs
    assert (recovery.kind, recovery.resumed, recovery.prompt) == ("resume", "s1", runner.RECOVER)
    assert runner.RECOVER == ("Your last message summed up the conversation. Now write your report to the brain: the "
                              "first word Done, Failed or Cancelled, the DONE WHEN items one per line, then DESCRIPTION:")
    assert r.reports == [("speed_dash_steve", "done", "Done: Steve dashes 15 blocks forward when he says speed dash.")]


async def test_when_the_resume_sums_up_again_the_last_real_report_or_ended_without_one(cfg):
    r = Run(cfg)
    r.harness.add(session("s1"), said("Done: Steve dashes 15 blocks forward when he says speed dash.\nDESCRIPTION: A dash."),
                  said("Let me check the test once more."), done(SUMMARY))
    r.harness.add(done(SUMMARY))  # the resume sums up again: its last real report is the run's
    r.harness.add(said("Working on it."), done(SUMMARY))  # a follow-up with no report at all
    r.harness.add(done(SUMMARY))
    await r.imps.start("speed_dash_steve", BRIEF)
    await r.settle()
    await r.imps.follow_up("speed_dash_steve", "faster")
    await r.settle()
    assert [run.prompt for run in r.harness.runs[1::2]] == [runner.RECOVER, runner.RECOVER]  # once a run
    assert r.reports == [
        ("speed_dash_steve", "done", "Done: Steve dashes 15 blocks forward when he says speed dash."),
        ("speed_dash_steve", "failed", "Failed: the implementer ended without a report (its last message summed up "
                                       "the conversation instead).")]
    assert runner.is_summary("  <summary>x</summary>") and not runner.is_summary("Done: <analysis> of it")
