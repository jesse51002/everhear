"""What players do, read from events and snapshots (the kit, DESIGN2 §5 "Input"): a held
right-click (`held_use`: pressed, held down, held for N s with a fill on the action bar, let go),
crouch gestures (`sneak_gesture`: each tap, three quick taps, or while held), each step on the
ground (`steps`) and each new chunk walked into (`chunks_entered`), and a random-event clock per
player that counts only their time online (`director`).

Each remembers what it saw in the kit's memory (self.state.kit), per player, so one instance
follows several. `held_use` and a held crouch also run a short step on the kit's timers.

**Calling back.** `held_use` and `sneak_gesture` call the class's own methods by name, those it
has (none is required): `on_press(ctx)`, `on_hold(ctx)` (every `repeat_s` while held down),
`on_charged(ctx)` (held for `hold_s`), `on_release(ctx, held_s)`, `on_sneaking(ctx)` (every
`every` s while crouched).
"""

from __future__ import annotations

import math
import random
from typing import NamedTuple

from ._base import call_back, later, limits, memory, step

RELEASE_S = 0.4                     # a right-click held down repeats a Use about every 0.2 s: none for
                                    # this long is a let-go
WATCH_S = 0.1                       # how often a held button is looked at
NEXT_S = 0.05                       # a held crouch's step: under a tick, so it comes at the next one
FILL = 10                           # squares in the action bar's fill
FELL_FROM = 3.0                     # a landing after a fall this high is a step with `fell`
CHUNKS_KEPT = 20000                 # chunks chunks_entered remembers, at most (the oldest go)
GESTURES = ("tap", "three_taps", "held")


def _optional(self, ctx, method, *args):
    if callable(getattr(self, method, None)):
        return call_back(self, ctx, method, *args)
    return None


# ---- a held right-click --------------------------------------------------------------------

def held_use(self, ctx, ev, hold_s: float = 0.0, repeat_s: float = 0.0, fill: bool = True) -> bool:
    """Feed it each Use (`self.listen(Use, "on_use")`, then `held_use(self, ctx, ev, ...)` in
    on_use). It calls `on_press(ctx)` when the button goes down, `on_hold(ctx)` every `repeat_s`
    while it is held (a gatling), `on_charged(ctx)` once it has been held `hold_s` (a charge; with
    `fill`, the action bar fills as it charges), and `on_release(ctx, held_s)` when it is let go.
    Returns True for the Use that pressed it."""
    mem = memory(self, "held")
    who = str(ev.who)
    m = mem.get(who)
    now = ctx.now
    if m is not None and m.get("down"):
        m["last"] = now
        return False
    mem[who] = {"down": True, "since": now, "last": now, "charged": False, "next": now + float(repeat_s)}
    _optional(self, ctx, "on_press")
    later(self, WATCH_S, "held_watch", who, float(hold_s), float(repeat_s), bool(fill))
    return True


@step("held_watch")
def _held_watch(self, ctx, who, hold_s, repeat_s, fill):
    m = memory(self, "held").get(who)
    if not m or not m.get("down"):
        return
    now = ctx.now
    held = now - m["since"]
    if now - m["last"] > RELEASE_S:
        m["down"] = False
        if fill and hold_s > 0 and not m["charged"]:
            self.message(" ", to=[who], where="actionbar")
        _optional(self, ctx, "on_release", round(held, 3))
        return
    if hold_s > 0 and not m["charged"]:
        if held >= hold_s:
            m["charged"] = True
            if fill:
                self.message("§a" + "■" * FILL, to=[who], where="actionbar")
            _optional(self, ctx, "on_charged")
        elif fill:
            n = int(FILL * held / hold_s)
            self.message("§e" + "■" * n + "§7" + "□" * (FILL - n), to=[who], where="actionbar")
    if repeat_s > 0 and now >= m["next"]:
        m["next"] = max(m["next"] + repeat_s, now)
        _optional(self, ctx, "on_hold")
    later(self, WATCH_S, "held_watch", who, hold_s, repeat_s, fill)


# ---- crouching ---------------------------------------------------------------------------------

def sneak_gesture(self, ctx, ev, mode: str = "three_taps", within: float = 1.5, every: float = 0.1) -> bool:
    """Feed it each Sneak and Unsneak (listen to both). True when the gesture is made: "tap" each
    time they crouch; "three_taps" when they crouch three times within `within` seconds; "held"
    when they crouch, and then `on_sneaking(ctx)` is called at once and every `every` s until they
    stand up.

    "held" goes by the events and the snapshot, never by one timer landing in time: the Sneak
    event is a tick whose snapshot has them down, so it counts there and then, however short the
    crouch; after it a step each tick calls again on the clock while they are still down (no
    Unsneak yet, and the snapshot says sneaking), and the first tick they are up ends it."""
    if mode not in GESTURES:
        raise ValueError("mode is one of %s, not %r" % (", ".join(GESTURES), mode))
    mem = memory(self, "sneak")
    who = str(ev.who)
    m = mem.setdefault(who, {"taps": [], "down": False})
    if getattr(ev, "kind", "") == "unsneak":
        m["down"] = False
        return False
    m["down"] = True
    if mode == "tap":
        return True
    if mode == "held":
        m["next"] = ctx.now + float(every)
        if not m.get("watching"):
            m["watching"] = True
            later(self, NEXT_S, "sneak_held", who, float(every))
        _optional(self, ctx, "on_sneaking")
        return True
    taps = [t for t in m["taps"] if ctx.now - t <= within] + [ctx.now]
    m["taps"] = [] if len(taps) >= 3 else taps
    return len(taps) >= 3


