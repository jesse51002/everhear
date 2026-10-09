import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import ANY

from helpers import append, new_store, read, temp_dir, write

from orchestrator.config import iso, now_ms, parse_time
from orchestrator.inbox import InboxFollower, task_message
from orchestrator.tasks import Reply


def setup(stale_minutes=10, now=None):
    d = temp_dir()
    inbox = os.path.join(d, "inbox.jsonl")
    store = new_store(d)
    pushed = []
    f = InboxFollower(inbox=inbox, store=store, stale_minutes=stale_minutes,
                      on_task=lambda tid, m: pushed.append((tid, m)), now=now)
    return SimpleNamespace(dir=d, inbox=inbox, store=store, pushed=pushed, f=f)


def line(o: dict) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":")) + "\n"


def recent() -> str:
    return iso(now_ms() - 5_000)


def size(text: str) -> int:
    return len(text.encode("utf-8"))


def test_a_missing_inbox_is_empty():
    s = setup()
    assert s.f.poll() == []
    assert s.pushed == []
    assert s.store.state.inbox_offset == 0


def test_a_half_written_line_waits_for_its_newline():
    s = setup()
    full = line({"id": "T1", "at": recent(), "task": "make it day", "context": ""})
    write(s.inbox, full[:20])
    assert s.f.poll() == []
    assert s.store.state.inbox_offset == 0
    append(s.inbox, full[20:])
    assert s.f.poll() == ["T1"]
    assert [p[0] for p in s.pushed] == ["T1"]
    assert s.store.state.inbox_offset == size(full)
    assert s.store.get("T1").status == "open"


def test_whole_lines_before_a_half_one_are_taken_the_half_one_later():
    s = setup()
    a = line({"id": "T1", "at": recent(), "task": "a"})
    b = line({"id": "T2", "at": recent(), "task": "b ü"})
    write(s.inbox, a + b[:10])
    assert s.f.poll() == ["T1"]
    append(s.inbox, b[10:])
    assert s.f.poll() == ["T2"]
    assert s.store.state.inbox_offset == size(a + b)
    assert s.store.get("T2").task == "b ü"


def test_a_duplicate_id_is_skipped_also_after_a_restart():
    s = setup()
    write(s.inbox, line({"id": "T1", "at": recent(), "task": "a"}) + line({"id": "T1", "at": recent(), "task": "again"}))
    assert s.f.poll() == ["T1"]
    assert len(s.pushed) == 1
    # a restart: the state is reloaded; the inbox is read again from 0
    store2 = new_store(s.dir)
    store2.state.inbox_offset = 0
    pushed2 = []
    InboxFollower(inbox=s.inbox, store=store2, stale_minutes=10, on_task=lambda tid, m: pushed2.append(tid)).poll()
    assert pushed2 == []


def test_a_shrunk_inbox_is_read_again_from_0():
    s = setup()
    write(s.inbox, line({"id": "T1", "at": recent(), "task": "a long first task text"}) + line({"id": "T2", "at": recent(), "task": "b"}))
    s.f.poll()
    write(s.inbox, line({"id": "T3", "at": recent(), "task": "c"}))
    assert s.f.poll() == ["T3"]
    assert s.store.state.inbox_offset == os.path.getsize(s.inbox)


def test_bad_json_and_lines_without_an_id_are_skipped():
    s = setup()
    write(s.inbox, "{not json\n" + line({"task": "no id"}) + line({"id": "T4", "at": recent(), "task": "ok"}))
    assert s.f.poll() == ["T4"]
    assert list(s.store.state.tasks) == ["T4"]


def test_an_id_without_a_task_is_failed_as_malformed():
    s = setup()
    write(s.inbox, line({"id": "T5", "at": recent()}) + line({"id": "T6", "at": recent(), "task": "   "}))
    assert s.f.poll() == []
    assert s.pushed == []
    for tid in ("T5", "T6"):
        t = s.store.get(tid)
        assert t.status == "failed"
        assert t.replies == [Reply(status="failed", text="malformed task", sent=False, n=ANY)]


def test_a_stale_task_is_failed_as_expired():
    s = setup(10)
    write(s.inbox, line({"id": "T7", "at": iso(now_ms() - 3600_000), "task": "too late"}))
    assert s.f.poll() == []
    assert s.pushed == []
    t = s.store.get("T7")
    assert t.status == "failed"
    assert t.replies[0].text == "Expired: it waited 60 minutes for the orchestrator."


def test_the_message_carries_id_time_task_and_context():
    s = setup(10, lambda: parse_time("2026-10-07T18:22:40.000Z"))
    write(s.inbox, line({"id": "T7", "at": "2026-10-07T18:22:31.412Z", "task": "build me a castle",
                         "context": "Steve is at 120 64 -40, near spawn. They want it big."}))
    s.f.poll()
    assert s.pushed[0][1] == (
        "TASK T7 at 18:22:31 UTC\nbuild me a castle\n\nContext from the conversation:\n"
        "Steve is at 120 64 -40, near spawn. They want it big.")
    assert task_message("T1", parse_time("2026-10-07T01:02:03Z"), "x", "") == "TASK T1 at 01:02:03 UTC\nx"


def test_the_offset_is_saved_in_state_json():
    s = setup()
    write(s.inbox, line({"id": "T1", "at": recent(), "task": "a"}))
    s.f.poll()
    saved = json.loads(read(os.path.join(s.dir, "state.json")))
    assert saved["inboxOffset"] == os.path.getsize(s.inbox)
    assert saved["tasks"]["T1"]["status"] == "open"


async def test_start_follows_appends():
    s = setup()
    write(s.inbox, "")
    s.f.start(20)
    append(s.inbox, line({"id": "T9", "at": recent(), "task": "later"}))
    await asyncio.sleep(0.12)
    s.f.stop()
    assert [p[0] for p in s.pushed] == ["T9"]
