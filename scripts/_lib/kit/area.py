"""Changing blocks: a fill of any size, a sphere, and the chunks they touch.

    area.fill((x1, y1, z1, x2, y2, z2), "stone")                # any size
    area.fill(box, "packed_ice", "replace #minecraft:ice")      # modes: replace <filter>, destroy, keep, hollow, outline
    area.sphere(r.end, 8, "air", spare_protected=True)          # a crater, carved from the middle out

A box is (x1, y1, z1, x2, y2, z2): whole blocks, both corners inside, in any order. One fill
is at most 32,768 blocks, and a chunk that is not loaded answers "That position is not
loaded" to a fill that touches it, so a fill is split on chunk edges, only the loaded chunks
are filled (the rest are logged as skipped: "the whole world" means its loaded part), and
what is left is merged back into as few pieces under 32,768 as it makes. A place in another
dimension says so: a Pos that knows it (aim's are), or dim=. Bedrock, barriers, command
blocks and end portals are never a fill's filter.
"""
import math
import re
import threading
import time

import mc
from . import world
from .world import MAX_FILL

UNTOUCHABLE = ("bedrock", "barrier", "command_block", "chain_command_block", "repeating_command_block",
               "structure_block", "jigsaw", "end_portal_frame", "end_portal", "end_gateway")

_lock = threading.Lock()


def norm(box):
    """A box as ints with its low corner first."""
    b = [int(math.floor(v)) for v in box]
    return (min(b[0], b[3]), min(b[1], b[4]), min(b[2], b[5]), max(b[0], b[3]), max(b[1], b[4]), max(b[2], b[5]))


def volume(box):
    """How many blocks a box holds."""
    x1, y1, z1, x2, y2, z2 = norm(box)
    return (x2 - x1 + 1) * (y2 - y1 + 1) * (z2 - z1 + 1)


def box(at, r, ry=None):
    """The box r blocks round a place in x and z, and ry (default r) in y."""
    x, y, z = (int(math.floor(v)) for v in at[:3])
    ry = r if ry is None else ry
    return (x - r, y - ry, z - r, x + r, y + ry, z + r)


def chunk_of(x, z):
    """The chunk (cx, cz) holding block column x, z."""
    return int(math.floor(x)) >> 4, int(math.floor(z)) >> 4


def chunk_box(cx, cz, y1, y2):
    """The box of one chunk between two heights."""
    return (cx * 16, y1, cz * 16, cx * 16 + 15, y2, cz * 16 + 15)


def chunks(b):
    """Every chunk (cx, cz) a box touches."""
    x1, _, z1, x2, _, z2 = norm(b)
    return [(cx, cz) for cx in range(x1 >> 4, (x2 >> 4) + 1) for cz in range(z1 >> 4, (z2 >> 4) + 1)]


def band(y, below, above, dim=None):
    """The heights from `below` under y to `above` over it, inside the world: (y1, y2)."""
    lo, hi = world.limits(dim)
    y = int(math.floor(y))
    return max(lo, y - below), min(hi, y + above)


def loaded_chunks(cs, dim=None):
    """The set of chunks, of a list of (cx, cz), that are loaded, in few commands."""
    cs = list(cs)
    ok = world.passes(["if loaded %d 64 %d" % (cx * 16, cz * 16) for cx, cz in cs], dim)
    return {c for c, good in zip(cs, ok) if good}


def minus(b, others):
    """A box with other boxes cut out of it, as a list of boxes."""
    out = [norm(b)]
    for p in others:
        p = norm(p)
        nxt = []
        for x1, y1, z1, x2, y2, z2 in out:
            if x1 > p[3] or x2 < p[0] or y1 > p[4] or y2 < p[1] or z1 > p[5] or z2 < p[2]:
                nxt.append((x1, y1, z1, x2, y2, z2))
                continue
            if x1 < p[0]:
                nxt.append((x1, y1, z1, p[0] - 1, y2, z2))
                x1 = p[0]
            if x2 > p[3]:
                nxt.append((p[3] + 1, y1, z1, x2, y2, z2))
                x2 = p[3]
            if y1 < p[1]:
                nxt.append((x1, y1, z1, x2, p[1] - 1, z2))
                y1 = p[1]
            if y2 > p[4]:
                nxt.append((x1, p[4] + 1, z1, x2, y2, z2))
                y2 = p[4]
            if z1 < p[2]:
                nxt.append((x1, y1, z1, x2, y2, p[2] - 1))
            if z2 > p[5]:
                nxt.append((x1, y1, p[5] + 1, x2, y2, z2))
        out = nxt
    return out


