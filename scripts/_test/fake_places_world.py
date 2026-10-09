"""A small Minecraft world for tests, answering RCON commands with 26.3's real replies.

    world = FakeWorld(players={"Steve": (197.5, 68.0, 251.5)})
    FakeRcon(password, responder=world.respond)   # over RCON, for a subprocess
    places.get(world.respond, "castle")           # or called directly, for pure code

It knows what the actions' handlers and the places module ask: `list`, a player's Pos,
Dimension, respawn and LastDeathLocation, `locate` (answers set in `world.located`),
`spreadplayers` (landings scripted in `world.landings`, else the centre at y 64),
`execute in <dim> positioned <x y z> (if|unless) block ~ ~[-1|1] ~ ...` (blocks set in
`world.blocks`, else grass under and air above; the tags #campfires and #replaceable), `effect`, `tp`, `spawnpoint`, `summon`, tags and counts,
the command storage, and a few plain commands (time, weather, give, kill, difficulty).
Anything else is "Unknown or incomplete command", as Minecraft says it. Python 3.9.

Every command is kept in `world.sent`.
"""
import math
import re
import threading
from typing import Dict, List, Optional

import mc_26_3 as R

OVERWORLD = "minecraft:overworld"
MOBS = {"zombie": "Zombie", "skeleton": "Skeleton", "creeper": "Creeper", "cow": "Cow", "sheep": "Sheep",
        "wolf": "Wolf", "enderman": "Enderman", "witch": "Witch", "armor_stand": "Armor Stand"}
HOSTILE = {"zombie", "skeleton", "creeper", "enderman", "witch"}
ITEMS = {"diamond": "Diamond", "cooked_beef": "Cooked Beef", "golden_apple": "Golden Apple",
         "firework_rocket": "Firework Rocket"}
CAMPFIRES = {"minecraft:campfire", "minecraft:soul_campfire"}
REPLACEABLE = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:water", "minecraft:lava",
               "minecraft:short_grass", "minecraft:tall_grass", "minecraft:fern", "minecraft:snow",
               "minecraft:vine", "minecraft:seagrass", "minecraft:dead_bush", "minecraft:fire"}
TAGS = {"#minecraft:campfires": CAMPFIRES, "#minecraft:replaceable": REPLACEABLE}
_NUM = r"-?\d+(?:\.\d+)?"


