"""The world as a call sees it: every online player's place, state (health, food, armor, XP
level, game mode, effects) and items by slot, the time and the weather (one `vfg state --json
--items`), the running copies (running.json, kept fresh by running.Refresher), the saved places
(`vfg place list`), the incantations (`vfg incantation list --json`, the engine's bindings among
them, marked), and the game engine's part (`vfg engine overview --json`, its classes and instances
in one SSH call, the game engine spec): which abilities, items, scenarios, actors and marks it has,
and who has what, kept small.

A brain loop reads the world once, at its start (Prototype_Brain_Loop), so it fetches the state
then, unless a snapshot younger than STATE_FRESH_S is there to share; past STATE_WAIT_S it goes
on with the last good one, and the fetch finishes for the next call. It is shown the items of the
speaker alone (a report's: its task's player's), and RUNNING NOW in full; another player's items
come from the loop's read tool (items()), from a state fetched for it. Respond takes the last
snapshot without waiting: banter needs no fresh position. Respond's players have their place and
state, no items. The incantations change rarely and are fetched at most every
INCANTATIONS_TTL_S; until `vfg incantation` exists they are "none". The engine's part is fetched
at most every ENGINE_TTL_S, and again after an engine action ran (engine_changed); while the engine
is not running it says so, in one line.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import running
from .config import log
from .listing import readable
from .vfg import Vfg, why

STATE_FRESH_S = 2.0
STATE_WAIT_S = 3.0
STATE_TIMEOUT_S = 15.0
INCANTATIONS_TTL_S = 30.0
ENGINE_TTL_S = 10.0
ENGINE_TIMEOUT_S = 10.0
ENGINE_DOWN = 6          # vfg's exit when the engine is not running
ENGINE_SHOWN = 12        # the classes of one kind shown; the rest are counted
ENGINE_SUMMARY = 80      # a class's summary, in characters
ENGINE_KINDS = (("ability", "abilities"), ("item", "items"), ("scenario", "scenarios"), ("actor", "actors"),
                ("mark", "marks"))
ENGINE_NONE = "ENGINE: unknown"
PLAYER_NAME = re.compile(r"^[A-Za-z0-9_]{1,16}$")


@dataclass
class Seen:
    """What one call is shown of the world."""
    players: str = "PLAYERS ONLINE: unknown"
    clock: str = "TIME: unknown. WEATHER: unknown."
    running: str = "RUNNING NOW: none"
    places: str = "SAVED PLACES: unknown"
    incantations: str = "INCANTATIONS: none"
    engine: str = ""
    copies: dict[str, dict] = field(default_factory=dict)
    known: set[str] | None = None  # the saved places' names; None when the list could not be read

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


def incantation_lines(data: Any) -> str:
    """`vfg incantation list --json` as one line each: the phrase, whose, and what it fires; an
    engine binding, the ability it casts."""
    items = data.get("incantations", data) if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = [{"phrase": k, **(v if isinstance(v, dict) else {"action": v})} for k, v in items.items()]
    rows = []
    for x in items if isinstance(items, list) else []:
        if not isinstance(x, dict):
            rows.append(f"- {x}")
            continue
        if x.get("engine"):
            rows.append(f"- \"{x.get('phrase', '?')}\" for {x.get('player') or '?'}: casts the engine ability "
                        f"{x.get('ability') or '?'}")
            continue
        values = x.get("values") if isinstance(x.get("values"), dict) else {}
        said = " ".join(f"{k}={json.dumps(v) if isinstance(v, (list, dict)) else v}" for k, v in values.items())
        rows.append(f"- \"{x.get('phrase', '?')}\" for {x.get('player') or '?'}: {x.get('action') or '?'}"
                    + (f" {said}" if said else ""))
    if not rows:
        return "INCANTATIONS: none"
    return "INCANTATIONS (a phrase a player says that fires an action at once, without you):\n" + "\n".join(rows)


def engine_lines(classes: Any, instances: Any) -> str:
    """ENGINE, from `vfg engine overview --json`'s classes and instances: each kind's
    classes by name with a short summary (marks by name alone), at most ENGINE_SHOWN a kind; then
    what each player has (their abilities, items and marks) and what runs for the world (its
    scenarios and actors)."""
    got = [c for c in (classes or {}).get("classes") or [] if isinstance(c, dict) and c.get("id")] \
        if isinstance(classes, dict) else []
    if not got:
        return "ENGINE: no abilities, items, scenarios or actors yet"
    rows = []
    for kind, words in ENGINE_KINDS:
        mine = sorted((c for c in got if c.get("kind") == kind), key=lambda c: str(c["id"]))
        if not mine:
            continue
        shown = [str(c["id"]) if kind == "mark" else
                 f"{c['id']} ({short(str(c.get('summary') or '').strip() or 'no summary', ENGINE_SUMMARY)})"
                 for c in mine[:ENGINE_SHOWN]]
        more = f", and {len(mine) - ENGINE_SHOWN} more" if len(mine) > ENGINE_SHOWN else ""
        rows.append(f"- {words}: {', '.join(shown)}{more}")
    have: dict[str, list[str]] = {}
    world: list[str] = []
    for i in (instances or {}).get("instances") or [] if isinstance(instances, dict) else []:
        if not isinstance(i, dict) or not i.get("id"):
            continue
        user, kind = str(i.get("user") or ""), str(i.get("kind") or "")
        if kind in ("scenario", "actor"):
            world.append(f"{kind} {i['id']} as {i.get('instance') or '?'}")
        elif PLAYER_NAME.match(user):
            have.setdefault(user, []).append(f"{kind} {i['id']}")
    rows += [f"- {who} has: {', '.join(dict.fromkeys(what))}" for who, what in sorted(have.items())]
    if world:
        rows.append(f"- running for the world: {', '.join(world)}")
    return ("ENGINE (the game engine's own abilities, items, scenarios, actors and marks, one version for "
            "everyone; the engine_ actions grant, give, start and bind them):\n" + "\n".join(rows))


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


def short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


class World:
    def __init__(self, vfg: Vfg, running_file: Path, catalog, *, refresher: running.Refresher | None = None,
                 clock=time.monotonic):
        self.vfg = vfg
        self.running_file = running_file
        self.catalog = catalog
        self.refresher = refresher
        self.clock = clock
        self.state: dict[str, Any] | None = None   # the last good `vfg state --json --items`
        self.state_at = float("-inf")
        self.fetching: asyncio.Task | None = None
        self.incantations = "INCANTATIONS: none"
        self.incantations_at = float("-inf")
        self.engine = ENGINE_NONE
        self.engine_at = float("-inf")
        self.later: set[asyncio.Task] = set()

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

    async def places(self) -> tuple[str, set[str] | None]:
        """The `SAVED PLACES: …` line, from `vfg place list` (vfg answers from its 30 s cache), and
        the places' names (None when the list could not be read)."""
        r = await self.vfg("place", "list", timeout=10)
        if r.code != 0:
            log("world", f"vfg place list failed ({why(r)})")
            return "SAVED PLACES: unknown (the list could not be read)", None
        lines = [x.strip() for x in r.stdout.splitlines() if x.strip() and x.strip() != "none"]
        return "SAVED PLACES: " + ("; ".join(lines) or "none"), {x.split()[0] for x in lines}

    async def fetch_incantations(self) -> str:
        if self.clock() - self.incantations_at < INCANTATIONS_TTL_S:
            return self.incantations
        self.incantations_at = self.clock()
        r = await self.vfg("incantation", "list", "--json", timeout=10)
        try:
            self.incantations = incantation_lines(json.loads(r.stdout)) if r.code == 0 else "INCANTATIONS: none"
        except ValueError:
            self.incantations = "INCANTATIONS: none"
        return self.incantations

    async def fetch_engine(self) -> str:
        """The ENGINE part: its classes and who has what (its instances), from one `vfg engine
        overview --json` (one SSH call), at most every ENGINE_TTL_S; "not running" while the
        engine is down, "unknown" when its classes could not be read. Instances the engine would
        not give leave the classes shown, and a log line."""
        if self.clock() - self.engine_at < ENGINE_TTL_S:
            return self.engine
        self.engine_at = self.clock()
        r = await self.vfg("engine", "overview", "--json", timeout=ENGINE_TIMEOUT_S)
        if r.code == ENGINE_DOWN:
            self.engine = "ENGINE: not running (vfg-engine is down): no engine action works now"
            return self.engine
        got = parsed(r, refused=True)   # exit 1: the engine refused one of the two, the other kept
        if got is None or not isinstance(got.get("classes"), list):
            log("world", f"vfg engine overview failed ({why(r)})")
            self.engine = f"{ENGINE_NONE} (it could not be read: {why(r)})"
            return self.engine
        if not got.get("ok"):
            log("world", f"vfg engine overview: {got.get('error') or why(r)}")
        self.engine = engine_lines(got, got if isinstance(got.get("instances"), list) else None)
        return self.engine

    def engine_changed(self) -> None:
        """An engine action ran: the next loop reads the engine again."""
        self.engine_at = float("-inf")

    def copies(self) -> dict[str, dict]:
        return running.read(self.running_file)

    async def seen(self, *, wait: float = STATE_WAIT_S, items: bool | Iterable[str] = True,
                   full: bool = False) -> Seen:
        """The world for one call: the state, the places, the incantations and the engine's part at
        once; `items` as players() takes it, and with `full` RUNNING NOW as the brain sees it
        (running.full)."""
        state, (places, known), incantations, engine = await asyncio.gather(
            self.fresh_state(wait), self.places(), self.fetch_incantations(), self.fetch_engine())
        copies = self.copies()
        shown = running.full(copies, self.catalog) if full else running.block(copies, self.catalog)
        return Seen(players=players(state, items), clock=clock(state), running=shown, places=places,
                    incantations=incantations, engine=engine, copies=copies, known=known)

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

    async def glance(self) -> Seen:
        """For Respond: the last state, never waited for (one is fetched for the next call), with
        no items, the running copies, the incantations and the engine's part as last fetched; no places."""
        state = await self.fresh_state(wait=0)
        copies = self.copies()
        return Seen(players=players(state, items=False), clock=clock(state),
                    running=running.block(copies, self.catalog), places="", incantations=self.incantations,
                    engine=self.engine if self.engine != ENGINE_NONE else "", copies=copies)

    def refetch_running(self) -> None:
        """After a run, an edit or a stop: fetch the running copies again, in the background."""
        if self.refresher is not None:
            t = asyncio.create_task(self.refresher.fetch(), name="running refetch")
            self.later.add(t)
            t.add_done_callback(self.later.discard)

    async def close(self) -> None:
        for t in [self.fetching, *self.later]:
            if t is not None and not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