def split(b, most=MAX_FILL):
    """A box cut in halves along its longest side until every piece holds at most `most`."""
    b = norm(b)
    if volume(b) <= most:
        return [b]
    x1, y1, z1, x2, y2, z2 = b
    sides = [x2 - x1, y2 - y1, z2 - z1]
    a = sides.index(max(sides))
    lo, hi = b[a], b[a + 3]
    mid = (lo + hi) // 2
    first, second = list(b), list(b)
    first[a + 3], second[a] = mid, mid + 1
    return split(tuple(first), most) + split(tuple(second), most)


def _rects(cs):
    """Chunks merged into rectangles of chunks: [(cx1, cz1, cx2, cz2)]."""
    cols = {}
    for cx, cz in sorted(set(cs)):
        col = cols.setdefault(cx, [])
        if col and col[-1][1] == cz - 1:
            col[-1][1] = cz
        else:
            col.append([cz, cz])
    rects, open_ = [], {}           # open_: (cz1, cz2) -> [cx1, cx2]
    for cx in sorted(cols):
        nxt = {}
        for cz1, cz2 in cols[cx]:
            span = open_.pop((cz1, cz2), None)
            nxt[(cz1, cz2)] = [span[0], cx] if span and span[1] == cx - 1 else [cx, cx]
        rects += [(v[0], k[0], v[1], k[1]) for k, v in open_.items()]
        open_ = nxt
    return rects + [(v[0], k[0], v[1], k[1]) for k, v in open_.items()]


def plan(b, dim=None, loaded=None):
    """How a box is filled: (pieces under 32,768 blocks, in loaded chunks, merged; how many
    chunks were skipped as not loaded). `loaded` is a dict kept between calls."""
    b = norm(b)
    cs = chunks(b)
    loaded = {} if loaded is None else loaded
    todo = [c for c in cs if c not in loaded]
    if todo:
        good = loaded_chunks(todo, dim)
        for c in todo:
            loaded[c] = c in good
    ok = [c for c in cs if loaded[c]]
    pieces = []
    for cx1, cz1, cx2, cz2 in _rects(ok):
        part = (max(b[0], cx1 * 16), b[1], max(b[2], cz1 * 16), min(b[3], cx2 * 16 + 15), b[4], min(b[5], cz2 * 16 + 15))
        pieces += split(part)
    return pieces, len(cs) - len(ok)


def _faces(b):
    x1, y1, z1, x2, y2, z2 = norm(b)
    out = [(x1, y1, z1, x2, y1, z2)]
    if y2 > y1:
        out.append((x1, y2, z1, x2, y2, z2))
    if y2 - y1 >= 2:
        ya, yb = y1 + 1, y2 - 1
        out += [(x1, ya, z1, x2, yb, z1)]
        if z2 > z1:
            out.append((x1, ya, z2, x2, yb, z2))
        if z2 - z1 >= 2:
            out.append((x1, ya, z1 + 1, x1, yb, z2 - 1))
            if x2 > x1:
                out.append((x2, ya, z1 + 1, x2, yb, z2 - 1))
    return out


def _filter_ok(mode):
    m = re.match(r"replace\s+(\S+)", mode or "")
    if not m:
        return True
    f = m.group(1).split("[")[0].split("{")[0].replace("minecraft:", "").lstrip("#")
    return f not in UNTOUCHABLE


_FILLED = re.compile(r"(?:filled|changed) (\d+)", re.I)


