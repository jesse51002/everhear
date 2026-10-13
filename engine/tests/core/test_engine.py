"""The engine as a process sees it (C1's shape): Settings.from_env, start / run(stop) / submit /
close, and the ops not covered elsewhere (instances, classes, log, status)."""

import os
import math
import threading
import time

import pytest

from vfg_engine.engine import Engine, Settings
from vfg_engine.io import Damage
from vfg_engine.testing import FakeIo

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "examples")



def test_settings_from_env():
    s = Settings.from_env({"VFG_ENGINE_TICK_HZ": "20", "VFG_ENGINE_SLOW_MS": "4", "VFG_ENGINE_CUT_MS": "40",
                           "VFG_ENGINE_CONTENT": "/a:/b", "VFG_ENGINE_STATE": "/s.json",
                           "VFG_ENGINE_EXPORT": "/e.json"})
    assert (s.tick_hz, s.slow_ms, s.cut_ms, s.content, s.state, s.export) == (20, 4, 40, ("/a", "/b"), "/s.json", "/e.json")
    d = Settings.from_env({})
    assert (d.tick_hz, d.slow_ms, d.cut_ms, d.content) == (10, 5, 50, ("/opt/vfg/made/main/content",))
    assert d.state == "/opt/vfg/engine/state.json" and d.export == "/opt/vfg/engine/incantations.json"
    with pytest.raises(ValueError, match="VFG_ENGINE_TICK_HZ"):
        Settings.from_env({"VFG_ENGINE_TICK_HZ": "fast"})


def test_run_ticks_on_the_main_thread_until_stopped_then_saves(tmp_path):
    io = FakeIo()
    io.join("Steve")
    e = Engine(io, Settings(tick_hz=50, content=(EXAMPLES,), state=str(tmp_path / "s.json")))
    e.start()
    stop = threading.Event()
    answers = {}

    def other_thread():
        answers["status"] = e.submit("status").result(timeout=2)
        stop.set()
    threading.Thread(target=other_thread).start()
    t = time.monotonic()
    e.run(stop)
    assert time.monotonic() - t < 20          # it returned once stopped (headroom for a busy box)
    assert answers["status"]["ok"] and answers["status"]["players"] == ["Steve"]
    assert (tmp_path / "s.json").exists()
    e.close()
    assert io.closed


def test_a_request_cancelled_before_its_tick_is_never_carried_out(rig):
    r = rig(examples=True)
    r.start()
    r.io.join("Steve")
    r.io.join("Alex", pos=(0.0, 64.0, 6.0))
    r.tick()
    r.op("grant", who="Steve", ability="fireball")
    r.op("bind", player="Steve", phrase="fire ball", ability="fireball")
    r.io.look_at("Steve", (0.0, 64.0, 6.0))
    late = r.engine.submit("cast", {"player": "Steve", "phrase": "fire ball", "cast_id": "late"})
    assert late.cancel()                       # the socket gave up on it (its 1 s passed)
    kept = r.engine.submit("status")
    r.run_for(5)
    assert late.cancelled() and kept.result(timeout=0)["ok"]
    assert not r.io.ops_of(Damage)             # the cast never fired
    assert not r.records("ability", "fireball")[0].runs
    assert any("cancelled" in m for m in r.lines())


def test_an_engine_that_failed_to_start_does_not_write_over_the_saved_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"version": 1, "records": [], "players": {"Steve": {"online": false}}}')

    class Down(FakeIo):
        def open(self):
            raise ConnectionRefusedError("Minecraft is not up yet")
    e = Engine(Down(), Settings(content=(EXAMPLES,), state=str(path)))
    with pytest.raises(ConnectionRefusedError):
        e.start()
    e.close()
    assert "Steve" in path.read_text()


def test_instances_classes_and_log_ops(rig):
    r = rig(examples=True)
    r.start()
    r.io.join("Steve")
    r.io.hold("Steve", "fire_staff")
    r.tick()
    r.op("apply", who="Steve", mark="giant", seconds=20)
    rows = r.op("instances")["instances"]
    assert {(x["kind"], x["id"], x["user"]) for x in rows} == {("item", "fire_staff", "Steve"), ("mark", "giant", "Steve")}
    applied_at = r.clock.t - 0.1                  # the tick before the one that answered
    assert math.isclose(next(x["ends"] for x in rows if x["id"] == "giant"), applied_at + 20, abs_tol=0.01)
    assert [x["id"] for x in r.op("instances", kind="mark")["instances"]] == ["giant"]
    assert r.op("instances", user="alex")["instances"] == []
    classes = r.op("classes")["classes"]
    assert {c["id"]: c["kind"] for c in classes} == {"fire_staff": "item", "fireball": "ability",
                                                    "giant": "mark", "shrunk": "mark"}
    lines = r.op("log", n=3)["lines"]
    assert len(lines) == 3 and {"t", "level", "msg"} <= set(lines[0])
    assert r.op("nonsense")["ok"] is False


