"""Marks: timed by the engine on their bearer, known for certain by their own ids (the game engine
spec, "Potions and marks", and its marks table).

    It ends early         its ids are removed; nothing else changes
    The same mark again   the mark decides (`again` in its Config): "refresh" restarts the one
                          there (everhear:mark/<id>), "stack" adds another beside it
                          (everhear:mark/<id>/1, /2, ...), which Minecraft adds up
    Death                 on_death: "end" (default) or "keep". After a respawn the engine reads
                          the bearer's modifiers back and puts right what should and should not be
                          there, whether or not Minecraft dropped them
    Milk                  on_milk: "keep" (default) or "end" (DESIGN, Decided 4)
    Offline               its timers and its end wait until the bearer is back (Decided 5)
    Relog, restart        kept (saved with the engine's state)
    Needs a potion        self.keep_potion(effect): as a beacon does, a ~3 s potion given again
                          every ~2 s while the mark lasts; when it ends the potion runs out by itself

A mark's length is `duration_s` (or the seconds it was applied for), from when it starts on its
bearer. Potions in general are given and forgotten: the engine never records or clears one.
"""

from __future__ import annotations

import logging
import math

from .handle import KEEP_POTION_EVERY_S, KEEP_POTION_S
from .io import GiveEffect
from .records import Record

log = logging.getLogger("vfg_engine")

UNTIL_STOPPED_WORD = "until stopped"


def apply(rt, who: str, mark: str, seconds=None, source=None, owner=None) -> str:
    """Put `mark` on `who`; the instance id (the one refreshed, for a refreshing mark already
    there). ValueError when there is no such mark or no such bearer."""
    info = rt.classes.get(mark)
    if info is None or info.kind != "mark":
        raise ValueError("%r is not a loaded mark" % mark)
    if seconds is not None:
        seconds = float(seconds)
        if not (seconds >= 0):
            raise ValueError("a mark's seconds must be 0 or more, not %r" % seconds)
        if math.isinf(seconds):
            seconds = UNTIL_STOPPED_WORD          # saved as words: JSON has no infinity
    bearer = rt.being(who)
    if bearer is None:
        if "-" in str(who):
            raise ValueError("%r is not a player or an actor the engine spawned" % who)
        bearer = rt.ensure_player(who)
    there = [r for r in rt.records_of(bearer.key, "mark") if r.cid == mark]
    if there and info.config.again == "refresh":
        rec = there[0]
        rec.args["seconds"] = seconds
        if rec.started:
            rt.later(lambda: set_end(rt, rec))
        return rec.iid
    stack = None
    if info.config.again == "stack":
        stack = max([r.stack or 0 for r in there] + [0]) + 1
    rec = rt.add(Record(rt.new_iid("mark", mark), "mark", mark, bearer.key, rt.now, source=source,
                        owner=owner, stack=stack, args={"seconds": seconds}))
    rt.later(lambda: rt.start(rec))
    return rec.iid


def set_end(rt, rec: Record):
    """The mark's end: the seconds it was applied for, else its duration_s, from now."""
    seconds = rec.args.get("seconds")
    if seconds is None:
        ok, seconds = rt.formula(rec, "duration_s")
        if not ok:
            return
    if seconds == UNTIL_STOPPED_WORD or seconds == math.inf:
        rec.ends = None
    elif rec.paused_at is not None:
        # its bearer is offline and its clock stopped when they left: the seconds count from
        # then, and the join that resumes it moves the end on by the time they were away
        rec.ends = rec.paused_at + float(seconds)
    else:
        rec.ends = rt.now + float(seconds)


def clear(rt, who: str, mark: str, reason: str = "cleared") -> int:
    n = 0
    for rec in [r for r in rt.records_of(who, "mark") if r.cid == mark]:
        rt.end_record(rec, reason)
        n += 1
    return n


def step(rt):
    """The marks phase: end the ones past their end, run their timers, keep their potions up."""
    for rec in list(rt.records.values()):
        if (rec.kind == "mark" and rec.started and rec.paused_at is None and not rec.ending
                and rec.ends is not None and rec.ends <= rt.now):
            rt.end_record(rec, "expired")
    rt.run_timers(("mark",))
    for rec in list(rt.records.values()):
        if rec.paused_at is not None or rec.ending or rec.user is None:
            continue
        for owned in [rec.owned] + [r.owned for r in rec.runs.values()]:
            for effect, keep in owned.potions.items():
                amplifier, particles, due = keep
                if due <= rt.now and rt.online(rec.user):
                    rt.queue(GiveEffect(rec.user, effect, KEEP_POTION_S, amplifier, particles), rt.potion_reply)
                    keep[2] = rt.now + KEEP_POTION_EVERY_S


def died(rt, who: str):
    for rec in rt.records_of(who, "mark"):
        if rt.classes[rec.cid].config.on_death == "end":
            rt.end_record(rec, "died")


def drank_milk(rt, who: str):
    for rec in rt.records_of(who, "mark"):
        if rt.classes[rec.cid].config.on_milk == "end":
            rt.end_record(rec, "cured by milk")
