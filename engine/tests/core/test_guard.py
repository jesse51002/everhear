"""The call limits and error isolation (the game engine spec, "One loop, in order", its rules, and
"When something goes wrong"): over 5 ms is logged, over 50 ms is cut by a timer signal, an error
ends only that instance with its cleanup, and the loop goes on."""

import re
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


def test_a_call_that_waits_long_on_minecraft_every_tick_is_logged_and_flagged_slow(rig):
    """out.ms leaves the waiting out, so a call that waits ~80 ms on Minecraft every tick (doing
    nothing itself) was never logged slow nor flagged; over a tick's 50 ms of waiting, it is."""
    waiter = FINE.replace("class Fine", "class Waiter").replace(
        "self.state.n += 1", "self.state.n += 1\n        ctx.query('data get entity @s Pos')")
    r = rig({"waiter": waiter})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="waiter")
    r.io.query_delay_s = 0.08
    r.tick(4)
    slow = [m for m in r.lines() if m.startswith("slow call") and "waiter" in m]
    assert slow and all(re.search(r"waited \d+ ms on Minecraft \(its own time", m) for m in slow)
    (rec,) = r.records(cid="waiter")
    status = r.op("status")
    flagged = {s["instance"]: s["why"] for s in status["slow"]}
    assert rec.iid in flagged and "waited" in flagged[rec.iid] and "on Minecraft" in flagged[rec.iid]
    assert any(m.startswith("flagged: %s" % rec.iid) for m in r.lines())


def test_a_short_wait_on_minecraft_is_not_slow(rig):
    waiter = FINE.replace("class Fine", "class Quick").replace(
        "self.state.n += 1", "self.state.n += 1\n        ctx.query('data get entity @s Pos')")
    r = rig({"quick": waiter})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="quick")
    r.io.query_delay_s = 0.01
    r.tick(4)
    assert not [m for m in r.lines() if m.startswith("slow call") and "quick" in m and "waited" in m]
    assert not [x for x in r.op("status")["slow"] if "waited" in x["why"]]


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


GREEDY = FINE.replace("class Fine", "class Greedy").replace(
    "        self.state.n += 1",
    "        self.state.n += 1\n"
    "        for i in range(6):\n"
    "            try:\n"
    "                ctx.query('data get entity @s Pos')\n"
    "            except RuntimeError as exc:\n"
    "                self.command('say %d: %s' % (i, exc))")


def _greedy_tick(rig, delay_s):
    r = rig({"greedy": GREEDY}, cut_ms=50)
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="greedy")
    r.io.query_delay_s = delay_s
    del r.io.deadlines[:]
    start = time.perf_counter()
    r.tick()
    took = time.perf_counter() - start
    said = [op.text for op in r.io.ops_of(Command) if op.text.startswith("say ")]
    return r, said, took


def test_a_call_that_waits_too_long_on_minecraft_is_refused_more_reads_in_words(rig):
    """Waiting has its own budget per call (VFG_ENGINE_IO_MS, here 250 ms): each read is given what
    is left of it, the one that runs past that is given up in words, and those after it are refused
    in words, so no call can stall the tick."""
    r, said, took = _greedy_tick(rig, 0.1)
    # reads 0 and 1 take 100 ms each; read 2 has the 50 ms left, and is given up at that
    assert [line.split(":")[0] for line in said] == ["say 2", "say 3", "say 4", "say 5"]
    assert "given up" in said[0] and re.search(r"within the [3-5]\d ms left", said[0]) and "(250 ms in all)" in said[0]
    assert all("refused" in line and "budget (250 ms)" in line for line in said[1:])
    assert len(r.io.queries) == 3
    first, second, third = r.io.deadlines
    assert first == 0.25 and abs(second - 0.15) < 0.03 and abs(third - 0.05) < 0.03
    assert took < 0.6
    assert r.records(cid="greedy")                               # refused reads; the call itself went on


def test_one_read_on_a_hung_server_waits_no_longer_than_the_budget(rig):
    """Before the deadline a read was bounded only by RCON's own 5 s timeout, with the cut paused:
    one read on a hung server stalled the loop for seconds. Now it is given up at the budget."""
    r, said, took = _greedy_tick(rig, 5.0)
    assert took < 1.0, took
    assert said[0].startswith("say 0:") and "given up" in said[0] and "250 ms left" in said[0]
    assert [line.split(":")[0] for line in said[1:]] == ["say 1", "say 2", "say 3", "say 4", "say 5"]
    assert len(r.io.queries) == 1 and r.io.deadlines == [0.25]
    assert r.records(cid="greedy")


