import time

from helpers import new_store, temp_dir, until, use_fake_vfg, write

from orchestrator.config import iso, now_ms
from orchestrator.reply import Outbox, ReplyArgs, create_reply_server, handle_reply, reply_schema
from orchestrator.tasks import clean_text
from orchestrator.vfg import vfg


def store_with(*ids):
    d = temp_dir()
    store = new_store(d)
    for tid in ids:
        store.add(tid, at=iso(now_ms()), task="t", context="")
    store.save()
    return d, store


def reply(store, task, status, text):
    return handle_reply(store, ReplyArgs(task=task, status=status, text=text))


# ---------------------------------------------------------------- the reply tool

def test_an_unknown_task_is_an_error():
    _, store = store_with("T1")
    r = reply(store, "T9", "done", "x")
    assert r["is_error"] is True
    assert "T9" in r["content"][0]["text"]


def test_done_closes_a_second_close_is_refused():
    _, store = store_with("T7")
    assert "is_error" not in reply(store, "T7", "done", "Day now.")
    assert store.get("T7").status == "done"
    again = reply(store, "T7", "failed", "oops")
    assert again["is_error"] is True
    assert again["content"][0]["text"] == "T7 is already closed as done."
    assert len(store.get("T7").replies) == 1


def test_progress_after_done_is_refused_progress_before_is_kept():
    _, store = store_with("T1")
    assert "is_error" not in reply(store, "T1", "progress", "half way")
    assert store.get("T1").status == "open"
    assert "is_error" not in reply(store, "T1", "failed", "no luck")
    assert reply(store, "T1", "progress", "more")["is_error"] is True
    assert [r.status for r in store.get("T1").replies] == ["progress", "failed"]


def test_text_is_cut_to_300_with_an_ellipsis_newlines_become_spaces():
    _, store = store_with("T1")
    reply(store, "T1", "done", "a\nb\r\n" + "x" * 400)
    text = store.get("T1").replies[0].text
    assert len(text) == 300
    assert text.endswith("…")
    assert text.startswith("a b x")
    assert clean_text("short") == "short"
    assert clean_text("y" * 300) == "y" * 300


def test_empty_text_is_refused():
    _, store = store_with("T1")
    assert reply(store, "T1", "done", " \n ")["is_error"] is True
    assert store.get("T1").status == "open"


def test_the_reply_is_saved_in_state_json_at_once():
    d, store = store_with("T1")
    reply(store, "T1", "done", "ok")
    again = new_store(d)
    assert again.get("T1").status == "done"
    assert again.get("T1").replies[0].sent is False


def test_the_mcp_server_is_named_orch_and_always_loaded():
    _, store = store_with()
    s = create_reply_server(store)
    assert s["type"] == "sdk"
    assert s["name"] == "orch"
    # Not in the TypeScript test: alwaysLoad rides on the config (the CLI's sdk server config
    # takes it), and the schema is the TypeScript runner's: an id pattern, the three statuses, non-empty text.
    assert s["alwaysLoad"] is True
    schema = reply_schema()
    assert schema["required"] == ["task", "status", "text"]
    assert schema["properties"]["task"] == {"type": "string", "pattern": r"^T\d+$", "description": 'The task id, like "T7".'}
    assert schema["properties"]["status"] == {"type": "string", "enum": ["progress", "done", "failed"]}
    assert schema["properties"]["text"]["minLength"] == 1
    assert "title" not in schema


# ---------------------------------------------------------------- the outbox

async def test_sends_in_order_with_an_argv_array():
    d, store = store_with("T1", "T2")
    fake = use_fake_vfg(d)
    store.reply("T1", "progress", "building; it's 'quoted' $HOME")
    store.reply("T2", "done", "day now")
    store.reply("T1", "done", "castle built")
    ob = Outbox(store, vfg, [5, 5], 5)
    ob.start()
    await until(lambda: ob.pending() == 0)
    await ob.stop()
    assert fake.calls() == [
        ["orch", "reply", "T1", "progress", "building; it's 'quoted' $HOME"],
        ["orch", "reply", "T2", "done", "day now"],
        ["orch", "reply", "T1", "done", "castle built"],
    ]
    assert new_store(d).unsent() == []


async def test_a_reply_is_retried_after_two_failures_and_not_dropped():
    d, store = store_with("T1", "T2")
    fake = use_fake_vfg(d)
    write(fake.fail_file, "2")
    store.reply("T1", "done", "first")
    store.reply("T2", "failed", "second")
    ob = Outbox(store, vfg, [20, 20], 20)
    ob.start()
    await until(lambda: ob.pending() == 0, 5000)
    await ob.stop()
    assert [" ".join(c[2:4]) for c in fake.calls()] == ["T1 done", "T1 done", "T1 done", "T2 failed"]


async def test_unsent_replies_are_sent_after_a_restart():
    d, store = store_with("T1")
    fake = use_fake_vfg(d)
    write(fake.fail_file, "100")
    store.reply("T1", "done", "it worked")
    ob = Outbox(store, vfg, [10], 10)
    ob.start()
    await until(lambda: len(fake.calls()) >= 2)
    await ob.stop()
    assert len(new_store(d).unsent()) == 1
    # restart: a new process loads state.json, EC2 is back
    write(fake.fail_file, "0")
    store2 = new_store(d)
    ob2 = Outbox(store2, vfg, [10], 10)
    ob2.start()
    await until(lambda: ob2.pending() == 0)
    await ob2.stop()
    assert new_store(d).get("T1").replies[0].sent is True
    assert fake.calls()[-1] == ["orch", "reply", "T1", "done", "it worked"]


async def test_drain_gives_up_at_its_deadline_and_keeps_the_reply():
    d, store = store_with("T1")
    fake = use_fake_vfg(d)
    write(fake.fail_file, "100")
    store.reply("T1", "failed", "stopped")
    ob = Outbox(store, vfg, [50], 50)
    ob.start()
    t = time.monotonic()
    assert await ob.drain(300) is False
    assert time.monotonic() - t < 1.0
    await ob.stop()
    assert len(new_store(d).unsent()) == 1
