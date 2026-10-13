"""Measures what McIo costs on a real 26.3 server (realmc) with Carpet's Steve and Alex: the numbers
in engine/MINECRAFT.md. Not a test (pytest does not collect it); run it by hand:

    eval "$(integration/realmc/env ge-mc)"
    cd engine && uv run --no-project python tests/mc/bench_mc.py

The server's own time is what a command's round trip costs in a tight loop (vanilla runs RCON
commands on its main thread, between ticks, and answers when they are done); the engine's cadence
(10 Hz) adds the wake-up of an idle server, shown as "at 10 Hz".
"""

import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)
sys.path.insert(0, os.path.join(REPO, "integration", "realmc"))
PROPS = os.environ["VFG_MC_PROPERTIES"]
os.environ.setdefault("REALMC_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(PROPS))))

import players  # noqa: E402

from vfg_engine import io as I  # noqa: E402
from vfg_engine.mc import McIo  # noqa: E402


def ms(xs):
    xs = sorted(xs)
    return "median %.3f ms, p95 %.3f ms" % (statistics.median(xs) * 1000, xs[int(len(xs) * 0.95)] * 1000)


def hot(io, cmd, n=200):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        io.rcon.command(cmd)
        ts.append(time.perf_counter() - t)
    return ts


def main():
    p = players.on(os.environ["REALMC_NAME"])
    for who, at in (("Steve", (0.5, 85.0, 0.5)), ("Alex", (-4.5, 85.0, 0.5))):
        if who not in p.online():
            p.spawn(who, at=at)
    io = McIo(PROPS)
    io.open()
    r = io.rcon.command
    r("kill @e[tag=ge_bench]")
    print("players:", sorted(io.names.online.values()))

    print("\n== the server's time per command (tight loop)")
    print("simple command (time query gametime):", ms(hot(io, "time query gametime")))
    for look, (yaw, pitch) in (("the floor 3 blocks on", (0, 30)), ("open sky (the whole 48 blocks)", (0, -80))):
        for who in ("Steve", "Alex"):
            p.look(who, yaw, pitch)
        time.sleep(0.1)
        print("snapshot function, 2 players looking at %s:" % look, ms(hot(io, "function everhear:io/snap")))
        ray = hot(io, "execute as Steve at @s run function everhear:ray/start")
        print("  one player's raycast alone:", ms(ray))
    reply = r("data get storage everhear:out snap")
    print("snapshot read: %d chars for 2 players;" % len(reply), ms(hot(io, "data get storage everhear:out snap")))
    r("recipe give Steve *")
    print("snapshot function with Steve's recipe book full (every recipe):", ms(hot(io, "function everhear:io/snap")))
    print("  one full read of that player's data:", ms(hot(io, "data modify storage everhear:io tmp set from entity Steve")))
    r("recipe take Steve *")
    print("  and with an empty book:", ms(hot(io, "data modify storage everhear:io tmp set from entity Steve")))

    print("\n== a tick as the engine runs it, at 10 Hz (events, snapshot with 5 tracked, flush of 10 ops)")
    us = ["0000be00-0000-4000-8000-%012x" % k for k in range(5)]
    io.flush([I.Summon("minecraft:husk", "minecraft:overworld", (6.5 + k, 85, 6.5), u, nbt="{NoAI:1b}",
                       tags=("ge_bench",)) for k, u in enumerate(us)])
    ev, sn, fl, tick = [], [], [], []
    ops = [I.Particle("minecraft:flame", "minecraft:overworld", (0.5, 86, 0.5)) for _ in range(5)] + \
          [I.AddModifier("Steve", "minecraft:scale", "everhear:bench", 0.1, "add_value"),
           I.RemoveModifier("Steve", "minecraft:scale", "everhear:bench")] + \
          [I.GiveEffect("Alex", "minecraft:speed", 2)] * 3
    for _ in range(100):
        t0 = time.perf_counter()
        io.events()
        t1 = time.perf_counter()
        snap = io.snapshot(track=us)
        t2 = time.perf_counter()
        io.flush(ops)
        t3 = time.perf_counter()
        ev.append(t1 - t0)
        sn.append(t2 - t1)
        fl.append(t3 - t2)
        tick.append(t3 - t0)
        time.sleep(max(0, 0.1 - (t3 - t0)))
    assert len(snap.tracked) == 5
    print("events() with nothing new:", ms(ev))
    print("snapshot(5 tracked):", ms(sn))
    print("flush(10 ops):", ms(fl), "-> per op %.3f ms" % (statistics.median(fl) * 100))
    print("the whole tick's I/O:", ms(tick))

    print("\n== flush, per op, in a batch of 100 (tight)")
    for label, batch in (
            ("particle", [I.Particle("minecraft:flame", "minecraft:overworld", (0.5, 86, 0.5))] * 100),
            ("modifier add+remove", [x for k in range(50) for x in (
                I.AddModifier("Alex", "minecraft:scale", "everhear:b%d" % k, 0.01, "add_value"),
                I.RemoveModifier("Alex", "minecraft:scale", "everhear:b%d" % k))]),
            ("effect give", [I.GiveEffect("Alex", "minecraft:speed", 1 + k % 3) for k in range(100)]),
            ("message (actionbar)", [I.Message(("Steve",), "x", "actionbar")] * 100)):
        ts = []
        for _ in range(5):
            t = time.perf_counter()
            res = io.flush(batch)
            ts.append(time.perf_counter() - t)
        print("%-22s %.3f ms per op (median of 5 batches); %d of 100 ok"
              % (label, statistics.median(ts) * 10, sum(x.ok for x in res)))

    print("\n== events: a page of 12, and a backlog of 120")
    for n in (12, 120):
        io.events()
        r("scoreboard players set #bench everhear.t 0")
        for _ in range(n):
            r("execute as Steve run function everhear:ev/milk")
        t = time.perf_counter()
        got = io.events()
        print("%d events: %d read in %.3f ms" % (n, len(got), (time.perf_counter() - t) * 1000))
    print("one page read:", len(r("data get storage everhear:out ev")), "chars")

    print("\n== the mirror with 60 husks near the players")
    io.flush([I.Summon("minecraft:husk", "minecraft:overworld", (-10 + (k % 10) * 2, 85, -10 + (k // 10) * 2),
                       "0000bf00-0000-4000-8000-%012x" % k, nbt="{NoAI:1b}", tags=("ge_bench",)) for k in range(60)])
    m = io.mirror()
    passes = m.passes
    time.sleep(2.0)
    print("passes in 2 s: %d; last pass %.3f ms; %d entities; age now %.3f s"
          % (m.passes - passes, m.last_ms, len(m.all()), m.age_s))
    print("mirror function:", ms(hot(io, "function everhear:io/mirror {r:32}", 50)))
    print("mirror read: %d chars;" % len(r("data get storage everhear:mirror")), ms(hot(io, "data get storage everhear:mirror", 50)))
    r("kill @e[tag=ge_bench]")
    print("\nstats:", io.stats())
    io.close()
    p.close()


if __name__ == "__main__":
    main()
