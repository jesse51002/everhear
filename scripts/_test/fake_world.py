"""A small fake Minecraft world behind fake_rcon.FakeRcon, for the kit's tests.

    world = World()
    world.add_player("Steve", (10.5, 64.0, -3.5), prefix="[Owner] ")
    fake = FakeRcon(password, responder=world.respond).start()

It answers the commands the kit sends the way 26.3 does, closely enough to test the kit's
plumbing: list, data get entity, scoreboards, execute chains (in, as, at, positioned, if and
unless block, loaded, entity, items, data; run), give, clear, fill (with the 32,768 limit and
"That position is not loaded"), forceload, bossbars, gamerules, tick query, summon, kill and
tag. Blocks below `ground` are solid, and `solid` adds more; chunks in `unloaded` are not
loaded. What it was told is kept in `fills`, `gives`, `bossbars`, `forced`, `gamerules`.
Anything it does not know gets "" (the default answer of fake_rcon is not used).
"""
import math
import re
import threading

OVERWORLD = "minecraft:overworld"
LIMITS = {OVERWORLD: (-64, 319), "minecraft:the_nether": (0, 255), "minecraft:the_end": (0, 255)}
NOT_LOADED = "That position is not loaded"
OUT = "That position is out of the world"


class Error(Exception):
    pass


