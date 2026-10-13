"""Ice Beam cast: say "ice beam" to fire an ice beam where you look, once, through walls, freezing and blinding everything it hits and turning the blocks along it to ice."""
ASKS = ["ice beam", "cast ice beam", "give me a staff that shoots like an ice beam and everything it touches turns to ice",
        "every mob it hits gets slowness and blindness", "make my ice beam bigger",
        "make sure my ice beam also hits players as well", "make sure the ice beam also goes through blocks",
        "make it freeze lava as well and turn it into ice, any liquid should freeze",
        "make my ice beam also break blocks as well, but the outer ring should be fully ice",
        "make my ice beam do no damage and just give slowness four and blindness four for ten seconds",
        "can you reduce the cooldown on my beam",
        "whenever you shoot it, it creates a path of ice below where it breaks"]
from kit import values, result, area, exclude, loop, world, fx, aim, items
import itertools
import math
import threading
import mc
import random
import re
import time

# --- preset: freeze (copied from _presets/freeze.py) ---
def freeze(hits, v):
    """Freeze every hit (a list of aim.Hit, from aim.near_line, around or lock_on) for
    freeze_seconds: it cannot walk or hurt anything, and a mob shows the frost (and takes
    the cold's 1 damage every 2 s, as in powder snow). With freeze_shell, a shell of
    freeze_block round each body, one block wide and two high, that melts when the freeze
    ends; it only fills air, so nothing is replaced (never bedrock, never a chest).
    freeze_blindness blinds it too (blindness IV, as the run's ice beam did). Pets and
    villagers are never frozen. Returns how many were frozen."""
    secs = int(math.ceil(v["freeze_seconds"]))
    tag = "%s_frz%d" % (mc.TAG, next(_FREEZE_N))      # each call tags with its own name
    if not aim.mark(hits, tag):                         # each once, where it was
        aim.untag(tag)
        return 0
    # Slowness and weakness X (amplifier 9; above 127 it is saved as a negative byte) leave
    # no speed and no attack, and wear off by themselves even if the script dies.
    mc.rcon("effect give @e[tag=%s] slowness %d 9 true" % (tag, secs))
    mc.rcon("effect give @e[tag=%s] weakness %d 9 true" % (tag, secs))
    if v["freeze_blindness"]:
        mc.rcon("effect give @e[tag=%s] blindness %d 3 true" % (tag, secs))
    mc.rcon("execute as @e[tag=%s,type=!player] run data merge entity @s {TicksFrozen:%d}" % (tag, 140 + 40 * secs))
    mc.rcon("execute at @e[tag=%s] run particle snowflake ~ ~1 ~ 0.4 0.6 0.4 0.02 25 force" % tag)
    frozen = [world.Pos(float(x), float(y), float(z), world.dim(hits[0].pos)) for x, y, z in _FREEZE_POS.findall(
        mc.rcon("execute as @e[tag=%s] run data get entity @s Pos" % tag))]
    aim.untag(tag)
    if v["freeze_shell"]:
        shells = [_freeze_shell(at, v["freeze_block"]) for at in frozen]
        with _FREEZE_LOCK:
            if not _FREEZE_SHELLS:
                mc.on_stop(lambda: [_freeze_melt(s) for s in list(_FREEZE_SHELLS)])
            _FREEZE_SHELLS.extend(shells)
        loop.background(_freeze_thaw, shells, secs)
    return len(frozen)


_FREEZE_N = itertools.cycle(range(1, 17))    # 16 names in turn, so aim.mark clears each only once
_FREEZE_POS = re.compile(r"\[(-?[\d.]+(?:E-?\d+)?)d, (-?[\d.]+(?:E-?\d+)?)d, (-?[\d.]+(?:E-?\d+)?)d\]")
_FREEZE_SHELLS = []         # shells still standing; a stop melts them
_FREEZE_LOCK = threading.Lock()


