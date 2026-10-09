"""Build a box, cylinder, dome, pyramid, staircase or wall of one block or a mix, at a place, facing a way."""
import math
import random
import re

import mc
from kit import aim, area, world

VALUES = {
    "shape_kind": {"type": "enum", "of": ["box", "cylinder", "dome", "pyramid", "staircase", "staircase_down", "wall"],
                   "default": "box", "live": True},
    "shape_width": {"type": "int", "min": 1, "max": 256, "default": 9, "live": True},
    "shape_height": {"type": "int", "min": 1, "max": 256, "default": 5, "live": True},
    "shape_depth": {"type": "int", "min": 1, "max": 256, "default": 9, "live": True},
    "shape_offset": {"type": "list", "of": "int", "default": [0, 0, 0], "live": True},
    "shape_facing": {"type": "enum", "of": ["look", "north", "east", "south", "west"], "default": "look", "live": True},
    "shape_blocks": {"type": "str", "default": "stone_bricks", "live": True},
    "shape_hollow": {"type": "bool", "default": False, "live": True},
}

_SHAPE_FILL_WAYS = {"south": (0, 1), "west": (-1, 0), "north": (0, -1), "east": (1, 0)}    # forward, as x and z
_SHAPE_FILL_CELL = 2        # a mix picks one block per cube this wide


def shape_fill(at, v, **change):
    """Build the shape with the middle of its base at `at`, moved by shape_offset [right, up,
    forward]. at is a place, a player's name or an aim.Look (whose yaw "look" follows; a place
    alone faces south). shape_width is across, shape_depth forward, shape_height up; a
    cylinder and a dome are round in width and depth, a pyramid steps in a block a layer, a
    staircase climbs to shape_height over shape_depth (staircase_down descends), a wall is one
    block thick. shape_hollow leaves air inside (over a staircase: three blocks of headroom).
    shape_blocks is a block or a mix, "70% stone_bricks 30% cobblestone"; air carves. Chunks
    that are not loaded are forceloaded for the build and let go after. Protected boxes and
    the bedrock floor are never built in. change: values for this call only, without shape_:
    shape_fill(p, v, kind="wall", width=21, offset=[0, 0, 12]). Returns the world.Box it
    covered, or None when `at` cannot be read."""
    def val(key):
        return change[key] if key in change else v["shape_" + key]
    look = aim.look(at) if isinstance(at, str) else at
    if look is None:
        return None
    base = look.feet if isinstance(look, aim.Look) else look
    way = val("facing")
    if way == "look":
        way = ["south", "west", "north", "east"][int(round(look.yaw / 90.0)) % 4] if isinstance(look, aim.Look) \
            else "south"
    (fx, fz), off, dim = _SHAPE_FILL_WAYS[way], (list(val("offset")) + [0, 0, 0])[:3], world.dim(base)
    rx, rz = -fz, fx
    x0 = int(math.floor(base[0])) + off[0] * rx + off[2] * fx
    y0 = int(math.floor(base[1])) + off[1]
    z0 = int(math.floor(base[2])) + off[0] * rz + off[2] * fz
    boxes = [(world.Box(x0 + u1 * rx + w1 * fx, y0 + y1, z0 + u1 * rz + w1 * fz,
                        x0 + u2 * rx + w2 * fx, y0 + y2, z0 + u2 * rz + w2 * fz, dim=dim), solid)
             for (u1, y1, w1, u2, y2, w2), solid in
             _shape_fill_parts(val("kind"), val("width"), val("height"), val("depth"), val("hollow"))]
    whole = world.Box(min(b[0] for b, _ in boxes), min(b[1] for b, _ in boxes), min(b[2] for b, _ in boxes),
                      max(b[3] for b, _ in boxes), max(b[4] for b, _ in boxes), max(b[5] for b, _ in boxes), dim=dim)
    cs = area.chunks(whole)
    added = area.forceload(whole) if len(area.loaded_chunks(cs, dim)) < len(cs) else 0
    mix, loaded = _shape_fill_mix(val("blocks")), {}
    for b, solid in boxes:
        if mc.stopping():
            break
        cells = [b] if not solid or len(mix) == 1 else _shape_fill_cells(b)
        for c in cells:
            block = "air" if not solid else random.choices([m[0] for m in mix], [m[1] for m in mix])[0]
            area.fill(c, block, spare_protected=True, keep_bedrock=True, loaded=loaded)
    if added:
        area.release(dim)
    return whole