def test_a_hung_world_read_is_given_up_at_the_budget_too(rig):
    prober = FINE.replace("class Fine", "class Prober").replace(
        "        self.state.n += 1",
        "        self.state.n += 1\n"
        "        try:\n"
        "            ctx.world.probe([(0, 64, 0)], 'solid')\n"
        "        except RuntimeError as exc:\n"
        "            self.command('say %s' % exc)")
    r = rig({"prober": prober}, cut_ms=50)
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="prober")
    r.io.read_delay_s = 5.0
    start = time.perf_counter()
    r.tick()
    assert time.perf_counter() - start < 1.0
    said = [op.text for op in r.io.ops_of(Command) if op.text.startswith("say ")]
    assert said and "probe" in said[0] and "given up" in said[0]


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


# ---- the RCON client honours a caller's deadline (mc/rcon.py, B's: item 3 of the review needed it)

def _fake_vanilla():
    import os
    import sys
    mc_tests = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mc")
    if mc_tests not in sys.path:
        sys.path.insert(0, mc_tests)
    import fake_vanilla
    return fake_vanilla


def test_rcon_gives_up_a_command_at_its_callers_deadline_and_reconnects(tmp_path, monkeypatch):
    import threading
    from vfg_engine.mc.rcon import Rcon, RconDeadline, RconError
    fv = _fake_vanilla()
    monkeypatch.setenv("VFG_RCON_LOCK", str(tmp_path / "rcon.lock"))
    release = threading.Event()

    def handler(cmd):
        if cmd == "hang":
            release.wait(5)
        return "echo " + cmd

    srv = fv.FakeVanilla(handler=handler)
    try:
        r = Rcon(fv.properties(tmp_path, srv))
        assert r.command("before") == "echo before"
        start = time.monotonic()
        with r.deadline(0.15):
            try:
                r.command("hang")
                raise AssertionError("no deadline")
            except RconDeadline as exc:
                assert isinstance(exc, RconError)
                assert "'hang'" in str(exc) and "deadline" in str(exc)
        assert 0.1 < time.monotonic() - start < 0.6           # not RCON's 5 s timeout
        assert not r.connected() and r.stats.broken == 1       # as after a timeout: dropped
        release.set()
        assert r.command("after") == "echo after"              # reconnected, in step
        assert r.stats.connects == 2
        with r.deadline(1.0):
            assert r.command("inside") == "echo inside"        # time enough: as without one
    finally:
        release.set()
        srv.close()


def test_rcon_waits_for_the_connection_no_longer_than_the_deadline(tmp_path, monkeypatch):
    """Another thread (the mirror) holding the connection is waited for within the deadline too."""
    import threading
    from vfg_engine.mc.rcon import Rcon, RconDeadline
    fv = _fake_vanilla()
    monkeypatch.setenv("VFG_RCON_LOCK", str(tmp_path / "rcon.lock"))
    srv = fv.FakeVanilla(handler=lambda cmd: "echo " + cmd)
    try:
        r = Rcon(fv.properties(tmp_path, srv))
        r.command("hello")
        holding, done = threading.Event(), threading.Event()

        def mirror():
            with r.held():
                holding.set()
                done.wait(5)

        t = threading.Thread(target=mirror, daemon=True)
        t.start()
        holding.wait(2)
        start = time.monotonic()
        try:
            with r.deadline(0.1):
                r.command("blocked")
            raise AssertionError("no deadline")
        except RconDeadline as exc:
            assert "another thread" in str(exc)
        assert time.monotonic() - start < 0.5
        done.set()
        t.join(2)
        assert r.command("free") == "echo free" and r.stats.broken == 0
    finally:
        done.set()
        srv.close()


def test_mcio_hands_rcons_deadline_on_as_the_contracts(tmp_path, monkeypatch):
    """rcon.py stays importable on its own (the toolkit loads it by path), so McIo.deadline turns
    its RconDeadline into vfg_engine.io.IoDeadline, which the guard words as IoBudget."""
    import threading
    from vfg_engine.io import IoDeadline
    from vfg_engine.mc.mcio import McIo
    from vfg_engine.mc.rcon import Rcon
    fv = _fake_vanilla()
    monkeypatch.setenv("VFG_RCON_LOCK", str(tmp_path / "rcon.lock"))
    release = threading.Event()
    srv = fv.FakeVanilla(handler=lambda cmd: release.wait(5) and "" or "late")
    try:
        io = McIo(rcon=Rcon(fv.properties(tmp_path, srv)), mirror=False)
        start = time.monotonic()
        try:
            with io.deadline(0.1):
                io.query("say hi")
            raise AssertionError("no deadline")
        except IoDeadline as exc:
            assert "deadline" in str(exc)
        assert time.monotonic() - start < 0.6
    finally:
        release.set()
        srv.close()