def _freeze_shell(at, block):
    """A shell of block round the body standing at a place, put into air only: a floor, a roof
    and four walls, leaving the body's own two blocks free. Returns (block, dim, boxes)."""
    x, y, z = (int(math.floor(c)) for c in at[:3])
    boxes = [(x - 1, y - 1, z - 1, x + 1, y - 1, z + 1), (x - 1, y + 2, z - 1, x + 1, y + 2, z + 1),
             (x - 1, y, z - 1, x + 1, y + 1, z - 1), (x - 1, y, z + 1, x + 1, y + 1, z + 1),
             (x - 1, y, z, x - 1, y + 1, z), (x + 1, y, z, x + 1, y + 1, z)]
    dim, loaded = world.dim(at), {}
    for b in boxes:
        area.fill(b, block, "keep", dim=dim, spare_protected=True, keep_bedrock=True, most_mspt=0, loaded=loaded)
    return (block, dim, boxes)


def _freeze_thaw(shells, secs):
    """Melt shells once their freeze is over."""
    if loop.wait(secs):
        for s in shells:
            _freeze_melt(s)


def _freeze_melt(shell):
    """Turn a shell's block back to air, in the shell's own boxes only. Plain fills (a few
    blocks each), because the kit's fill does nothing once the script is stopping."""
    block, dim, boxes = shell
    with _FREEZE_LOCK:
        if shell not in _FREEZE_SHELLS:
            return
        _FREEZE_SHELLS.remove(shell)
    for b in boxes:
        mc.rcon(world.run(dim, "fill %d %d %d %d %d %d air replace %s" % (b + (block,))))


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


# --- preset: theme_music (copied from _presets/theme_music.py) ---
def theme_music(at, v, *, tune=None):
    """Play the tune theme_tune names (or `tune`, for this call: the throw after a charge's
    choir), in the background, at a player (or a place), and return how many seconds it
    lasts. theme_to: "near" is heard within theme_reach blocks of it, "everyone" by every
    player where they stand, "player" by that player alone. theme_loop: again each time it
    ends (boss music while the boss lives), until a newer theme, "none" or a stop. A newer
    theme at the same player (or for everyone) cuts this one off, a record too; "none" plays
    nothing and only cuts off what plays there."""
    to = v["theme_to"]
    if to == "player" and not isinstance(at, str):
        to = "near"                     # a place has nobody to play it to alone
    where = at if isinstance(at, str) else "%d_%d_%d" % tuple(at[:3])
    key = "theme" if to == "everyone" else "theme_" + where      # one theme at a time per player or place
    hear = {"everyone": "@a", "player": at}.get(to)
    _THEME_MUSIC_GEN[key] = gen = _THEME_MUSIC_GEN.get(key, 0) + 1   # a loop under this key ends
    fx.stop_melody(key)
    seconds = _theme_music_play(at, v, tune or v["theme_tune"], hear, key)
    if seconds and v["theme_loop"]:
        loop.background(_theme_music_again, at, v, tune, hear, key, gen, seconds)
    return seconds