def _shape_fill_parts(kind, width, height, depth, hollow):
    """The shape as boxes in its own frame, (u1, y1, w1, u2, y2, w2) with u to the right, y up
    and w forward from the middle of its base, each with True for blocks or False for air."""
    if kind == "wall":
        depth, hollow = 1, False
    hollow = hollow and min(width, depth, height) >= 3
    layers = {}                     # (y1, y2, solid) -> the columns (u, w) that have it
    for i in range(width):
        for k in range(depth):
            top = _shape_fill_top(kind, i, k, width, height, depth)
            if top is None:
                continue
            runs = [(0, top, True)]
            if hollow and kind.startswith("staircase"):
                runs = [(top, top, True), (top + 1, top + 3, False)]
            elif hollow:
                inner = _shape_fill_top(kind, i - 1, k - 1, width - 2, height - 2, depth - 2)
                air = None if inner is None else min(inner + 1, top - 1)     # the inside's top, under the shell
                if air is not None and air >= 1:
                    runs = [(0, 0, True), (1, air, False), (air + 1, top, True)]
            for run in runs:
                layers.setdefault(run, set()).add((i - width // 2, k - depth // 2))
    return [((u1, y1, w1, u2, y2, w2), solid)
            for (y1, y2, solid), cols in layers.items() for u1, w1, u2, w2 in _shape_fill_rects(cols)]


def _shape_fill_top(kind, i, k, width, height, depth):
    """The top of the column i across and k forward from the shape's corner, or None outside it."""
    if not (0 <= i < width and 0 <= k < depth):
        return None
    if kind in ("box", "wall"):
        return height - 1
    if kind == "staircase":
        return min(height - 1, k * height // depth)
    if kind == "staircase_down":
        return min(height - 1, (depth - 1 - k) * height // depth)
    if kind == "pyramid":
        return min(height - 1, i, k, width - 1 - i, depth - 1 - k)
    a, b = (i + 0.5) / (width / 2.0) - 1, (k + 0.5) / (depth / 2.0) - 1
    if a * a + b * b > 1:
        return None
    if kind == "cylinder":
        return height - 1
    return max(0, int(math.ceil(height * math.sqrt(1 - a * a - b * b))) - 1)     # dome


def _shape_fill_rects(cols):
    """A set of columns (u, w) as few rectangles (u1, w1, u2, w2): runs forward, then side by side."""
    rows = []
    for u, w in sorted(cols):
        if rows and rows[-1][0] == u and rows[-1][2] == w - 1:
            rows[-1][2] = w
        else:
            rows.append([u, w, w])
    spans = {}
    for u, w1, w2 in rows:
        s = spans.setdefault((w1, w2), [])
        if s and s[-1][1] == u - 1:
            s[-1][1] = u
        else:
            s.append([u, u])
    return [(u1, w1, u2, w2) for (w1, w2), s in spans.items() for u1, u2 in s]


def _shape_fill_mix(text):
    """[(block, weight)] from "70% stone_bricks, 30% cobblestone"; a block with no % weighs 1."""
    mix, weight = [], None
    for w, block in re.findall(r"(\d+(?:\.\d+)?)%|([^\s,%\[{]+(?:\[[^\]]*\])?(?:\{[^}]*\})?)", text):
        if w:
            weight = float(w)
        else:
            mix.append((block, 1.0 if weight is None else weight))
            weight = None
    return mix or [("stone", 1.0)]


def _shape_fill_cells(b):
    """A box cut into cubes _SHAPE_FILL_CELL wide."""
    n = _SHAPE_FILL_CELL
    return [world.Box(x, y, z, min(x + n - 1, b[3]), min(y + n - 1, b[4]), min(z + n - 1, b[5]), dim=world.dim(b))
            for x in range(b[0], b[3] + 1, n) for y in range(b[1], b[4] + 1, n) for z in range(b[2], b[5] + 1, n)]
