"""A one-off that did nothing it was asked to (nobody online, no such place, nothing to run...) says so
in words and reports a truthy `failed` fact, so `vfg action run` answers ok: false; one that did its
work reports none. On FakeIo, nobody online unless a case needs someone."""

import pytest

NOBODY = [                         # (scenario, args): started with nobody of them online
    ("blood_moon", {"players": "Nobody"}),
    ("castle", {"players": "Nobody"}),
    ("chosen_one", {"player": "Nobody"}),
    ("closing_ring", {"players": "Nobody"}),
    ("defend_gregor", {"players": "Nobody"}),
    ("horde", {"players": "Nobody"}),
    ("lightning_storm", {"players": "Nobody"}),
    ("meteor", {"players": "Nobody"}),
    ("mountains", {"players": "Nobody"}),
    ("nuke", {"players": "Nobody"}),
    ("parkour", {"players": "Nobody"}),
    ("sanctuary", {"players": "Nobody"}),
    ("sky_platform", {"players": "Nobody"}),
    ("sniper_nest", {"players": "Nobody"}),
    ("teleport", {"players": "Nobody"}),
    ("weapon_chest", {"players": "Nobody", "contents": "guns"}),
    ("command", {}),
    ("forget_place", {"name": "nowhere"}),
    ("save_place", {}),
    ("place_barrier", {}),
]


@pytest.mark.parametrize("scenario, args", NOBODY, ids=[n for n, _ in NOBODY])
def test_a_one_off_that_did_nothing_says_failed(rig, scenario, args):
    r = rig(scenario)
    r.start()
    reply = r.op("start", scenario=scenario, args=args, wait_s=1)
    assert reply["ok"], reply
    got = r.result(reply["instance"])
    assert got.get("facts", {}).get("failed"), got
    assert got.get("summary"), got


def test_a_one_off_that_did_its_work_reports_no_failure(rig):
    r = rig("forget_place", "save_place")
    r.start()
    r.io.join("Steve")
    r.tick()
    saved = r.result(r.start_scenario("save_place", name="home", player="Steve"))
    assert not saved["facts"].get("failed"), saved
    forgot = r.result(r.start_scenario("forget_place", name="home"))
    assert forgot["summary"] == "forgot home" and not forgot["facts"].get("failed"), forgot
