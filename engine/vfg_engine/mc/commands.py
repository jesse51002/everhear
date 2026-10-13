"""Each write op (vfg_engine.io) as its exact Minecraft 26.3 command, and each reply as an OpResult
with the reason in words (the game engine spec: "the writer, the only RCON writer"; Contracts: io.py).

    command_for(GiveEffect("Steve", "minecraft:poison", 30, 2))
    -> ["effect give Steve minecraft:poison 30 2 false"]
    result_for(op, "Unable to apply this effect (target is either immune to effects, or has something stronger)")
    -> OpResult(ok=False, reply=..., error="has something stronger")

Every reply below was seen on a 26.3 server (engine/MINECRAFT.md). A reply this module does not know
is ok unless it reads as an error (Minecraft's "<--[HERE]" parse errors and the like).

Standard library only, besides vfg_engine.io.
"""

from __future__ import annotations

import json
import math
import re
import uuid as _uuid

from ..io import (AddModifier, Bossbar, Command, Damage, EntityData, Fill, Forceload, GiveEffect, GiveItem,
                  Gamerule, Ignite, Message, Motion, OpResult, Particle, Remove, RemoveModifier, SetBlock,
                  Sound, Spawnpoint, StopSound, Summon, TakeItem, Teleport)
from .rcon import MAX_COMMAND

# Server commands that are the owner's to run, never the engine's or content's. The one list is
# scripts/_lib/commands.py (REFUSED); this is a copy because the engine's package stands alone on the
# box, and engine/tests/mc/test_commands.py checks the two are the same.
REFUSED = frozenset({"stop", "op", "deop", "ban", "ban-ip", "pardon", "pardon-ip", "whitelist", "kick",
                     "save-off"})

OPERATIONS = ("add_value", "add_multiplied_base", "add_multiplied_total")
MESSAGE_WHERE = ("chat", "actionbar", "title", "subtitle")
_NAME = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_ID = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
_SOUND_SOURCE = "master"
FILL_MOST = 32768            # blocks one fill changes at most (Minecraft's limit)
FORCELOAD_MOST = 256         # chunks one forceload names at most
FILL_MODES = ("replace", "keep", "destroy", "hollow", "outline")
BOSSBAR_COLORS = ("blue", "green", "pink", "purple", "red", "white", "yellow")
LAVA_SECONDS = 15.0          # how long lava sets a player alight; the burning_time modifier scales it
_BLOCK = re.compile(r"^#?[a-z0-9_.-]+(:[a-z0-9_./-]+)?(\[[a-z0-9_=,]*\])?(\{.*\})?$")
_BB_TAG = "everhear.bb"      # players a bossbar is shown to, tagged for the one command that names them


class OpRefused(ValueError):
    """The op cannot be turned into a command (it would be wrong, or not ours to send)."""


def refused_word(cmd: str) -> str | None:
    """The refused word in a command (its first word, or the word after any `run`), or None."""
    words = cmd.strip().split()
    if not words:
        return None
    checks = [words[0]]
    for i, w in enumerate(words[:-1]):
        if w.lower() == "run":
            checks.append(words[i + 1])
    for w in checks:
        w = w.lstrip("/").lower()
        if w in REFUSED:
            return w
    return None


def resource(name: str, what: str) -> str:
    """`speed` and `minecraft:speed` are one id; anything else that is not an id is refused."""
    name = (name or "").strip()
    if ":" not in name:
        name = "minecraft:" + name
    if not _ID.match(name):
        raise OpRefused("%r is not a %s id" % (name, what))
    return name


def target(t: str) -> str:
    """A player's name or an entity's uuid, as a command's target (never a selector: an op names one)."""
    t = (t or "").strip()
    if _NAME.match(t):
        return t
    try:
        return str(_uuid.UUID(t))
    except ValueError:
        raise OpRefused("%r is neither a player's name nor an entity's uuid" % (t,)) from None


def uuid_ints(u: str) -> list[int]:
    """A uuid as Minecraft's four signed ints ([I; a, b, c, d])."""
    n = _uuid.UUID(u).int
    out = []
    for shift in (96, 64, 32, 0):
        v = (n >> shift) & 0xFFFFFFFF
        out.append(v - (1 << 32) if v >= 1 << 31 else v)
    return out


