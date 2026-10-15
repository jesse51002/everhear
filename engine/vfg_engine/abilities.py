"""Abilities: grants, casts and their runs (the game engine spec, "Abilities and their config").

An ability is granted to a being (a player or an actor): one instance per grant. A passive one
(no `on_cast`) runs its `start`, `every` and `listen` from the grant. A triggered one runs
`on_cast(ctx, ev)` once per cast, and each cast is a run of its own:

- the engine checks the cooldown before anything runs, and starts it when the cast is accepted
  (or when the run ends, with `cooldown_from = "end"`; DESIGN, Decided 1);
- with `charge_s` above 0 the cast is "charging", `on_charge(ctx, ev)` runs each tick meanwhile
  (ev.progress from 0 to 1), and on_cast runs once the charge is done;
- the run ends at `duration_s` after it fires, unless it runs UNTIL_STOPPED, and what it made
  (its modifiers, listens, timers, holds, spawns) goes with it; its `every` ticks while it runs;
- a second cast while a run is going is the ability's decision: `ctx.running` lists the other
  runs, each of which it may `.end()`, and it may `self.end()` the new one. The engine adds no
  rule beyond "each cast is a run".

All damage goes through the engine, and each run's damage is counted per target. `damage(ctx)`
is what one target should take from one cast (per second, for a run that lasts until stopped and
for a passive ability); a target that took well over it is flagged in the log.
"""

from __future__ import annotations

import logging
import math

from .content import ticks_every
from .events import make
from .records import Record, Run

log = logging.getLogger("vfg_engine")

DRIFT_FACTOR = 1.5      # flagged past this many times the declared damage ...
DRIFT_SLACK = 0.5       # ... plus this much (rounding, a tick's worth)


def triggered(cls) -> bool:
    return callable(getattr(cls, "on_cast", None))


def grant(rt, who: str, ability: str, owner: str | None = None):
    """Grant `ability` to `who` (a player's name or an actor's uuid); the instance id, or None
    when they already have it. ValueError when there is no such ability or no such being."""
    info = rt.classes.get(ability)
    if info is None or info.kind != "ability":
        raise ValueError("%r is not a loaded ability" % ability)
    being = rt.being(who)
    if being is None:
        if who in rt.snapshot.tracked or "-" in str(who):
            raise ValueError("%r is not an actor the engine spawned" % who)
        being = rt.ensure_player(who)
    if ability in being.grants:
        return None
    rec = rt.add(Record(rt.new_iid("ability", ability), "ability", ability, being.key, rt.now,
                        owner=owner))
    being.grants[ability] = rec.iid
    rt.broken.pop("%s|%s" % (ability, being.key), None)
    rt.later(lambda: rt.start(rec))
    return rec.iid


def revoke(rt, who: str, ability: str) -> bool:
    being = rt.being(who)
    iid = being.grants.get(ability) if being is not None else None
    if not iid or iid not in rt.records:
        return False
    rt.end_record(rt.records[iid], "revoked")
    return True


def cast(rt, being, ability: str, by: str = "op", phrase=None, cast_id=None, lend_owner=None):
    """(status, extra): "fired", "charging", "cooldown" (+remaining_s), "not_granted", "unknown"
    or "failed" (+error). A fired cast's on_cast runs right after the calling code returns."""
    info = rt.classes.get(ability)
    if info is None or info.kind != "ability":
        return "unknown", {"error": "%r is not a loaded ability" % ability}
    iid = being.grants.get(ability)
    draining = rt.records.get(iid) if iid else None
    if draining is not None and draining.notes.get("release_after_runs"):
        if lend_owner is not None and lend_owner in rt.records:    # lent again before it went
            del draining.notes["release_after_runs"]
            draining.owner = lend_owner
            rt.records[lend_owner].owned.grants.append([being.key, ability])
        else:
            return "not_granted", {}
    if iid is None and lend_owner is not None and lend_owner in rt.records:
        try:
            iid = grant(rt, being.key, ability, owner=lend_owner)
        except ValueError as exc:
            return "failed", {"error": str(exc)}
        owner = rt.records[lend_owner]
        owner.owned.grants.append([being.key, ability])
    if iid is None or iid not in rt.records:
        return "not_granted", {}
    rec = rt.records[iid]
    if not triggered(info.cls):
        return "failed", {"error": "%s is passive: it runs from its grant and cannot be cast" % ability}
    if being.is_player and not being.online:
        return "failed", {"error": "%s is not online" % being.key}
    if not being.alive:
        return "failed", {"error": "%s is dead" % being.key}
    until = being.cooldowns.get(ability, 0.0)
    if until > rt.now:
        return "cooldown", {"remaining_s": round(until - rt.now, 2)}
    config = info.config
    try:
        state = rt.new_state(info, "%s/run" % rec.iid)
    except ValueError as exc:
        log.error("%s: a cast was refused: %s", rec.iid, exc)
        return "failed", {"error": str(exc)}
    n = rec.next_run
    rec.next_run += 1
    run = Run(n=n, started=rt.now, fire_at=rt.now + float(config.charge_s),
              cast={"ability": ability, "phrase": phrase, "cast_id": cast_id, "by": by},
              state=state)
    rec.runs[n] = run
    if config.cooldown_from == "cast":
        being.cooldowns[ability] = rt.now + float(config.cooldown_s)
    if config.charge_s > 0:
        return "charging", {"run": n, "instance": rec.iid, "ready_in_s": float(config.charge_s)}
    rt.later(lambda: fire(rt, rec, n))
    return "fired", {"run": n, "instance": rec.iid}


