"""Mountain Trees: spruce trees planted on the grass slopes of the mountains area, below y 130."""
ASKS = ["some pines on the mountain", "put spruce trees on the mountain slopes", "plant trees on the mountains"]
import random
import re
import time
from mc import rcon, pos, log
from kit import values, result, area, loop, world

v = values()
# The same area as mountains with the same values: `away` blocks east of the first player the
# player value names, or at corner (a saved place, x y z or a player, its y not used). In the
# overworld.
if v["corner"]:
    at = world.place(v["corner"])
    if at is None:
        result("no trees: %s is not a saved place nor a player online" % v["corner"], corner=v["corner"], trees=0)
        raise SystemExit(0)
    X0, _, Z0 = at.block()
else:
    near = [c for c in (pos(p) for p in loop.who(v["player"])) if c]
    if not near:
        log("nowhere to plant: %s is not online, and no corner" % v["player"])
        result("no trees: %s is not online" % v["player"], trees=0)
        raise SystemExit(0)
    X0, Z0 = int(near[0][0]) + v["away"], int(near[0][2]) - 50
N = v["n"]
random.seed(v["seed"])
AREA = (X0, Z0, X0 + 208, Z0 + 108)
area.forceload(world.Box(AREA[0], 0, AREA[1], AREA[2], 0, AREA[3]), wait=6)    # released at the end or on a stop
placed = tries = 0
fails = 0
while placed < N and tries < N * 8:
    tries += 1
    x, z = X0 + random.randint(0, 200), Z0 + random.randint(0, 100)
    y = 200
    while y > 60 and "passed" in rcon("execute if block %d %d %d #minecraft:replaceable" % (x, y, z)).lower():
        y -= 1
    if "passed" not in rcon("execute if block %d %d %d grass_block" % (x, y, z)).lower() or y > 130:
        continue
    if tries % 20 == 0:
        m = re.search(r"([\d.]+) ?ms", rcon("tick query"))
        while m and float(m.group(1)) >= 45:
            time.sleep(0.5)
            m = re.search(r"([\d.]+) ?ms", rcon("tick query"))
    r = rcon("place feature minecraft:spruce %d %d %d" % (x, y + 1, z))
    if "Placed" in r:
        placed += 1
    else:
        fails += 1
        # simple spruce: trunk plus stepped leaf cone
        h = random.randint(6, 10)
        y0 = y + 1
        rcon("fill %d %d %d %d %d %d spruce_log" % (x, y0, z, x, y0 + h, z))
        for k in range(h + 2):
            yy = y0 + h + 1 - k
            r_ = 0 if k == 0 else (1 if k < 3 else (2 if k % 2 else 1)) if k < h - 2 else 0
            if k >= 2 and k < h:
                r_ = 1 + (k % 3 == 0) + (k > 4)
            rcon("fill %d %d %d %d %d %d spruce_leaves[persistent=true] keep" % (x - r_, yy, z - r_, x + r_, yy, z + r_))
        rcon("fill %d %d %d %d %d %d spruce_log" % (x, y0, z, x, y0 + h, z))
        placed += 1
log("trees placed", placed, "tries", tries)
area.release()
result("%d spruce trees planted on the slopes over x %d..%d, z %d..%d" % (placed, X0, X0 + 200, Z0, Z0 + 100),
       trees=placed, tries=tries, corner="%d %d" % (X0, Z0))
