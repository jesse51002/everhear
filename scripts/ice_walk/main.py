"""Ice Walk: everything round a player as they walk turns to packed ice, above, below and buried, ten blocks each way; a landing from a fall flashes like lightning."""
ASKS = ["can you turn everything I walk on into ice", "also on regular ground too",
        "for the freezing the ground around me, can you do it in like a ten block radius",
        "a light and strike on impact",
        "wherever I step, also above me and below me as well as all the blocks around me, not just the top blocks",
        "everything between ten blocks has to turn to ice blocks",
        "it should turn the blocks that are already buried into ice",
        "why is it not changing grass blocks", "don't exclude anything from the ice list, just make it include everything",
        "freeze the water and lava too", "ice under my feet"]
from kit import values, fx, loop, world, area
import math
import re
import mc
import random

VALUES = {
    "player":         {"type": "player", "default": "@a", "live": True},
    "flash":          {"type": "bool", "default": True, "live": True},
    "walk_every":     {"type": "number", "min": 0.5, "max": 32, "default": 2, "live": True},
    "under_radius":   {"type": "int", "min": 0, "max": 32, "default": 10, "live": True},
    "under_depth":    {"type": "int", "min": 1, "max": 64, "default": 11, "live": True},
    "under_above":    {"type": "int", "min": 0, "max": 64, "default": 11, "live": True},
    "replace_rules":  {"type": "list", "of": "str", "live": True, "default": [
        "solid water lava bubble_column kelp kelp_plant #minecraft:replaceable_by_trees "
        "#minecraft:flowers #minecraft:crops #minecraft:saplings > packed_ice",
    ]},
    "replace_rate":   {"type": "int", "min": 1, "max": 100, "default": 100, "live": True},
    "replace_cell":   {"type": "int", "min": 1, "max": 16, "default": 4, "live": True},
    "replace_radius": {"type": "int", "min": 0, "max": 128, "default": 4, "live": True},
}


# --- preset: walk_steps (copied from _presets/walk_steps.py) ---
_WALK_STEPS_POS = re.compile(r"\[(-?[\d.]+(?:[eE]-?\d+)?)d, (-?[\d.]+(?:[eE]-?\d+)?)d, (-?[\d.]+(?:[eE]-?\d+)?)d\]")


def walk_steps(player, v):
    """For each player a player value names ("@a", "Steve,Alex"), yield (name, world.Pos of
    their feet) when they stand on the ground walk_every blocks or more from where they last
    did, a block higher or lower, or in another dimension; and once when first seen. In the
    air (jumping, falling, flying, swimming) nothing comes.

        for p, at in loop(walk_steps(v["player"], v)):
            replace_blocks(under_feet(at, v), v)
    """
    last = {}
    while not mc.stopping():
        for p in loop.who(player):
            reply = mc.rcon("execute as %s if entity @s[nbt={OnGround:1b}] run data get entity @s Pos" % p)
            m = _WALK_STEPS_POS.search(reply)
            if not m:
                continue
            at = world.Pos(float(m.group(1)), float(m.group(2)), float(m.group(3)), world.dim_of(p))
            was = last.get(p)
            if (was is not None and was.dim == at.dim and math.floor(was[1] - 0.01) == math.floor(at[1] - 0.01)
                    and math.hypot(at[0] - was[0], at[2] - was[2]) < v["walk_every"]):
                continue
            last[p] = at
            yield p, at
        loop.wait(0.1)


# --- preset: under_feet (copied from _presets/under_feet.py) ---
def under_feet(at, v):
    """at: a player's name, or where they stand (a world.Pos, as walk_steps gives). The
    world.Box from the block they stand on (a slab's too) down under_depth blocks,
    under_radius out each way, and under_above blocks up from it: 2 takes in the feet and the
    head, which a rule that leaves air alone never fills. None when a player cannot be read."""
    if isinstance(at, str):
        p = mc.pos(at)
        if not p:
            return None
        at = world.Pos(p[0], p[1], p[2], world.dim_of(at))
    x, y, z = int(math.floor(at[0])), int(math.floor(at[1] - 0.01)), int(math.floor(at[2]))
    r = v["under_radius"]
    return world.Box(x - r, y - v["under_depth"] + 1, z - r, x + r, y + v["under_above"], z + r, dim=world.dim(at))