def _split_opts(s):
    """Selector options split on commas outside braces and brackets."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return [o.strip() for o in out if o.strip()]


def _token(s):
    """The first whitespace-delimited argument of s, keeping [...] and {...} whole."""
    s = s.lstrip()
    depth, i = 0, 0
    while i < len(s):
        ch = s[i]
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == " " and depth == 0:
            break
        i += 1
    return s[:i], s[i:].lstrip()


class World(object):
    def __init__(self, ground=64):
        self.lock = threading.RLock()
        self.ground = ground                 # every block with y < ground is solid
        self.solid, self.air = set(), set()  # (dim, x, y, z)
        self.unloaded = set()                # (dim, cx, cz)
        self.players = {}
        self.entities = []
        self.objectives = {}
        self.scores = {}
        self.fills, self.gives, self.clears, self.commands = [], [], [], []
        self.bossbars = {}
        self.bossbars_removed = []
        self.forced = set()
        self.gamerules = {"mob_griefing": "true", "fall_damage": "true", "keep_inventory": "false",
                          "advance_time": "true", "pvp": "true"}
        self.mspt = 12.0
        self.next_id = 1
        self.no_negation = False             # True: refuse "!" in an item predicate, as an older server would

    # ---- setting the world up ----

    def add_player(self, name, pos, rot=(0.0, 0.0), dim=OVERWORLD, prefix="", gamemode="survival"):
        with self.lock:
            self.players[name] = {"type": "player", "name": name, "pos": list(pos), "rot": list(rot), "dim": dim,
                                  "prefix": prefix, "gamemode": gamemode, "health": 20.0, "tags": set(),
                                  "main": None, "off": None, "inv": []}
            return self.players[name]

    def add_entity(self, type_, pos, dim=OVERWORLD, tags=(), health=20.0, name=None, owner=None):
        with self.lock:
            e = {"type": type_, "pos": list(pos), "dim": dim, "tags": set(tags), "health": health,
                 "name": name or type_.replace("_", " ").title(), "owner": owner, "id": self.next_id, "rot": [0.0, 0.0]}
            self.next_id += 1
            self.entities.append(e)
            return e

    def hold(self, name, main=None, off=None):
        """Put items (their vfg_item id, "plain" for a carrot on a stick with none, or None) in a player's hands."""
        with self.lock:
            p = self.players[name]
            p["main"] = self._carrot(main) if main else None
            p["off"] = self._carrot(off) if off else None

    @staticmethod
    def _carrot(data):
        return {"id": "minecraft:carrot_on_a_stick", "data": None if data == "plain" else data}

    def click(self, name, n=1):
        """A right-click with whatever is held: every used:carrot_on_a_stick objective goes up."""
        with self.lock:
            p = self.players[name]
            carrots = sum(1 for h in (p["main"], p["off"]) if h and h["id"].endswith("carrot_on_a_stick"))
            if not carrots:
                return
            for obj, crit in self.objectives.items():
                if crit == "minecraft.used:minecraft.carrot_on_a_stick":
                    self.scores[(obj, name)] = self.scores.get((obj, name), 0) + n * carrots

    def sneak(self, name, ticks=2):
        with self.lock:
            for obj, crit in self.objectives.items():
                if crit == "minecraft.custom:minecraft.sneak_time":
                    self.scores[(obj, name)] = self.scores.get((obj, name), 0) + ticks

    # ---- blocks and chunks ----

    def is_loaded(self, dim, x, z):
        cx, cz = int(math.floor(x)) >> 4, int(math.floor(z)) >> 4
        return (dim, cx, cz) not in self.unloaded or (dim, cx, cz) in self.forced

    def is_solid(self, dim, x, y, z):
        c = (dim, x, y, z)
        return c not in self.air and (c in self.solid or y < self.ground)

    def _block_at(self, dim, x, y, z):
        lo, hi = LIMITS.get(dim, LIMITS[OVERWORLD])
        if not lo <= y <= hi:
            raise Error(OUT)
        if not self.is_loaded(dim, x, z):
            raise Error(NOT_LOADED)
        return self.is_solid(dim, x, y, z)

    # ---- selectors ----

    def _all(self):
        return list(self.players.values()) + self.entities

    def _select(self, sel, ctx):
        """The entities a selector names, in the context (executor, dim, pos)."""
        executor, dim, pos = ctx
        m = re.match(r"^(@[aeprs])(?:\[(.*)\])?$", sel)
        if not m:
            p = self.players.get(sel)
            return [p] if p else []
        kind, opts = m.group(1), _split_opts(m.group(2) or "")
        if kind == "@s":
            cands = [executor] if executor else []
        elif kind in ("@a", "@p", "@r"):
            cands = list(self.players.values())
        else:
            cands = self._all()
        origin = list(pos)
        limit, sort = None, None
        for o in opts:
            k, _, v = o.partition("=")
            if k in ("x", "y", "z"):
                origin["xyz".index(k)] = float(v)
        out = []
        for e in cands:
            ok = True
            for o in opts:
                k, _, v = o.partition("=")
                neg = v.startswith("!")
                v = v[1:] if neg else v
                if k == "type":
                    if kind == "@a":
                        raise Error("Option 'type' isn't applicable here")
                    t = v.replace("minecraft:", "")
                    if t in ("potion", "boat", "chest_boat"):
                        raise Error("Invalid or unknown entity type 'minecraft:%s'" % t)
                    hit = e["type"] == t
                    ok = ok and (hit != neg)
                elif k == "tag":
                    hit = v in e["tags"]
                    ok = ok and (hit != neg)
                elif k == "name":
                    hit = e.get("name") == v
                    ok = ok and (hit != neg)
                elif k == "gamemode":
                    hit = e.get("gamemode") == v
                    ok = ok and (hit != neg)
                elif k == "distance":
                    if e["dim"] != dim:
                        ok = False
                        continue
                    d = math.sqrt(sum((e["pos"][i] - origin[i]) ** 2 for i in range(3)))
                    lo, _, hi = v.partition("..")
                    if lo and d < float(lo):
                        ok = False
                    if hi and d > float(hi):
                        ok = False
                elif k == "limit":
                    limit = int(v)
                elif k == "sort":
                    sort = v
            if ok:
                out.append(e)
        if sort == "nearest":
            out.sort(key=lambda e: sum((e["pos"][i] - origin[i]) ** 2 for i in range(3)))
        if limit is not None:
            out = out[:limit]
        return out

    def _coords(self, s, pos):
        out = []
        for i in range(3):
            tok, s = _token(s)
            if tok.startswith("~"):
                out.append(pos[i] + (float(tok[1:]) if tok[1:] else 0.0))
            elif tok.startswith("^"):
                raise Error("Local coordinates are not supported by this fake")
            else:
                out.append(float(tok))
        return out, s

    # ---- items ----

    def _item_matches(self, item, pred):
        if self.no_negation and "[!" in pred:
            raise Error("Unknown item component '!custom_data'...%s<--[HERE]" % pred[:40])
        if item is None:
            return False
        m = re.match(r"^(\*|[a-z0-9_:]+)(?:\[(.*)\])?$", pred)
        if not m:
            raise Error("Unknown item predicate %s" % pred)
        base, tests = m.group(1), m.group(2)
        if base != "*" and base.replace("minecraft:", "") != item["id"].replace("minecraft:", ""):
            return False
        if tests:
            t = re.match(r'^(!?)custom_data~\{vfg_item:"([^"]*)"\}$', tests)
            if not t:
                raise Error("Unknown item test %s" % tests)
            hit = item.get("data") == t.group(2)
            return hit != (t.group(1) == "!")
        return True

    def _slots(self, e, slots):
        if e["type"] != "player":
            return []
        if slots == "weapon.*":
            return [e["main"], e["off"]]
        if slots == "weapon.mainhand":
            return [e["main"]]
        if slots == "weapon.offhand":
            return [e["off"]]
        if slots == "container.*":
            return [e["main"]] + e["inv"]
        raise Error("Unknown slot %s" % slots)

    # ---- execute ----

    def _execute(self, rest, ctxs):
        """Runs the subcommands; returns (the command after run or None, contexts left, the
        last condition's kind)."""
        last = None
        while True:
            word, rest = _token(rest)
            if word == "":
                return None, ctxs, last
            if word == "run":
                return rest, ctxs, last
            if word == "in":
                d, rest = _token(rest)
                ctxs = [(e, d, p) for e, _, p in ctxs]
            elif word == "as":
                sel, rest = _token(rest)
                ctxs = [(x, d, p) for c in ctxs for (e, d, p) in [c] for x in self._select(sel, c)]
            elif word == "at":
                sel, rest = _token(rest)
                ctxs = [(e, x["dim"], list(x["pos"])) for c in ctxs for (e, d, p) in [c] for x in self._select(sel, c)]
            elif word == "positioned":
                nxt = []
                coords_rest = rest
                for e, d, p in ctxs:
                    xyz, coords_rest = self._coords(rest, p)
                    nxt.append((e, d, xyz))
                if not ctxs:
                    _, coords_rest = self._coords(rest, [0, 0, 0])
                rest, ctxs = coords_rest, nxt
            elif word in ("if", "unless"):
                kind, rest = _token(rest)
                last = kind
                want = word == "if"
                keep = []
                if kind == "block":
                    after = rest
                    for c in ctxs:
                        xyz, after = self._coords(rest, c[2])
                        pred, after = _token(after)
                        x, y, z = (int(math.floor(v)) for v in xyz)
                        solid = self._block_at(c[1], x, y, z)
                        ok = (not solid) if pred == "#minecraft:replaceable" else solid
                        if ok == want:
                            keep.append(c)
                    if not ctxs:
                        _, after = self._coords(rest, [0, 0, 0])
                        _, after = _token(after)
                    rest = after
                elif kind == "loaded":
                    after = rest
                    for c in ctxs:
                        xyz, after = self._coords(rest, c[2])
                        if self.is_loaded(c[1], xyz[0], xyz[2]) == want:
                            keep.append(c)
                    if not ctxs:
                        _, after = self._coords(rest, [0, 0, 0])
                    rest = after
                elif kind == "entity":
                    sel, rest = _token(rest)
                    found = [len(self._select(sel, c)) for c in ctxs]
                    keep = [c for c, n in zip(ctxs, found) if bool(n) == want]
                    self._found = sum(found)
                elif kind == "items":
                    _, rest = _token(rest)  # entity
                    target, rest = _token(rest)
                    slots, rest = _token(rest)
                    pred, rest = _token(rest)
                    for c in ctxs:
                        es = self._select(target, c)
                        hit = any(self._item_matches(i, pred) for e in es for i in self._slots(e, slots))
                        if hit == want:
                            keep.append(c)
                elif kind == "data":
                    _, rest = _token(rest)  # entity
                    target, rest = _token(rest)
                    path, rest = _token(rest)
                    for c in ctxs:
                        es = self._select(target, c)
                        e = es[0] if es else None
                        if path == "Health":
                            hit = e is not None and e.get("health") is not None
                        elif path == "Owner":
                            hit = e is not None and bool(e.get("owner"))
                        else:
                            hit = e is not None
                        if hit == want:
                            keep.append(c)
                else:
                    raise Error("Unknown execute condition %s" % kind)
                ctxs = keep
            else:
                raise Error("Unknown execute word %s" % word)

    # ---- commands ----

    def respond(self, cmd):
        with self.lock:
            self.commands.append(cmd)
            try:
                return self._run(cmd, (None, OVERWORLD, [0.0, 0.0, 0.0]))
            except Error as e:
                return str(e)

    def _run(self, cmd, ctx):
        cmd = cmd.strip()
        if cmd.startswith("execute "):
            rest, ctxs, last = self._execute(cmd[len("execute "):], [ctx])
            if rest is None:
                if last == "entity":
                    return "Test passed, count: %d" % self._found if ctxs else "Test failed"
                return "Test passed" if ctxs else "Test failed"
            return "".join(self._run(rest, c) for c in ctxs)
        word = cmd.split(" ", 1)[0]
        args = cmd[len(word):].strip()
        fn = getattr(self, "_cmd_" + word, None)
        return fn(args, ctx) if fn else ""

    def _cmd_list(self, args, ctx):
        names = [p["prefix"] + n for n, p in self.players.items()]
        return "There are %d of a max of 20 players online: %s" % (len(names), ", ".join(names))

    def _cmd_tick(self, args, ctx):
        return ("Average time per tick: %.1fms (Target: 50.0ms)Percentiles: P50: %.1fms P95: %.1fms P99: %.1fms. "
                "Sample: 100" % (self.mspt, self.mspt, self.mspt, self.mspt))

    def _cmd_data(self, args, ctx):
        m = re.match(r"get entity (\S+) (\S+)$", args)
        if not m:
            return ""
        es = self._select(m.group(1), ctx)
        if not es:
            return "No entity was found"
        e, path = es[0], m.group(2)
        who = e["prefix"] + e["name"] if e["type"] == "player" else e["name"]
        if path == "Pos":
            return "%s has the following entity data: [%rd, %rd, %rd]" % (who, e["pos"][0], e["pos"][1], e["pos"][2])
        if path == "Rotation":
            return "%s has the following entity data: [%rf, %rf]" % (who, e["rot"][0], e["rot"][1])
        if path == "Dimension":
            return '%s has the following entity data: "%s"' % (who, e["dim"])
        if path == "Health" and e.get("health") is not None:
            return "%s has the following entity data: %rf" % (who, e["health"])
        return "Found no elements matching %s" % path

    def _cmd_scoreboard(self, args, ctx):
        m = re.match(r"objectives add (\S+) (\S+)", args)
        if m:
            if m.group(1) in self.objectives:
                return "An objective already exists by that name"
            self.objectives[m.group(1)] = m.group(2)
            return "Created new objective [%s]" % m.group(1)
        m = re.match(r"objectives remove (\S+)", args)
        if m:
            if m.group(1) not in self.objectives:
                return "Unknown scoreboard objective '%s'" % m.group(1)
            del self.objectives[m.group(1)]
            for k in [k for k in self.scores if k[0] == m.group(1)]:
                del self.scores[k]
            return "Removed objective [%s]" % m.group(1)
        m = re.match(r"players get (\S+) (\S+)", args)
        if m:
            obj = m.group(2)
            if obj not in self.objectives:
                return "Unknown scoreboard objective '%s'" % obj
            for e in self._select(m.group(1), ctx):
                name = e["name"]
                if (obj, name) in self.scores:
                    return "%s has %d [%s]" % (name, self.scores[(obj, name)], obj)
                return "Can't get value of %s for %s; none is set" % (obj, name)
            return "No entity was found"
        m = re.match(r"players set (\S+) (\S+) (-?\d+)", args)
        if m:
            for e in self._select(m.group(1), ctx):
                self.scores[(m.group(2), e["name"])] = int(m.group(3))
            return "Set [%s] for %s to %s" % (m.group(2), m.group(1), m.group(3))
        return ""

    def _cmd_give(self, args, ctx):
        target, item = _token(args)
        out = ""
        for e in self._select(target, ctx):
            m = re.search(r'vfg_item:"([^"]*)"', item)
            got = self._carrot(m.group(1) if m else "plain")
            e["inv"].append(got)
            self.gives.append((e["name"], item))
            out += "Gave 1 [%s] to %s" % (m.group(1) if m else item, e["prefix"] + e["name"])
        return out

    def _cmd_clear(self, args, ctx):
        target, pred = _token(args)
        out = ""
        for e in self._select(target, ctx):
            n = 0
            for slot in ("main", "off"):
                if self._item_matches(e[slot], pred):
                    e[slot] = None
                    n += 1
            keep = [i for i in e["inv"] if not self._item_matches(i, pred)]
            n += len(e["inv"]) - len(keep)
            e["inv"] = keep
            self.clears.append((e["name"], pred))
            out += ("Removed %d item(s) from player %s" % (n, e["name"])) if n else "No items were found on player %s" % e["name"]
        return out

    def _cmd_fill(self, args, ctx):
        xyz1, rest = self._coords(args, ctx[2])
        xyz2, rest = self._coords(rest, ctx[2])
        block, mode = _token(rest)
        a = [int(math.floor(v)) for v in xyz1]
        b = [int(math.floor(v)) for v in xyz2]
        lo = [min(a[i], b[i]) for i in range(3)]
        hi = [max(a[i], b[i]) for i in range(3)]
        vol = (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)
        if vol > 32768:
            return "Too many blocks in the specified area (maximum 32768, but specified %d)" % vol
        ylo, yhi = LIMITS.get(ctx[1], LIMITS[OVERWORLD])
        if lo[1] < ylo or hi[1] > yhi:
            return OUT
        for cx in range(lo[0] >> 4, (hi[0] >> 4) + 1):
            for cz in range(lo[2] >> 4, (hi[2] >> 4) + 1):
                if not self.is_loaded(ctx[1], cx * 16, cz * 16):
                    return NOT_LOADED
        self.fills.append((ctx[1], tuple(lo + hi), block, mode.strip()))
        return "Successfully filled %d block(s)" % vol

    def _cmd_forceload(self, args, ctx):
        dim = ctx[1]
        if args == "query":
            mine = sorted((cx, cz) for d, cx, cz in self.forced if d == dim)
            if not mine:
                return "No force loaded chunks were found in %s" % dim
            return "%d force loaded chunks were found in %s at: %s" % (
                len(mine), dim, ", ".join("[%d, %d]" % c for c in mine))
        m = re.match(r"query (-?\d+) (-?\d+)$", args)
        if m:
            c = (dim, int(m.group(1)) >> 4, int(m.group(2)) >> 4)
            return "Chunk at [%d, %d] in %s is %smarked for force loading" % (c[1], c[2], dim, "" if c in self.forced else "not ")
        m = re.match(r"(add|remove) (-?\d+) (-?\d+)(?: (-?\d+) (-?\d+))?$", args)
        if m:
            x1, z1 = int(m.group(2)), int(m.group(3))
            x2, z2 = (int(m.group(4)), int(m.group(5))) if m.group(4) else (x1, z1)
            cs = [(dim, cx, cz) for cx in range(min(x1, x2) >> 4, (max(x1, x2) >> 4) + 1)
                  for cz in range(min(z1, z2) >> 4, (max(z1, z2) >> 4) + 1)]
            if len(cs) > 256:
                return "Too many chunks in the specified area (maximum 256, but specified %d)" % len(cs)
            if m.group(1) == "add":
                self.forced.update(cs)
                return "Marked %d chunks in %s to be force loaded" % (len(cs), dim)
            self.forced.difference_update(cs)
            return "Unmarked %d chunks in %s for force loading" % (len(cs), dim)
        return ""

    def _cmd_bossbar(self, args, ctx):
        m = re.match(r"add (\S+) (.*)$", args)
        if m:
            if m.group(1) in self.bossbars:
                return "A bossbar already exists with the ID '%s'" % m.group(1)
            self.bossbars[m.group(1)] = {"name": m.group(2), "value": 0, "max": 100, "players": None}
            return "Created custom bossbar [%s]" % m.group(2)
        m = re.match(r"set (\S+) (\S+) (.*)$", args)
        if m:
            bar = self.bossbars.get(m.group(1))
            if bar is None:
                return "No bossbar exists with the ID '%s'" % m.group(1)
            bar[m.group(2)] = int(m.group(3)) if m.group(2) in ("value", "max") else m.group(3)
            return "Custom bossbar has changed"
        m = re.match(r"remove (\S+)$", args)
        if m:
            if m.group(1) not in self.bossbars:
                return "No bossbar exists with the ID '%s'" % m.group(1)
            del self.bossbars[m.group(1)]
            self.bossbars_removed.append(m.group(1))
            return "Removed custom bossbar"
        return ""

    def _cmd_gamerule(self, args, ctx):
        parts = args.split()
        name = parts[0].replace("minecraft:", "")
        if name not in self.gamerules:
            return "Incorrect argument for command...gamerule %s<--[HERE]" % name
        if len(parts) == 1:
            return "Game rule %s is currently set to %s" % (name, self.gamerules[name])
        self.gamerules[name] = parts[1]
        return "Game rule %s is now set to %s" % (name, parts[1])

    def _cmd_summon(self, args, ctx):
        type_, rest = _token(args)
        xyz, rest = self._coords(rest, ctx[2]) if rest and not rest.startswith("{") else (list(ctx[2]), rest)
        m = re.search(r"Tags:\[([^\]]*)\]", rest)
        tags = re.findall(r'"([^"]+)"', m.group(1)) if m else []
        h = re.search(r"Health:([\d.]+)f", rest)
        name = re.search(r'CustomName:"([^"]*)"', rest)
        self.add_entity(type_.replace("minecraft:", ""), xyz, ctx[1], tags, float(h.group(1)) if h else 20.0,
                        name.group(1) if name else None)
        return "Summoned new %s" % (name.group(1) if name else type_)

    def _cmd_kill(self, args, ctx):
        es = [e for e in self._select(args.strip(), ctx) if e["type"] != "player"]
        if not es:
            return "No entity was found"
        self.entities = [e for e in self.entities if e not in es]
        return "Killed %d entities" % len(es) if len(es) > 1 else "Killed %s" % es[0]["name"]

    def _cmd_tag(self, args, ctx):
        sel, rest = _token(args)
        op, rest = _token(rest)
        tag = rest.strip()
        es = self._select(sel, ctx)
        for e in es:
            (e["tags"].add if op == "add" else e["tags"].discard)(tag)
        return "Added tag '%s' to %d entities" % (tag, len(es)) if es else "No entity was found"

    def _cmd_damage(self, args, ctx):
        sel, rest = _token(args)
        amount, _ = _token(rest)
        es = self._select(sel, ctx)
        for e in es:
            if e.get("health") is not None:
                e["health"] = max(0.0, e["health"] - float(amount))
        return "Applied %s damage to %s" % (amount, es[0]["name"]) if es else "No entity was found"
