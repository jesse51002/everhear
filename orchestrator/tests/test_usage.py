import re

from helpers import new_store, temp_dir, use_fake_vfg

from orchestrator.usage import UsageMeter, component_of, join_refs, usage_args, usage_deltas
from orchestrator.vfg import vfg


def mu(inp, out, cost, cache_read=0, cache_write=0):
    return {"inputTokens": inp, "outputTokens": out, "cacheReadInputTokens": cache_read,
            "cacheCreationInputTokens": cache_write, "webSearchRequests": 0, "costUSD": cost,
            "contextWindow": 200000, "maxOutputTokens": 32000}


OPUS = "claude-opus-4-8"
HAIKU = "claude-haiku-4-6"
SONNET = "claude-sonnet-4-6"


# ---------------------------------------------------------------- usage deltas

def test_a_first_result_one_row_per_model_in_is_input_plus_cache_read_plus_cache_write():
    rows, snapshot = usage_deltas({}, {OPUS: mu(100, 50, 0.5, 1000, 200), SONNET: mu(10, 5, 0.03)}, ["T1"])
    assert rows == [
        {"component": "orchestrator", "model": OPUS, "in": 1300, "out": 50, "cost": 0.5, "cacheRead": 1000,
         "cacheWrite": 200, "ref": "T1"},
        {"component": "implementer", "model": SONNET, "in": 10, "out": 5, "cost": 0.03, "cacheRead": 0, "cacheWrite": 0,
         "ref": "T1"},
    ]
    assert snapshot[OPUS] == {"in": 1300, "out": 50, "cost": 0.5, "cacheRead": 1000, "cacheWrite": 200}


def test_a_resumed_session_the_delta_from_the_snapshot():
    snap = {OPUS: {"in": 1300, "out": 50, "cost": 0.5}}
    rows, _ = usage_deltas(snap, {OPUS: mu(200, 80, 0.75, 1000, 200)}, [])
    # a snapshot from before the cache was counted: this row's cache share is unknown, so left out
    assert rows == [{"component": "orchestrator", "model": OPUS, "in": 100, "out": 30, "cost": 0.25}]
    snap = {OPUS: {"in": 1300, "out": 50, "cost": 0.5, "cacheRead": 1000, "cacheWrite": 200}}
    rows, _ = usage_deltas(snap, {OPUS: mu(200, 80, 0.75, 1900, 300)}, [])
    assert rows == [{"component": "orchestrator", "model": OPUS, "in": 1100, "out": 30, "cost": 0.25,
                     "cacheRead": 900, "cacheWrite": 100}]


def test_a_lower_total_a_new_session_counts_from_zero():
    snap = {OPUS: {"in": 5000, "out": 900, "cost": 2}}
    rows, snapshot = usage_deltas(snap, {OPUS: mu(40, 20, 0.1)}, [])
    assert rows == [{"component": "orchestrator", "model": OPUS, "in": 40, "out": 20, "cost": 0.1,
                     "cacheRead": 0, "cacheWrite": 0}]
    assert snapshot[OPUS] == {"in": 40, "out": 20, "cost": 0.1, "cacheRead": 0, "cacheWrite": 0}


def test_an_unchanged_model_writes_no_row_refs_are_left_out_when_none():
    snap = {HAIKU: {"in": 10, "out": 5, "cost": 0.01}}
    rows, _ = usage_deltas(snap, {HAIKU: mu(10, 5, 0.01)}, [])
    assert rows == []
    assert usage_args({"component": "implementer", "model": HAIKU, "in": 1, "out": 2, "cost": 0.5}) == [
        "usage", "add", "--component", "implementer", "--model", HAIKU, "--in", "1", "--out", "2", "--cost", "0.500000"]


def test_refs_fit_in_vfgs_64_characters_newest_kept():
    many = ["T%d" % (i + 1) for i in range(30)]
    ref = join_refs(many)
    assert len(ref) <= 64
    assert ref.endswith("T29,T30")
    assert all(re.match(r"^T\d+$", x) for x in ref.split(","))
    assert join_refs(["T1", "T2"]) == "T1,T2"
    assert join_refs([]) == ""


