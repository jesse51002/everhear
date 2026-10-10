"""Mountains: a natural mountain range 200 by 100 blocks beside a player: snowy peaks, spruce, two caves, two lakes, biomes by height."""
ASKS = ["add a mountain biome", "let's do the mountain biome", "a mountain range with snow caps, pines, caves and a lake or two"]
import math
import random
import re
import time
from mc import rcon, pos, log
from kit import values, result, area, loop, world

v = values()
# Where: the north-west corner (the least x and z) of its 200 x 100 area, `away` blocks east of
# the first player the player value names (the range spans their z), or at corner: a saved
# place, x y z or a player, its y not used. In the overworld.
if v["corner"]:
    at = world.place(v["corner"])
    if at is None:
        result("no mountains: %s is not a saved place nor a player online" % v["corner"], corner=v["corner"])
        raise SystemExit(0)
    if at.dim:
        log("corner %s is in %s: the range is built in the overworld" % (v["corner"], at.dim))
    X0, _, Z0 = at.block()
else:
    near = [c for c in (pos(p) for p in loop.who(v["player"])) if c]
    if not near:
        log("nobody to build it beside: %s is not online, and no corner" % v["player"])
        result("no mountains: %s is not online" % v["player"], players=[])
        raise SystemExit(0)
    X0, Z0 = int(near[0][0]) + v["away"], int(near[0][2]) - 50
W, D = 200, 100
NX, NZ = W // 2, D // 2
random.seed(v["seed"])
cnt = [0]
t_start = time.time()
mspt_max = [0.0]


def mspt():
    m = re.search(r"([\d.]+) ?ms", rcon("tick query"))
    return float(m.group(1)) if m else 0.0


def cmd(c):
    cnt[0] += 1
    if cnt[0] % 60 == 0:
        while True:
            m = mspt()
            mspt_max[0] = max(mspt_max[0], m)
            if m < 45:
                break
            time.sleep(0.5)
    r = rcon(c)
    return r


# forceload own area (only the chunks not already forced; released at the end or on a stop)
log("building at x%d-%d z%d-%d" % (X0, X0 + W, Z0, Z0 + D))
area.forceload(world.Box(X0, 0, Z0 - 16, X0 + W, 0, Z0 + D + 16), wait=8)
cx1, cx2 = X0 >> 4, (X0 + W) >> 4
unl = 0
for cx in range(cx1, cx2 + 1):
    for z in range(Z0, Z0 + D + 1, 16):
        if "passed" not in rcon("execute if loaded %d 64 %d" % (cx * 16 + 8, z)).lower():
            unl += 1
log("unloaded chunk probes:", unl)
if unl:
    time.sleep(10)


def solid_air(x, y, z):
    return "passed" in rcon("execute if block %d %d %d #minecraft:replaceable" % (x, y, z)).lower()


def ground(x, z):
    y = 130
    while y > 40 and solid_air(x, y, z):
        y -= 1
    return y


