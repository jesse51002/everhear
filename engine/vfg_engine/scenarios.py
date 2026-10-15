"""Scenarios: world-level things with phases and a timeline (the game engine spec, "Actors,
players and scenarios").

A scenario is started by the `start` op (or by content, `self.start_scenario`, which then owns
it), with Args checked against its `class Args(Args)` (`self.args`). Its result
(`self.result(summary, **facts)`) is what the `start` op answers when it waits for it. Its phases are
its own state and its timeline is timers (`self.after(30, "phase_two")`). It hears every event,
not only those about a user (it has none). It owns what it spawns, the marks it applies and the
world settings it holds, so ending it (`stop`, its duration, `self.end()`) leaves nothing in the
world.

A zone is a scenario watching an area (`self.watch("arena", center=(0, 64, 0), radius=20)`):
the engine works out from each snapshot who came in and who went out, and sends `enter` and
`exit` events, which the watching instance always hears.
"""

from __future__ import annotations

import math

from . import args as argtypes
from .events import make
from .records import Record


def start(rt, scenario: str, args=None, source=None, owner=None) -> str:
    """Start a scenario with `args` checked against its Args (ValueError, in words, when they do
    not fit); owned by `owner` (an instance) when one started it. Its instance id."""
    info = rt.classes.get(scenario)
    if info is None or info.kind != "scenario":
        raise ValueError("%r is not a loaded scenario" % scenario)
    given = argtypes.validate(info.cls.Args, args, rt).model_dump(mode="json")
    rec = rt.add(Record(rt.new_iid("scenario", scenario), "scenario", scenario, None, rt.now,
                        source=source, owner=owner, args=given))
    rt.later(lambda: rt.start(rec))
    return rec.iid


def set_end(rt, rec: Record):
    ok, seconds = rt.formula(rec, "duration_s")
    if ok:
        rec.ends = None if seconds == math.inf else rt.now + float(seconds)


def step(rt):
    """The scenarios phase: end the ones past their end, then run their timers."""
    for rec in list(rt.records.values()):
        if rec.kind == "scenario" and rec.started and not rec.ending and rec.ends is not None and rec.ends <= rt.now:
            rt.end_record(rec, "duration")
    rt.run_timers(("scenario",))


def inside(area: dict, dim: str, pos) -> bool:
    if dim != area["dim"]:
        return False
    if "height" in area:                     # a flat cylinder; its height None is all of it
        c = area["center"]
        if math.hypot(pos[0] - c[0], pos[2] - c[2]) > area["radius"]:
            return False
        return area["height"] is None or abs(pos[1] - c[1]) <= area["height"] / 2
    if "radius" in area:
        return math.dist(pos, area["center"]) <= area["radius"]
    lo, hi = area["box"]
    return all(lo[i] <= pos[i] <= hi[i] for i in range(3))


def crossings(rt) -> list:
    """Enter and Exit events for every watched area, from this tick's snapshot."""
    out = []
    players = rt.snapshot.players
    for rec in list(rt.records.values()):
        if rec.ending or rec.paused_at is not None:
            continue
        for owned in [rec.owned] + [r.owned for r in rec.runs.values()]:
            for name, area in owned.areas.items():
                now_in = sorted(p.name for p in players.values() if p.alive and inside(area, p.dim, p.pos))
                was_in = area.get("inside") or []
                for who in now_in:
                    if who not in was_in:
                        out.append(make("enter", who, {"area": name}, area=name, instance=rec.iid))
                for who in was_in:
                    if who not in now_in:
                        out.append(make("exit", who, {"area": name}, area=name, instance=rec.iid))
                area["inside"] = now_in
    return out