def test_the_implementers_model_is_the_implementer_anything_else_the_orchestrator():
    assert component_of("claude-sonnet-4-6") == "implementer"  # default implementer: sonnet
    assert component_of("claude-opus-4-8") == "orchestrator"
    assert component_of("claude-haiku-4-6") == "orchestrator"
    assert component_of("claude-haiku-4-6", "haiku") == "implementer"
    assert component_of("claude-3-5-HAIKU", "haiku") == "implementer"
    assert component_of("claude-sonnet-5", "haiku") == "orchestrator"
    assert component_of("claude-sonnet-4-6", "claude-sonnet-4-6") == "implementer"


def test_each_row_carries_its_own_models_cost_sonnet_is_priced_as_sonnet():
    rows, _ = usage_deltas({}, {OPUS: mu(1000, 100, 0.09), SONNET: mu(1000, 100, 0.0045)}, [], "sonnet")
    assert next(r for r in rows if r["model"] == SONNET) == {
        "component": "implementer", "model": SONNET, "in": 1000, "out": 100, "cost": 0.0045, "cacheRead": 0, "cacheWrite": 0}
    assert next(r for r in rows if r["model"] == OPUS)["cost"] == 0.09


# ---------------------------------------------------------------- the usage meter

async def test_rows_go_to_vfg_usage_add_with_the_tasks_open_since_the_previous_row():
    d = temp_dir()
    fake = use_fake_vfg(d)
    store = new_store(d)
    meter = UsageMeter(store, vfg)
    store.add("T1", at="x", task="a", context="")
    store.add("T2", at="x", task="b", context="")
    meter.note(["T1", "T2"])
    store.reply("T1", "done", "ok")
    await meter.on_result({OPUS: mu(100, 10, 0.2), SONNET: mu(50, 5, 0.01)})
    # T2 is still open; T3 opens and closes before the next row
    store.add("T3", at="x", task="c", context="")
    meter.note(["T3"])
    store.reply("T3", "done", "ok")
    store.reply("T2", "done", "ok")
    await meter.on_result({OPUS: mu(150, 20, 0.3), SONNET: mu(50, 5, 0.01)})
    # nothing open since: no ref
    await meter.on_result({OPUS: mu(160, 25, 0.31), SONNET: mu(50, 5, 0.01)})
    rows = [c for c in fake.calls() if c[0] == "usage"]
    none = ["--cache-read", "0", "--cache-write", "0"]
    assert rows == [
        ["usage", "add", "--component", "orchestrator", "--model", OPUS, "--in", "100", "--out", "10", "--cost", "0.200000", *none, "--ref", "T1,T2"],
        ["usage", "add", "--component", "implementer", "--model", SONNET, "--in", "50", "--out", "5", "--cost", "0.010000", *none, "--ref", "T1,T2"],
        ["usage", "add", "--component", "orchestrator", "--model", OPUS, "--in", "50", "--out", "10", "--cost", "0.100000", *none, "--ref", "T2,T3"],
        ["usage", "add", "--component", "orchestrator", "--model", OPUS, "--in", "10", "--out", "5", "--cost", "0.010000", *none],
    ]
    assert new_store(d).state.usage[OPUS] == {"in": 160, "out": 25, "cost": 0.31, "cacheRead": 0, "cacheWrite": 0}


# Not in the TypeScript tests: the cache, so a cache that is not hitting shows in the rows.
async def test_rows_carry_the_cache_read_and_written_since_the_last_row():
    d = temp_dir()
    fake = use_fake_vfg(d)
    store = new_store(d)
    meter = UsageMeter(store, vfg, "claude-haiku-5-5")
    await meter.on_result({OPUS: mu(10, 5, 0.1, 9000, 1000), "claude-haiku-5-5": mu(3, 40, 0.01, 0, 31000)})
    await meter.on_result({OPUS: mu(15, 9, 0.2, 19500, 1200), "claude-haiku-5-5": mu(8, 90, 0.02, 31000, 33000)})
    rows = [c for c in fake.calls() if c[0] == "usage"]
    flags = [(c[3], c[c.index("--in") + 1], c[c.index("--cache-read") + 1], c[c.index("--cache-write") + 1]) for c in rows]
    assert flags == [("orchestrator", "10010", "9000", "1000"), ("implementer", "31003", "0", "31000"),
                     ("orchestrator", "10705", "10500", "200"), ("implementer", "33005", "31000", "2000")]
