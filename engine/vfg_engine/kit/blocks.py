"""Building with blocks (the kit, DESIGN2 §5 "Blocks"): `build_shape` (a box, a cylinder, a dome,
a pyramid, a staircase or a wall of a block or a mix, facing a way), `sign` (a sign with text) and
`chest` (a chest with vanilla and EverHear items in it). All of it goes through the engine's block
queue, `self.blocks`, which splits the work, spreads it over ticks, keeps to loaded chunks and
spares protected boxes and the bedrock floor.

A mix ("70% stone_bricks 30% cobblestone") and replace rules ("snow > air; solid > lava") are read
by the core (vfg_engine.args: mix, rules, pick), the same reader the BlockMix and ReplaceRules
args use; the queue takes the text as it is. Fills and replaces themselves are `self.blocks`'s
(`fill`, `replace`, `sphere`, `setblock`). A forceload is the instance's, let go when it ends.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from ..mc.rcon import MAX_COMMAND
from ._base import floor3, where

KINDS = ("box", "cylinder", "dome", "pyramid", "staircase", "staircase_down", "wall")
FACINGS = ("look", "north", "east", "south", "west")
WAYS = {"south": (0, 1), "west": (-1, 0), "north": (0, -1), "east": (1, 0)}     # forward, as x and z
BACK = {"south": "north", "north": "south", "east": "west", "west": "east"}
MAX_FORCELOAD = 256
CHEST_SLOTS = 27
SETBLOCK_ROOM = 96                  # a setblock command besides its block: "execute in … run setblock x y z … strict"
MOST_ROUND = 64                     # a cylinder, dome or pyramid is at most this many blocks across,
MOST_PARTS = 2000                   # and at most this many boxes (a hollow dome 48 x 24 x 48 is 1685: ~20 ms)
SIGN_LINE = 40                      # characters a sign line keeps


@dataclass(frozen=True)
class Built:
    """What build_shape covered: the box from corner `a` to corner `b` (inclusive), and the way
    its forward faced ("south"...: a sign on its front faces the other way, BACK[facing])."""
    a: tuple[int, int, int]
    b: tuple[int, int, int]
    facing: str
    dim: str

    @property
    def middle(self) -> tuple[float, float, float]:
        return ((self.a[0] + self.b[0]) / 2 + 0.5, float(self.a[1]), (self.a[2] + self.b[2]) / 2 + 0.5)


def _in_dim(dim: str, cmd: str) -> str:
    """A raw command run in a dimension (bare in the overworld, where RCON's own commands run)."""
    return cmd if dim == "minecraft:overworld" else "execute in %s run %s" % (dim, cmd)


def compass(yaw: float) -> str:
    """The compass way a yaw faces: south, west, north or east."""
    return ["south", "west", "north", "east"][int(round(float(yaw) / 90.0)) % 4]


def _facing_of(x, facing: str) -> str:
    if facing != "look":
        return facing
    yaw = getattr(x, "yaw", None)
    return compass(yaw) if isinstance(yaw, (int, float)) else "south"


def build_shape(self, ctx, at, kind: str = "box", width: int = 9, height: int = 5, depth: int = 9,
                blocks: str = "minecraft:stone_bricks", facing: str = "look", offset=(0, 0, 0),
                hollow: bool = False, load: bool = True, dim: str | None = None) -> Built:
    """Build a shape with the middle of its base at `at` (a position, a player, a Spot), moved
    by `offset` [right, up, forward]: `width` across, `depth` forward, `height` up. "cylinder" and
    "dome" are round in width and depth, "pyramid" steps in a block a layer, "staircase" climbs to
    `height` over `depth` ("staircase_down" descends), "wall" is one block thick. `blocks`: a
    block or a mix ("70% stone_bricks 30% cobblestone"); air carves. `hollow`: air inside a
    one-block shell (a staircase: three blocks of headroom). `facing`: "look" (the way a player or
    a Spot faces; south for a bare position) or a compass way. `load`: forceload its chunks for
    the build (up to 256). Returns the Built box and the way it faced."""
    if kind not in KINDS:
        raise ValueError("kind is one of %s, not %r" % (", ".join(KINDS), kind))
    if facing not in FACINGS:
        raise ValueError("facing is one of %s, not %r" % (", ".join(FACINGS), facing))
    base, d = where(ctx, at, dim)
    if isinstance(at, str):
        at = ctx.world.player(at) or at
    way = _facing_of(at, facing)
    (fx, fz) = WAYS[way]
    rx, rz = -fz, fx
    off = (list(offset) + [0, 0, 0])[:3]
    bx, by, bz = floor3(base)
    x0, y0, z0 = bx + off[0] * rx + off[2] * fx, by + off[1], bz + off[0] * rz + off[2] * fz
    boxes = []
    for (u1, y1, w1, u2, y2, w2), solid in shape_parts(kind, int(width), int(height), int(depth), hollow):
        a = (x0 + u1 * rx + w1 * fx, y0 + y1, z0 + u1 * rz + w1 * fz)
        b = (x0 + u2 * rx + w2 * fx, y0 + y2, z0 + u2 * rz + w2 * fz)
        boxes.append(((min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2])),
                      (max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2])), solid))
    lo = tuple(min(bx_[0][i] for bx_ in boxes) for i in range(3))
    hi = tuple(max(bx_[1][i] for bx_ in boxes) for i in range(3))
    q = self.blocks
    if load and ((hi[0] >> 4) - (lo[0] >> 4) + 1) * ((hi[2] >> 4) - (lo[2] >> 4) + 1) <= MAX_FORCELOAD:
        q.forceload(lo, hi, dim=d)
    for a, b, solid in boxes:
        q.fill(a, b, blocks if solid else "minecraft:air", dim=d)
    return Built(lo, hi, way, d)


