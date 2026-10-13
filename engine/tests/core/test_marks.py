"""Marks: timed by the engine with their own modifier ids (the game engine spec, "Potions and
marks" and its marks table)."""

import math

from vfg_engine.io import GiveEffect

GIANT = ("minecraft:scale", "everhear:mark/giant")
SHRUNK = ("minecraft:scale", "everhear:mark/shrunk")

STACKED = '''
class Weight(Mark):
    class Config(MarkConfig):
        summary = "Slower, more with each one."
        duration_s = 20
        damage = 0
        again = "stack"

    def start(self, ctx):
        self.modifier("movement_speed", -0.1, "add_multiplied_total")


class Curse(Mark):
    class Config(MarkConfig):
        summary = "A curse for life; it glows."
        duration_s = UNTIL_STOPPED
        damage = 0
        on_death = "keep"

    def start(self, ctx):
        self.modifier("max_health", -4, "add_value")
        self.keep_potion("glowing")
'''


def _steve(rig, files=None):
    r = rig(files, examples=True)
    r.start()
    r.io.join("Steve")
    r.tick()
    return r


def test_giant_and_shrunk_then_shrunk_ending_early_leaves_giant_exact(rig):
    r = _steve(rig)
    r.op("apply", who="Steve", mark="giant")
    r.op("apply", who="Steve", mark="shrunk")
    r.tick()
    assert math.isclose(r.io.attribute_value("Steve", "minecraft:scale"), 1.5)
    assert r.op("clear", who="Steve", mark="shrunk")["ok"]
    r.tick()
    assert r.io.attribute_value("Steve", "minecraft:scale") == 2.0
    assert GIANT in r.mods("Steve") and SHRUNK not in r.mods("Steve")


def test_a_mark_ends_at_its_duration(rig):
    r = _steve(rig)
    r.op("apply", who="Steve", mark="shrunk", seconds=2)
    r.run_for(1.5)
    assert SHRUNK in r.mods("Steve")
    r.run_for(1.0)
    assert SHRUNK not in r.mods("Steve") and not r.records("mark")
    assert any("expired" in m for m in r.lines())


def test_the_same_mark_again_refreshes_or_stacks_as_the_mark_says(rig):
    r = _steve(rig, {"weights": STACKED})
    r.op("apply", who="Steve", mark="giant", seconds=5)
    r.run_for(3)
    r.op("apply", who="Steve", mark="giant", seconds=5)          # refresh: one instance, a new end
    (giant,) = r.records("mark", "giant")
    assert math.isclose(giant.ends, r.clock.t + 5 - 0.1, abs_tol=0.11)
    r.op("apply", who="Steve", mark="weight")
    r.op("apply", who="Steve", mark="weight")
    r.tick()
    ids = sorted(k[1] for k in r.mods("Steve") if k[0] == "minecraft:movement_speed")
    assert ids == ["everhear:mark/weight/1", "everhear:mark/weight/2"]
    assert math.isclose(r.io.attribute_value("Steve", "minecraft:movement_speed"), 0.81)


def test_death_ends_a_mark_unless_it_says_it_survives_and_respawn_puts_it_right(rig):
    r = _steve(rig, {"weights": STACKED})
    r.io.death_drops_modifiers = True                  # whether Minecraft drops them is not measured
    r.op("apply", who="Steve", mark="giant")
    r.op("apply", who="Steve", mark="curse")
    r.tick()
    assert GIANT in r.mods("Steve") and ("minecraft:max_health", "everhear:mark/curse") in r.mods("Steve")
    r.io.kill("Steve")
    r.tick()
    assert not r.records("mark", "giant") and r.records("mark", "curse")
    r.io.respawn("Steve")
    r.tick(2)
    assert GIANT not in r.mods("Steve")
    assert r.mods("Steve")[("minecraft:max_health", "everhear:mark/curse")] == (-4.0, "add_value")