# --- preset: replace_blocks (copied from _presets/replace_blocks.py) ---
# "solid" in a rule: every block a player cannot walk through, as five 26.3 tags that hold no
# bedrock, barrier or command block. Glass, glowstone and sea lanterns are not in them.
_REPLACE_BLOCKS_SOLID = ["#minecraft:mineable/pickaxe", "#minecraft:mineable/axe", "#minecraft:mineable/shovel",
                         "#minecraft:mineable/hoe", "#minecraft:wool"]
# A weight ("70%") or a block (with its [states] and {nbt}), between spaces and commas.
_REPLACE_BLOCKS_WORD = re.compile(r"(\d+(?:\.\d+)?)%|([^\s,%\[{]+(?:\[[^\]]*\])?(?:\{[^}]*\})?)")
_replace_blocks_known = {}      # block id or #tag -> True when this server takes it
_replace_blocks_said = set()    # rules refused and said so once


def replace_blocks(where, v, *, rules=None):
    """Apply every rule in replace_rules (or `rules`, given for this call), in order, to `where`: a
    box (world.Box, as area.box, under_feet and enters_chunk give), a place (the box
    replace_radius round it), or a list of them (a ray's path: r.every(3)). A rule is
    "<from ...> > <to>": from is block ids, #tags or solid; to is a block or a mix, "70% netherrack,
    30% magma_block", where keep leaves the block as it was. One block fills in big pieces; a
    mix, or replace_rate under 100 (the share of cells that change), picks per cube of
    replace_cell blocks, the same pick at the same place every time. Blocks change without
    updating their neighbours (strict), so water turned to lava stays lava, sand stays put and
    nothing drops. Protected boxes and the bedrock floor are left alone, and a player the change
    left inside a block is lifted out. On 26.3 #minecraft:dirt is only dirt, coarse_dirt and
    rooted_dirt: name grass_block, podzol, mycelium, mud and the rest, or say solid. Returns how
    many blocks changed."""
    boxes = _replace_blocks_boxes(where, v["replace_radius"])
    rules = [r for r in (_replace_blocks_rule(t) for t in _replace_blocks_texts(rules or v["replace_rules"])) if r]
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
# At each step a player takes on the ground (walk_every blocks, or a block up or down, or a
# teleport), every block that replace_rules names in the box round them becomes packed ice:
# under_radius each way, under_depth down from the block they stand on, under_above up from
# it. Solid ground, water, lava, plants and leaves; never air, so nobody is shut in; never
# bedrock (frozen bedrock in the run opened the floor of the world), nor what is protected.
# under_radius=2 under_depth=1 under_above=0 is a path of ice under the feet only.
last = {}
for p, at in loop(walk_steps(v["player"], v)):
    replace_blocks(under_feet(at, v), v)
    was, last[p] = last.get(p), at
    if v["flash"] and was is not None and was.dim == at.dim and was[1] - at[1] >= 3:
        # A landing after a fall: a flash, sparks and thunder; no bolt, so no fire and no harm.
        fx.particle("flash{color:[1.0,1.0,1.0,1.0]}", at, up=1)
        fx.particle("electric_spark", at, spread=1, count=80, speed=0.5, up=1)
        fx.particle("end_rod", at, spread=(0.6, 0.1, 0.6), count=60, speed=0.3, up=0.2)
        fx.sound("entity.lightning_bolt.thunder", at, volume=2)
        fx.sound("entity.lightning_bolt.impact", at, volume=2)
        mc.log("landing flash for %s at %d %d %d" % ((p,) + at.block()))
