"""The world as a call sees it (the game engine spec): every online player's place, state (health,
food, armor, XP level, game mode, effects) and items by slot, the time and the weather (one `vfg
state --json --items`), and the game engine's part (one `vfg engine overview --json`, one SSH
call): RUNNING NOW, every instance the engine runs (a scenario, an ability granted, an item held,
a mark borne, an actor); SAVED PLACES, the engine's own places; INCANTATIONS, its bindings; and
ENGINE, every class it has, each with its summary, a scenario's and a mark's args with them.

A brain loop reads the world once, at its start (Prototype_Brain_Loop), so it fetches the state and
the engine then, unless a read younger than STATE_FRESH_S is there to share; past STATE_WAIT_S it
goes on with the last good one, and the fetch finishes for the next call. An engine action that ran
makes the next loop read the engine afresh (engine_changed). It is shown the items of the speaker
alone (a report's: its task's player's); another player's items come from the loop's read tool
(items()), from a state fetched for it, and a class's Config as written from the other
(config()). Respond takes the last of each without waiting: banter needs no fresh position, and
its players have their place and state, no items. While the engine is not running the engine's
parts say so, in one line each.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .config import log
from .listing import readable
from .vfg import Vfg, why

STATE_FRESH_S = 2.0
STATE_WAIT_S = 3.0
STATE_TIMEOUT_S = 15.0
ENGINE_TIMEOUT_S = 15.0
ENGINE_DOWN = 6          # vfg's exit when the engine is not running
SUMMARY_MAX = 120        # a class's summary, in characters
DESCRIPTION_MAX = 80     # an arg's description
RUNNING_MAX = 40         # the instances RUNNING NOW shows; the rest are counted
KINDS = (("scenario", "SCENARIOS"), ("ability", "ABILITIES"), ("item", "ITEMS"), ("mark", "MARKS"),
         ("actor", "ACTORS"))


@dataclass
class Seen:
    """What one call is shown of the world."""
    players: str = "PLAYERS ONLINE: unknown"
    clock: str = "TIME: unknown. WEATHER: unknown."
    running: str = "RUNNING NOW: unknown"
    places: str = "SAVED PLACES: unknown"
    incantations: str = "INCANTATIONS: unknown"
    engine: str = "ENGINE: unknown"
    instances: list[dict] = field(default_factory=list)  # the engine's instances, as overview gives them
    classes: list[dict] = field(default_factory=list)    # the engine's classes, as overview gives them
    known: set[str] | None = None  # the saved places' names; None when they could not be read

    def show(self) -> str:
        parts = ("THE WORLD NOW:", self.players, self.clock, self.running, self.places, self.incantations, self.engine)
        return "\n".join(x for x in parts if x)


ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X")


def number(x: Any) -> str | None:
    """20.0 as `20`, 14.5 as `14.5`; None for anything that is not a number."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return f"{x:.1f}".removesuffix(".0")