def fill(b, block, mode="", dim=None, spare_protected=False, keep_bedrock=False, most_mspt=45.0, loaded=None):
    """Fill a box with a block, of any size, where it is loaded; returns how many blocks
    changed. mode: "" (replace everything), "replace <block or #tag>", "destroy", "keep",
    "hollow" (a shell of block, air inside) or "outline" (a shell, inside untouched).
    spare_protected leaves the protected boxes out; keep_bedrock stays clear of the bedrock
    floor (and the Nether's roof). Waits while the server is slower than most_mspt."""
    dim = dim or world.dim(b)
    mode = (mode or "").strip()
    if not _filter_ok(mode):
        mc.log("refused: a fill never replaces", mode.split()[1])
        return 0
    b = norm(b)
    lo, hi = world.NO_BEDROCK.get(dim or world.OVERWORLD) if keep_bedrock else world.limits(dim)
    b = (b[0], max(b[1], lo), b[2], b[3], min(b[4], hi), b[5])
    if b[1] > b[4]:
        return 0
    if mode in ("hollow", "outline"):
        n = 0
        if mode == "hollow" and b[3] - b[0] >= 2 and b[4] - b[1] >= 2 and b[5] - b[2] >= 2:
            n += fill((b[0] + 1, b[1] + 1, b[2] + 1, b[3] - 1, b[4] - 1, b[5] - 1), "air", "", dim,
                      spare_protected, keep_bedrock, most_mspt, loaded)
        for face in _faces(b):
            n += fill(face, block, "", dim, spare_protected, keep_bedrock, most_mspt, loaded)
        return n
    boxes = minus(b, world.protected(dim)) if spare_protected else [b]
    loaded = {} if loaded is None else loaded
    n = skipped = errors = 0
    tail = (" " + mode) if mode else ""
    for part in boxes:
        pieces, skip = plan(part, dim, loaded)
        skipped += skip
        for x1, y1, z1, x2, y2, z2 in pieces:
            if mc.stopping():
                return n
            if most_mspt:
                world.throttle(most_mspt)
            reply = mc.rcon(world.run(dim, "fill %d %d %d %d %d %d %s%s" % (x1, y1, z1, x2, y2, z2, block, tail)))
            m = _FILLED.search(reply)
            if m:
                n += int(m.group(1))
            elif "no blocks" not in reply.lower():
                errors += 1
                if errors <= 3:
                    mc.log("fill %d %d %d %d %d %d %s%s: %s" % (x1, y1, z1, x2, y2, z2, block, tail,
                                                               reply[:160] or "(no reply)"))
    if skipped:
        mc.log("fill: %d chunk(s) not loaded, skipped" % skipped)
    return n


