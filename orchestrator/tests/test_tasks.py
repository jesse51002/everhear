# Not in the TypeScript tests: the TypeScript runner's state.json loads, and is written back as it was.
import json
import os

from helpers import new_store, read, temp_dir, write

TS_STATE = {
    "inboxOffset": 412,
    "usage": {"claude-opus-4-8": {"in": 1300, "out": 50, "cost": 0.5}},
    "tasks": {
        "T2": {"at": "2026-10-08T06:01:00.000Z", "task": "rain TNT", "context": "", "seenAt": "2026-10-08T06:01:00.120Z",
               "status": "done", "nudged": False,
               "replies": [{"status": "done", "text": "TNT rain is running.", "sent": True, "n": 2}],
               "closedAt": "2026-10-08T06:01:40.500Z"},
        "T10": {"at": "2026-10-08T06:02:00.000Z", "task": "build a hut", "context": "Steve is at 120 64 -40.",
                "seenAt": "2026-10-08T06:02:00.100Z", "status": "open", "nudged": True,
                "replies": [{"status": "progress", "text": "Working on the hut.", "sent": False, "n": 3}]},
    },
    "nextReply": 4,
    "sessionId": "5f0c6d2e-0000-4000-8000-000000000000",
}


def test_a_state_json_written_by_the_typescript_runner_loads_and_is_written_back_as_it_was():
    d = temp_dir()
    text = json.dumps(TS_STATE, indent=2) + "\n"
    write(os.path.join(d, "state.json"), text)
    store = new_store(d)
    assert store.state.session_id == TS_STATE["sessionId"]
    assert store.state.inbox_offset == 412
    assert store.open_ids() == ["T10"]
    assert store.get("T2").closed_at == "2026-10-08T06:01:40.500Z"
    assert [(tid, r.n) for tid, r in store.unsent()] == [("T10", 3)]
    store.save()
    assert read(os.path.join(d, "state.json")) == text
    assert store.reply("T10", "done", "The hut is built.").reply.n == 4