# Each tune is a list of notes: (seconds from the start, a note block instrument or a sound
# id, a note from 0 to 24 where 12 is the sound's own pitch, volume). The run's tunes, as
# its scripts played them; a volume of 3 is heard 48 blocks away.
_THEME_MUSIC_TUNES = {
    "galick_gun": [         # a rising purple fanfare, 1.5 s
        (0, "guitar", 0, 3), (0, "block.beacon.power_select", 4, 2), (0.2, "pling", 4, 3), (0.4, "guitar", 7, 3),
        (0.6, "pling", 12, 3), (0.8, "guitar", 16, 3), (0.8, "bell", 16, 3), (1.0, "pling", 19, 3),
        (1.2, "bell", 24, 3),
        (1.5, "bell", 24, 3), (1.5, "pling", 24, 3), (1.5, "guitar", 19, 3), (1.5, "guitar", 16, 3),
        (1.5, "block.beacon.activate", 12, 3), (1.5, "block.amethyst_block.chime", 24, 3),
    ],
    "hollow_purple": [      # a slow fall from bell to bass, then a boom, 3 s
        (0, "bass", 7, 3), (0, "didgeridoo", 7, 3), (0, "block.beacon.power_select", 5, 2),
        (0.4, "bell", 19, 3), (0.4, "block.amethyst_block.chime", 3, 2),
        (0.8, "bell", 15, 3), (0.8, "bass", 3, 3),
        (1.2, "bell", 14, 3),
        (1.6, "bell", 12, 3), (1.6, "didgeridoo", 2, 3),
        (2.0, "bell", 10, 3),
        (2.4, "bell", 7, 3), (2.4, "bass", 0, 3), (2.4, "didgeridoo", 0, 3),
        (3.0, "bell", 0, 3), (3.0, "block.amethyst_block.resonate", 0, 3), (3.0, "entity.generic.explode", 0, 2),
    ],
    "ice_beam": [           # high chimes and xylophone, 2.5 s
        (0, "chime", 24, 3), (0.2, "iron_xylophone", 22, 3), (0.4, "bell", 19, 3), (0.6, "chime", 17, 3),
        (0.8, "iron_xylophone", 19, 3), (1.0, "bell", 22, 3), (1.2, "chime", 24, 3), (1.4, "iron_xylophone", 22, 3),
        (1.6, "bell", 24, 3), (1.6, "block.amethyst_block.chime", 24, 2), (1.9, "chime", 17, 3),
        (2.2, "iron_xylophone", 24, 3), (2.5, "bell", 24, 3), (2.5, "chime", 19, 3),
    ],
    "spirit_bomb": [        # a choir swelling while the bomb charges: a chord every 1.1 s, louder each time, 6 s
        (0.0, "flute", 2, 1), (0.05, "flute", 9, 1), (0.0, "chime", 6, 1), (0.1, "bell", 14, 1),
        (0.0, "block.beacon.ambient", 2, 1),
        (1.1, "flute", 4, 1.5), (1.15, "flute", 11, 1.5), (1.1, "chime", 8, 1.5), (1.2, "bell", 16, 1.5),
        (1.1, "block.beacon.ambient", 4, 1.5), (1.6, "chime", 13, 1.5),
        (2.2, "flute", 5, 2), (2.25, "flute", 12, 2), (2.2, "chime", 9, 2), (2.3, "bell", 17, 2),
        (2.2, "block.beacon.ambient", 5, 2),
        (3.3, "flute", 7, 2.5), (3.35, "flute", 14, 2.5), (3.3, "chime", 11, 2.5), (3.4, "bell", 19, 2.5),
        (3.3, "block.beacon.ambient", 7, 2.5), (3.8, "chime", 16, 2.5),
        (4.4, "flute", 9, 3), (4.45, "flute", 16, 3), (4.4, "chime", 13, 3), (4.5, "bell", 21, 3),
        (4.4, "block.beacon.ambient", 9, 3),
        (5.5, "flute", 11, 3), (5.55, "flute", 18, 3), (5.5, "chime", 15, 3), (5.6, "bell", 23, 3),
        (5.5, "block.beacon.ambient", 11, 3), (6.0, "chime", 20, 3),
    ],
    "spirit_bomb_throw": [  # the release: a bright chord climbing, 1 s
        (0, "flute", 12, 3), (0, "chime", 16, 3), (0, "bell", 19, 3), (0, "block.beacon.activate", 12, 3),
        (0.25, "flute", 16, 3), (0.25, "bell", 24, 3),
        (0.5, "flute", 19, 3), (0.5, "chime", 24, 3), (0.5, "block.amethyst_block.resonate", 24, 3),
        (0.9, "bell", 24, 3), (0.9, "chime", 19, 3),
    ],
    "super_saiyan": [       # a fast climbing riff with a bass under every third note, then a chord, 2 s
        (0, "block.beacon.activate", 18, 0.8),
        (0.0, "bit", 6, 1), (0.0, "bass", 0, 1), (0.14, "pling", 9, 1), (0.28, "guitar", 11, 1),
        (0.42, "bell", 12, 1), (0.42, "bass", 0, 1), (0.56, "bit", 14, 1), (0.7, "pling", 16, 1),
        (0.84, "guitar", 17, 1), (0.84, "bass", 5, 1), (0.98, "bell", 19, 1), (1.12, "bit", 21, 1),
        (1.26, "pling", 22, 1), (1.26, "bass", 10, 1), (1.4, "guitar", 24, 1), (1.54, "bell", 21, 1),
        (1.68, "bit", 24, 1), (1.68, "bass", 12, 1), (1.82, "pling", 24, 1),
        (1.96, "pling", 12, 1), (1.96, "bell", 12, 1), (1.96, "pling", 16, 1), (1.96, "bell", 16, 1),
        (1.96, "pling", 19, 1), (1.96, "bell", 19, 1), (1.96, "pling", 24, 1), (1.96, "bell", 24, 1),
    ],
    "boss": [               # Pigstep, with a growl, a wither and thunder over it, 148 s
        (0, "music_disc.pigstep", 12, 2),
        (20, "entity.ender_dragon.growl", 12, 0.4), (55, "entity.wither.ambient", 12, 0.4),
        (90, "entity.lightning_bolt.thunder", 12, 0.4), (125, "entity.ender_dragon.growl", 12, 0.4),
    ],
}