def uuid_str(ints) -> str:
    """Minecraft's four ints as a hyphenated lower-case uuid."""
    n = 0
    for v in ints:
        n = (n << 32) | (int(v) & 0xFFFFFFFF)
    return str(_uuid.UUID(int=n))


def num(x: float) -> str:
    """A number as a command takes it: no exponent, no needless zeros."""
    x = float(x)
    if not math.isfinite(x):
        raise OpRefused("%r is not a finite number" % (x,))
    text = ("%.6f" % x).rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def vec(v) -> str:
    if len(v) != 3:
        raise OpRefused("a position is three numbers, not %r" % (v,))
    return " ".join(num(c) for c in v)


def text_component(text: str) -> str:
    """Plain text as a 26.3 text component: an SNBT string (JSON's quoting is valid SNBT)."""
    return json.dumps(str(text), ensure_ascii=False)


def _snbt_string(text: str) -> str:
    return json.dumps(str(text), ensure_ascii=False)


def top_level_keys(inner: str) -> set[str]:
    """The keys of an SNBT compound's own entries (its text without the outer braces), not those of
    compounds inside it: Summon.nbt may hold an item whose components have keys of their own."""
    keys, depth, i, n = set(), 0, 0, len(inner)
    expect_key = True
    while i < n:
        c = inner[i]
        if c in "\"'":
            j = i + 1
            while j < n and inner[j] != c:
                j += 2 if inner[j] == "\\" else 1
            if depth == 0 and expect_key:
                keys.add(inner[i + 1:j])
                expect_key = False
            i = j + 1
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        elif c == "," and depth == 0:
            expect_key = True
        elif c == ":" and depth == 0:
            expect_key = False
        elif depth == 0 and expect_key and not c.isspace():
            m = re.match(r"[A-Za-z0-9._+-]+", inner[i:])
            if m:
                keys.add(m.group())
                i += m.end()
                expect_key = False
                continue
        i += 1
    return keys


def block_state(text: str, what: str = "block") -> str:
    """A block (or a #tag in a filter) as a command takes it: an id, its [properties], its {data}."""
    text = (text or "").strip()
    if not _BLOCK.match(text):
        raise OpRefused("%r is not a %s (an id, with [properties] or {data})" % (text, what))
    return text


def block_pos(p) -> tuple[int, int, int]:
    if len(p) != 3:
        raise OpRefused("a block position is three whole numbers, not %r" % (p,))
    return tuple(int(math.floor(float(c))) for c in p)


def _players_or_all(players) -> list[str]:
    for p in players:
        if not _NAME.match(p or ""):
            raise OpRefused("%r is not a player's name" % (p,))
    return list(players) or ["@a"]


def _in_dim(dim: str, cmd: str) -> str:
    """The command run in a dimension. The RCON source is in the overworld, so it goes bare there,
    and a failure keeps Minecraft's own words."""
    dim = resource(dim, "dimension")
    return cmd if dim == "minecraft:overworld" else "execute in %s run %s" % (dim, cmd)