@step("sneak_held")
def _sneak_held(self, ctx, who, every):
    m = memory(self, "sneak").get(who)
    p = ctx.world.player(who)
    if not m or not m.get("down") or p is None or not p.sneaking:
        if m:
            m["down"] = m["watching"] = False
        return
    if ctx.now >= m.get("next", 0.0):
        due = m.get("next", 0.0) + every
        m["next"] = due if due > ctx.now else ctx.now + every     # a late tick: no burst to catch up
        _optional(self, ctx, "on_sneaking")
    later(self, NEXT_S, "sneak_held", who, every)


# ---- walking ---------------------------------------------------------------------------------

class Step(NamedTuple):
    pos: tuple                      # their feet
    dim: str
    fell: float                     # how far they fell to land here (0 for a plain step)


def steps(self, ctx, who, every: float = 2.0) -> Step | None:
    """Call it from tick() for a player: a Step when they stand on the ground `every` blocks (along
    the ground) from where the last step was, or a block higher or lower, or in another dimension,
    or the first time; and when they land from a fall (`fell`, the height, when 3 or more). None
    otherwise (in the air: jumping, falling, flying, swimming)."""
    name = who if isinstance(who, str) else getattr(who, "name", None)
    p = ctx.world.player(name) if name else None
    if p is None or not p.alive:
        return None
    m = memory(self, "steps").setdefault(p.name, {})
    x, y, z = p.pos
    if not p.on_ground:
        m["top"] = max(float(m.get("top", y)), y)
        return None
    fell = float(m.pop("top", y)) - y
    last = m.get("at")
    if (last is not None and m.get("dim") == p.dim and math.floor(last[1] - 0.01) == math.floor(y - 0.01)
            and math.hypot(x - last[0], z - last[2]) < every and fell < FELL_FROM):
        return None
    m["at"], m["dim"] = [x, y, z], p.dim
    return Step((x, y, z), p.dim, round(fell, 2) if fell >= FELL_FROM else 0.0)


def chunks_entered(self, ctx, who, around: int = 0, below: int = 32, above: int = 40, dims=None) -> list:
    """Call it from tick() for a player: the boxes ((x1, y1, z1), (x2, y2, z2)) of each chunk they
    are in, or within `around` chunks of it, that nobody seen by this instance was in before, from
    `below` blocks under their feet to `above` over them (inside the world). `dims`: only in these
    dimensions (None: all). Each chunk comes once (the kit remembers the last 20,000)."""
    name = who if isinstance(who, str) else getattr(who, "name", None)
    p = ctx.world.player(name) if name else None
    if p is None or not p.alive or (dims and p.dim not in dims and p.dim.removeprefix("minecraft:") not in dims):
        return []
    mem = memory(self, "chunks")
    seen = mem.setdefault("seen", [])
    have = set(seen)
    x, y, z = p.pos
    cx, cz = math.floor(x) >> 4, math.floor(z) >> 4
    lo, hi = limits(p.dim)
    y1, y2 = max(lo, math.floor(y) - int(below)), min(hi, math.floor(y) + int(above))
    out = []
    for i in range(-int(around), int(around) + 1):
        for j in range(-int(around), int(around) + 1):
            key = "%s|%d|%d" % (p.dim, cx + i, cz + j)
            if key in have:
                continue
            have.add(key)
            seen.append(key)
            out.append(((16 * (cx + i), y1, 16 * (cz + j)), (16 * (cx + i) + 15, y2, 16 * (cz + j) + 15)))
    if len(seen) > CHUNKS_KEPT:
        del seen[:len(seen) - CHUNKS_KEPT]
    return out


# ---- the director ----------------------------------------------------------------------------

def _pick(events, weights):
    weights = [max(0.0, float(w)) for w in list(weights or []) + [1.0] * len(events)][:len(events)]
    return random.choices(list(events), weights)[0] if sum(weights) > 0 else None


def director(self, ctx, events, weights=None, min_s: float = 25.0, max_s: float = 60.0, each: bool = True,
             players="@a") -> list[tuple[str, str]]:
    """A random-event clock: call it from tick(). Every `min_s` to `max_s` seconds a player is
    online, one of `events` is picked by `weights` (in the same order; a missing weight is 1, 0
    never) for them. `each`: every player on their own clock; else one clock for all, its event to
    one of them at random. `players`: a Players arg. Returns [(event, player's name)] due now: the
    class does them (`behind`, a spot behind someone, is ahead(ctx, p, -k); a sound only they
    hear is self.sound(..., to=[p]))."""
    from ..args import players as online
    mem = memory(self, "director")
    clocks = mem.setdefault("clocks", {})
    now = ctx.now
    dt = min(5.0, max(0.0, now - float(mem.get("at", now))))
    mem["at"] = now
    names = online(ctx, players)
    lo = float(min_s)
    hi = max(lo, float(max_s))
    out = []
    for clock in (names if each else (["*"] if names else [])):
        if clock not in clocks:
            clocks[clock] = random.uniform(lo, hi)
            continue
        clocks[clock] -= dt
        if clocks[clock] > 0:
            continue
        clocks[clock] = random.uniform(lo, hi)
        event = _pick(events, weights)
        if event is not None:
            out.append((event, clock if each else random.choice(names)))
    return out