def fire(rt, rec: Record, n: int):
    """The charge is done: work out the run's end and declared damage, then on_cast."""
    run = rec.runs.get(n)
    if run is None or run.fired or rec.iid not in rt.records:
        return
    if not rec.started:          # a grant lent by this cast: its start comes first
        rt.start(rec)
        if not rec.started or n not in rec.runs:
            rt.end_run(rec, n, "its grant could not start")
            return
    run.fired = True
    ok, duration = rt.formula(rec, "duration_s", run)
    if not ok or n not in rec.runs:
        return
    run.ends = None if duration == math.inf else rt.now + float(duration)
    ok, declared = rt.formula(rec, "damage", run)
    if not ok or n not in rec.runs:
        return
    run.declared = float(declared)
    info = rt.classes[rec.cid]
    if ticks_every(info.cls):
        rt.add_tick_timer(rec, n, ticks_every(info.cls))
    ev = make("cast", rec.user, dict(run.cast), **run.cast)
    rt.call(rec, "on_cast", ev, run=run)
    rt.later(lambda: rt.deliver(ev))
    if n in rec.runs and run.ends is not None and run.ends <= rt.now:
        rt.end_run(rec, n, "duration")


def step(rt):
    """The abilities phase: charges that are done fire; runs past their end end."""
    for rec in list(rt.records.values()):
        if rec.kind != "ability" or rec.paused_at is not None or rec.ending:
            continue
        for n, run in list(rec.runs.items()):
            if rec.iid not in rt.records:
                break
            if not run.fired and run.fire_at <= rt.now:
                fire(rt, rec, n)
            elif not run.fired:
                charging(rt, rec, run)
            elif run.fired and run.ends is not None and run.ends <= rt.now:
                rt.end_run(rec, n, "duration")
            elif run.fired and run.ends is None:
                check_drift(rt, rec, run, final=False)


def charging(rt, rec: Record, run: Run):
    """While charge_s counts down: the ability's on_charge(ctx, ev) each tick, if it has one (the
    charge show); ev.progress goes from 0 to 1. What it makes belongs to the cast's run."""
    if not callable(getattr(rt.classes[rec.cid].cls, "on_charge", None)) or not rec.started:
        return
    span = max(run.fire_at - run.started, 1e-9)
    progress = min(1.0, max(0.0, (rt.now - run.started) / span))
    rt.call(rec, "on_charge", make("cast", rec.user, dict(run.cast), progress=progress, **run.cast), run=run)


def run_ended(rt, rec: Record, run: Run, reason: str):
    info = rt.classes.get(rec.cid)
    being = rt.being(rec.user)
    if info is not None and being is not None and info.config.cooldown_from == "end" and run.fired:
        being.cooldowns[rec.cid] = rt.now + float(info.config.cooldown_s)
    check_drift(rt, rec, run, final=True)
    log.info("%s run %d ended (%s)%s", rec.iid, run.n, reason,
             "; dealt %s" % _dealt_words(run) if run.dealt else "")


def count_damage(rt, rec: Record, run: Run | None, target: str, amount: float):
    if run is None:
        return       # damage outside a cast (a passive ability, an item, a mark) is not measured yet
    run.dealt[target] = run.dealt.get(target, 0.0) + amount
    check_drift(rt, rec, run, final=False)


def check_drift(rt, rec: Record, run: Run, final: bool):
    if run.flagged or run.declared is None or not run.dealt:
        return
    allowed = run.declared
    if run.ends is None:      # until stopped: damage is per second
        allowed = run.declared * max(1.0, rt.now - run.fire_at)
    target, most = max(run.dealt.items(), key=lambda kv: kv[1])
    if most > allowed * DRIFT_FACTOR + DRIFT_SLACK:
        run.flagged = True
        log.warning("damage drift: %s run %d dealt %.1f to %s, declared %.1f%s", rec.iid, run.n, most,
                    target, allowed, "" if final else " (so far)")


def _dealt_words(run: Run) -> str:
    return ", ".join("%.1f to %s" % (v, k) for k, v in sorted(run.dealt.items()))
