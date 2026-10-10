"""python -m brain from start to stop, every part faked: the start's steps in order, the
history it never acts on, the feed followed, and a stop that fails what it interrupts."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from brain import config
from brain.__main__ import GREETING, serve
from brain.tasks import Tasks
from brain.vfg import VfgResult

from conftest import FakeAsk, FakeImplementers, FakeRefs, FakeVfg, catalog, cmd, decision, part


def feed(seq: int, at: datetime, rest: str, processed: bool = False) -> str:
    stamp = at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return json.dumps({"seq": seq, "at": stamp, "line": f"LINE#{seq} {stamp[11:19]} {rest}",
                       "processed_at": stamp if processed else None})


async def test_a_start_answers_only_what_is_new_follows_the_feed_and_a_stop_fails_what_it_cut(tmp_path):
    cfg = config.load({"VFG_HOME": str(tmp_path / "home"), "VFG_OWNER": "KrownJM", "VFG_BRAIN_HISTORY_HOURS": "6"})
    Tasks(cfg.tasks_file).open("Alex", "a castle", "a castle at home")  # T1, open when the last run died
    now = datetime.now(UTC)
    vfg = FakeVfg().on("context", "--since", answer=VfgResult(0, "\n".join([
        feed(10, now - timedelta(minutes=30), "Steve voice action_oriented 0.9 - | give me bread"),  # history
        feed(11, now - timedelta(minutes=29), "Alex voice context 0.9 - | so hungry", processed=True),
        feed(12, now - timedelta(seconds=20), "Steve voice action_oriented 0.9 - | give me a diamond pickaxe"),
    ]) + "\n", ""))
    impl, stop, seen_after = FakeImplementers(), asyncio.Event(), []

    async def watch(after: int):
        seen_after.append(after)
        yield f"LINE#13 {now:%H:%M:%S} Alex voice action_oriented 0.9 - | a diamond pickaxe for me too"
        await stop.wait()

    ask = FakeAsk(Decision=decision(part("command", "give a diamond pickaxe", cmd("give Steve diamond_pickaxe")),
                                    reply="there you go"))
    run = asyncio.create_task(serve(cfg, stop, vfg=vfg, watch=watch, catalog=catalog(), refs=FakeRefs(), ask=ask,
                                    make_implementers=lambda on_report: impl))
    for _ in range(200):
        if len([s for s in vfg.said() if s[0] == "there you go"]) == 2:
            break
        await asyncio.sleep(0.001)
    stop.set()
    assert await run == 0

    firsts = [c[:2] for c in vfg.calls]
    assert firsts[0] == ["up"] and {"watchers", "lookout", "jev", "context"} <= {c[0] for c in vfg.calls[1:6]}
    assert vfg.of("watchers")[0][2] == "join,leave,death,advancement,server"
    assert "conversational: a line said to the AI" in vfg.of("lookout")[0][2]
    assert "command — Run vanilla commands" in vfg.of("jev", "menu", "set")[0][3]
    assert vfg.of("context", "--since") == [["context", "--since", "-6h", "--json"]] and seen_after == [12]
    assert ["context", "mark", "10"] in vfg.calls  # history: marked read, never acted on
    assert len(ask.calls) == 2 and "give me bread" not in ask.calls[0]["prompt"].split("THE EVENT")[1]
    assert [GREETING] in vfg.said()
    assert ("result T1", "T1 failed: interrupted, the brain restarted while it ran") in vfg.rows("result")
    tasks = json.loads(cfg.tasks_file.read_text())["tasks"]
    assert [t["status"] for t in tasks.values()] == ["failed", "done", "done"] and impl.closed