# A record plays on in the players' game once started (fx.melody stops it when a newer
# melody starts, and on a stop); each with how long it lasts, in seconds.
_THEME_MUSIC_RECORDS = {"music_disc.pigstep": 148.0}
_THEME_MUSIC_GEN = {}           # key -> how many themes have started under it: a loop plays on while it is the last


def _theme_music_play(at, v, name, hear, key):
    """Start one tune under a key; how long it lasts (0 for "none")."""
    notes = _THEME_MUSIC_TUNES.get(name)
    if not notes:
        return 0
    fx.melody(notes, at, to=hear, key=key, reach=v["theme_reach"])
    return max([n[0] + 0.5 for n in notes] + [_THEME_MUSIC_RECORDS.get(n[1], 0) for n in notes])


def _theme_music_again(at, v, tune, hear, key, gen, seconds):
    """Play it again each time it ends, while theme_loop is on and nothing newer started there."""
    while loop.wait(seconds) and v["theme_loop"] and _THEME_MUSIC_GEN.get(key) == gen:
        seconds = _theme_music_play(at, v, tune or v["theme_tune"], hear, key)
        if not seconds:
            return


# --- preset: damage_hits (copied from _presets/damage_hits.py) ---
def damage_hits(hits, shooter, v):
    """Hurt every hit once (hits: a list of aim.Hit, from aim.near_line, around or lock_on,
    or several of those added together), by damage_amount of damage_type, as the shooter, so
    a kill is theirs; damage_kill kills instead. damage_what: "all", "mobs" (no players),
    "hostile" (monsters only: never an animal, and no players) or "players". damage_most:
    only the nearest that many (1: the first only; 0: all). shooter: a player's name, or
    their aim.look(), with which a hit whose head the look passes takes damage_head times the
    damage. Never the shooter, a name in damage_spare, a pet, a villager or anything else
    exclude spares. Returns how many."""
    by = getattr(shooter, "player", shooter) or ""
    spare = [by] + list(v["damage_spare"])
    names = set(mc.bare(s) for s in spare if s and not s.startswith("@"))
    what = v["damage_what"]
    hits = [h for h in sorted(hits, key=lambda h: h.t) if mc.bare(h.name) not in names
            and (h.player if what == "players" else (what == "all" or not h.player))]
    if v["damage_most"]:
        hits = hits[:v["damage_most"]]
    body = "%s_hit%d" % (mc.TAG, next(_DAMAGE_N))      # each call tags with its own name
    head = [h for h in hits if v["damage_head"] > 1 and _damage_on_head(h, shooter)]
    count = {}
    for tag, some in ((body, [h for h in hits if h not in head]), (body + "h", head)):
        if some:
            count[tag] = aim.mark(some, tag, spare, hostile=what == "hostile")
    for tag, n in count.items():
        if n:
            if v["damage_kill"]:
                _damage_all(tag, 1000000, "generic_kill", by)
            else:
                _damage_all(tag, v["damage_amount"] * (1 if tag == body else v["damage_head"]), v["damage_type"], by)
        aim.untag(tag)
    return sum(count.values())