def ticks(t: Any) -> str:
    """An effect's duration: 600 ticks as `0:30`, an hour or more as `1:00:00`, -1 as `∞`."""
    if not isinstance(t, int) or isinstance(t, bool):
        return ""
    if t < 0:
        return "∞"
    m, sec = divmod(t // 20, 60)
    return f"{m // 60}:{m % 60:02d}:{sec:02d}" if m >= 60 else f"{m}:{sec:02d}"


def effect(e: dict) -> str:
    """`speed II 0:30`: the effect's name, its level past I, and the time left."""
    name = str(e.get("id") or "?").removeprefix("minecraft:").replace("_", " ")
    amp = e.get("amplifier")
    level = (ROMAN[amp] if amp < len(ROMAN) else str(amp + 1)) if isinstance(amp, int) and amp > 0 else ""
    return " ".join(x for x in (name, level, ticks(e.get("duration"))) if x)


def condition(p: dict) -> str:
    """`14/20 health, 18/20 food, armor 12, level 5, survival; effects: speed II 0:30`: what
    Minecraft answered for one player, and nothing it did not (vfg state's null)."""
    health, most = number(p.get("health")), number(p.get("max_health"))
    food, armor = number(p.get("food")), number(p.get("armor"))
    level = number(p.get("xp_level"))
    bits = [f"{health}/{most} health" if health and most else f"{health} health" if health else "",
            f"{food}/20 food" if food else "", f"armor {armor}" if armor else "",
            f"level {level}" if level else "", str(p.get("game_mode") or "")]
    out = ", ".join(b for b in bits if b)
    got = p.get("effects")
    if isinstance(got, list):
        on = ", ".join(effect(e) for e in got if isinstance(e, dict))
        out += ("; " if out else "") + (f"effects: {on}" if on else "no effects")
    return out


def items_line(p: dict) -> str:
    """One online player's items by slot, as `item modify` names the slots."""
    return readable([str(x) for x in p.get("items") or []]) or "items unknown"


def players(world: dict[str, Any] | None, items: bool | Iterable[str] = True) -> str:
    """PLAYERS ONLINE, each with where they are, their state and their items by slot: every
    player's with `items` True, none with False, else only the players it names (`- Steve at 197
    68 251 in overworld: 14/20 health, …; Steve's items, by slot: …`)."""
    if not isinstance(world, dict):
        return "PLAYERS ONLINE: unknown (the server's state could not be read)"
    if world.get("rcon"):
        return f"PLAYERS ONLINE: unknown (Minecraft did not answer: {world['rcon']})"
    shown = None if isinstance(items, bool) else set(items)
    rows = []
    for p in world.get("online") or []:
        where = " ".join(str(c) for c in p["pos"]) if p.get("pos") else "?"
        said = [condition(p)]
        if items is True or (shown is not None and p.get("name") in shown):
            said.append(items_line(p))
        said = "; ".join(x for x in said if x)
        rows.append(f"- {p.get('name')} at {where} in {p.get('dimension') or '?'}" + (f": {said}" if said else ""))
    return "PLAYERS ONLINE:\n" + ("\n".join(rows) or "nobody")


def clock(world: dict[str, Any] | None) -> str:
    if not isinstance(world, dict) or world.get("rcon"):
        return "TIME: unknown. WEATHER: unknown."
    t = world.get("time") or {}
    day = f", day {t['day']}" if isinstance(t.get("day"), int) else ""
    now = f"{t.get('phase') or '?'} ({t.get('daytime', '?')}{day})"
    return f"TIME: {now}. WEATHER: {world.get('weather') or 'unknown'}."


def short(text: Any, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def utc(t: Any) -> str:
    """An engine time (epoch seconds) as HH:MM:SS UTC, as the conversation's rows are; "" for none."""
    if isinstance(t, bool) or not isinstance(t, (int, float)) or t < 1e9:
        return ""
    return datetime.fromtimestamp(t, UTC).strftime("%H:%M:%S")


def value(v: Any) -> str:
    """A value as an action writes it: text as it is, anything else as compact JSON."""
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def args_of(c: dict) -> list[dict]:
    """A class's Args as `classes` gives them, [{"name", "type", "default", "bounds", "description"}]."""
    spec = c.get("args")
    if isinstance(spec, dict):
        spec = [dict(v, name=k) for k, v in spec.items() if isinstance(v, dict)]
    return [x for x in spec or [] if isinstance(x, dict) and x.get("name")]


def arg_line(x: dict) -> str:
    """`    args.count: whole number 1..100, default 10: how many fall`."""
    kind = str(x.get("type") or "?")
    b = x.get("bounds") if isinstance(x.get("bounds"), dict) else {}
    lo, hi = b.get("ge", b.get("gt")), b.get("le", b.get("lt"))
    if lo is not None or hi is not None:
        kind += f" {'' if lo is None else value(lo)}..{'' if hi is None else value(hi)}"
    default = f", default {value(x['default'])}" if "default" in x and x["default"] is not None else ""
    said = short(x.get("description"), DESCRIPTION_MAX)
    return f"    args.{x['name']}: {kind}{default}" + (f": {said}" if said else "")


def engine_lines(classes: list[dict]) -> str:
    """ENGINE: every class the engine has, by kind, scenarios first: its id and summary, and under a
    scenario and a mark its args, one line each."""
    got = [c for c in classes if isinstance(c, dict) and c.get("id")]
    if not got:
        return "ENGINE: no classes yet (nothing to start, grant, give or apply)"
    rows = []
    for kind, head in KINDS:
        mine = sorted((c for c in got if c.get("kind") == kind), key=lambda c: str(c["id"]))
        if not mine:
            continue
        rows.append(f"{head}:")
        for c in mine:
            rows.append(f"- {c['id']}: {short(c.get('summary') or 'no summary', SUMMARY_MAX)}")
            rows += [arg_line(x) for x in args_of(c)] if kind in ("scenario", "mark") else []
    return ("ENGINE (every class the game engine has, one version for everyone: a scenario is started, an "
            "ability granted, an item given, a mark applied; under each scenario and mark its args, each by its "
            "name):\n" + "\n".join(rows))


def running_lines(instances: list[dict]) -> str:
    """RUNNING NOW: every instance the engine runs, by kind: its name (what engine_stop takes for a
    scenario's), its class, whose it is, and when it ends."""
    got = [i for i in instances if isinstance(i, dict) and i.get("instance")]
    if not got:
        return "RUNNING NOW: nothing (no scenario runs, and nobody has an ability, an item or a mark)"
    order = dict((k, n) for n, (k, _) in enumerate(KINDS))
    got.sort(key=lambda i: (order.get(i.get("kind"), len(order)), str(i.get("instance"))))
    rows = []
    for i in got[:RUNNING_MAX]:
        kind, user = str(i.get("kind") or "?"), i.get("user")
        whose = {"ability": f"granted to {user}", "item": f"held by {user}", "mark": f"on {user}",
                 "actor": "in the world", "scenario": "for the world"}.get(kind, str(user or ""))
        if kind in ("ability", "item", "mark") and not user:
            whose = "nobody's"
        ends = utc(i.get("ends"))
        rows.append(f"- {i['instance']}: {kind} {i.get('id') or '?'}, {whose}" + (f", ends {ends} UTC" if ends else ""))
    if len(got) > RUNNING_MAX:
        rows.append(f"- and {len(got) - RUNNING_MAX} more")
    return ("RUNNING NOW (every instance the engine runs: its name, its class, whose, when it ends; a scenario "
            "is stopped by its name, an ability revoked, a mark cleared, an item taken):\n" + "\n".join(rows))


def places_lines(places: list[dict]) -> tuple[str, set[str]]:
    """SAVED PLACES, `castle 197 68 251 overworld (castle_steve)` each, and their names."""
    got = [p for p in places if isinstance(p, dict) and p.get("name")]
    if not got:
        return "SAVED PLACES: none", set()
    shown = []
    for p in sorted(got, key=lambda p: str(p["name"])):
        pos = p.get("pos") if isinstance(p.get("pos"), (list, tuple)) else []
        at = " ".join(str(round(c)) for c in pos if isinstance(c, (int, float)) and not isinstance(c, bool)) or "?"
        dim = str(p.get("dim") or "?").removeprefix("minecraft:")
        shown.append(f"{p['name']} {at} {dim}" + (f" ({p['by']})" if p.get("by") else ""))
    return "SAVED PLACES: " + "; ".join(shown), {str(p["name"]) for p in got}


def incantation_lines(items: list[dict]) -> str:
    """INCANTATIONS: each binding, a line: the phrase, whose, and the ability it casts or the
    scenario it starts with its set args."""
    rows = []
    for x in items:
        if not isinstance(x, dict):
            continue
        does = (f"starts {x['scenario']}" + (f" {value(x['args'])}" if x.get("args") else "") if x.get("scenario")
                else f"casts {x.get('ability') or '?'}")
        rows.append(f"- \"{x.get('phrase', '?')}\" for {x.get('player') or '?'}: {does}")
    if not rows:
        return "INCANTATIONS: none"
    return ("INCANTATIONS (a phrase a player says that casts an ability or starts a scenario at once, without "
            "you):\n" + "\n".join(rows))


def parsed(r, refused: bool = False) -> dict | None:
    """A `vfg … --json` answer's object, or None when it failed or is not one; with `refused`, the
    reply of one vfg exits 1 for (it says ok false) too."""
    if r.code != 0 and not (refused and r.code == 1):
        return None
    try:
        got = json.loads(r.stdout)
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


class World:
    def __init__(self, vfg: Vfg, *, clock=time.monotonic):
        self.vfg = vfg
        self.clock = clock
        self.state: dict[str, Any] | None = None   # the last good `vfg state --json --items`
        self.state_at = float("-inf")
        self.fetching: asyncio.Task | None = None
        self.engine: dict[str, Any] | None = None  # the last `vfg engine overview --json`, or why not
        self.engine_at = float("-inf")
        self.engine_fetching: asyncio.Task | None = None

    async def fresh_state(self, wait: float = STATE_WAIT_S, younger: float = STATE_FRESH_S) -> dict[str, Any] | None:
        """The state now: a snapshot younger than `younger` s, else a fetch, waited for up to
        `wait` s; past it (or with `wait` 0) the last good one, while the fetch goes on."""
        if self.state is not None and self.clock() - self.state_at < younger:
            return self.state
        if self.fetching is None or self.fetching.done():
            self.fetching = asyncio.create_task(self._fetch_state(), name="state")
        if wait > 0:
            try:
                await asyncio.wait_for(asyncio.shield(self.fetching), wait)
            except TimeoutError:
                log("world", f"vfg state took over {wait:g} s: the last state there is")
        return self.state

    async def _fetch_state(self) -> dict[str, Any] | None:
        """One `vfg state --json --items`: the state it got, kept as the last good one, or None."""
        t0 = time.perf_counter()
        r = await self.vfg("state", "--json", "--items", timeout=STATE_TIMEOUT_S)
        try:
            got = json.loads(r.stdout) if r.code in (0, 5) else None
        except ValueError:
            got = None
        if not isinstance(got, dict):
            log("world", f"vfg state failed ({why(r)}), {int((time.perf_counter() - t0) * 1000)} ms")
            return None
        self.state, self.state_at = got, self.clock()
        return got

    async def fresh_engine(self, wait: float = STATE_WAIT_S, younger: float = STATE_FRESH_S) -> dict[str, Any] | None:
        """The engine's part now, as fresh_state is the state: an overview younger than `younger` s
        (and none since an engine action ran), else a fetch, waited for up to `wait` s."""
        if self.engine is not None and self.clock() - self.engine_at < younger:
            return self.engine
        if self.engine_fetching is None or self.engine_fetching.done():
            self.engine_fetching = asyncio.create_task(self._fetch_engine(), name="engine")
        if wait > 0:
            try:
                await asyncio.wait_for(asyncio.shield(self.engine_fetching), wait)
            except TimeoutError:
                log("world", f"vfg engine overview took over {wait:g} s: the last one there is")
        return self.engine

    async def _fetch_engine(self) -> dict[str, Any] | None:
        """One `vfg engine overview --json`: {"classes", "instances", "places", "incantations"} as it
        gave them, a part it refused left out (its error logged); or {"down": why} while the engine
        is not running, {"error": why} when it could not be read (the last good parts kept)."""
        r = await self.vfg("engine", "overview", "--json", timeout=ENGINE_TIMEOUT_S)
        if r.code == ENGINE_DOWN:
            self.engine, self.engine_at = {"down": why(r)}, self.clock()
            return self.engine
        got = parsed(r, refused=True)   # exit 1: the engine refused a part, the others kept
        if got is None:
            log("world", f"vfg engine overview failed ({why(r)})")
            last = self.engine if self.engine and "down" not in self.engine else {}
            self.engine = dict(last, error=why(r))
            return self.engine
        if not got.get("ok"):
            log("world", f"vfg engine overview: {got.get('error') or why(r)}")
        self.engine, self.engine_at = got, self.clock()
        return got

    def engine_changed(self) -> None:
        """An engine action ran: the next loop reads the engine afresh."""
        self.engine_at = float("-inf")

    def instances(self) -> list[dict]:
        """The engine's instances as last read."""
        got = (self.engine or {}).get("instances")
        return [i for i in got if isinstance(i, dict)] if isinstance(got, list) else []

    def classes(self) -> list[dict]:
        """The engine's classes as last read."""
        got = (self.engine or {}).get("classes")
        return [c for c in got if isinstance(c, dict)] if isinstance(got, list) else []

    def engine_parts(self) -> tuple[str, str, str, str, set[str] | None]:
        """(RUNNING NOW, SAVED PLACES, INCANTATIONS, ENGINE, the places' names) from the last read."""
        e = self.engine or {}
        if "down" in e:
            down = "not running (vfg-engine is down): nothing the engine has can be started, granted or bound now"
            return (f"RUNNING NOW: unknown, the engine is {down}", "SAVED PLACES: unknown (the engine is not running)",
                    "INCANTATIONS: unknown (the engine is not running)", f"ENGINE: {down}", None)
        why_not = f" (it could not be read: {short(e.get('error'), 200)})" if e.get("error") else ""

        def part(key, show, empty):
            got = e.get(key)
            return show([x for x in got if isinstance(x, dict)]) if isinstance(got, list) else empty + why_not
        running = part("instances", running_lines, "RUNNING NOW: unknown")
        incantations = part("incantations", incantation_lines, "INCANTATIONS: unknown")
        engine = part("classes", engine_lines, "ENGINE: unknown")
        if isinstance(e.get("places"), list):
            places, known = places_lines([p for p in e["places"] if isinstance(p, dict)])
        else:
            places, known = "SAVED PLACES: unknown" + why_not, None
        return running, places, incantations, engine, known

    async def seen(self, *, wait: float = STATE_WAIT_S, items: bool | Iterable[str] = True) -> Seen:
        """The world for one call: the state and the engine at once; `items` as players() takes it."""
        state, _ = await asyncio.gather(self.fresh_state(wait), self.fresh_engine(wait))
        running, places, incantations, engine, known = self.engine_parts()
        return Seen(players=players(state, items), clock=clock(state), running=running, places=places,
                    incantations=incantations, engine=engine, instances=self.instances(), classes=self.classes(),
                    known=known)

    async def items(self, player: str) -> tuple[bool, str]:
        """The read tool: (True, `Alex's items, by slot: …`) from a state fetched for it, or (False,
        why not): the player is not online, or no fresh state could be read. It fetches its own,
        waiting for it up to STATE_TIMEOUT_S, never a snapshot or a fetch begun before it (the step's
        actions may have changed the items), and an older state never passes for a fresh one: it reads
        its own fetch's answer, never the last good state another fetch left."""
        state = await self._fetch_state()
        if state is None:
            return False, "the server's state could not be read just now: nothing read, try again"
        if state.get("rcon"):
            return False, f"Minecraft did not answer: {state['rcon']}"
        for p in state.get("online") or []:
            if str(p.get("name") or "").lower() == player.strip().lower():
                return True, items_line(p)
        return False, f"{player} is not online"

    async def config(self, name: str) -> tuple[bool, str]:
        """The read tool for an engine class's Config: (True, `made/main/content/fireball.py, lines 8
        to 17: the Config of Fireball, as written` and the class as written), or (False, why not),
        from `vfg engine config get`, never a copy kept from before (it may have been rewritten)."""
        r = await self.vfg("engine", "config", "get", name.strip(), timeout=ENGINE_TIMEOUT_S + 10)
        if r.code == 0 and r.stdout.strip():
            return True, r.stdout.rstrip("\n")
        if r.code == ENGINE_DOWN:
            return False, "the engine is not running: nothing read"
        return False, why(r)

    async def glance(self) -> Seen:
        """For Respond: the last state and the last engine read, never waited for (they are fetched
        for the next call), with no items."""
        state = await self.fresh_state(wait=0)
        await self.fresh_engine(wait=0)
        running, places, incantations, engine, known = self.engine_parts()
        return Seen(players=players(state, items=False), clock=clock(state), running=running, places="",
                    incantations=incantations, engine=engine, instances=self.instances(), classes=self.classes(),
                    known=known)

    async def close(self) -> None:
        for t in (self.fetching, self.engine_fetching):
            if t is not None and not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