samples = sorted(ground(X0 + dx, Z0 + dz) for dx, dz in ((0, 0), (W, 0), (0, D), (W, D), (W // 2, D // 2), (W // 4, D // 2), (3 * W // 4, D // 2)))
BASE = max(58, min(samples[len(samples) // 2], 80))
log("ground samples", samples, "base", BASE)

PEAKS = [(X0 + 25, Z0 + 45, 100, 36), (X0 + 70, Z0 + 62, 112, 40), (X0 + 118, Z0 + 48, 104, 38),
         (X0 + 158, Z0 + 66, 92, 34), (X0 + 183, Z0 + 38, 70, 28), (X0 + 95, Z0 + 85, 60, 26),
         (X0 + 140, Z0 + 20, 55, 26), (X0 + 45, Z0 + 15, 50, 24)]
LAKES = [(X0 + 92, Z0 + 22, 11), (X0 + 170, Z0 + 86, 10)]
PH = [random.uniform(0, 6.28) for _ in range(6)]


def height(x, z):
    h = 0.0
    for px, pz, A, r in PEAKS:
        d = math.hypot(x - px, z - pz)
        # rough, ridged radius
        ang = math.atan2(z - pz, x - px)
        rr = r * (1 + 0.25 * math.sin(3 * ang + px) + 0.15 * math.sin(5 * ang + pz))
        v = A * math.exp(-(d / rr) ** 2 * 1.3)
        h = max(h, v) if v > 0 else h
        h += 0.15 * v * 0
    h += 5 * math.sin(x * 0.11 + PH[0]) * math.sin(z * 0.13 + PH[1]) + 3 * math.sin(x * 0.27 + z * 0.19 + PH[2])
    h += 4 * abs(math.sin(x * 0.05 + PH[3]) * math.cos(z * 0.06 + PH[4])) * min(1, h / 40)
    return BASE + h


def lake_of(x, z):
    for lx, lz, R in LAKES:
        d = math.hypot(x - lx, z - lz)
        if d < R + 7:
            return lx, lz, R, d
    return None


def fill(x1, y1, z1, x2, y2, z2, b):
    if y2 < y1:
        return
    cmd("fill %d %d %d %d %d %d %s" % (x1, y1, z1, x2, y2, z2, b))


def pick(ix, iz):
    r = (ix * 73856093 ^ iz * 19349663) % 100
    return "andesite" if r < 35 else ("tuff" if r < 60 else "stone")


hmap = {}
trees = []
peak_max = 0
zone = {}
for ix in range(NX):
    for iz in range(NZ):
        x, z = X0 + 2 * ix, Z0 + 2 * iz
        h = height(x + 1, z + 1)
        lk = lake_of(x + 1, z + 1)
        if lk:
            lx, lz, R, d = lk
            if d <= R:
                hmap[(ix, iz)] = None
                fill(x, BASE - 8, z, x + 1, BASE - 5, z + 1, "stone")
                fill(x, BASE - 4, z, x + 1, BASE - 4, z + 1, "gravel")
                fill(x, BASE - 3, z, x + 1, BASE + 14, z + 1, "air")
                fill(x, BASE - 3, z, x + 1, BASE - 1, z + 1, "water")
                continue
            t = (d - R) / 7.0
            h = (BASE - 4) * (1 - t) + max(h, BASE + 8) * t
        if h < BASE + 2:
            continue
        H = int(h)
        hmap[(ix, iz)] = H
        rel = H - BASE
        peak_max = max(peak_max, H)
        zone[(ix, iz)] = 2 if rel >= 56 else (1 if rel >= 28 else (0 if rel >= 6 else -1))
        fill(x, BASE - 8, z, x + 1, H - 4, z + 1, "stone")
        if rel < 28:
            fill(x, H - 3, z, x + 1, H - 1, z + 1, "dirt")
            fill(x, H, z, x + 1, H, z + 1, "grass_block")
            if random.random() < 0.07 and rel > 4:
                trees.append((x, H + 1, z))
        else:
            m = pick(ix, iz)
            fill(x, H - 3, z, x + 1, H - 1, z + 1, m)
            if rel >= 56:
                fill(x, H - 1, z, x + 1, H, z + 1, "snow_block")
            elif rel >= 42:
                fill(x, H, z, x + 1, H, z + 1, m)
                fill(x, H + 1, z, x + 1, H + 1, z + 1, "snow[layers=%d]" % random.randint(2, 5))
            else:
                if random.random() < 0.5:
                    fill(x, H, z, x + 1, H, z + 1, "grass_block")
                    if random.random() < 0.15:
                        trees.append((x, H + 1, z))
                else:
                    fill(x, H, z, x + 1, H, z + 1, m)
    if ix % 10 == 0:
        log("terrain column %d/%d fills %d" % (ix, NX, cnt[0]))
log("terrain done, peak y", peak_max, "fills", cnt[0])


def carve(sx, sz, tx, tz, y0, length):
    d = math.hypot(tx - sx, tz - sz)
    ux, uz = (tx - sx) / d, (tz - sz) / d
    x, z, y = float(sx), float(sz), float(y0)
    for i in range(length // 3):
        a, b = int(x), int(z)
        fill(a - 2, int(y), b - 2, a + 2, int(y) + 3, b + 2, "air")
        x += ux * 3 + random.uniform(-0.8, 0.8)
        z += uz * 3 + random.uniform(-0.8, 0.8)
        y += 0.6
        if i % 5 == 2:
            fill(int(x) - 3, int(y), int(z) - 3, int(x) + 3, int(y) + 4, int(z) + 3, "air")
    return (int(x), int(y), int(z))


def entrance(px, pz, away):
    """walk from peak outward to where rel is ~14 and return that point"""
    ang = away
    for r in range(10, 90):
        x, z = px + r * math.cos(ang), pz + r * math.sin(ang)
        if height(x, z) - BASE < 16:
            return int(x), int(z)
    return int(px + 60), int(pz)


for pk, ang in ((PEAKS[1], 1.2), (PEAKS[2], 0.6)):
    ex, ez = entrance(pk[0], pk[1], ang)
    y0 = BASE + 13
    end = carve(ex, ez, pk[0], pk[1], y0, 45)
    log("cave from", ex, y0, ez, "to", end)

# spruce trees
placed = 0
random.shuffle(trees)
for x, y, z in trees[:260]:
    r = cmd("place feature minecraft:spruce %d %d %d" % (x, y, z))
    if "Placed" in r:
        placed += 1
log("trees placed", placed, "of", min(len(trees), 260))

# biomes by 4x4 cell runs along z
BIO = {2: "minecraft:jagged_peaks", 1: "minecraft:snowy_slopes", 0: "minecraft:grove"}
nb = 0
for bx in range(X0, X0 + W, 4):
    runs = []
    for bz in range(Z0, Z0 + D, 4):
        zs = [zone.get(((bx - X0) // 2 + i, (bz - Z0) // 2 + j), -1) for i in (0, 1) for j in (0, 1)]
        z_ = max(zs)
        if runs and runs[-1][0] == z_ and (bz - runs[-1][1]) < 52:
            runs[-1][2] = bz + 3
        else:
            runs.append([z_, bz, bz + 3])
    for z_, a, b in runs:
        if z_ < 0:
            continue
        cmd("fillbiome %d %d %d %d %d %d %s" % (bx, BASE - 8, a, bx + 3, BASE + 118, b, BIO[z_]))
        nb += 1
log("fillbiome commands", nb)
area.release()
log("done in %.0fs, fills/commands %d, peak y %d, mspt max %.1f" % (time.time() - t_start, cnt[0], peak_max, mspt_max[0]))
result("mountain range built over x %d..%d, z %d..%d: its highest peak at y %d, %d trees, in %.0f s" % (
    X0, X0 + W, Z0, Z0 + D, peak_max, placed, time.time() - t_start), corner="%d %d" % (X0, Z0),
    size="%d x %d" % (W, D), peak=peak_max, trees=placed, seed=v["seed"])
