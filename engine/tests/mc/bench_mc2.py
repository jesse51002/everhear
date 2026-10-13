"""Measures part 2 on a real 26.3 server (realmc): each read on demand, each new op, the datapack's
added per-tick work, and explosion sizes (the numbers in engine/MINECRAFT.md). Not a test:

    eval "$(integration/realmc/env ge-mc)"
    cd engine && uv run --no-project python tests/mc/bench_mc2.py
"""

import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ENGINE)
sys.path.insert(0, os.path.join(os.path.dirname(ENGINE), "integration", "realmc"))
PROPS = os.environ["VFG_MC_PROPERTIES"]
os.environ.setdefault("REALMC_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(PROPS))))

import players  # noqa: E402

from vfg_engine import io as I  # noqa: E402
from vfg_engine.mc import McIo  # noqa: E402

OW = "minecraft:overworld"


def timed(fn, n=50):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t)
    xs = sorted(ts)
    return "median %.3f ms, p95 %.3f ms" % (statistics.median(xs) * 1000, xs[int(len(xs) * 0.95)] * 1000)


def main():
    p = players.on(os.environ["REALMC_NAME"])
    for who, at in (("Steve", (0.5, 85.0, 0.5)), ("Alex", (-4.5, 85.0, 0.5))):
        if who not in p.online():
            p.spawn(who, at=at)
    io = McIo(PROPS, mirror=False)
    io.open()
    r = io.rcon.command
    r("kill @e[type=!player]")
    print("== reads on demand (McIo, tight loop: the round trips and the server's time)")
    for n in (1, 10, 100):
        pts = [(x % 10 - 5, 84, x // 10 - 5) for x in range(n)]
        print("probe %3d points:" % n, timed(lambda: io.probe(OW, pts, "solid"), 30))
    print("ground_y:", timed(lambda: io.ground_y(OW, 3, 7)))
    for length, d in ((10, (0, -0.2, 1)), (50, (0, 0.3, 1)), (200, (1, 0.05, 0)), (1000, (1, 0.0, 0.01))):
        look = io.ray(OW, (0.5, 120, 0.5), d, length)
        print("ray %4d blocks (went %.0f, %s):" % (length, look.distance, look.kind),
              timed(lambda: io.ray(OW, (0.5, 120, 0.5), d, length), 20))
    print("loaded:", timed(lambda: io.loaded(OW, 0, 0)))
    print("mspt (cached a second):", timed(io.mspt), "| uncached:", timed(lambda: io.rcon.command("tick query")))
    print("world_id (cached):", timed(io.world_id))
    for k in range(60):
        r("summon husk %.1f 85 %.1f {NoAI:1b,Tags:[\"ge_bench\"]}" % (-12 + (k % 10) * 2.5, -12 + (k // 10) * 2.5))
    print("entities, radius 32 round 60 husks:", timed(lambda: io.entities(OW, (0.5, 85, 0.5), 32), 20),
          "(%d found)" % len(io.entities(OW, (0.5, 85, 0.5), 32)))
    print("entities, 3 types, radius 32:", timed(lambda: io.entities(OW, (0.5, 85, 0.5), 32,
                                                                       ["husk", "zombie", "wolf"]), 20))
    print("entities, radius 5 (2 found):", timed(lambda: io.entities(OW, (-12, 85, -12), 4), 30))

    print("\n== ops (flush of one op, tight)")
    for label, op in (
            ("Fill 27 blocks", I.Fill(OW, (20, 90, 20), (22, 92, 22), "minecraft:stone")),
            ("Fill 27 back to air", I.Fill(OW, (20, 90, 20), (22, 92, 22), "minecraft:air")),
            ("SetBlock", I.SetBlock(OW, (20, 90, 20), "minecraft:glass")),
            ("Motion", I.Motion("0000be00-0000-4000-8000-000000000001", (0, 0.5, 0))),
            ("EntityData", I.EntityData("0000be00-0000-4000-8000-000000000001", "{Glowing:1b}")),
            ("Bossbar value", I.Bossbar("bench", value=5)),
            ("StopSound", I.StopSound(("Steve",))),
            ("Spawnpoint", I.Spawnpoint("Steve", OW, (0, 85, 0))),
            ("Ignite a mob", I.Ignite("0000be00-0000-4000-8000-000000000001", 5)),
            ("Sound to one player", I.Sound("minecraft:block.note_block.pling", OW, (0, 86, 0), to=("Steve",))),
            ("Particle force", I.Particle("minecraft:flame", OW, (0, 86, 0), force=True))):
        if label == "Motion":
            io.flush([I.Summon("minecraft:husk", OW, (30.5, 85, 30.5), "0000be00-0000-4000-8000-000000000001",
                               tags=("ge_bench",))])
        if label == "Bossbar value":
            io.flush([I.Bossbar("bench", "Bench")])
        print("%-22s" % label, timed(lambda: io.flush([op]), 30))
    io.flush([I.Bossbar("bench", remove=True), I.SetBlock(OW, (20, 90, 20), "minecraft:air")])
    big = I.Fill(OW, (-31, 50, -31), (0, 81, 0), "minecraft:stone")         # 32 x 32 x 32 = 32768
    t = time.perf_counter(); res = io.flush([big])[0]; ms = (time.perf_counter() - t) * 1000
    print("Fill 32768 blocks (the most one command takes): %.1f ms, %s" % (ms, res.reply))
    t = time.perf_counter(); io.flush([I.Fill(OW, (-31, 50, -31), (0, 81, 0), "minecraft:stone")]); ms = (time.perf_counter() - t) * 1000
    print("  the same again (nothing changes): %.1f ms" % ms)
    t = time.perf_counter(); io.flush([I.Ignite("Steve", 2)]); ms = (time.perf_counter() - t) * 1000
    print("Ignite a player: %.2f ms" % ms)
    time.sleep(2.5)

    print("\n== the datapack's per-tick work (its tick function, tight)")
    print("tick, nothing to do:", timed(lambda: r("function everhear:tick"), 100))
    io.watch_mined(["minecraft:sponge", "minecraft:oak_log", "minecraft:diamond_ore", "minecraft:stone"])
    print("tick, 4 kinds of block watched:", timed(lambda: r("function everhear:tick"), 100))
    io.watch_mined([])
    steve = io.names.uuid_of("Steve")
    from vfg_engine.mc.commands import uuid_ints
    own = "Owner:[I;%d,%d,%d,%d]" % tuple(uuid_ints(steve))
    for k in range(20):
        r("summon arrow %.1f 130 %.1f {NoGravity:1b,Motion:[0.0d,0.0d,0.0d],%s}" % (k - 10, -30.5, own))
    print("tick, 20 of a player's arrows in flight:", timed(lambda: r("function everhear:tick"), 100))
    r("kill @e[type=arrow]")

    print("\n== a melee hit's reward, the target found near, or (as for a shot) anywhere loaded")
    n = int(r("execute if entity @e").split("Count: ")[-1]) if "Count" in r("execute if entity @e") else 0
    print("entities loaded:", n)
    print("@e scan with `on attacker` (the far search):",
          timed(lambda: r("execute as Steve run execute as @e[tag=!x] if function everhear:ev/by_me run say x"), 50))

    print("\n== explosions: blocks broken in a stone cube (counted by filling them back)")
    cube = (-31, 50, -31), (-11, 70, -11)                  # 21 x 21 x 21 of stone, its middle at -21 60 -21
    fill_back = "fill %d %d %d %d %d %d minecraft:stone replace #minecraft:air" % (cube[0] + cube[1])
    r("fill %d %d %d %d %d %d minecraft:stone" % (cube[0] + cube[1]))
    for label, cmd in (
            ("TNT, explosion_power 4 (vanilla TNT)", 'summon tnt -20.5 60 -20.5 {fuse:0s,explosion_power:4.0f}'),
            ("TNT, explosion_power 8", 'summon tnt -20.5 60 -20.5 {fuse:0s,explosion_power:8.0f}'),
            ("creeper, ExplosionRadius 4", 'summon creeper -20.5 60 -20.5 {Fuse:0s,ignited:1b,ExplosionRadius:4b}'),
            ("creeper, ExplosionRadius 8", 'summon creeper -20.5 60 -20.5 {Fuse:0s,ignited:1b,ExplosionRadius:8b}'),
            ("creeper, charged, ExplosionRadius 4", 'summon creeper -20.5 60 -20.5 {Fuse:0s,ignited:1b,ExplosionRadius:4b,powered:1b}')):
        r("fill -21 60 -21 -20 61 -20 minecraft:air")               # room for it to stand in
        r(fill_back)
        r("fill -21 60 -21 -20 61 -20 minecraft:air")
        print(r(cmd)[:40], end=" | ")
        time.sleep(1.2)
        out = r(fill_back)
        broken = int(out.split("filled ")[1].split(" ")[0]) - 4 if "filled" in out else 0
        r("kill @e[type=item]")
        print("%-38s %d blocks broken" % (label, broken))
    r("fill %d %d %d %d %d %d minecraft:stone" % (cube[0] + cube[1]))
    r("kill @e[tag=ge_bench]")
    io.close()
    p.close()


if __name__ == "__main__":
    main()
