"""The call limits and error isolation (the game engine spec, "One loop, in order", its rules, and
"When something goes wrong"): over 5 ms is logged, over 50 ms is cut by a timer signal, an error
ends only that instance with its cleanup, and the loop goes on."""

import time

from vfg_engine.guard import Guard
from vfg_engine.io import Command

STUCK = '''
import time

class Stuck(Ability):
    every = 0.1

    class Config(AbilityConfig):
        summary = "Gets stuck on its third tick."
        damage = 0
        duration_s = UNTIL_STOPPED
        charge_s = 0
        cooldown_s = 0
        scale_per_point = 0

    class State(State):
        n: int = 0

    def start(self, ctx):
        self.modifier("movement_speed", 0.1, "add_value")
        self.listen(Hit, "on_hit")

    def tick(self, ctx):
        self.state.n += 1
        if self.state.n == 3:
            t = time.perf_counter()
            while time.perf_counter() - t < 30:
                try:
                    pass
                except Exception:          # cannot keep a cut call alive
                    pass

    def on_hit(self, ctx, ev):
        pass

    def stop(self, ctx, reason):
        self.command("say stuck stopped: " + reason)
'''

FINE = '''
class Fine(Ability):
    every = 0.1

    class Config(AbilityConfig):
        summary = "Keeps ticking."
        damage = 0
        duration_s = UNTIL_STOPPED
        charge_s = 0
        cooldown_s = 0
        scale_per_point = 0

    class State(State):
        n: int = 0

    def tick(self, ctx):
        self.state.n += 1
'''

BROKEN = FINE.replace("class Fine", "class Broken").replace("self.state.n += 1", "self.state.n += 1\n        if self.state.n == 2:\n            raise ValueError('bad tick')")


def test_the_guard_cuts_a_sleeping_call_at_the_limit():
    g = Guard(slow_ms=5, cut_ms=50)
    try:
        t = time.perf_counter()
        out = g.call("sleeper", time.sleep, 2)
        took = time.perf_counter() - t
    finally:
        g.uninstall()
    assert not out.ok and out.cut
    assert 0.045 < took < 1.5                 # cut at 50 ms, not the 2 s (headroom for a busy box)
    assert "50 ms" in out.error


def test_the_guard_lets_a_quick_call_through_and_times_it():
    g = Guard(slow_ms=5, cut_ms=50)
    try:
        out = g.call("quick", lambda: 7)
    finally:
        g.uninstall()
    assert out.ok and out.value == 7 and out.ms < 50


def test_a_stuck_call_is_cut_and_only_that_instance_ends_with_its_cleanup(rig):
    r = rig({"stuck": STUCK, "fine": FINE}, cut_ms=50)
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="stuck")
    r.op("grant", who="Steve", ability="fine")
    r.tick()
    assert ("minecraft:movement_speed", "everhear:ability/stuck") in r.mods("Steve")
    t = time.perf_counter()
    r.run_for(0.5)
    assert time.perf_counter() - t < 10                     # not the 30 s it wanted (headroom for a busy box)
    assert not r.records(cid="stuck")
    assert r.records(cid="fine")[0].state.n >= 5            # the loop went on
    assert ("minecraft:movement_speed", "everhear:ability/stuck") not in r.mods("Steve")
    stops = [op.text for op in r.io.ops_of(Command) if "stuck stopped" in op.text]
    assert stops and "cut" in stops[0]
    assert any("cut" in m for m in r.lines())


def test_an_error_ends_only_that_instance(rig):
    r = rig({"broken": BROKEN, "fine": FINE})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="broken")
    r.op("grant", who="Steve", ability="fine")
    r.run_for(1)
    assert not r.records(cid="broken")
    assert r.records(cid="fine")[0].state.n >= 8
    assert any("bad tick" in m for m in r.lines())
    assert "broken" not in r.rt.players["Steve"].grants