def test_respawn_when_minecraft_kept_them_removes_the_ones_that_end(rig):
    r = _steve(rig, {"weights": STACKED})
    r.io.death_drops_modifiers = False
    r.op("apply", who="Steve", mark="giant")
    r.tick()
    r.io.kill("Steve")
    r.tick()
    r.io.respawn("Steve")
    r.tick(2)
    assert GIANT not in r.mods("Steve")


def test_milk_cures_only_the_marks_that_say_so(rig):
    r = _steve(rig)
    r.op("apply", who="Steve", mark="giant")
    r.op("apply", who="Steve", mark="shrunk")
    r.tick()
    r.io.drink_milk("Steve")
    r.tick(2)
    assert GIANT in r.mods("Steve") and SHRUNK not in r.mods("Steve")


def test_offline_the_timers_pause_and_relog_keeps_the_mark(rig):
    r = _steve(rig)
    r.op("apply", who="Steve", mark="shrunk", seconds=3)
    r.run_for(1)
    r.io.leave("Steve")
    r.run_for(10)
    assert r.records("mark", "shrunk")                        # paused, not expired
    r.io.join("Steve")
    r.run_for(1)
    assert r.records("mark", "shrunk") and SHRUNK in r.mods("Steve")
    r.run_for(1.5)
    assert not r.records("mark", "shrunk")


def test_a_mark_that_needs_a_potion_keeps_it_up_like_a_beacon(rig):
    r = _steve(rig, {"weights": STACKED})
    r.op("apply", who="Steve", mark="curse")
    r.run_for(5)
    gives = [op for op in r.io.ops_of(GiveEffect) if op.effect == "minecraft:glowing"]
    assert 2 <= len(gives) <= 4 and all(op.seconds == 3.0 for op in gives)
    r.op("clear", who="Steve", mark="curse")
    n = len(r.io.ops_of(GiveEffect))
    r.run_for(5)
    assert len(r.io.ops_of(GiveEffect)) == n                   # it stops; the potion runs out by itself


def test_potions_are_given_and_forgotten(rig):
    from vfg_engine.io import Command
    r = _steve(rig)
    r.io.players["Steve"].effects.clear()
    # potions stack in the fake as measured on 26.3
    from vfg_engine.io import GiveEffect as G
    res = r.io.flush([G("Steve", "minecraft:poison", 60, 0), G("Steve", "minecraft:poison", 5, 2),
                      G("Steve", "minecraft:poison", 2, 0)])
    assert [x.ok for x in res] == [True, True, False] and res[2].error == "has something stronger"
    top = r.io.players["Steve"].effects["minecraft:poison"]
    assert (top.amplifier, top.ticks, top.hidden.amplifier, top.hidden.ticks) == (2, 100, 0, 1200)
    r.io.advance(6.5)
    back = r.io.players["Steve"].effects["minecraft:poison"]
    assert (back.amplifier, back.ticks) == (0, 1070)
    assert not [op for op in r.io.sent if isinstance(op, Command) and "effect clear" in op.text]


def test_a_mark_applied_for_ever_lasts_and_still_saves(rig):
    import json
    r = _steve(rig)
    r.op("apply", who="Steve", mark="giant", seconds=float("inf"))
    r.run_for(40)
    (rec,) = r.records("mark", "giant")
    assert rec.ends is None
    r.engine.save(force=True)
    doc = json.loads((r.tmp / "state.json").read_text())
    assert any(d["cid"] == "giant" for d in doc["records"])


def test_a_refresh_while_the_bearer_is_offline_counts_from_when_they_left(rig):
    r = _steve(rig)
    r.op("apply", who="Steve", mark="shrunk", seconds=3)
    r.run_for(1)
    r.io.leave("Steve")
    r.run_for(10)
    r.op("apply", who="Steve", mark="shrunk", seconds=3)     # refreshed while away: 3 s left
    r.run_for(10)
    r.io.join("Steve")
    r.run_for(2.5)
    assert r.records("mark", "shrunk")
    r.run_for(1.0)
    assert not r.records("mark", "shrunk")                     # 3 s after coming back, not 13
