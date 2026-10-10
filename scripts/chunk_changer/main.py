"""Chunk Changer: change blocks by rules in every chunk a player walks into, once each, as they go; snow to random blocks to start with."""
ASKS = ["whatever I ask to change blocks, do it chunk by chunk, a script that changes the blocks whenever I enter a chunk",
        "replace all the snow, all the types of snow, with random blocks",
        "when I enter a chunk, every single block that is green turns into cobwebs",
        "it should be every chunk I enter", "change the blocks as I walk, not just where I am now"]
from kit import values, area, loop, world
import queue
import random
import re
import mc

VALUES = {
    "player":         {"type": "player", "default": "@a", "live": True},
    "around":         {"type": "int", "min": 0, "max": 3, "default": 1, "live": True},
    "replace_rules":  {"type": "list", "of": "str", "live": True, "default": [
        "#minecraft:snow > white_wool light_gray_wool red_wool blue_wool lime_wool yellow_wool cyan_wool "
        "purple_wool orange_wool pink_wool white_concrete light_blue_concrete green_concrete "
        "magenta_concrete terracotta orange_terracotta brown_terracotta oak_planks spruce_planks "
        "dark_oak_planks stone andesite granite diorite stone_bricks deepslate quartz_block glass "
        "coal_ore lapis_ore",
    ]},
    "replace_rate":   {"type": "int", "min": 1, "max": 100, "default": 100, "live": True},
    "replace_cell":   {"type": "int", "min": 1, "max": 16, "default": 4, "live": True},
    "replace_radius": {"type": "int", "min": 0, "max": 128, "default": 4, "live": True},
    "enters_dims":    {"type": "list", "of": ["overworld", "the_nether", "the_end"], "default": ["overworld", "the_nether", "the_end"], "live": True},
    "enters_below":   {"type": "int", "min": 0, "max": 384, "default": 32, "live": True},
    "enters_above":   {"type": "int", "min": 0, "max": 384, "default": 40, "live": True},
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


# --- preset: enters_chunk (copied from _presets/enters_chunk.py) ---
def enters_chunk(player, v):
    """For each player a player value names ("@a", "Steve,Alex"), yield (name, the world.Box
    of the chunk they are in, from enters_below blocks under their feet to enters_above over
    them) the first time anyone is in that chunk of that dimension, in the dimensions
    enters_dims names. A chunk is done once for as long as the script runs.

        for p, box in loop(enters_chunk(v["player"], v)):
            todo.put(box)           # a queue.Queue that a loop.background worker empties

    Nobody is looked at while the loop's body runs, so a slow change (seconds, with a mix)
    done in the loop itself misses the chunks walked through meanwhile: hand it to a worker,
    as chunk_changer does."""
    done = set()
    while not mc.stopping():
        for p in loop.who(player):
            at, dim = mc.pos(p), world.dim_of(p)
            if not at or (dim or world.OVERWORLD).split(":")[-1] not in v["enters_dims"]:
                continue
            cx, cz = area.chunk_of(at[0], at[2])
            if (dim, cx, cz) in done:
                continue
            done.add((dim, cx, cz))
            y1, y2 = area.band(at[1], v["enters_below"], v["enters_above"], dim)
            yield p, area.chunk_box(cx, cz, y1, y2, dim)
        loop.wait(0.5)


v = values(VALUES)
# Each chunk a player enters, from enters_below under their feet to enters_above over them,
# and with around=1 the eight round it too (so a player at a corner sees no seam), nearest
# first, each once: replace_blocks works through them in the background while the walking is
# watched, so a chunk crossed while another is changing is not missed. The run's green rule,
# after the snow rule or alone: replace_rules="#minecraft:leaves #minecraft:flowers grass_block
# short_grass tall_grass fern large_fern moss_block vine lily_pad cactus sugar_cane bamboo kelp
# kelp_plant seagrass tall_seagrass > cobweb". The blocks stay changed after a stop.
todo, seen = queue.Queue(), set()


def work():
    while not mc.stopping():
        try:
            p, box = todo.get(timeout=0.2)
        except queue.Empty:
            continue
        n = replace_blocks(box, v)
        mc.log("chunk %d %d%s: %d blocks changed, %d waiting" % (box[0] >> 4, box[2] >> 4, " in " + box.dim
                                                                if box.dim else "", n, todo.qsize()))


loop.background(work)
for p, box in loop(enters_chunk(v["player"], v)):
    cx, cz, k = box[0] >> 4, box[2] >> 4, v["around"]
    near = [(dx, dz) for dx in range(-k, k + 1) for dz in range(-k, k + 1)]
    for dx, dz in sorted(near, key=lambda d: abs(d[0]) + abs(d[1])):
        if (box.dim, cx + dx, cz + dz) not in seen:
            seen.add((box.dim, cx + dx, cz + dz))
            todo.put((p, area.chunk_box(cx + dx, cz + dz, box[1], box[4], box.dim)))
