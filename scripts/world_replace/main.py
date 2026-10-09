"""World Replace: replace a block with another, or a mix, round every player at once; the whole world means the part of it that is loaded."""
ASKS = ["remove all the obsidian in the world", "delete all of the obsidian in the world",
        "replace all of the end stone with bedrock", "can you clear all the water",
        "replace all the snow with random blocks", "turn every block of this into that, everywhere",
        "I'm gonna make the whole world ice"]
from kit import values, area, loop, world
import random
import re
import mc

VALUES = {
    "player":         {"type": "player", "default": "@a"},
    "radius":         {"type": "int", "min": 1, "max": 512, "default": 80},
    "height":         {"type": "int", "min": 0, "max": 384, "default": 0},
    "dims":           {"type": "list", "of": ["overworld", "the_nether", "the_end"], "default": ["overworld", "the_nether", "the_end"]},
    "replace_rules":  {"type": "list", "of": "str", "live": True, "default": ["obsidian > air"]},
    "replace_rate":   {"type": "int", "min": 1, "max": 100, "default": 100, "live": True},
    "replace_cell":   {"type": "int", "min": 1, "max": 16, "default": 4, "live": True},
    "replace_radius": {"type": "int", "min": 0, "max": 128, "default": 4, "live": True},
}


# --- preset: replace_blocks (copied from _presets/replace_blocks.py) ---
# "solid" in a rule: every block a player cannot walk through, as five 26.3 tags that hold no
# bedrock, barrier or command block. Glass, glowstone and sea lanterns are not in them.
_REPLACE_BLOCKS_SOLID = ["#minecraft:mineable/pickaxe", "#minecraft:mineable/axe", "#minecraft:mineable/shovel",
                         "#minecraft:mineable/hoe", "#minecraft:wool"]
# A weight ("70%") or a block (with its [states] and {nbt}), between spaces and commas.
_REPLACE_BLOCKS_WORD = re.compile(r"(\d+(?:\.\d+)?)%|([^\s,%\[{]+(?:\[[^\]]*\])?(?:\{[^}]*\})?)")
_replace_blocks_known = {}      # block id or #tag -> True when this server takes it
_replace_blocks_said = set()    # rules refused and said so once


def replace_blocks(where, v):
    """Apply every rule in replace_rules, in order, to `where`: a box (world.Box, as area.box,
    under_feet and enters_chunk give), a place (the box replace_radius round it), or a list of
    them (a ray's path: r.every(3)). A rule is "<from ...> > <to>": from is block ids, #tags or
    solid; to is a block or a mix, "70% netherrack, 30% magma_block", where keep leaves the
    block as it was. One block fills in big pieces; a mix, or replace_rate under 100 (the share
    of cells that change), picks per cube of replace_cell blocks, the same pick at the same
    place every time. Blocks change without updating their neighbours (strict), so water
    turned to lava stays lava, sand stays put and nothing drops. Protected boxes and the
    bedrock floor are left alone, and a player the change left inside a block is lifted out.
    Returns how many blocks changed."""
    boxes = _replace_blocks_boxes(where, v["replace_radius"])
    rules = [r for r in (_replace_blocks_rule(t) for t in _replace_blocks_texts(v["replace_rules"])) if r]
    loaded, n = {}, 0               # per dimension: which chunks are loaded, asked once
    for b in boxes:
        seen = loaded.setdefault(world.dim(b), {})
        for text, froms, mix in rules:
            if len(mix) == 1 and v["replace_rate"] >= 100:
                if mix[0][0] != "keep":
                    n += sum(area.fill(b, mix[0][0], "replace %s strict" % f, spare_protected=True, keep_bedrock=True,
                                       loaded=seen) for f in froms)
                continue
            for cell in _replace_blocks_cells(b, v["replace_cell"]):
                if mc.stopping():
                    return n
                pick = random.Random("%s %d %d %d" % (text, cell[0], cell[1], cell[2]))
                if pick.random() * 100 >= v["replace_rate"]:
                    continue
                block = pick.choices([m[0] for m in mix], [m[1] for m in mix])[0]
                if block != "keep":
                    n += sum(area.fill(cell, block, "replace %s strict" % f, spare_protected=True, keep_bedrock=True,
                                       loaded=seen) for f in froms)
    if boxes and n:
        _replace_blocks_free(boxes)
    return n


def _replace_blocks_boxes(where, r):
    """`where` as a list of boxes: a box, a place (the box r round it), or a list of them."""
    if len(where) in (3, 6) and all(isinstance(x, (int, float)) for x in where):
        where = [where]
    return [area.norm(w) if len(w) == 6 else area.box(w, r) for w in where]


