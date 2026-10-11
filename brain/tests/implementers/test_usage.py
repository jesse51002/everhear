"""A run's usage rows (usage.py): its session's totals less the last run's."""
from brain.implementers.usage import rows, usage_args

from .fakes import haiku

H = "claude-haiku-5-5"


def test_a_run_writes_what_its_session_spent_since_the_last_run():
    first, snap = rows({}, haiku(100, 50, 0.5, read=1000, write=200), "castle_steve")
    assert first == [{"component": "implementer", "model": H, "ref": "castle_steve", "in": 1300, "out": 50, "cacheRead": 1000,
                      "cacheWrite": 200, "cost": 0.5}]
    # a follow-up resumed the session, whose totals the CLI restored
    again, snap = rows(snap, haiku(200, 80, 0.75, read=1900, write=300), "castle_steve")
    assert [(r["in"], r["out"], r["cacheRead"], r["cacheWrite"], r["cost"]) for r in again] == [
        (1100, 30, 900, 100, 0.25)]
    # nothing moved: no row; a total lower than the last: a session started anew, counted from zero
    assert rows(snap, haiku(200, 80, 0.75, read=1900, write=300), "castle_steve")[0] == []
    fresh, _ = rows(snap, haiku(40, 20, 0.1), "castle_steve")
    assert [(r["in"], r["out"], r["cost"]) for r in fresh] == [(40, 20, 0.1)]


def test_a_row_as_vfg_usage_add_takes_it():
    [row], _ = rows({}, haiku(10, 2, 0.5, read=7, write=1), "galick_gun_styrk")
    assert usage_args(row) == ["usage", "add", "--component", "implementer", "--model", H, "--in", "18", "--out", "2",
                               "--cost", "0.500000", "--cache-read", "7", "--cache-write", "1", "--ref", "galick_gun_styrk"]