def command_for(op) -> list[str]:
    """The command(s) one op becomes, in order. OpRefused when it cannot be one."""
    if isinstance(op, AddModifier):
        if op.operation not in OPERATIONS:
            raise OpRefused("operation %r is not one of %s" % (op.operation, ", ".join(OPERATIONS)))
        mid = resource(op.id, "modifier")
        if not mid.startswith("everhear:"):
            raise OpRefused("the engine adds only everhear:* modifiers, not %s" % mid)
        return ["attribute %s %s modifier add %s %s %s" % (target(op.target), resource(op.attribute, "attribute"),
                                                           mid, num(op.amount), op.operation)]
    if isinstance(op, RemoveModifier):
        mid = resource(op.id, "modifier")
        if not mid.startswith("everhear:"):
            raise OpRefused("the engine removes only everhear:* modifiers, never %s" % mid)
        return ["attribute %s %s modifier remove %s" % (target(op.target), resource(op.attribute, "attribute"), mid)]
    if isinstance(op, GiveEffect):
        # `effect give` takes whole seconds: a fraction is rounded, and anything shorter is one second.
        if op.seconds == float("inf"):
            length = "infinite"
        elif not op.seconds > 0 or not math.isfinite(op.seconds):
            raise OpRefused("an effect lasts more than 0 seconds, not %r" % (op.seconds,))
        else:
            length = str(max(1, int(round(op.seconds))))
        amp = int(op.amplifier)
        if not 0 <= amp <= 255:
            raise OpRefused("amplifier %r is outside 0..255" % (op.amplifier,))
        return ["effect give %s %s %s %d %s" % (target(op.target), resource(op.effect, "effect"), length, amp,
                                                "false" if op.particles else "true")]
    if isinstance(op, GiveItem):
        if not _NAME.match(op.player or ""):
            raise OpRefused("%r is not a player's name" % (op.player,))
        if not op.item_id or not re.match(r"^[a-z0-9_./-]{1,64}$", op.item_id):
            raise OpRefused("%r is not an EverHear item id (a-z 0-9 _ . / -, at most 64)" % (op.item_id,))
        count = int(op.count)
        if not 1 <= count <= 99:
            raise OpRefused("count %r is outside 1..99" % (op.count,))
        parts = ['custom_data={everhear:{item:%s}}' % _snbt_string(op.item_id)]
        if op.name:
            parts.append("item_name=%s" % text_component(op.name))
        for key, value in (op.components or {}).items():
            key = resource(key, "component")
            if key == "minecraft:custom_data":
                raise OpRefused("custom_data is the engine's (it carries the EverHear id)")
            if key == "minecraft:item_name" and op.name:
                raise OpRefused("item_name is given twice (name and components)")
            parts.append("%s=%s" % (key[len("minecraft:"):] if key.startswith("minecraft:") else key, value))
        return ["give %s %s[%s] %d" % (op.player, resource(op.base, "item"), ",".join(parts), count)]
    if isinstance(op, TakeItem):
        if not _NAME.match(op.player or ""):
            raise OpRefused("%r is not a player's name" % (op.player,))
        return ["clear %s *[custom_data~{everhear:{item:%s}}]" % (op.player, _snbt_string(op.item_id))]
    if isinstance(op, Summon):
        ints = uuid_ints(op.uuid)
        fields = []
        extra = (op.nbt or "").strip()
        if extra:
            if not (extra.startswith("{") and extra.endswith("}")):
                raise OpRefused("nbt is a compound, {...}, not %r" % extra[:40])
            inner = extra[1:-1].strip()
            if {"UUID", "Tags"} & top_level_keys(inner):
                raise OpRefused("UUID and Tags are the engine's: give tags in Summon.tags")
            if inner:
                fields.append(inner)
        # Minecraft keeps the last of a repeated key, so the engine's come last.
        if op.tags:
            for t in op.tags:
                if not re.match(r"^[A-Za-z0-9_.+-]+$", t):
                    raise OpRefused("%r is not a tag (A-Z a-z 0-9 _ . + -)" % (t,))
            fields.append("Tags:[%s]" % ",".join(_snbt_string(t) for t in op.tags))
        fields.append("UUID:[I;%s]" % ",".join(str(i) for i in ints))
        return [_in_dim(op.dim, "summon %s %s {%s}" % (resource(op.entity_type, "entity type"), vec(op.pos),
                                                         ",".join(fields)))]
    if isinstance(op, Remove):
        return ['function everhear:remove {u:"%s"}' % str(_uuid.UUID(op.uuid))]
    if isinstance(op, Damage):
        if not op.amount > 0:
            raise OpRefused("damage is more than 0, not %r" % (op.amount,))
        cmd = "damage %s %s %s" % (target(op.target), num(op.amount), resource(op.damage_type, "damage type"))
        if op.by:
            cmd += " by %s" % target(op.by)
        return [cmd]
    if isinstance(op, Particle):
        count = int(op.count)
        if not 0 <= count <= 10000:
            raise OpRefused("count %r is outside 0..10000" % (op.count,))
        force, to = getattr(op, "force", False), tuple(getattr(op, "to", ()) or ())
        head = "particle %s %s %s %s %d" % (resource(op.name, "particle"), vec(op.pos), vec(op.delta), num(op.speed), count)
        if not force and not to:
            return [_in_dim(op.dim, head)]
        mode = "force" if force else "normal"
        return [_in_dim(op.dim, "%s %s %s" % (head, mode, who)) for who in _players_or_all(to)]
    if isinstance(op, Sound):
        to = tuple(getattr(op, "to", ()) or ())
        return [_in_dim(op.dim, "playsound %s %s %s %s %s %s" % (resource(op.name, "sound"), _SOUND_SOURCE, who,
                                                                vec(op.pos), num(op.volume), num(op.pitch)))
                for who in _players_or_all(to)]
    if isinstance(op, Fill):
        a, b = block_pos(op.a), block_pos(op.b)
        size = (abs(a[0] - b[0]) + 1) * (abs(a[1] - b[1]) + 1) * (abs(a[2] - b[2]) + 1)
        if size > FILL_MOST:
            raise OpRefused("the box is %d blocks, over the %d one fill changes (the block queue splits it)"
                            % (size, FILL_MOST))
        if op.mode not in FILL_MODES:
            raise OpRefused("mode is one of %s, not %r" % (", ".join(FILL_MODES), op.mode))
        cmd = "fill %d %d %d %d %d %d %s" % (a + b + (block_state(op.block),))
        if op.replace:
            if op.mode == "keep":
                raise OpRefused("a replace filter goes with mode replace, destroy, hollow or outline, not keep")
            cmd += " replace %s" % block_state(op.replace, "block filter")
        # strict (no block updates) is a mode of its own: it goes with replace, not with the others
        if op.mode == "replace":
            cmd += " strict" if op.strict else ""
        else:
            cmd += " " + op.mode
        return [_in_dim(op.dim, cmd)]
    if isinstance(op, SetBlock):
        if op.mode not in ("replace", "keep", "destroy"):
            raise OpRefused("mode is one of replace, keep, destroy, not %r" % (op.mode,))
        cmd = "setblock %d %d %d %s" % (block_pos(op.pos) + (block_state(op.block),))
        if op.mode == "replace":
            cmd += " strict" if op.strict else ""
        else:
            cmd += " " + op.mode
        return [_in_dim(op.dim, cmd)]
    if isinstance(op, Forceload):
        (x1, z1), (x2, z2) = [tuple(int(math.floor(float(c))) for c in p) for p in (op.a, op.b)]
        chunks = (abs((x1 >> 4) - (x2 >> 4)) + 1) * (abs((z1 >> 4) - (z2 >> 4)) + 1)
        if chunks > FORCELOAD_MOST:
            raise OpRefused("%d chunks, over the %d one forceload names" % (chunks, FORCELOAD_MOST))
        return [_in_dim(op.dim, "forceload %s %d %d %d %d" % ("add" if op.add else "remove", x1, z1, x2, z2))]
    if isinstance(op, Motion):
        t = target(op.target)
        if _NAME.match(t):
            raise OpRefused("a player's Motion cannot be set (26.3 writes no player's data): launch a player with "
                            "wind charges (engine/MINECRAFT.md)")
        return ["data merge entity %s {Motion:[%sd,%sd,%sd]}" % ((t,) + tuple(num(c) for c in op.vec))]
    if isinstance(op, EntityData):
        snbt = (op.snbt or "").strip()
        if not (snbt.startswith("{") and snbt.endswith("}")):
            raise OpRefused("entity data is a compound, {...}, not %r" % snbt[:40])
        if {"UUID"} & top_level_keys(snbt[1:-1]):
            raise OpRefused("an entity's UUID is not changed")
        return ["data merge entity %s %s" % (target(op.target), snbt)]
    if isinstance(op, Bossbar):
        bid = resource(op.id if ":" in op.id else "everhear:" + op.id, "bossbar")
        if op.remove:
            return ["bossbar remove %s" % bid]
        cmds = []
        if op.name is not None:          # made if missing, renamed if there
            cmds += ["bossbar add %s %s" % (bid, text_component(op.name)),
                     "bossbar set %s name %s" % (bid, text_component(op.name))]
        if op.color is not None:
            if op.color not in BOSSBAR_COLORS:
                raise OpRefused("color is one of %s, not %r" % (", ".join(BOSSBAR_COLORS), op.color))
            cmds.append("bossbar set %s color %s" % (bid, op.color))
        if op.max is not None:
            if int(op.max) < 1:
                raise OpRefused("max is at least 1, not %r" % (op.max,))
            cmds.append("bossbar set %s max %d" % (bid, int(op.max)))
        if op.value is not None:
            cmds.append("bossbar set %s value %d" % (bid, max(0, int(op.value))))
        if op.players is not None:
            names = list(op.players)
            for p in names:
                if not _NAME.match(p or ""):
                    raise OpRefused("%r is not a player's name" % (p,))
            if not names:
                cmds.append("bossbar set %s players" % bid)
            elif len(names) == 1:
                cmds.append("bossbar set %s players %s" % (bid, names[0]))
            else:                        # one selector names several players: a tag for the moment
                cmds += ["tag %s add %s" % (p, _BB_TAG) for p in names]
                cmds += ["bossbar set %s players @a[tag=%s]" % (bid, _BB_TAG), "tag @a[tag=%s] remove %s" % (_BB_TAG, _BB_TAG)]
        if not cmds:
            raise OpRefused("a bossbar op that changes nothing")
        return cmds
    if isinstance(op, StopSound):
        tail = " * %s" % resource(op.name, "sound") if op.name else ""
        return ["stopsound %s%s" % (who, tail) for who in _players_or_all(tuple(op.players))]
    if isinstance(op, Spawnpoint):
        if not _NAME.match(op.player or ""):
            raise OpRefused("%r is not a player's name" % (op.player,))
        return [_in_dim(op.dim, "spawnpoint %s %d %d %d" % ((op.player,) + block_pos(op.pos)))]
    if isinstance(op, Ignite):
        seconds = float(op.seconds)
        if not 0 < seconds <= 1638:
            raise OpRefused("a fire lasts more than 0 and at most 1638 seconds (a short's ticks), not %r" % (op.seconds,))
        ticks = max(1, int(round(seconds * 20)))
        factor = seconds / LAVA_SECONDS - 1.0
        return ['function everhear:ignite {t:"%s",f:%s,k:%d}' % (target(op.target), num(factor), ticks)]
    if isinstance(op, Message):
        if op.where not in MESSAGE_WHERE:
            raise OpRefused("where is one of %s, not %r" % (", ".join(MESSAGE_WHERE), op.where))
        whom = list(op.players) or ["@a"]
        for p in op.players:
            if not _NAME.match(p):
                raise OpRefused("%r is not a player's name" % (p,))
        body = text_component(op.text)
        if op.where == "chat":
            return ["tellraw %s %s" % (w, body) for w in whom]
        return ["title %s %s %s" % (w, op.where, body) for w in whom]
    if isinstance(op, Teleport):
        cmd = "tp %s %s" % (target(op.target), vec(op.pos))
        if op.yaw is not None or op.pitch is not None:
            cmd += " %s %s" % ("~" if op.yaw is None else num(op.yaw), "~" if op.pitch is None else num(op.pitch))
        return [_in_dim(op.dim, cmd)]
    if isinstance(op, Gamerule):
        if not re.match(r"^[a-z0-9_]+$", op.name or ""):
            raise OpRefused("%r is not a 26.3 gamerule name (snake_case)" % (op.name,))
        value = str(op.value).strip()
        if not re.match(r"^[A-Za-z0-9_.-]+$", value):
            raise OpRefused("%r is not a gamerule value" % (op.value,))
        return ["gamerule %s %s" % (op.name, value)]
    if isinstance(op, Command):
        text = (op.text or "").strip().lstrip("/")
        if not text:
            raise OpRefused("an empty command")
        word = refused_word(text)
        if word:
            raise OpRefused("%r is the server owner's to run, never the engine's" % word)
        return [text]
    raise OpRefused("%s is not an op the engine knows" % type(op).__name__)


