"""The whole dispatcher: crash recovery from a saved state, a stop handing the unfinished, and
`python -m dispatcher` itself (the lock, SIGTERM, the log) against a fake vfg on PATH."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from conftest import CATALOG, FakeLLM, FakeRefs, FakeVfg, cmd, fail, ok, planned, select, tp
from dispatcher.__main__ import serve
from dispatcher.config import Config, now_iso

HERE = Path(__file__).resolve().parent.parent


def config(tmp: Path, **kw) -> Config:
    cfg = Config(home=tmp / "vfg-home", dispatch_dir=tmp / "dispatch", orch_dir=tmp / "orch",
                 repo=tmp / "repo", window_s=5, stop_grace_s=0.5)
    return replace(cfg, **kw)


async def until(check, timeout: float = 3) -> None:
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "timed out waiting"
        await asyncio.sleep(0.01)


def task_line(tid: str, task: str, context: str = "", at: str = "") -> str:
    return json.dumps({"id": tid, "at": at or now_iso(), "task": task, "context": context}) + "\n"


async def test_crash_recovery_hands_in_flight_tasks_with_what_was_done_and_a_waiting_batch_at_once(tmp_path):
    cfg = config(tmp_path)  # a 5 s window: the hand below must not wait for it
    cfg.dispatch_dir.mkdir(parents=True)
    cfg.inbox.write_text(task_line("T3", "build a castle") + task_line("T5", "bread then a village"))
    cfg.state_file.write_text(json.dumps({
        "offset": cfg.inbox.stat().st_size,
        "recent": ["T3", "T5"],
        "tasks": {"T5": {
            "id": "T5", "at": "2026-10-09T12:00:00.000Z", "task": "bread then a village", "context": "ctx",
            "picks": [{"action": "command", "instruction": "give Alex bread"},
                      {"action": "teleport", "instruction": "teleport Alex to a village"}],
            "orchestrator": "", "next": 1,
            "done": [{"instruction": "give Alex bread", "report": "bread — Gave 1 [Bread] to Alex"}],
            "dropped": []}},
        "batch": [{"id": "T3", "at": "2026-10-09T11:59:00.000Z", "task": "build a castle", "context": ""}],
        "outbox": [{"id": "T5", "status": "progress", "text": "bread — Gave 1 [Bread] to Alex"}],
    }))
    vfg, stop = FakeVfg(), asyncio.Event()
    t0 = time.monotonic()
    running = asyncio.create_task(serve(cfg, stop, vfg=vfg, ask=FakeLLM(), catalog=CATALOG, refs=FakeRefs(),
                                        poll_s=0.01))
    await until(lambda: vfg.hands() and len(vfg.replies()) == 2)
    assert time.monotonic() - t0 < 1
    stop.set()
    assert await running == 0

    # T3 had nothing left to say: handed at once. T5 waits until its replies are out, so the
    # conversation hears "handed on" before the orchestrator can answer.
    assert [[x["id"] for x in hand] for hand in vfg.hands()] == [["T3"], ["T5"]]
    last_t5_reply = max(i for i, a in enumerate(vfg.calls) if a[:3] == ["orch", "reply", "T5"])
    t5_hand = next(i for i, a in enumerate(vfg.calls) if a[:2] == ["orch", "hand"] and '"T5"' in a[2])
    assert last_t5_reply < t5_hand
    hand = [x for h in vfg.hands() for x in h]
    assert hand[1]["task"] == "teleport Alex to a village"
    assert hand[1]["context"] == (
        "ctx\n\nDispatcher: done bread — Gave 1 [Bread] to Alex; interrupted teleport Alex to a village: "
        "the dispatcher restarted while it ran; it may or may not have happened")
    assert vfg.replies() == [("T5", "progress", "bread — Gave 1 [Bread] to Alex"),
                             ("T5", "progress", "handed on to the orchestrator: teleport Alex to a village")]
    assert vfg.actions() == []  # nothing ran again
    saved = json.loads(cfg.state_file.read_text())
    assert saved["tasks"] == {} and saved["batch"] == [] and saved["outbox"] == []


async def test_sigterm_lets_a_running_action_finish_then_hands_everything_unfinished(tmp_path):
    cfg = config(tmp_path)
    cfg.dispatch_dir.mkdir(parents=True)
    cfg.inbox.write_text(task_line("T1", "village then bread") + task_line("T2", "take Steve to a desert"))

    async def selection(prompt):
        if "village" in prompt.split("\nTASK: ")[1]:
            return select(("teleport", "teleport Alex to a village"), ("command", "give Alex bread"),
                          ("teleport", "teleport Alex to a desert"))
        return select(("teleport", "teleport Steve to a desert"), ("command", "give Steve water"))

    async def planning(prompt):
        if "Alex" in prompt.split("INSTRUCTION:")[1]:
            return planned(tp("Alex", "structure", "village"), say="to a village")
        return planned(tp("Steve", "biome", "desert"), say="to a desert")

    llm = FakeLLM(Selection=[selection, selection], Planned_teleport=[planning, planning])
    started = {"n": 0}

    async def teleport(argv):
        started["n"] += 1
        if "Steve" in argv[4]:
            await asyncio.sleep(60)  # longer than the grace: cancelled
        await asyncio.sleep(0.2)     # shorter: finishes
        return ok("teleported Alex to a village at 1 70 2")

    vfg = FakeVfg().on("action", "run", "teleport", answer=teleport)
    stop = asyncio.Event()
    running = asyncio.create_task(serve(cfg, stop, vfg=vfg, ask=llm, catalog=CATALOG, refs=FakeRefs(), poll_s=0.01))
    await until(lambda: started["n"] == 2)
    stop.set()  # what SIGTERM does
    assert await asyncio.wait_for(running, 5) == 0

    assert [a[2] for a in vfg.actions()] == ["teleport", "teleport"]  # the bread and the desert never started
    by_id = {x["id"]: x for hand in vfg.hands() for x in hand}
    assert sorted(by_id) == ["T1", "T2"] and len([x for h in vfg.hands() for x in h]) == 2
    assert by_id["T1"]["task"] == "give Alex bread; then teleport Alex to a desert"
    assert by_id["T1"]["context"] == ("Dispatcher: done to a village — teleported Alex to a village at 1 70 2; "
                                      "not started: give Alex bread; teleport Alex to a desert (the dispatcher stopped)")
    assert by_id["T2"]["task"] == "take Steve to a desert"  # nothing was done: the original
    assert by_id["T2"]["context"] == ("Dispatcher: interrupted teleport Steve to a desert: the dispatcher stopped "
                                      "while it ran; it may or may not have happened; not started: give Steve water")
    assert ("T1", "progress", "to a village — teleported Alex to a village at 1 70 2") in vfg.replies()
    saved = json.loads(cfg.state_file.read_text())
    assert saved["tasks"] == {} and saved["batch"] == [] and saved["outbox"] == []
    assert saved["offset"] == cfg.inbox.stat().st_size


async def test_a_stop_while_vfg_orch_hand_fails_keeps_the_batch_for_the_next_start(tmp_path, monkeypatch):
    import dispatcher.__main__ as main_module
    monkeypatch.setattr(main_module, "HAND_ON_STOP_S", 0.1)
    cfg = config(tmp_path)
    cfg.dispatch_dir.mkdir(parents=True)
    cfg.inbox.write_text(task_line("T1", "build a castle"))
    vfg = FakeVfg().on("orch", "hand", answer=fail(1, "locked"))
    llm = FakeLLM(Selection=[select(orchestrator="build a castle")])
    stop = asyncio.Event()
    running = asyncio.create_task(serve(cfg, stop, vfg=vfg, ask=llm, catalog=CATALOG, refs=FakeRefs(), poll_s=0.01))
    await until(lambda: vfg.replies())
    stop.set()
    assert await asyncio.wait_for(running, 5) == 0
    saved = json.loads(cfg.state_file.read_text())
    assert [x["id"] for x in saved["batch"]] == ["T1"]


async def test_a_task_that_waited_too_long_is_failed_as_expired_and_never_run(tmp_path):
    from datetime import UTC, datetime, timedelta

    cfg = config(tmp_path, window_s=0.01)
    cfg.dispatch_dir.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    cfg.inbox.write_text(task_line("T1", "teleport Alex to a village", at=old)
                         + task_line("T2", "build a castle"))
    llm = FakeLLM(Selection=[select(orchestrator="build a castle")])  # one answer: T2's alone
    vfg, stop = FakeVfg(), asyncio.Event()
    running = asyncio.create_task(serve(cfg, stop, vfg=vfg, ask=llm, catalog=CATALOG, refs=FakeRefs(), poll_s=0.01))
    await until(lambda: vfg.hands() and len(vfg.replies()) == 2)
    stop.set()
    assert await running == 0
    assert vfg.replies("T1") == [("T1", "failed", "Expired: it waited 30 minutes for the dispatcher.")]
    assert [c.prompt for c in llm.calls if "teleport" in c.prompt.split("\nTASK: ")[-1]] == []
    assert vfg.actions() == [] and [x["id"] for x in vfg.hands()[0]] == ["T2"]
    saved = json.loads(cfg.state_file.read_text())
    assert saved["tasks"] == {} and saved["outbox"] == [] and "T1" in saved["recent"]


async def test_tasks_run_while_the_refs_load_with_command_handed_on_until_they_arrive(tmp_path, monkeypatch):
    from dispatcher import refs as refs_module

    loaded = asyncio.Event()

    async def slow_load(dispatch_dir, vfg, *, http=None, log=print):
        await loaded.wait()  # a first fetch: about 95 s for real
        return FakeRefs()

    monkeypatch.setattr(refs_module, "load", slow_load)
    cfg = config(tmp_path, window_s=0.01)
    cfg.dispatch_dir.mkdir(parents=True)

    async def selection(prompt):
        task = prompt.split("TASK: ", 1)[1].split("\n", 1)[0]
        if "village" in task and "bread" in task:
            return select(("teleport", "teleport Alex to a village"), ("command", "give Alex bread"))
        if "village" in task:
            return select(("teleport", "teleport Alex to a village"))
        return select(("command", "give Alex bread"))

    llm = FakeLLM(Selection=[selection] * 4, Planned_teleport=[planned(tp("Alex", "structure", "village"),
                                                                       say="village")] * 2,
                  Planned_command=[planned(cmd("give Alex minecraft:bread"), say="bread")] * 2)
    in_village = asyncio.Event()
    leave_village = asyncio.Event()

    async def teleport(argv):
        if argv[-3] == "T3":  # the task that is running when the refs arrive
            in_village.set()
            await leave_village.wait()
        return ok("teleported Alex to a village at 1 70 2")

    vfg = FakeVfg().on("action", "run", "teleport", answer=teleport)
    stop = asyncio.Event()
    running = asyncio.create_task(serve(cfg, stop, vfg=vfg, ask=llm, catalog=CATALOG, poll_s=0.01))

    def send(*lines):
        with open(cfg.inbox, "a") as f:
            f.write("".join(lines))

    send(task_line("T1", "take me to a village"), task_line("T2", "give me bread"),
         task_line("T3", "a village, then bread"))
    handed = lambda tid: any(x["id"] == tid for h in vfg.hands() for x in h)  # noqa: E731
    await until(lambda: vfg.replies("T1") and handed("T2") and in_village.is_set())
    assert vfg.replies("T1") == [("T1", "done", "village — teleported Alex to a village at 1 70 2")]
    assert vfg.replies("T2") == [("T2", "progress", "handed on to the orchestrator: give me bread")]
    assert "the command references are not loaded" in next(x for h in vfg.hands() for x in h if x["id"] == "T2")["context"]

    loaded.set()  # the refs arrive while T3 is still teleporting
    await asyncio.sleep(0.05)
    leave_village.set()
    send(task_line("T4", "give me bread"))
    await until(lambda: vfg.replies("T4") and handed("T3"))
    stop.set()
    assert await running == 0

    assert vfg.replies("T4") == [("T4", "done", "bread — done")]  # a later task: command runs
    t3 = next(x for h in vfg.hands() for x in h if x["id"] == "T3")  # T3 kept what it started with
    assert t3["task"] == "give Alex bread" and "the command references are not loaded" in t3["context"]
    assert [a[2] for a in vfg.actions()] == ["teleport", "teleport", "command"]


async def test_a_failed_refs_load_is_retried_in_the_background_until_command_comes_back(tmp_path, monkeypatch, capsys):
    from dispatcher import refs as refs_module
    from dispatcher.vfg import VfgResult

    async def fake_load(dispatch_dir, vfg, *, http=None, log=print):
        r = await vfg("action", "refs", "--version", timeout=30)
        if r.code != 0:
            raise refs_module.RefsError("no command references: `vfg action refs --version` failed")
        return FakeRefs()

    monkeypatch.setattr(refs_module, "load", fake_load)
    cfg = config(tmp_path)
    cfg.dispatch_dir.mkdir(parents=True)
    vfg = FakeVfg().on("action", "refs", answer=[fail(255, "ssh: connect to host"), fail(255, "ssh: connect to host"),
                                                 VfgResult(0, "26.3\n", "")])
    llm = FakeLLM(Selection=[select(("command", "give Alex bread"))],
                  Planned_command=[planned(cmd("give Alex minecraft:bread"), say="bread")] * 2)
    stop = asyncio.Event()
    running = asyncio.create_task(serve(cfg, stop, vfg=vfg, ask=llm, catalog=CATALOG, poll_s=0.01,
                                        refs_retry_s=0.05))
    refs_calls = lambda: [a for a in vfg.calls if a[:2] == ["action", "refs"]]  # noqa: E731
    await until(lambda: len(refs_calls()) == 3)
    await asyncio.sleep(0.05)
    with open(cfg.inbox, "a") as f:
        f.write(task_line("T1", "give me bread"))
    await until(lambda: vfg.replies())
    stop.set()
    assert await running == 0
    assert vfg.replies() == [("T1", "done", "bread — done")]  # command ran: no hand-on
    assert vfg.hands() == []
    out = capsys.readouterr().out
    assert "refs attempt 2: loading the references again" in out
    assert "refs attempt 3: loading the references again" in out
    assert "refs loaded at attempt 3: command picks run again" in out


async def test_a_hung_model_call_is_cut_at_the_model_timeout_and_handed_on(tmp_path, monkeypatch):
    """Through the real structured(), against a fake OpenRouter whose planner call never answers."""
    import httpx
    import vfg_llm

    monkeypatch.setenv("OPEN_ROUTER_API", "sk-test-not-a-key")
    hung = asyncio.Event()

    async def openrouter(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] and "timeout" not in body
        if body["response_format"]["json_schema"]["name"] == "Selection":
            answer = {"parts": [{"part": "a village", "to": "teleport", "instruction": "teleport Alex to a village"}]}
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}],
                                             "usage": {"cost": 0.00001}})
        hung.set()
        await asyncio.sleep(60)  # the planner call hangs
        raise AssertionError("never answered")

    cfg = config(tmp_path, window_s=0.01, model_timeout_s=0.5)
    cfg.dispatch_dir.mkdir(parents=True)
    cfg.inbox.write_text(task_line("T1", "take me to a village"))
    vfg, stop = FakeVfg(), asyncio.Event()
    async with httpx.AsyncClient(transport=httpx.MockTransport(openrouter)) as http:
        monkeypatch.setattr(vfg_llm, "client", http)
        t0 = time.monotonic()
        running = asyncio.create_task(serve(cfg, stop, vfg=vfg, catalog=CATALOG, refs=FakeRefs(), poll_s=0.01))
        await until(lambda: vfg.hands(), timeout=5)
        took = time.monotonic() - t0
        stop.set()
        assert await running == 0
    assert hung.is_set() and took < 2  # cut at 0.5 s, not structured()'s own 30 s
    [[line]] = vfg.hands()
    assert line["task"] == "take me to a village"
    assert "the action planner failed: no answer from" in line["context"] and "within 0.5 s" in line["context"]
    assert vfg.actions() == []


# ---------------------------------------------------------------- the real process

FAKE_VFG = """\
#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_VFG_LOG"], "a") as f:
    f.write(json.dumps(args) + "\\n")