def snbt_string(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def snbt(v):
    """Print a value the way Minecraft prints SNBT (keys in insertion order)."""
    if isinstance(v, dict):
        return "{" + ", ".join("%s: %s" % (k, snbt(x)) for k, x in v.items()) + "}"
    if isinstance(v, tuple) and v and v[0] == "I;":
        return "[I; " + ", ".join(str(x) for x in v[1:]) + "]"
    if isinstance(v, list):
        return "[" + ", ".join(snbt(x) for x in v) + "]"
    if isinstance(v, str):
        return snbt_string(v)
    return str(v)


class Player(object):
    def __init__(self, name, pos, dim=OVERWORLD, respawn=None, death=None):
        self.name = name
        self.pos = [float(c) for c in pos]
        self.dim = dim
        self.respawn = respawn   # (x, y, z, dim) or None
        self.death = death       # (x, y, z, dim) or None
        self.effects = []
        self.spawnpoint = None


class FakeWorld(object):
    def __init__(self, players=None, peaceful=False, world_spawn=(0, 72, 0)):
        self.players = {}  # type: Dict[str, Player]
        for name, pos in (players or {}).items():
            self.add(name, pos)
        self.peaceful = peaceful
        self.world_spawn = world_spawn
        self.located = {}          # (kind, "minecraft:x" or "#minecraft:x") -> (x, y, z[, found id]) | "missing" | "invalid"
        self.landings = []         # scripted spreadplayers results for players: (x, y, z) or "fail"
        self.blocks = {}           # (dim, x, y, z) -> block id
        self.storage = {}          # place name -> {"pos": [...], "by": str, "dim": str}
        self.entities = []         # summoned: {"type", "tags": set, "pos", "dim", "name"}
        self.spread_fail = 0       # how many entity spreadplayers fail before one works
        self.sent = []             # type: List[str]
        self.lock = threading.Lock()

    def add(self, name, pos, dim=OVERWORLD, respawn=None, death=None):
        self.players[name] = Player(name, pos, dim, respawn, death)
        return self.players[name]

    def block(self, dim, x, y, z, block):
        self.blocks[(dim, x, y, z)] = block

    def save_place(self, name, pos, dim=OVERWORLD, by=""):
        self.storage[name] = {"pos": list(pos), "by": by, "dim": dim}

    # ------------------------------------------------------------------ dispatch

    def respond(self, cmd):
        # type: (str) -> Optional[str]
        with self.lock:
            self.sent.append(cmd)
            return self._respond(cmd.strip(), OVERWORLD, None, cmd)

    def _respond(self, c, dim, at, full):
        m = re.match(r"^execute in (\S+) (.*)$", c)
        if m:
            return self._respond(m.group(2), m.group(1), at, full)
        m = re.match(r"^positioned (%s) (%s) (%s) (.*)$" % (_NUM, _NUM, _NUM), c)
        if m:
            p = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
            return self._respond(m.group(4), dim, p, full)
        if c.startswith("run "):
            return self._respond(c[4:], dim, at, full)
        if c.startswith("unless block ") or c.startswith("if block "):
            return self.ground_check(c, dim, at)
        if c.startswith("execute "):
            return self._execute(c, full)
        if c == "list":
            names = list(self.players)
            return R.LIST % (len(names), ", ".join(names)) if names else R.LIST_NOBODY
        for prefix, fn in (("data get entity ", self.data_get_entity), ("data get storage ", self.storage_get),
                           ("data modify storage ", self.storage_set), ("data remove storage ", self.storage_remove),
                           ("locate ", self.locate), ("spreadplayers ", self.spread), ("effect give ", self.effect),
                           ("tp ", self.tp), ("spawnpoint ", self.spawnpoint), ("summon ", self.summon),
                           ("tag ", self.tag), ("give ", self.give), ("kill ", self.kill)):
            if c.startswith(prefix):
                return fn(c[len(prefix):], dim, at)
        if c.startswith("time set "):
            return "Set minecraft:overworld to time marker minecraft:%s" % c.split()[-1]
        if c.startswith("weather "):
            return "Set the weather to %s" % c.split()[-1]
        if c.startswith("difficulty "):
            return "The difficulty did not change; it is already set to %s" % c.split()[-1].capitalize()
        if c.startswith("say "):
            return ""
        return "Unknown or incomplete command. See below for error%s<--[HERE]" % c[:10]

    def _execute(self, c, full):
        m = re.match(r"^execute if entity @e\[tag=([A-Za-z0-9_.+-]+)\]$", c)
        if m:
            n = sum(1 for e in self.entities if m.group(1) in e["tags"])
            return R.TEST_PASSED_COUNT % n if n else R.TEST_FAILED
        m = re.match(r"^execute (?:at|as) (\S+) run (.*)$", c)
        if m:
            # A forked execute: nothing when no one matched, and an inner failure prints nothing either.
            p = self.players.get(m.group(1))
            if p is None:
                return ""
            out = self._respond(m.group(2), p.dim, tuple(p.pos), full)
            return out if not self._is_failure(out) else ""
        return ""

    @staticmethod
    def _is_failure(out):
        return out.startswith(("No ", "Can't", "Could not", "Unable", "Monsters", "Unknown")) or "<--[HERE]" in out

    # ------------------------------------------------------------------ players

    def data_get_entity(self, rest, dim, at):
        who, _, path = rest.partition(" ")
        p = self.players.get(who)
        if p is None:
            return R.NO_ENTITY
        if path == "Pos":
            return R.ENTITY_DATA % (who, "[%sd, %sd, %sd]" % tuple(repr(c) for c in p.pos))
        if path == "Dimension":
            return R.ENTITY_DATA % (who, snbt_string(p.dim))
        if path == "respawn":
            if p.respawn is None:
                return R.NO_ELEMENTS % path
            x, y, z, d = p.respawn
            return R.ENTITY_DATA % (who, "{pos: [I; %d, %d, %d], forced: 0b, pitch: 0.0f, dimension: %s, yaw: 0.0f}"
                                    % (x, y, z, snbt_string(d)))
        if path == "LastDeathLocation":
            if p.death is None:
                return R.NO_ELEMENTS % path
            x, y, z, d = p.death
            return R.ENTITY_DATA % (who, "{dimension: %s, pos: [I; %d, %d, %d]}" % (snbt_string(d), x, y, z))
        return R.NO_ELEMENTS % path

    def effect(self, rest, dim, at):
        who, effect = rest.split()[0], rest.split()[1]
        targets = list(self.players.values()) if who == "@a" else [self.players[who]] if who in self.players else []
        if not targets:
            return R.NO_ENTITY
        for p in targets:
            p.effects.append(rest)
        nice = effect.split(":")[-1].replace("_", " ").title()
        return R.EFFECT % (nice, targets[0].name if len(targets) == 1 else "%d targets" % len(targets))

    def tp(self, rest, dim, at):
        parts = rest.split()
        p = self.players.get(parts[0])
        if p is None:
            return R.NO_ENTITY
        if len(parts) == 2:
            q = self.players.get(parts[1])
            if q is None:
                return R.NO_ENTITY
            p.pos, p.dim = list(q.pos), q.dim
            return R.TELEPORTED % (p.name, q.name)
        p.pos, p.dim = [float(v) for v in parts[1:4]], dim
        return R.TELEPORTED_TO % (p.name, p.pos[0], p.pos[1], p.pos[2])

    def spawnpoint(self, rest, dim, at):
        parts = rest.split()
        p = self.players.get(parts[0])
        if p is None:
            return R.NO_PLAYER
        x, y, z = [int(v) for v in parts[1:4]]
        p.spawnpoint = (x, y, z, dim)
        p.respawn = (x, y, z, dim)
        return R.SPAWNPOINT % (x, y, z, dim, p.name)

    # ------------------------------------------------------------------ locate and land

    def locate(self, rest, dim, at):
        kind, ident = rest.split()
        v = self.located.get((kind, ident))
        if v is None or v == "missing":
            return (R.LOCATE_BIOME_NOT_FOUND if kind == "biome" else R.LOCATE_STRUCTURE_NOT_FOUND) % ident
        if v == "invalid":
            if kind == "structure":
                return R.LOCATE_STRUCTURE_INVALID % ident
            return "Can't find element '%s' of type 'minecraft:worldgen/biome'...%s<--[HERE]" % (ident, ident[-10:])
        x, y, z = v[:3]
        found = v[3] if len(v) > 3 else None
        ax, az = (at[0], at[2]) if at else (0, 0)
        d = int(math.hypot(x - ax, z - az))
        ys = "~" if kind == "structure" else str(y)
        if ident.startswith("#"):
            return R.LOCATE_FOUND_TAG % (ident, found or ident.replace("#", ""), x, ys, z, d)
        return R.LOCATE_FOUND % (ident, x, ys, z, d)

    def spread(self, rest, dim, at):
        m = re.match(r"^(\S+) (\S+) (\d+) (\d+) under (-?\d+) false (\S+)$", rest)
        if not m:
            return "Incorrect argument for commandspreadplayers %s<--[HERE]" % rest[:20]
        sx, sz = m.group(1), m.group(2)
        if sx == "~":
            cx, cz = float(self.world_spawn[0]), float(self.world_spawn[2])
        else:
            cx, cz = float(sx), float(sz)
            if "." not in sx:
                cx += 0.5
            if "." not in sz:
                cz += 0.5
        target = m.group(6)
        tm = re.match(r"^@e\[tag=([A-Za-z0-9_.+-]+)\]$", target)
        if tm:
            batch = [e for e in self.entities if tm.group(1) in e["tags"]]
            if not batch:
                return R.NO_ENTITY
            if self.spread_fail > 0:
                self.spread_fail -= 1
                return R.SPREAD_FAILED % (len(batch), cx, cz)
            r = int(m.group(4))
            for i, e in enumerate(batch):
                a = i * 2 * math.pi / len(batch)
                e["pos"] = [math.floor(cx + r * 0.7 * math.cos(a)) + 0.5, 64.0, math.floor(cz + r * 0.7 * math.sin(a)) + 0.5]
                e["dim"] = dim
            return R.SPREAD % (len(batch), cx, cz, "%.2f" % (r * 0.7))
        p = self.players.get(target)
        if p is None:
            return R.NO_ENTITY
        landing = self.landings.pop(0) if self.landings else (math.floor(cx), 64, math.floor(cz))
        if landing == "fail":
            return R.SPREAD_FAILED % (1, cx, cz)
        p.pos = [math.floor(landing[0]) + 0.5, float(landing[1]), math.floor(landing[2]) + 0.5]
        p.dim = dim
        return R.SPREAD % (1, cx, cz, "0.00")

    def ground_check(self, rest, dim, at):
        if at is None:
            return R.TEST_PASSED
        x, y, z = int(math.floor(at[0])), int(math.floor(at[1])), int(math.floor(at[2]))
        for word, off, block in re.findall(r"(if|unless) block ~ ~(-?\d+)? ~ (\S+)", rest):
            dy = int(off or 0)
            here = self.blocks.get((dim, x, y + dy, z), "minecraft:grass_block" if dy < 0 else "minecraft:air")
            matches = here in TAGS[block] if block in TAGS else here == block
            if (word == "unless" and matches) or (word == "if" and not matches):
                return R.TEST_FAILED
        return R.TEST_PASSED

    # ------------------------------------------------------------------ mobs

    def summon(self, rest, dim, at):
        m = re.match(r"^minecraft:([a-z0-9_]+) ~ ~ ~(?: (\{.*\}))?$", rest)
        if not m:
            return "Incorrect argument for commandsummon %s<--[HERE]" % rest[:20]
        mob, nbt = m.group(1), m.group(2) or ""
        if mob == "player":
            return R.SUMMON_PLAYER
        if mob not in MOBS:
            return R.SUMMON_UNKNOWN % (mob, mob)
        if self.peaceful and mob in HOSTILE:
            return R.SUMMON_PEACEFUL
        tags = set(re.findall(r'"([A-Za-z0-9_.+-]+)"', re.search(r"Tags:\[([^\]]*)\]", nbt).group(1))) \
            if "Tags:[" in nbt else set()
        nm = re.search(r'CustomName:"((?:[^"\\]|\\.)*)"', nbt)
        name = nm.group(1).replace('\\"', '"').replace("\\\\", "\\") if nm else None
        self.entities.append({"type": mob, "tags": tags, "pos": list(at or (0, 64, 0)), "dim": dim, "name": name,
                              "nbt": nbt})
        return R.SUMMONED % (name or MOBS[mob])

    def tag(self, rest, dim, at):
        m = re.match(r"^@e\[tag=([A-Za-z0-9_.+-]+)\] remove (\S+)$", rest)
        if not m:
            return "Incorrect argument for commandtag %s<--[HERE]" % rest[:20]
        hit = [e for e in self.entities if m.group(1) in e["tags"]]
        if not hit:
            return R.NO_ENTITY
        for e in hit:
            e["tags"].discard(m.group(2))
        return R.TAG_REMOVED % (m.group(2), len(hit))

    def kill(self, rest, dim, at):
        m = re.match(r"^@e\[tag=([A-Za-z0-9_.+-]+)\]$", rest)
        hit = [e for e in self.entities if m and m.group(1) in e["tags"]]
        if not hit:
            return R.NO_ENTITY
        self.entities = [e for e in self.entities if e not in hit]
        return R.KILLED % len(hit)

    def give(self, rest, dim, at):
        parts = rest.split()
        if len(parts) < 2:
            return "Unknown or incomplete command. See below for errorgive<--[HERE]"
        item = re.split(r"[\[{]", parts[1])[0].split(":")[-1]  # minecraft:x[components] is x
        if item not in ITEMS:
            return "Unknown item '%s'give %s<--[HERE]" % (parts[1], rest)
        n = parts[2] if len(parts) > 2 else "1"
        if not n.isdigit():
            return "Expected integer...%s<--[HERE]" % rest[-12:]
        who = list(self.players) if parts[0] == "@a" else [parts[0]] if parts[0] in self.players else []
        if not who:
            return R.NO_PLAYER
        if len(who) == 1:
            return "Gave %s [%s] to %s" % (n, ITEMS[item], who[0])
        return "Gave %s [%s] to %d players" % (n, ITEMS[item], len(who))

    # ------------------------------------------------------------------ storage

    def storage_get(self, rest, dim, at):
        parts = rest.split()
        if parts[0] != "everhear:places":
            return "Storage %s has the following contents: {}" % parts[0]
        if len(parts) == 1:
            return R.STORAGE % snbt(self.storage)
        v = self.storage.get(parts[1])
        if v is None:
            return R.NO_ELEMENTS % parts[1]
        return R.STORAGE % snbt(v)

    def storage_set(self, rest, dim, at):
        m = re.match(r'^everhear:places ([a-z0-9_]+) set value \{pos:\[(-?\d+),(-?\d+),(-?\d+)\],dim:"([^"]*)",'
                     r'by:"([^"]*)"\}$', rest)
        if not m:
            return "Expected compound tag...%s<--[HERE]" % rest[-12:]
        v = {"pos": [int(m.group(2)), int(m.group(3)), int(m.group(4))], "by": m.group(6), "dim": m.group(5)}
        if self.storage.get(m.group(1)) == v:
            return R.NOTHING_CHANGED
        self.storage[m.group(1)] = v
        return R.STORAGE_MODIFIED

    def storage_remove(self, rest, dim, at):
        parts = rest.split()
        if len(parts) == 2 and parts[0] == "everhear:places" and parts[1] in self.storage:
            del self.storage[parts[1]]
            return R.STORAGE_MODIFIED
        return R.NOTHING_CHANGED