_DAMAGE_N = itertools.cycle(range(1, 17))    # 16 names in turn, so aim.mark clears each only once


def _damage_all(tag, amount, kind, by):
    """Damage everything tagged, once, as `by`; as nobody if `by` cannot be found (gone offline)."""
    cmd = "execute as @e[tag=%s] run damage @s %.1f minecraft:%s" % (tag, amount, kind)
    if by and "Applied" in mc.rcon(cmd + " by " + by):
        return
    mc.rcon(cmd)


def _damage_on_head(h, look):
    """True if a look (aim.look) passes within half a block of the head of a body standing at
    the hit: 1.7 above its feet, as tall as a player or a zombie."""
    if not hasattr(look, "origin"):
        return False
    o, d = look.origin, look.dir
    w = [h.pos[0] - o[0], h.pos[1] + 1.7 - o[1], h.pos[2] - o[2]]
    t = w[0] * d[0] + w[1] * d[1] + w[2] * d[2]
    return t > 0 and math.sqrt(sum((w[i] - t * d[i]) ** 2 for i in range(3))) <= 0.5


v = values()
quiet = {}                      # when each player's tune is over


def under(at):
    """The 3 x 3 under a place on the beam, two blocks down: the floor of its tunnel."""
    x, y, z = at.block()
    return world.Box(x - 1, y - 2, z - 1, x + 1, y - 2, z + 1, dim=at.dim)


def _tunnel_box(at):
    """The tunnel's cross-section at one point of the beam: a 10 by 10 by 10 block cube."""
    x, y, z = at.block()
    return world.Box(x - 5, y - 5, z - 5, x + 4, y + 4, z + 4, dim=at.dim)


fired = []
for p in loop.who(v["player"]):
    r = aim.ray(p, v["length"], blocks=not v["through_walls"])
    if r is None:
        continue
    fx.particle("snowflake", p, spread=(0.6, 0.8, 0.6), count=14, speed=0.05, up=0.9)
    fx.line(r.start.moved(0, -0.3, 0), r.end, "dust{color:[0.6,0.85,1.0],scale:1.5}", every=2, spread=1.2, count=10)
    fx.particle("snowflake", r.end, spread=2, count=40, speed=0.1)
    fx.sound("block.glass.break", p, volume=2, pitch=1.2)
    fx.sound("entity.player.hurt_freeze", p, volume=2)
    hits = aim.near_line(r, v["width"] / 2.0, shooter=p, with_players=v["damage_what"] in ("all", "players"))
    n = freeze(hits, v)
    if v["damage_amount"] or v["damage_kill"]:      # no damage by default: it freezes and blinds
        damage_hits(hits, p, v)
    if time.time() >= quiet.get(p, 0):
        quiet[p] = time.time() + theme_music(p, v)
    along = r.every(2)[1:]
    if v["tunnel"]:
        replace_blocks([_tunnel_box(at) for at in along], v, rules=["solid water lava > air"])
    m = replace_blocks(along, v)
    if v["path"]:
        m += replace_blocks([under(at) for at in r.every(1)[1:]], v, rules=["air water lava > packed_ice"])
    # The platform goes last, so the tunnel's carving cannot take it away: 5 by 5 of packed ice
    # directly under the caster's feet, filling only air, water and other replaceable blocks.
    feet = mc.pos(p)
    if feet:
        fl = world.Pos(feet[0], feet[1], feet[2], world.dim_of(p))
        x, y, z = fl.block()
        area.fill((x - 2, y - 1, z - 2, x + 2, y - 1, z + 2), "packed_ice", "replace #minecraft:replaceable",
                  dim=fl.dim, spare_protected=True, keep_bedrock=True)
    mc.log("%s fired: %d frozen, %d blocks to ice, %.0f long (%s)" % (p, n, m, r.length, r.why))
    fired.append((p, n, m))
result("ice beam fired by %s: %d frozen, %d blocks to ice" % (
    ", ".join(f[0] for f in fired) or v["player"], sum(f[1] for f in fired), sum(f[2] for f in fired)),
    player=v["player"], frozen=sum(f[1] for f in fired), blocks=sum(f[2] for f in fired))
