"""McIo: the engine's Io (vfg_engine.io) over RCON to a vanilla 26.3 server with the EverHear
datapack (the game engine spec: "the datapack", "Reading the world without waiting", the writer).

    io = McIo()                  # VFG_MC_PROPERTIES (else /opt/mc/server.properties) names the server
    io.open()                    # connects, checks the datapack and its version, starts the mirror
    io.events()                  # every event since the last call, in order, each handed over once
    io.snapshot(track=[uuid])    # every player, with their raycast, and the tracked entities
    io.flush(ops)                # every op in order, as one batch under the RCON lock
    io.close()

Per tick the engine pays two RCON exchanges, the spec's "one RCON read": events() runs
`function everhear:io/tick {upto:N}`, which takes off what was read, copies the queue's next page
and builds the snapshot, and reads both with `data get storage everhear:out`; snapshot() then uses
that snapshot, taken a moment before, when it asks for the same tracked entities (a changed list
costs one command to set it and the two of a snapshot of its own). A backlog of more than a page
(12 events) costs one more pair per page. Measured costs are in engine/MINECRAFT.md.

The queue keeps an event until the engine says it has it: each read takes off the events numbered up
to the last one already read, so an event read is taken off on the next call. Events are deduped by
number, so a page read twice (a reconnect between the two exchanges) is handed over once. Across a
restart of the engine an event read but not yet taken can come again (at most a page); close()
takes everything read first, so a clean stop hands nothing over twice.

The reads on demand (part 2: probe, ground_y, ray, entities, loaded, mspt, world_id) are each one
datapack function and one `data get` at most; watch_mined sets which blocks' mining is an event.

Errors: RCON being down raises RconError (an OSError, in words) from events, snapshot, query,
read_modifiers and read_gamerule; flush never raises for it, and says per op what did not go.
A datapack missing or of another version raises DatapackError from open().

Standard library only, besides vfg_engine.io.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import re
import time
from typing import Iterable, Sequence

from ..io import IoDeadline, Look, OpResult, Snapshot
from . import commands, datapack, reading, snbt
from .mirror import McMirror
from .rcon import PROPERTIES, Rcon, RconDeadline, RconError

log = logging.getLogger("vfg_engine.mc")

PAGE = 12                  # events per page: everhear:io/events copies this many
MAX_PAGES = 50             # pages per events() call (600 events); the rest wait for the next tick
RAY_RANGE = float(os.environ.get("VFG_ENGINE_RAY_BLOCKS") or 48)
MIRROR_RADIUS = int(float(os.environ.get("VFG_ENGINE_MIRROR_RADIUS") or 32))
MIRROR_INTERVAL = float(os.environ.get("VFG_ENGINE_MIRROR_S") or 0.1)
MIRROR_BUDGET = float(os.environ.get("VFG_ENGINE_MIRROR_BUDGET") or 0.1)
_TRACK_CHUNK = 24          # tracked uuids per command (each {u:"…"} is 45 bytes; a command 1446)
_GAMERULE = re.compile(r"^Game rule (\S+) is (?:currently|now|already) set to:? (.+)$")
_RETURNED = re.compile(r"returned (-?\d+)$")
_MSPT = re.compile(r"Average time per tick: ([\d.]+) ?ms")
# probe's named tests: (the datapack's test function, its block or tag)
PROBE_TESTS = {"solid": ("not", "#everhear:not_solid"), "open": ("two", "#everhear:open")}
_PROBE_CHUNK = 50          # points per command ({x:…,y:…,z:…} each, under RCON's 1446 bytes)
RAY_LONGEST = 1000.0       # blocks: a ray of 2000 steps
LOOKS_KEPT = 5             # snapshots' looks kept per player for a mine's place (half a second)
NOT_LOADED = -99999        # everhear:io/ground's answer for a chunk not loaded


class McIo:
    """vfg_engine.io.Io for a real server. One per engine; thread-safe where the contract needs it
    (the mirror runs on its own thread and shares the connection)."""

    def __init__(self, properties: str = PROPERTIES, *, ray_range: float = RAY_RANGE,
                 mirror_radius: int = MIRROR_RADIUS, mirror_interval: float = MIRROR_INTERVAL,
                 mirror_budget: float = MIRROR_BUDGET, mirror: bool = True, last_n: int = 0,
                 rcon: Rcon | None = None):
        self.rcon = rcon or Rcon(properties)
        self.ray_range = float(ray_range)
        self.tables = reading.Tables()
        self.names = reading.Names()
        self.last_n = int(last_n)          # the last event number handed over
        self.overflowed = 0                # the datapack's #over when last read
        self.queue_len = 0
        self._track_sent: tuple[str, ...] | None = None
        self._track_connects = -1
        self._ahead = None                 # (snapshot doc, tracked list, monotonic time) from events()
        self._looks: dict[str, list] = {}   # player -> [(block id, block)], their last few snapshots' looks
        self._mspt = (0.0, None)           # (monotonic time, value): tick query at most once a second
        self._world = (None, None)         # (connects, id)
        self.mined: tuple[str, ...] = ()   # the block kinds watch_mined set
        self._mined_on = None              # (connection, world id) they were set on
        self._opened = False
        self._mirror = McMirror(self, interval=mirror_interval, radius=mirror_radius, budget=mirror_budget)
        self._mirror_on = bool(mirror)
        self.timing = {"events_ms": 0.0, "snapshot_ms": 0.0, "flush_ms": 0.0, "flush_ops": 0}

    # ---- open / close ------------------------------------------------------------------------

    def open(self) -> None:
        """Connects, checks the datapack is loaded and is the version this engine reads, reads its
        tables, sets the raycast's range, learns who is online, and starts the mirror."""
        self.rcon.reopen()
        self.tables = datapack.check(self._cmd)
        rmax = max(1, int(round(self.ray_range * reading.RAY_UNIT)))
        self._cmd("scoreboard players set #rmax everhear.t %d" % rmax)
        counter = self._score("#n", "everhear.q")
        if counter is not None and counter < self.last_n:
            log.warning("the datapack's event counter is at %d, behind the %d last handed over: a new "
                        "world or a reset pack; counting from there", counter, self.last_n)
            self.last_n = counter
        self.overflowed = self._score("#over", "everhear.q") or 0
        self.refresh_names()
        self._track_sent = None
        self._opened = True
        if self._mirror_on:
            self._mirror.start()

    def close(self) -> None:
        """Stops the mirror, takes off the queue what was read, and closes the connection."""
        self._mirror.stop()
        if self._opened:
            try:
                self._cmd("function everhear:io/events {upto:%d}" % self.last_n)
            except (RconError, OSError) as e:
                log.warning("close: could not take the last events read off the queue (%s); up to a "
                            "page may come again on the next start", e)
        self._opened = False
        self.rcon.close()

    def _cmd(self, command: str) -> str:
        return self.rcon.command(command)

    def _score(self, holder: str, objective: str) -> int | None:
        reply = self._cmd("scoreboard players get %s %s" % (holder, objective))
        m = re.match(r"^\S+ has (-?\d+) \[", reply)
        return int(m.group(1)) if m else None

    def refresh_names(self) -> None:
        self.names.update(self._cmd("list uuids"))

    # ---- events ------------------------------------------------------------------------------

    def events(self) -> list:
        """Every event since the last call, in number order, each handed over once (see the module)."""
        start = time.perf_counter()
        docs = []
        self._ahead = None
        for page_no in range(MAX_PAGES):
            with self.rcon.held():
                if page_no == 0 and self._track_sent is not None and self._track_fresh():
                    # the first page and the snapshot in one function and one read
                    self._cmd("function everhear:io/tick {upto:%d}" % self.last_n)
                    out = snbt.data_reply(self._cmd("data get storage everhear:out"))
                    page = out.get("ev") or {}
                    if isinstance(out.get("snap"), dict):
                        self._ahead = (out["snap"], self._track_sent, time.monotonic())
                else:
                    self._cmd("function everhear:io/events {upto:%d}" % self.last_n)
                    page = snbt.data_reply(self._cmd("data get storage everhear:out ev"))
            last = int(page.get("last", 0))
            if last < self.last_n:
                log.warning("the datapack's event counter went back from %d to %d (a new world or a "
                            "reset pack): counting from there", self.last_n, last)
                self.last_n = 0
                continue
            over = int(page.get("over", 0))
            if over > self.overflowed:
                log.error("the datapack's queue was full: %d event(s) turned away since the last read "
                          "(%d in all)", over - self.overflowed, over)
            self.overflowed = over
            queue = page.get("q") or []
            for doc in queue:
                n = int(doc.get("n", 0))
                if n <= self.last_n:
                    continue                      # handed over already
                docs.append(doc)
                self.last_n = n
            self.queue_len = int(page.get("len", 0))
            if self.queue_len <= len(queue):
                break
        # Names come from `list uuids`: one more read when an event names a UUID not seen yet (a
        # player who just joined, or a mob, which is then remembered as no player's).
        asked = {commands.uuid_str(x) for d in docs
                 for x in (d.get("u"), (d.get("d") or {}).get("target"), (d.get("d") or {}).get("attacker"))
                 if isinstance(x, list) and len(x) == 4}
        unknown = {u for u in asked if self.names.name(u) is None and u not in self.names.not_players}
        if unknown:
            self.refresh_names()
            self.names.not_players.update(u for u in unknown if self.names.name(u) is None)
        out = [self._placed(reading.event(d, self.tables, self.names)) for d in docs]
        self.timing["events_ms"] = (time.perf_counter() - start) * 1000
        return out

    def _placed(self, ev):
        """A mine event's place: Minecraft's statistic says nothing of where, so it is the latest block
        of the kind mined that the player looked at in their last few snapshots (half a second)."""
        if ev.kind == "mine":
            for block_id, block in reversed(self._looks.get(ev.player, ())):
                if block_id == ev.data.get("block"):
                    ev.data["pos"] = block
                    break
        return ev

    # ---- snapshot ----------------------------------------------------------------------------

    # How old events()' snapshot may be for snapshot() to use it; a flush or a query in between
    # (anything that may change the world) drops it.
    AHEAD_S = 0.05

    def snapshot(self, track: Iterable[str] = ()) -> Snapshot:
        """Every online player (the datapack's compound, raycast included) and, of the uuids in
        `track`, those that exist and are alive now. Uses the one events() read a moment ago when
        it was for the same tracked list; otherwise reads one now."""
        start = time.perf_counter()
        wanted = tuple(dict.fromkeys(str(u).lower() for u in track))
        ahead, self._ahead = self._ahead, None
        if ahead is not None and ahead[1] == wanted and time.monotonic() - ahead[2] < self.AHEAD_S:
            doc = ahead[0]
        else:
            with self.rcon.held():
                if wanted != self._track_sent or not self._track_fresh():
                    self._send_track(wanted)
                self._cmd("function everhear:io/snap")
                doc = snbt.data_reply(self._cmd("data get storage everhear:out snap"))
        uuids = [commands.uuid_str(p["u"]) for p in doc.get("p") or [] if isinstance(p.get("u"), list)]
        if any(self.names.online.get(u) is None for u in uuids) or len(uuids) != len(self.names.online):
            self.refresh_names()
        players = {}
        for p in doc.get("p") or []:
            if not isinstance(p.get("u"), list):
                continue
            u = commands.uuid_str(p["u"])
            name = self.names.name(u) or u
            players[name] = snap = reading.player(p, name, self.tables)
            look = snap.looking_at
            if look.kind == "block":
                seen = self._looks.setdefault(name, [])
                seen.append((look.block_id, look.block))
                del seen[:-LOOKS_KEPT]
        tracked = {}
        for e in doc.get("e") or []:
            snap = reading.entity(e, self.tables, self.names)
            if snap is not None and snap.uuid in wanted:
                tracked[snap.uuid] = snap
        self.timing["snapshot_ms"] = (time.perf_counter() - start) * 1000
        return Snapshot(game_tick=int(doc.get("time", 0)), players=players, tracked=tracked)

    def _track_fresh(self) -> bool:
        """The tracked list in the world is the one last sent (not lost to a reconnect: a new
        connection may be a restarted server)."""
        return self.rcon.stats.connects == self._track_connects

    def _send_track(self, wanted: tuple[str, ...]):
        """Sets everhear:io track to `wanted`, in commands under RCON's limit."""
        entries = ['{u:"%s"}' % u for u in wanted if _is_uuid(u)]
        self._cmd("data modify storage everhear:io track set value [%s]" % ",".join(entries[:_TRACK_CHUNK]))
        for i in range(_TRACK_CHUNK, len(entries), _TRACK_CHUNK):
            for e in entries[i:i + _TRACK_CHUNK]:
                self._cmd("data modify storage everhear:io track append value %s" % e)
        self._track_sent = wanted
        self._track_connects = self.rcon.stats.connects

    # ---- the mirror and queries ----------------------------------------------------------------

    def mirror(self) -> McMirror:
        return self._mirror

    @contextlib.contextmanager
    def deadline(self, seconds: float):
        """The reads this thread makes inside answer within `seconds` in all, waiting for the RCON
        lock included, else io.IoDeadline is raised (from rcon's RconDeadline) and the connection
        is dropped, as after a timeout. The engine bounds each content read by what is left of its
        call's I/O budget this way."""
        with self.rcon.deadline(seconds):
            try:
                yield self
            except RconDeadline as exc:
                raise IoDeadline(str(exc)) from exc

    def query(self, command: str) -> str:
        """One command now, and its whole reply (content's ctx.query). RconError when RCON is down;
        a refused command (the owner's, or over RCON's length) raises commands.OpRefused."""
        self._ahead = None                    # it may change the world: the next snapshot is read anew
        text = (command or "").strip().lstrip("/")
        word = commands.refused_word(text)
        if word:
            raise commands.OpRefused("%r is the server owner's to run, never the engine's" % word)
        too_long = commands.check_length(text)
        if too_long:
            raise commands.OpRefused(too_long)
        return self._cmd(text)

    def read_modifiers(self, target: str) -> dict[tuple[str, str], float]:
        """Every everhear:* attribute modifier on a player or entity: (attribute, id) -> amount.
        {} when it has none; RconError when RCON is down, LookupError when there is no such target."""
        reply = self._cmd("data get entity %s attributes" % commands.target(target))
        if reply.startswith("No entity was found") or reply.startswith("No player was found"):
            raise LookupError("no player or entity %r" % target)
        try:
            attrs = snbt.data_reply(reply)
        except snbt.NoData:
            return {}
        out = {}
        for a in attrs or []:
            for m in a.get("modifiers") or []:
                mid = str(m.get("id", ""))
                if mid.startswith("everhear:"):
                    out[(str(a.get("id", "")), mid)] = float(m.get("amount", 0.0))
        return out

    def read_gamerule(self, name: str) -> str | None:
        """A gamerule's value as Minecraft prints it ("true", "3"), or None for no such rule."""
        if not re.match(r"^[a-z0-9_]+$", name or ""):
            return None
        m = _GAMERULE.match(self._cmd("gamerule %s" % name).strip())
        return m.group(2).strip() if m else None

    # ---- reads on demand (part 2): each one function and one read, counted against the caller's call

    def probe(self, dim: str, points, test: str) -> list[bool]:
        """For each point, whether it passes `test`: a block id or #tag ("is" it), "solid" (not in
        #everhear:not_solid: air, liquids, plants, torches, signs…), or "open" (it and the block above
        in #everhear:open: air). A point in an unloaded chunk is False. One function for every point."""
        pts = [commands.block_pos(q) for q in points]
        if not pts:
            return []
        if test in PROBE_TESTS:
            f, b = PROBE_TESTS[test]
        else:
            f, b = "is", commands.block_state(test, "block test")
            if "{" in b or '"' in b:
                raise commands.OpRefused("a probe tests a block id, [properties] or #tag, not block data")
        d = commands.resource(dim, "dimension")
        entries = ["{x:%d,y:%d,z:%d}" % q for q in pts]
        with self.rcon.held():
            self._cmd('data modify storage everhear:io pr set value {d:"%s",f:"%s",b:"%s",p:[%s]}'
                      % (d, f, b, ",".join(entries[:_PROBE_CHUNK])))
            for i in range(_PROBE_CHUNK, len(entries), _PROBE_CHUNK):
                self._cmd("data modify storage everhear:io pp set value [%s]" % ",".join(entries[i:i + _PROBE_CHUNK]))
                self._cmd("data modify storage everhear:io pr.p append from storage everhear:io pp[]")
            self._cmd("function everhear:io/probe")
            out = snbt.data_reply(self._cmd("data get storage everhear:out probe"))
        if not isinstance(out, list) or len(out) != len(pts):
            raise commands.OpRefused("the probe answered %d of %d points" % (len(out) if isinstance(out, list) else 0, len(pts)))
        return [bool(v) for v in out]

    def ground_y(self, dim: str, x: int, z: int) -> int | None:
        """Where feet stand in the column x z: the first block above the highest that blocks
        movement, leaves aside (Minecraft's motion_blocking_no_leaves height). None when its chunk is
        not loaded. In the nether that is above the roof."""
        reply = self._cmd('function everhear:io/ground {d:"%s",x:%d,z:%d}'
                          % (commands.resource(dim, "dimension"), int(math.floor(x)), int(math.floor(z))))
        m = _RETURNED.search(reply.strip())
        if not m:
            raise commands.OpRefused("no height for %s %s: %s" % (x, z, reply[:120]))
        y = int(m.group(1))
        return None if y == NOT_LOADED else y

    def ray(self, dim: str, origin, direction, length: float, through_blocks: bool = False) -> Look:
        """A ray from `origin` along `direction`, `length` blocks (at most 1000), half a block a step,
        to the first block that is not see-through (#everhear:ray_through, taken as a full cube; its
        face found to 1/32); entities do not stop it. Look "block", or "none" at its end (or where it
        reached an unloaded chunk, shorter). through_blocks: nothing stops it (no command is sent)."""
        dx, dy, dz = (float(c) for c in direction)
        norm = math.sqrt(dx * dx + dy * dy + dz * dz)
        if norm < 1e-9:
            raise commands.OpRefused("a ray needs a direction")
        dx, dy, dz = dx / norm, dy / norm, dz / norm
        length = max(0.0, min(float(length), RAY_LONGEST))
        origin = tuple(float(c) for c in origin)
        if through_blocks:
            return Look("none", tuple(o + d * length for o, d in zip(origin, (dx, dy, dz))), length)
        yaw = math.degrees(math.atan2(-dx, dz))
        pitch = math.degrees(-math.asin(max(-1.0, min(1.0, dy))))
        with self.rcon.held():
            self._cmd('function everhear:io/ray {d:"%s",x:%s,y:%s,z:%s,yaw:%s,pitch:%s,n:%d}'
                      % ((commands.resource(dim, "dimension"),) + tuple(commands.num(c) for c in origin)
                         + (commands.num(yaw), commands.num(pitch), int(round(length * reading.RAY_UNIT)))))
            doc = snbt.data_reply(self._cmd("data get storage everhear:out ray"))
        return reading.look(doc, origin, (dx, dy, dz), self.tables)

    def entities(self, dim: str, center, radius: float, types=None) -> list:
        """Every entity within `radius` of `center`, now, in loaded chunks: living or not, players and
        markers aside (players are the snapshot's), or only those of `types` ("minecraft:player"
        among them brings players). Dying ones are left out. Each costs the server about 25 µs."""
        kinds = [commands.resource(t, "entity type") for t in (types or ())]
        c = tuple(commands.num(v) for v in center)
        with self.rcon.held():
            self._cmd('data modify storage everhear:io en set value {d:"%s",x:%s,y:%s,z:%s,r:%s,t:[%s]}'
                      % ((commands.resource(dim, "dimension"),) + c + (commands.num(max(0.0, float(radius))),
                                                                        ",".join('{t:"%s"}' % t for t in kinds))))
            self._cmd("function everhear:io/entities")
            docs = snbt.data_reply(self._cmd("data get storage everhear:out ents"))
        out = []
        for e in docs or []:
            snap = reading.entity(e, self.tables, self.names)
            if snap is not None:
                out.append(snap)
        return out

    def loaded(self, dim: str, x: int, z: int) -> bool:
        """Whether the chunk holding x z is loaded."""
        reply = self._cmd("execute in %s if loaded %d 0 %d" % (commands.resource(dim, "dimension"),
                                                                 int(math.floor(x)), int(math.floor(z))))
        return reply.startswith("Test passed")

    def mspt(self) -> float:
        """The server's average milliseconds per tick (`tick query`), read at most once a second."""
        at, value = self._mspt
        if value is None or time.monotonic() - at > 1.0:
            m = _MSPT.search(self._cmd("tick query"))
            value = float(m.group(1)) if m else 0.0
            self._mspt = (time.monotonic(), value)
        return value

    def world_id(self) -> str:
        """The world's id, as the datapack keeps it (made the first time it loads in a world): a
        fresh world has a new one. Read again after every reconnect (the server may have restarted
        on another world)."""
        connects, wid = self._world
        if wid is None or connects != self.rcon.stats.connects:
            wid = self._read_world()
        return wid

    def _read_world(self) -> str:
        """The world id read now. A dead connection is found by this command (and dropped: the
        next reconnects), so after it the connection count is current."""
        doc = snbt.data_reply(self._cmd("data get storage everhear:meta world"))
        wid = "%08x%08x" % (int(doc.get("a", 0)) & 0xFFFFFFFF, int(doc.get("b", 0)) & 0xFFFFFFFF)
        self._world = (self.rcon.stats.connects, wid)
        return wid

    def watch_mined(self, blocks) -> list[str]:
        """The block kinds whose mining is a `mine` event, all of them (the list replaces the last).
        Each gets a minecraft.mined:<block> objective, zeroed when it is made. On a new connection or
        a new world (a restarted server, a fresh world: the objectives may be gone) every objective
        is made again, so the core's resend, every few seconds, puts mining back. Returns the ids
        that are no 26.3 block (not watched)."""
        # Read now, not from the cache: a server restarted under a live socket is noticed only when a
        # command fails, so the count of connections is current only after one has gone out.
        wid = self._read_world()
        here = (self.rcon.stats.connects, wid)
        if here != self._mined_on:
            self.mined = ()
        wanted, unknown = [], []
        for b in blocks:
            bid = commands.resource(str(b), "block")
            try:
                i = self.tables.blocks.index(bid)
            except ValueError:
                unknown.append(bid)
                continue
            if bid not in wanted:
                wanted.append(bid)
        entries = []
        with self.rcon.held():
            for bid in wanted:
                obj = "everhear.mn.%d" % self.tables.blocks.index(bid)
                if bid not in self.mined:
                    # the criterion names the block with dots: minecraft.mined:minecraft.sponge
                    out = self._cmd("scoreboard objectives add %s minecraft.mined:%s" % (obj, bid.replace(":", ".")))
                    if out.startswith("Created new objective"):
                        self._cmd("scoreboard players reset * %s" % obj)
                    elif "already exists" not in out:
                        unknown.append(bid)
                        continue
                entries.append('{o:"%s",b:"%s"}' % (obj, bid))
            self._cmd("data modify storage everhear:config mine set value []")
            for i in range(0, len(entries), 20):
                self._cmd("data modify storage everhear:io mw set value [%s]" % ",".join(entries[i:i + 20]))
                self._cmd("data modify storage everhear:config mine append from storage everhear:io mw[]")
        self.mined = tuple(b for b in wanted if b not in unknown)
        self._mined_on = here
        return unknown

    # ---- writes ------------------------------------------------------------------------------

    def flush(self, ops: Sequence) -> list[OpResult]:
        """Every op in order, as one batch: the RCON lock is held from the first command to the last
        reply, so nothing else on the server runs between them. One OpResult per op; a command that
        could not go says so, and the rest still go."""
        start = time.perf_counter()
        self._ahead = None                    # the world changes: the next snapshot is read anew
        results: list[OpResult] = []
        try:
            with self.rcon.held():
                for op in ops:
                    results.append(self._one(op))
        except RconError as e:            # the lock itself could not be had
            why = "not sent: %s" % e
            results += [OpResult(False, "", why) for _ in ops[len(results):]]
        self.timing["flush_ms"] = (time.perf_counter() - start) * 1000
        self.timing["flush_ops"] = len(ops)
        return results

    def _one(self, op) -> OpResult:
        try:
            cmds = commands.command_for(op)
        except (commands.OpRefused, ValueError, TypeError, AttributeError) as e:
            return OpResult(False, "", "refused: %s" % e)
        for c in cmds:
            too_long = commands.check_length(c)
            if too_long:
                return OpResult(False, "", too_long)        # in FakeIo's words
        replies = []
        for c in cmds:
            try:
                replies.append(self._cmd(c))
            except RconError as e:
                if replies:
                    return OpResult(False, " | ".join(replies), "partly sent (%d of %d), then: %s"
                                    % (len(replies), len(cmds), e))
                return OpResult(False, "", "not sent: %s" % e)
        return commands.result_for(op, " | ".join(replies))

    # ---- what the engine's status shows ------------------------------------------------------

    def stats(self) -> dict:
        return {"rcon": self.rcon.stats.as_dict(), "timing": dict(self.timing), "last_n": self.last_n,
                "queue": self.queue_len, "overflowed": self.overflowed,
                "mirror": {"age_s": None if self._mirror.age_s == float("inf") else round(self._mirror.age_s, 3),
                           "passes": self._mirror.passes, "failures": self._mirror.failures,
                           "last_ms": round(self._mirror.last_ms, 3), "server_ms": round(self._mirror.server_ms, 3),
                           "every_s": round(self._mirror.wait_s, 3), "last_error": self._mirror.last_error,
                           "entities": len(self._mirror.all())}}


def _is_uuid(u: str) -> bool:
    return bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", u))