if args[:2] == ["action", "refs"]:
    print("vfg: the box is not reachable", file=sys.stderr); sys.exit(1)
if args[:2] == ["orch", "hand"]:
    for line in json.loads(args[2]): print("handed " + line["id"])
elif args[:2] == ["orch", "reply"]:
    print("12")
elif args[:2] == ["place", "list"]:
    print("none")
"""


def test_python_m_dispatcher_takes_the_lock_hands_on_and_exits_0_on_sigterm(tmp_path):
    home, repo, bin_ = tmp_path / "vfg-home", tmp_path / "repo", tmp_path / "bin"
    for d in (home / "dispatch", repo, bin_):
        d.mkdir(parents=True)
    (bin_ / "vfg").write_text(FAKE_VFG.format(python=sys.executable))
    (bin_ / "vfg").chmod(0o755)
    calls = tmp_path / "vfg-calls.jsonl"
    (home / "dispatch" / "inbox.jsonl").write_text(task_line("T1", "build me a tower"))
    env = {**os.environ, "VFG_HOME": str(home), "VFG_REPO": str(repo), "PATH": f"{bin_}:{os.environ['PATH']}",
           "FAKE_VFG_LOG": str(calls), "VFG_DISPATCH_WINDOW_S": "0", "HOME": str(tmp_path / "home")}
    env.pop("OPEN_ROUTER_API", None)
    proc = subprocess.Popen([sys.executable, "-m", "dispatcher"], cwd=HERE, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        end = time.monotonic() + 15
        while not (calls.exists() and '"hand"' in calls.read_text()):
            assert proc.poll() is None and time.monotonic() < end, "the dispatcher never handed T1 on"
            time.sleep(0.05)
        second = subprocess.run([sys.executable, "-m", "dispatcher"], cwd=HERE, env=env,
                                capture_output=True, text=True, timeout=15)
        assert second.returncode == 1 and "holds" in second.stdout
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, out
    lines = out.strip().splitlines()
    assert all(re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z [a-z]+ \S", x) for x in lines), out
    assert "command picks are handed on until they load" in out and "stopped" in lines[-1]
    sent = [json.loads(x) for x in calls.read_text().splitlines()]
    hand = next(a for a in sent if a[:2] == ["orch", "hand"])
    assert json.loads(hand[2])[0]["task"] == "build me a tower"
    assert ["orch", "reply", "T1", "progress", "handed on to the orchestrator: build me a tower"] in sent
    state = json.loads((home / "dispatch" / "state.json").read_text())
    assert state["tasks"] == {} and state["batch"] == [] and state["outbox"] == []