# ---- replies ------------------------------------------------------------------------------------

# A reply that starts like one of these did not take; the words are what OpResult.error says.
_ERRORS = [
    ("No player was found", "no player found"),
    ("No entity was found", "no entity found"),
    # "(target is either immune to effects, or has something stronger)": immunity is rare (a
    # wither or a dragon), and the core reads it as the stronger one's
    ("Unable to apply this effect", "has something stronger"),
    ("Unable to summon entity due to duplicate UUIDs", "an entity with that uuid already exists"),
    ("Unable to summon entity", "could not be summoned"),
    ("Target is invulnerable to the given damage type", "invulnerable to it (or still unhurtable after a hit)"),
    ("Unknown or incomplete command", "Minecraft did not understand the command"),
    ("Incorrect argument for command", "Minecraft did not understand the command"),
    ("Unknown item", "no such item"),
    ("Unknown particle", "no such particle"),
    ("That position is out of the world", "the position is outside the world"),
    ("That position is out of this world", "the position is outside the world"),
    ("That position is not loaded", "that part of the world is not loaded"),
    ("Too many blocks in the specified area", "too many blocks for one fill"),
    ("Too many chunks in the specified area", "too many chunks for one forceload"),
    ("Unknown block type", "no such block"),
    ("Unable to modify player data", "a player's data cannot be written"),
    ("No bossbar exists with the ID", "no such bossbar (give it a name to make it)"),
    ("Invalid position", "the position is outside the world"),
]
_MODIFIER_PRESENT = re.compile(r"^Modifier \S+ is already present on attribute")
_MODIFIER_MISSING = re.compile(r"^Attribute .+ has no modifier \S+$")
_NO_ATTRIBUTE = re.compile(r"^Entity .+ has no attribute ")
_UNKNOWN_ELEMENT = re.compile(r"^Can't find element '([^']+)' of type '([^']+)'")