def shape_parts(kind: str, width: int, height: int, depth: int, hollow: bool = False):
    """The shape as boxes in its own frame, ((u1, y1, w1, u2, y2, w2), solid) with u to the right,
    y up and w forward from the middle of its base; solid False is air (the old shape_fill's)."""
    if kind == "wall":
        depth, hollow = 1, False
    hollow = hollow and min(width, depth, height) >= 3
    if kind in ("box", "wall"):                  # closed form: a handful of boxes at any size
        u1, w1 = -(width // 2), -(depth // 2)
        u2, w2, top = u1 + width - 1, w1 + depth - 1, height - 1
        if not hollow:
            return [((u1, 0, w1, u2, top, w2), True)]
        return [((u1, 0, w1, u2, 0, w2), True), ((u1, top, w1, u2, top, w2), True),
                ((u1, 1, w1, u2, top - 1, w1), True), ((u1, 1, w2, u2, top - 1, w2), True),
                ((u1, 1, w1 + 1, u1, top - 1, w2 - 1), True), ((u2, 1, w1 + 1, u2, top - 1, w2 - 1), True),
                ((u1 + 1, 1, w1 + 1, u2 - 1, top - 1, w2 - 1), False)]
    if kind != "staircase" and kind != "staircase_down" and max(width, depth) > MOST_ROUND:
        raise ValueError("a %s is at most %d blocks across (not %d x %d): it is built of many boxes; "
                         "build a bigger one from boxes" % (kind, MOST_ROUND, width, depth))
    layers: dict[tuple, set] = {}
    for i in range(width):
        for k in range(depth):
            top = _top(kind, i, k, width, height, depth)
            if top is None:
                continue
            runs = [(0, top, True)]
            if hollow and kind.startswith("staircase"):
                runs = [(top, top, True), (top + 1, top + 3, False)]
            elif hollow:
                inner = _top(kind, i - 1, k - 1, width - 2, height - 2, depth - 2)
                air = None if inner is None else min(inner + 1, top - 1)
                if air is not None and air >= 1:
                    runs = [(0, 0, True), (1, air, False), (air + 1, top, True)]
            for run in runs:
                layers.setdefault(run, set()).add((i - width // 2, k - depth // 2))
    parts = [((u1, y1, w1, u2, y2, w2), solid)
             for (y1, y2, solid), cols in sorted(layers.items()) for u1, w1, u2, w2 in _rects(cols)]
    if len(parts) > MOST_PARTS:
        raise ValueError("a %s %d x %d x %d%s is %d boxes, over the %d one call queues: make it smaller"
                         % (kind, width, height, depth, " hollow" if hollow else "", len(parts), MOST_PARTS))
    return parts


def _top(kind, i, k, width, height, depth):
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


def _rects(cols):
    """A set of columns (u, w) as few rectangles (u1, w1, u2, w2): runs forward, then side by side."""
    rows = []
    for u, w in sorted(cols):
        if rows and rows[-1][0] == u and rows[-1][2] == w - 1:
            rows[-1][2] = w
        else:
            rows.append([u, w, w])
    spans: dict[tuple, list] = {}
    for u, w1, w2 in rows:
        s = spans.setdefault((w1, w2), [])
        if s and s[-1][1] == u - 1:
            s[-1][1] = u
        else:
            s.append([u, u])
    return [(u1, w1, u2, w2) for (w1, w2), s in spans.items() for u1, u2 in s]


# ---- a sign and a chest ---------------------------------------------------------------------

def sign(self, ctx, at, lines, facing: str = "south", wood: str = "oak", wall: bool = True,
         dim: str | None = None):
    """A sign at `at` showing up to four `lines` (each cut at 40 characters), its text facing
    `facing` (a reader stands that way from it). `wall`: hung on the block behind it; else a
    standing sign. `wood`: oak, spruce, birch, dark_oak..."""
    if facing not in WAYS:
        raise ValueError("facing is north, east, south or west, not %r" % (facing,))
    pos, d = where(ctx, at, dim)
    texts = [json.dumps(str(t)[:SIGN_LINE], ensure_ascii=False) for t in list(lines)[:4]]
    texts += ['""'] * (4 - len(texts))
    if wall:
        block = "minecraft:%s_wall_sign[facing=%s]" % (wood, facing)
    else:
        block = "minecraft:%s_sign[rotation=%d]" % (wood, {"south": 0, "west": 4, "north": 8, "east": 12}[facing])
    self.blocks.setblock(floor3(pos), "%s{front_text:{messages:[%s]}}" % (block, ",".join(texts)), dim=d)


def chest(self, ctx, at, items, facing: str = "north", dim: str | None = None) -> int:
    """A chest at `at`, facing `facing`, holding `items` in order (27 at most): "diamond 4"
    (a vanilla item and how many, 1 when left out) or "everhear:railgun" (an EverHear item, as
    the engine gives it: its base, its name and look, its id; self.item_stack).

    One command carries at most 1446 bytes (RCON's), and EverHear stacks are long, so the chest is
    queued with as many stacks as fit one command, and each other stack after it as a block-data
    job (`self.blocks.data`, Items append): the queue places them in that order, under its rules,
    and `done(ctx)` comes once the chest is full. A stack too long for any command is refused here,
    in words. Returns how many stacks go in after the chest."""
    if facing not in WAYS:
        raise ValueError("facing is north, east, south or west, not %r" % (facing,))
    pos, d = where(ctx, at, dim)
    items = list(items)
    if len(items) > CHEST_SLOTS:
        raise ValueError("a chest holds %d stacks, not %d" % (CHEST_SLOTS, len(items)))
    stacks = []
    for slot, text in enumerate(items):
        name, _, n = str(text).strip().partition(" ")
        count = int(n) if n.strip() else 1
        if name.startswith("everhear:"):
            stack = self.item_stack(name.split(":", 1)[1], count).strip()
            stacks.append("{Slot:%db,%s" % (slot, stack.lstrip("{")))
        else:
            stacks.append('{Slot:%db,id:"%s",count:%d}' % (slot, name if ":" in name else "minecraft:" + name, count))
    x, y, z = floor3(pos)
    append = _in_dim(d, "data modify block %d %d %d Items append value " % (x, y, z))
    for text, stack in zip(items, stacks):
        size = len((append + stack).encode("utf-8"))
        if size > MAX_COMMAND:
            raise ValueError("%s in a chest is a stack of %d bytes; one command holds %d: give it fewer or "
                             "shorter components" % (text, size - len(append.encode("utf-8")), MAX_COMMAND))
    block = "minecraft:chest[facing=%s]" % facing
    room = MAX_COMMAND - SETBLOCK_ROOM - len(block.encode("utf-8")) - len("{Items:[]}")
    first = []
    while len(first) < len(stacks) and len(",".join(first + [stacks[len(first)]]).encode("utf-8")) <= room:
        first.append(stacks[len(first)])
    q = self.blocks
    q.setblock((x, y, z), "%s{Items:[%s]}" % (block, ",".join(first)) if first else block, dim=d)
    for stack in stacks[len(first):]:
        q.data((x, y, z), "Items", stack, "append", dim=d)
    return len(stacks) - len(first)
