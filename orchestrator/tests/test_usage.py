import re

from helpers import new_store, temp_dir, use_fake_vfg

from orchestrator.usage import UsageMeter, call_units, component_of, join_refs, split_shared, usage_args, usage_deltas
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


# ---------------------------------------------------------------- one model for both (every agent on Haiku 5.5)

H55 = "claude-haiku-5-5"


def call(mid, fresh, read, write, out, model=H55, w1h=0):
    return {"id": mid, "model": model, "content": [],
            "usage": {"input_tokens": fresh, "cache_read_input_tokens": read, "cache_creation_input_tokens": write,
                      "output_tokens": out, "cache_creation": {"ephemeral_5m_input_tokens": write - w1h,
                                                               "ephemeral_1h_input_tokens": w1h}}}


def test_a_calls_units_are_anthropics_ratios_and_five_times_past_100k_prompt_tokens_on_haiku_5_5():
    u = call("m", 10, 1000, 200, 20, w1h=100)["usage"]
    assert call_units(H55, u) == 10 + 100 * 1.25 + 100 * 2 + 1000 * 0.1 + 20 * 5
    at = call("m", 1000, 95000, 4000, 100)["usage"]  # prompt 100,000: not over
    over = call("m", 1000, 95001, 4000, 100)["usage"]
    assert call_units(H55, at) == 1000 + 95000 * 0.1 + 4000 * 1.25 + 500
    assert call_units(H55, over) == 5 * (1000 + 95001 * 0.1 + 4000 * 1.25 + 500)
    assert call_units("claude-opus-5-5", over) == 1000 + 95001 * 0.1 + 4000 * 1.25 + 500


def test_split_shared_the_main_threads_calls_become_the_orchestrators_row_the_rest_the_implementers():
    main = {H55: {"in": 1100, "out": 10, "cacheRead": 1000, "cacheWrite": 0, "units": 100 + 100 + 50}}
    rows = [{"component": "implementer", "model": H55, "in": 5100, "out": 30, "cost": 0.0007, "cacheRead": 3000,
             "cacheWrite": 1000, "ref": "T1"}]
    orch, impl = split_shared(rows, main)
    # the rest: 4000 in (2000 read, 1000 written, 1000 fresh), 20 out: 1000 + 1250 + 200 + 100 = 2550 units
    assert {k: orch[k] for k in ("component", "in", "out", "cacheRead", "cacheWrite", "ref")} == {
        "component": "orchestrator", "in": 1100, "out": 10, "cacheRead": 1000, "cacheWrite": 0, "ref": "T1"}
    assert {k: impl[k] for k in ("component", "in", "out", "cacheRead", "cacheWrite", "ref")} == {
        "component": "implementer", "in": 4000, "out": 20, "cacheRead": 2000, "cacheWrite": 1000, "ref": "T1"}
    assert abs(orch["cost"] - 0.0007 * 250 / 2800) < 1e-12
    assert abs(orch["cost"] + impl["cost"] - 0.0007) < 1e-12


def test_split_shared_leaves_other_rows_alone_and_drops_an_empty_side():
    opus_row = {"component": "orchestrator", "model": OPUS, "in": 10, "out": 1, "cost": 0.1}
    impl_row = {"component": "implementer", "model": H55, "in": 50, "out": 5, "cost": 0.01}
    assert split_shared([opus_row, impl_row], {}) == [opus_row, impl_row]
    assert split_shared([opus_row], {OPUS: {"in": 10, "out": 1, "cacheRead": 0, "cacheWrite": 0, "units": 15}}) == [opus_row]
    # only the main thread ran: one orchestrator row with the whole cost
    only = split_shared([impl_row], {H55: {"in": 50, "out": 5, "cacheRead": 0, "cacheWrite": 0, "units": 75}})
    assert only == [{"component": "orchestrator", "model": H55, "in": 50, "out": 5, "cost": 0.01}]


async def test_with_every_agent_on_haiku_5_5_the_orchestrators_calls_still_get_their_own_rows():
    d = temp_dir()
    fake = use_fake_vfg(d)
    store = new_store(d)
    meter = UsageMeter(store, vfg, H55)
    first = call("msg_1", 100, 1000, 0, 10)
    meter.note_main_call(first)
    meter.note_main_call(first)  # the same message again, for its next content block: counted once
    meter.note_main_call({"id": "msg_x", "content": [], "usage": {"input_tokens": 5}})  # no model: not counted
    await meter.on_result({H55: mu(400, 30, 0.0004, 2000, 1000)})
    meter.note_main_call(call("msg_2", 10, 0, 0, 1))
    await meter.on_result({H55: mu(410, 31, 0.00041, 2000, 1000)})  # the main thread alone
    await meter.on_result({H55: mu(510, 41, 0.00051, 2000, 1000)})  # implementers alone
    rows = [c for c in fake.calls() if c[0] == "usage"]
    got = [(c[3], c[c.index("--in") + 1], c[c.index("--out") + 1], c[c.index("--cache-read") + 1]) for c in rows]
    assert got == [("orchestrator", "1100", "10", "1000"), ("implementer", "2300", "20", "1000"),
                   ("orchestrator", "10", "1", "0"),
                   ("implementer", "100", "10", "0")]
    costs = [float(c[c.index("--cost") + 1]) for c in rows]
    assert abs(costs[0] + costs[1] - 0.0004) < 2e-6 and costs[2] == 0.00001 and costs[3] == 0.0001