def reply_error(reply: str) -> str | None:
    """The reason in words when a reply says a command did not take, else None."""
    r = reply.strip()
    for start, words in _ERRORS:
        if r.startswith(start):
            return words
    if _MODIFIER_PRESENT.match(r):
        return "already present"
    if _MODIFIER_MISSING.match(r):
        return "no modifier with that id (not present)"     # the core matches "no modifier"/"not present"
    if _NO_ATTRIBUTE.match(r):
        return "the target has no such attribute"
    m = _UNKNOWN_ELEMENT.match(r)
    if m:
        return "no %s %s" % (m.group(2).replace("minecraft:", "").replace("_", " "), m.group(1))
    if "<--[HERE]" in r:
        return "Minecraft did not understand the command: " + r.split("...", 1)[0][:160]
    return None


def result_for(op, reply: str) -> OpResult:
    """The OpResult of one op from its command's reply (for an op of several commands, the
    replies joined by " | ")."""
    if isinstance(op, (Message, Sound, Particle, StopSound, Bossbar)) and " | " in reply:
        bad = [b for b in (reply_error(r) for r in reply.split(" | ")) if b]
        return OpResult(not bad, reply, "; ".join(sorted(set(bad))) or None)
    if isinstance(op, Message):
        err = reply_error(reply)
        return OpResult(err is None, reply, err)
    err = reply_error(reply)
    if err is not None:
        return OpResult(False, reply, err)
    r = reply.strip()
    if isinstance(op, Remove):
        if r.endswith("returned 1"):
            return OpResult(True, reply)
        return OpResult(False, reply, "no such entity (gone already, dying, or a player: players are never removed)")
    if isinstance(op, TakeItem) and r.startswith("No items were found"):
        return OpResult(True, reply)          # they carry none: what a take is for
    if isinstance(op, (Summon, Teleport)) and r == "":
        return OpResult(False, reply, "Minecraft gave no reply (it did not take)")
    if isinstance(op, Gamerule) and not (r.startswith("Game rule") or r == ""):
        return OpResult(False, reply, "Minecraft did not set it")
    if isinstance(op, Ignite):
        if r.endswith("returned 1"):
            return OpResult(True, reply)
        return OpResult(False, reply, "no such target, or no air at a player's feet or head to light them from")
    if isinstance(op, SetBlock) and r.startswith("Could not set the block"):
        if op.mode == "keep":
            return OpResult(False, reply, "something is there already (keep)")
        return OpResult(True, reply)      # it is that block already
    return OpResult(True, reply)


def check_length(cmd: str) -> str | None:
    """Why a command cannot go over RCON (too long), or None."""
    size = len(cmd.encode("utf-8"))
    if size > MAX_COMMAND:
        return "the command is %d bytes, over the %d RCON takes" % (size, MAX_COMMAND)
    return None
