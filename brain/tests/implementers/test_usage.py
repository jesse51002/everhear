"""A run's usage (usage.py): its requests' tokens, each priced at its own size and time, one row a
run with its largest request; nothing a harness counted for the session before it."""
from brain.implementers import usage
from brain.implementers.usage import RunUsage, cost, usage_args

from .fakes import request

THURSDAY_PEAK, THURSDAY_NOON = 1_760_000_000.0, 1_760_011_200.0  # 2025-10-09, 08:53 and 12:00 UTC


def test_a_run_sums_its_requests_each_priced_at_its_own_size_and_time():
    u = RunUsage("castle_steve")
    a = u.add(request(80000, 0, 0, 500, at=THURSDAY_PEAK))
    b = u.add(request(1000, 80000, 0, 300, at=THURSDAY_NOON))
    assert a == (80000 * 0.30 + 500 * 1.20) / 1e6
    assert b == (1000 * 0.15 + 80000 * 0.003 + 300 * 0.60) / 1e6  # off-peak: half
    assert u.rows() == [{"component": "implementer", "model": "deepseek-flash", "ref": "castle_steve", "in": 161000,
                         "out": 800, "reasoning": 0, "cacheRead": 80000, "cacheWrite": 0, "cost": a + b,
                         "list_cost": a + b, "priced_by": "our table", "largest": 81000, "broken": 0}]
    # a run that made no request writes no row
    assert RunUsage("castle_steve").rows() == []


def test_reasoning_is_part_of_the_output_priced_once_and_summed_apart():
    u = RunUsage("blizzard_styrka")
    thought = request(1000, 0, 0, 900)
    thought.reasoning = 850  # of the 900 output tokens
    assert u.add(thought) == cost(request(1000, 0, 0, 900)) == (1000 * 0.30 + 900 * 1.20) / 1e6
    u.add(request(1000, 0, 0, 100))
    [row] = u.rows()
    assert (row["out"], row["reasoning"]) == (1000, 850)
    assert "--out" in usage_args(row) and "reasoning" not in " ".join(usage_args(row))  # vfg usage add has no column


def test_a_broken_request_is_counted_and_said_and_without_a_bill_the_table_prices_the_run():
    u = RunUsage("blizzard_styrka")
    whole = request(1000, 0, 0, 100)
    whole.billed = 0.0002
    broken = request(500, 0, 0, 0)
    broken.broken = True  # what was read before the break: no bill
    u.add(whole)
    u.add(broken)
    [row] = u.rows()
    assert (row["broken"], row["in"], row["priced_by"]) == (1, 1500, "our table")


def test_haiku_is_priced_by_each_requests_own_size():
    small = request(50000, 40000, 10000, 1000, model="claude-haiku-5-5")
    big = request(50000, 40000, 10001, 1000, model="claude-haiku-5-5")
    assert cost(small) == (50000 * 0.10 + 40000 * 0.01 + 10000 * 0.125 + 1000 * 0.50) / 1e6
    assert cost(big) == (50000 * 0.50 + 40000 * 0.05 + 10001 * 0.625 + 1000 * 2.50) / 1e6


def test_a_model_with_no_price_counts_at_zero_and_is_said_once(monkeypatch):
    said = []
    monkeypatch.setattr(usage, "log", lambda area, msg: said.append(msg))
    monkeypatch.setattr(usage, "_unpriced", set())
    u = RunUsage("x")
    assert u.add(request(10, 0, 0, 1, model="gpt-x")) == 0 and u.add(request(10, 0, 0, 1, model="gpt-x")) == 0
    assert said == ["no price for gpt-x: its requests are counted at $0"]
    assert [r["cost"] for r in u.rows()] == [0]


def test_a_resumed_session_counts_only_this_runs_requests():
    """The double count is gone: a follow-up after a restart is a new RunUsage, its rows its own requests."""
    first, again = RunUsage("t"), RunUsage("t")
    first.add(request(90000, 0, 0, 100))
    again.add(request(500, 90000, 0, 50))
    assert [r["in"] for r in first.rows() + again.rows()] == [90000, 90500]


def test_a_row_as_vfg_usage_add_takes_it():
    u = RunUsage("galick_gun_styrk")
    u.add(request(10, 7, 1, 2, model="claude-haiku-5-5"))
    [row] = u.rows()
    assert usage_args(row) == ["usage", "add", "--component", "implementer", "--model", "claude-haiku-5-5",
                               "--in", "18", "--out", "2", "--cost", "%.6f" % row["cost"], "--cache-read", "7",
                               "--cache-write", "1", "--largest", "18", "--ref", "galick_gun_styrk"]


def test_a_run_whose_every_request_says_what_it_was_billed_costs_that_with_the_list_price_beside_it():
    from brain.implementers.harness.base import Request
    billed = RunUsage("t")
    billed.add(Request("deepseek/deepseek-v4.1-flash", 1000, 9000, 0, 100, 1_760_000_000.0, billed=0.0004))
    billed.add(Request("deepseek/deepseek-v4.1-flash", 200, 10000, 0, 50, 1_760_000_000.0, billed=0.0002))
    [row] = billed.rows()
    assert (row["priced_by"], round(row["cost"], 6)) == ("the provider", 0.0006)
    assert round(row["list_cost"], 6) == round((1200 * 0.30 + 19000 * 0.006 + 150 * 1.20) / 1e6, 6)
    mixed = RunUsage("t")  # one request without it: the table, for the whole row
    mixed.add(Request("deepseek/deepseek-v4.1-flash", 1000, 0, 0, 10, 1_760_000_000.0, billed=0.0003))
    mixed.add(Request("deepseek/deepseek-v4.1-flash", 1000, 0, 0, 10, 1_760_000_000.0))
    [row] = mixed.rows()
    assert row["priced_by"] == "our table" and row["cost"] == row["list_cost"]