def sphere_boxes(at, r, slack=None):
    """A ball of radius r round a place as boxes: slabs `slack` wide (default r/12, at least
    1), banded in height so each band's width varies by at most `slack`, the middle first.
    It covers the ball and a little more at its rim (5% at radius 8, 2% at 300)."""
    bx, by, bz = (int(math.floor(v)) for v in at[:3])
    r = int(r)
    slack = max(1, r // 12) if slack is None else max(1, int(slack))
    big = (r + 0.5) ** 2
    out = []
    for dxa in range(-r, r + 1, slack):
        dxb = min(r, dxa + slack - 1)
        mid = (dxa + dxb) / 2.0
        r2 = big - mid * mid
        if r2 < 0:
            continue
        rho = int(math.sqrt(r2))

        def half(d):
            return int(math.sqrt(max(r2 - d * d, 0)))
        d0 = 0
        while d0 <= rho:
            d1 = d0
            while d1 + 1 <= rho and half(d0) - half(d1 + 1) <= slack:
                d1 += 1
            h = half((d0 + d1) / 2.0)
            for a, b in ([(-d1, d1)] if d0 == 0 else [(d0, d1), (-d1, -d0)]):
                out.append((bx + dxa, by + a, bz - h, bx + dxb, by + b, bz + h))
            d0 = d1 + 1

    def far(b):
        return sum(max(lo - c, c - hi, 0) ** 2 for lo, hi, c in ((b[0], b[3], bx), (b[1], b[4], by), (b[2], b[5], bz)))
    out.sort(key=far)
    return out


def sphere(at, r, block="air", spare_protected=True, keep_bedrock=True, replace=None, dim=None, most_mspt=45.0):
    """Carve a ball of radius r round a place: every block in it becomes `block` (only the
    blocks matching `replace`, when given), the middle first, where loaded, leaving the
    protected boxes and the bedrock floor alone unless told otherwise. Returns blocks changed."""
    dim = dim or world.dim(at)
    loaded, n = {}, 0
    mode = "replace " + replace if replace else ""
    for b in sphere_boxes(at, r):
        if mc.stopping():
            break
        n += fill(b, block, mode, dim, spare_protected, keep_bedrock, most_mspt, loaded)
    return n


# ---- forceload, owned and released ------------------------------------------------------

_forced = set()     # (dim, cx, cz) this script added


def _release_all():
    release()


def forceload(b, dim=None, wait=5.0):
    """Keep the chunks under a box loaded until release() or the script stops. Chunks that
    were forceloaded already are left alone, and never released by this script. Waits up to
    `wait` s for them to load. Returns how many chunks it added."""
    dim = dim or world.dim(b)
    cs = chunks(b)
    reply = mc.rcon(world.run(dim, "forceload query"))
    if len(reply.encode("utf-8")) < world.MAX_REPLY - 100 and "force" in reply.lower():
        already = {(int(x), int(z)) for x, z in re.findall(r"\[(-?\d+), (-?\d+)\]", reply)}
    else:
        already = set()
        for cx, cz in cs:
            if "is marked" in mc.rcon(world.run(dim, "forceload query %d %d" % (cx * 16, cz * 16))).lower():
                already.add((cx, cz))
    with _lock:
        new = [c for c in cs if c not in already and (dim, c[0], c[1]) not in _forced]
        if new and not _forced:
            mc.on_stop(_release_all)
        _forced.update((dim, cx, cz) for cx, cz in new)
    for cx1, cz1, cx2, cz2 in _rects(new):
        for r in _cap(cx1, cz1, cx2, cz2):
            mc.rcon(world.run(dim, "forceload add %d %d %d %d" % (r[0] * 16, r[1] * 16, r[2] * 16 + 15, r[3] * 16 + 15)))
    if new and wait:
        end = time.time() + wait
        while time.time() < end and not mc.stopping():
            if len(loaded_chunks(new, dim)) == len(new):
                break
            time.sleep(0.5)
    if new:
        mc.log("forceloaded %d chunk(s)" % len(new))
    return len(new)


def _cap(cx1, cz1, cx2, cz2):
    """A rectangle of chunks cut into pieces of at most 256 chunks."""
    out, width = [], cz2 - cz1 + 1
    step = max(1, world.MAX_FORCELOAD // width)
    for x in range(cx1, cx2 + 1, step):
        x2 = min(cx2, x + step - 1)
        if width > world.MAX_FORCELOAD:
            for z in range(cz1, cz2 + 1, world.MAX_FORCELOAD):
                out.append((x, z, x, min(cz2, z + world.MAX_FORCELOAD - 1)))
        else:
            out.append((x, cz1, x2, cz2))
    return out


def release(dim=None):
    """Stop force loading every chunk this script forceloaded (in one dimension, when given)."""
    with _lock:
        mine = [c for c in _forced if dim is None or c[0] == dim]
        _forced.difference_update(mine)
    by_dim = {}
    for d, cx, cz in mine:
        by_dim.setdefault(d, []).append((cx, cz))
    for d, cs in by_dim.items():
        for cx1, cz1, cx2, cz2 in _rects(cs):
            for r in _cap(cx1, cz1, cx2, cz2):
                mc.rcon(world.run(d, "forceload remove %d %d %d %d" % (r[0] * 16, r[1] * 16, r[2] * 16 + 15, r[3] * 16 + 15)))
    if mine:
        mc.log("released %d forceloaded chunk(s)" % len(mine))