def test_a_slow_call_is_logged_and_one_that_keeps_going_slow_is_flagged(rig):
    slow = FINE.replace("class Fine", "class Slowpoke").replace(
        "self.state.n += 1", "self.state.n += 1\n        import time as _t\n        _s = _t.perf_counter()\n"
        "        while _t.perf_counter() - _s < 0.008: pass")
    r = rig({"slowpoke": slow})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="slowpoke")
    r.run_for(0.5)
    assert any(m.startswith("slow call") for m in r.lines())
    status = r.op("status")
    assert [s["instance"] for s in status["slow"]] == [r.records(cid="slowpoke")[0].iid]


def test_waiting_on_minecraft_is_not_the_contents_time_and_is_never_cut(rig):
    """A slow server must not cut content doing nothing wrong: a query that takes 120 ms, over the
    50 ms cut, is waited for, and the call goes on."""
    q = FINE.replace("class Fine", "class Asker").replace(
        "self.state.n += 1", "self.state.n += 1\n        ctx.query('data get entity @s Pos')")
    r = rig({"asker": q}, cut_ms=50)
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="asker")
    r.io.query_delay_s = 0.12
    r.tick(3)
    (rec,) = r.records(cid="asker")
    assert rec.state.n >= 2 and len(r.io.queries) >= 2
    assert not any("cut" in m for m in r.lines())


def test_a_call_that_waits_too_long_on_minecraft_is_refused_more_reads_in_words(rig):
    """Waiting has its own budget per call (VFG_ENGINE_IO_MS, here 250 ms): past it, a read is
    refused with an error that says why, so no call can stall the tick."""
    greedy = FINE.replace("class Fine", "class Greedy").replace(
        "        self.state.n += 1",
        "        self.state.n += 1\n"
        "        for i in range(6):\n"
        "            try:\n"
        "                ctx.query('data get entity @s Pos')\n"
        "            except RuntimeError as exc:\n"
        "                self.command('say refused after %d: %s' % (i, exc))\n"
        "                break")
    r = rig({"greedy": greedy}, cut_ms=50)
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="greedy")
    r.io.query_delay_s = 0.1
    r.tick()
    said = [op.text for op in r.io.ops_of(Command) if op.text.startswith("say refused")]
    assert said and said[0].startswith("say refused after 3:") and "budget (250 ms)" in said[0]
    assert r.records(cid="greedy")                               # refused reads; the call itself went on


def test_content_burning_cpu_is_still_cut_beside_its_reads(rig):
    hog = FINE.replace("class Fine", "class Hog").replace(
        "self.state.n += 1", "self.state.n += 1\n        ctx.query('x')\n"
        "        import time as _t\n        _s = _t.perf_counter()\n        while _t.perf_counter() - _s < 30: pass")
    r = rig({"hog": hog}, cut_ms=50)
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="hog")
    r.io.query_delay_s = 0.1
    r.tick(2)
    assert not r.records(cid="hog") and any("cut" in m for m in r.lines())


def test_setting_an_attribute_on_self_is_an_error_that_says_use_state(rig):
    bad = FINE.replace("class Fine", "class Sloppy").replace("self.state.n += 1", "self.count = 1")
    r = rig({"sloppy": bad})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="sloppy")
    r.tick(2)
    assert not r.records(cid="sloppy")
    assert any("self.state" in m for m in r.lines())


def test_a_cut_never_escapes_the_guard_while_it_does_its_own_bookkeeping(monkeypatch):
    """Content that swallows the first cut and then raises: the 10 ms re-cut must not land in the
    guard's own handling of that error (here made slow on purpose)."""
    import vfg_engine.guard as guard_module

    def slow_trace(exc, lines=3):
        time.sleep(0.03)
        return "trace"
    monkeypatch.setattr(guard_module, "_short_trace", slow_trace)

    def sly():
        try:
            time.sleep(1)
        except BaseException:
            pass
        raise ValueError("after the cut")
    g = Guard(slow_ms=5, cut_ms=50)
    try:
        out = g.call("sly", sly)
    finally:
        g.uninstall()
    assert not out.ok and out.cut and "after the cut" in out.error