def test_a_refusal_keeps_the_quotes_its_words_hold(rig):
    # engine_e2e.py: `give Alex nope` answered "nope' is not a loaded item", its first quote cut off
    r = rig(examples=True)
    r.start()
    r.io.join("Steve")
    r.tick()
    assert r.op("give", player="Steve", item="nope") == {"ok": False, "error": "'nope' is not a loaded item"}
    assert r.op("unbind", player="Steve", phrase="ice wall") == {
        "ok": False, "error": "Steve has no incantation 'ice wall'"}      # a KeyError: its message, not its repr


def test_a_grant_to_someone_offline_starts_when_they_join(rig):
    passive = '''
class Armour(Ability):
    class Config(AbilityConfig):
        summary = "A little armour."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = UNTIL_STOPPED
        damage = 0

    def start(self, ctx):
        self.modifier("armor", 2, "add_value")
'''
    r = rig({"armour": passive})
    r.start()
    assert r.op("grant", who="Sam", ability="armour")["ok"]
    (rec,) = r.records("ability")
    assert not rec.started
    r.io.join("Sam")
    r.tick(2)
    assert rec.started and ("minecraft:armor", "everhear:ability/armour") in r.mods("Sam")


def test_a_fireball_thrown_then_the_staff_dropped_still_lands(rig):
    r = rig(examples=True)
    r.start()
    r.io.join("Steve")
    r.io.join("Alex", pos=(0.0, 64.0, 6.0))
    r.io.hold("Steve", "fire_staff")
    r.tick()
    r.io.look_at("Steve", (0.0, 64.0, 6.0))
    r.io.use("Steve")
    r.tick()
    r.io.unhold("Steve")                        # drops the staff mid-burn
    r.run_for(5)
    assert any(d.damage_type == "minecraft:explosion" and d.target == "Alex" for d in r.io.ops_of(Damage))
    assert not r.records("ability")             # and the lent grant went once it was done
    assert not r.rt.players["Steve"].has("fireball")


def test_a_ticks_damage_on_one_target_by_one_dealer_goes_as_one_op(rig):
    # engine_e2e.py, 2026-10-13: Fireball's last burn (1.05, on_fire) and its explosion (8.4) went in
    # one tick as two ops, and 26.3's 10 unhurtable ticks after the first took only 7.35 of the second
    r = rig(examples=True)
    r.start()
    r.io.join("Steve")
    r.io.join("Alex", pos=(0.0, 64.0, 6.0))
    r.io.hold("Steve", "fire_staff")
    r.tick()
    r.io.look_at("Steve", (0.0, 64.0, 6.0))
    r.io.use("Steve")
    r.run_for(5)
    hits = [(d.target, d.by, d.damage_type, round(d.amount, 4)) for d in r.io.ops_of(Damage)]
    assert hits == [("Alex", "Steve", "minecraft:on_fire", 1.05), ("Alex", "Steve", "minecraft:on_fire", 1.05),
                    ("Alex", "Steve", "minecraft:explosion", 9.45)]      # the last burn and the blast, one op
    # two dealers in one tick stay two ops
    from vfg_engine.engine import merge_damage
    two = [(Damage("u", 2, "minecraft:generic", by="Steve"), None), (Damage("u", 3, "minecraft:magic", by="Alex"), None),
           (Damage("U", 4, "minecraft:magic", by="steve"), None)]
    assert [op for op, _cb in merge_damage(two)] == [Damage("u", 6, "minecraft:magic", by="Steve"),
                                                      Damage("u", 3, "minecraft:magic", by="Alex")]


def test_content_that_swallows_the_cut_is_still_cut(rig):
    sly = '''
import time as _t


class Sly(Ability):
    every = 0.1

    class Config(AbilityConfig):
        summary = "Catches everything."
        damage = 0
        duration_s = UNTIL_STOPPED
        charge_s = 0
        cooldown_s = 0
        scale_per_point = 0

    def tick(self, ctx):
        end = _t.perf_counter() + 30.0
        while _t.perf_counter() < end:
            try:
                _t.perf_counter()
            except:          # noqa: E722 - the point of the test
                pass
'''
    r = rig({"sly": sly}, cut_ms=50)
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="sly")
    t = time.perf_counter()
    r.tick(2)
    assert time.perf_counter() - t < 10       # cut, not the 30 s it wanted (headroom for a busy box)
    assert not r.records(cid="sly")


def test_instances_say_which_abilities_are_lent(rig):
    r = rig(examples=True)
    r.start()
    r.io.join("Steve")
    r.io.join("Alex")
    r.io.hold("Steve", "fire_staff")
    r.tick()
    r.op("grant", who="Alex", ability="fireball")
    r.io.use("Steve")                         # the staff lends Steve fireball
    r.tick()
    rows = {row["user"]: row for row in r.op("instances", kind="ability")["instances"]}
    staff = next(rec.iid for rec in r.records("item"))
    assert rows["Steve"]["lent_by"] == staff and "lent_by" not in rows["Alex"]