def _replace_blocks_texts(items):
    """The rules as written: a command line splits "stone > 70% netherrack, 30% magma_block" at
    its comma, and a piece with no > belongs to the rule beside it."""
    out = []
    for t in items:
        if out and (">" not in t or ">" not in out[-1]):
            out[-1] += ", " + t
        else:
            out.append(t)
    return out


def _replace_blocks_rule(text):
    """(text, [from ...], [(block, weight) ...]) from "stone, deepslate > 70% netherrack, 30%
    magma_block"; None, logged, when it is not a rule this server takes."""
    left, arrow, right = text.partition(">")
    if not arrow:
        return _replace_blocks_refuse(text, "it is <from ...> > <to mix>")
    froms = []
    for _, f in _REPLACE_BLOCKS_WORD.findall(left):
        froms += _REPLACE_BLOCKS_SOLID if f == "solid" else [f] if f else []
    mix, weight = [], None
    for w, block in _REPLACE_BLOCKS_WORD.findall(right):
        if w:
            weight = float(w)
        else:
            mix.append((block, 1.0 if weight is None else weight))
            weight = None
    bad = [b for b in froms + [b for b, _ in mix if b != "keep"] if not _replace_blocks_takes(b)]
    bad += [b for b, _ in mix if b.startswith("#")]
    if not froms or not mix or bad or not sum(w for _, w in mix):
        return _replace_blocks_refuse(text, ", ".join(bad) or "it is <from ...> > <to mix>")
    return text, froms, mix


def _replace_blocks_refuse(text, why):
    """Say once that a rule was refused, and why; None."""
    if text not in _replace_blocks_said:
        _replace_blocks_said.add(text)
        mc.log("rule refused: %r: %s" % (text, why))


def _replace_blocks_takes(block):
    """True if this server takes a block id or #tag (asked once each: 26.3 dropped some)."""
    if block not in _replace_blocks_known:
        reply = mc.rcon("execute if block 0 0 0 %s" % block)
        _replace_blocks_known[block] = not re.search(r"Unknown|Invalid|Expected|<--\[HERE\]", reply)
        if not _replace_blocks_known[block]:
            mc.log("not a block or tag on this server:", block, "-", reply[:100])
    return _replace_blocks_known[block]


def _replace_blocks_cells(b, size):
    """A box cut into cubes `size` wide, on a grid the whole world shares."""
    x1, y1, z1, x2, y2, z2 = b
    return [world.Box(max(x, x1), max(y, y1), max(z, z1), min(x + size - 1, x2), min(y + size - 1, y2),
                      min(z + size - 1, z2), dim=world.dim(b))
            for x in range(x1 - x1 % size, x2 + 1, size)
            for y in range(y1 - y1 % size, y2 + 1, size)
            for z in range(z1 - z1 % size, z2 + 1, size)]


def _replace_blocks_free(boxes):
    """Lift every player whose feet or head a change left inside a solid block, a block at a time."""
    x1, y1, z1 = (min(b[i] for b in boxes) for i in range(3))
    x2, y2, z2 = (max(b[i] for b in boxes) for i in range(3, 6))
    sel = "@a[x=%d,y=%d,z=%d,dx=%d,dy=%d,dz=%d]" % (x1, y1 - 2, z1, x2 - x1, y2 - y1 + 2, z2 - z1)
    for _ in range(8):
        lifted = False
        for up in (0.5, 1.5):
            reply = mc.rcon(world.prefix(boxes[0]) + "as %s at @s if block ~ ~%g ~ #minecraft:blocks_motion "
                            "run tp @s ~ ~1 ~" % (sel, up))
            lifted = lifted or "teleported" in reply.lower()
        if not lifted:
            return


v = values(VALUES)
# Round each player the player value names, in a dimension dims allows: radius blocks each way,
# the whole height of the world (or height blocks above and below their feet). Only loaded
# chunks change, so what is far from everyone is left as it is: for every chunk as a player
# walks in, see chunk_changer. Then it ends; the blocks stay as they are.
# The run's asks, as values: replace_rules="end_stone > bedrock" dims=the_end radius=64;
# replace_rules="water bubble_column seagrass tall_seagrass kelp kelp_plant > air" radius=150.
total = 0
for p in loop.who(v["player"]):
    at, dim = mc.pos(p), world.dim_of(p)
    if not at or (dim or world.OVERWORLD).split(":")[-1] not in v["dims"]:
        mc.log("skipped", p, "(not online, or in a dimension dims leaves out)")
        continue
    r, h = v["radius"], v["height"]
    lo, hi = area.band(at[1], h, h, dim) if h else world.limits(dim)
    n = replace_blocks(world.Box(at[0] - r, lo, at[2] - r, at[0] + r, hi, at[2] + r, dim=dim), v)
    total += n
    mc.log("round %s in %s: %d blocks changed" % (p, dim or world.OVERWORLD, n))
mc.log("done: %d blocks changed" % total)
