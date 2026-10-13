"""`engine reload`, and a restart, which takes the same path (the game engine spec, "Live updates").

1. Each file is imported fresh and every class in it checked (vfg_engine.loader, .checks). A file
   that fails to import, or a class that fails a check, changes nothing: the old version keeps
   running and the error goes back to whoever asked.
2. The registry swaps each class that passed, and every live instance of it switches at once,
   rebuilt from its saved state (`rebuild`):
   - a state that no longer fits the new class's State stops that instance cleanly;
   - a listen or a timer naming a method the new class lacks is dropped, with a log line;
   - a changed `every` changes its tick.
3. When the Config's values changed, `on_config(ctx, old)` runs if the class has one; else the
   instance restarts with the new values (stop, then start again), keeping its state and its end.
   A cast already running is not restarted: it goes on under the new numbers.

A full reload (no paths) also unloads classes whose files are gone, ending their instances.
A restart restores the saved records the same way (`attach`); a record whose class did not load
is kept aside (an orphan) and comes back when its class loads.
"""

from __future__ import annotations

import logging
import os
import sys

from . import abilities
from .actors import body_of
from .content import ticks_every
from .loader import content_files, load_file
from .records import Record, _dump

log = logging.getLogger("vfg_engine")

KIND_ORDER = {"actor": 0, "scenario": 1, "item": 2, "ability": 3, "mark": 4}


def reload(rt, paths=None) -> dict:
    full = not paths
    if full:
        for folder in rt.settings.content:
            if not os.path.isdir(folder):
                log.warning("content folder %s does not exist; nothing is loaded from it", folder)
    files = content_files(rt.settings.content) if full else [os.path.abspath(p) for p in paths]
    loaded, errors, stopped = [], [], []
    switched = 0
    claimed: dict[str, str] = {}
    for path in files:
        res = load_file(path, rt.guard, rt.now)
        errors.extend(res.errors)
        if res.module is None:
            continue
        defined = {e.get("id") for e in res.errors if e.get("id")}
        keep = []
        for info in res.classes:
            holder = _file_of(rt, info.id)
            if (holder and holder != path) or claimed.get(info.id, path) != path:
                errors.append({"file": path, "class": info.name, "id": info.id, "check": "base",
                               "error": "the id %r is already taken by %s" % (info.id, holder or claimed[info.id])})
                continue
            old = rt.classes.get(info.id)
            if old is not None and old.kind != info.kind:
                errors.append({"file": path, "class": info.name, "id": info.id, "check": "base",
                               "error": "%r is a %s; a %s cannot take its id" % (info.id, old.kind, info.kind)})
                continue
            claimed[info.id] = path
            keep.append(info)
        for info in keep:
            n, st = swap(rt, info)
            switched += n
            stopped.extend(st)
            loaded.append(info.id)
        before = rt.files.get(path, set())
        for cid in before - {i.id for i in keep} - defined:
            stopped.extend(unload(rt, cid, "its class is no longer in %s" % os.path.basename(path)))
        rt.files[path] = {i.id for i in keep} | (before & defined)
        rt.modules.add(res.module)
    if full:
        for path in [p for p in rt.files if p not in files]:
            for cid in rt.files.pop(path):
                stopped.extend(unload(rt, cid, "its file %s is gone" % os.path.basename(path)))
    forget_modules(rt)
    rt.export_incantations()
    for e in errors:
        log.warning("reload refused %s%s: %s (%s); the old version, if any, keeps running",
                    os.path.basename(e["file"]), " " + e["class"] if e.get("class") else "", e["error"], e["check"])
    if loaded:
        log.info("reload: %s loaded; %d instances switched, %d stopped", ", ".join(loaded), switched, len(stopped))
    return {"ok": not errors, "loaded": loaded, "errors": errors, "switched": switched, "stopped": stopped,
            **({"error": "%d check%s failed" % (len(errors), "" if len(errors) == 1 else "s")} if errors else {})}


def forget_modules(rt):
    """Take every content module no loaded class comes from any more out of sys.modules. One a
    class still runs from (its file's newer version failed a check) stays."""
    in_use = {info.module for info in rt.classes.values()}
    for name in list(rt.modules):
        if name not in in_use:
            sys.modules.pop(name, None)
            rt.modules.discard(name)


def _file_of(rt, cid) -> str | None:
    info = rt.classes.get(cid)
    return info.file if info is not None else None


def swap(rt, new) -> tuple[int, list[dict]]:
    """Put `new` in the registry; every live instance of its id switches. (switched, stopped)."""
    old = rt.classes.get(new.id)
    rt.classes[new.id] = new
    for key in [k for k in rt.broken if k.split("|", 1)[0] == new.id]:
        del rt.broken[key]
    switched, stopped = 0, []
    for rec in [r for r in rt.records.values() if r.cid == new.id and not r.ending]:
        why = rebuild(rt, rec, new)
        if why:
            rt.end_record(rec, "reload: %s" % why)
            stopped.append({"instance": rec.iid, "reason": why})
            continue
        switched += 1
        if old is not None and rec.started and old.config.model_dump() != new.config.model_dump():
            if callable(getattr(new.cls, "on_config", None)):
                rt.call(rec, "on_config", old.config)
            else:
                restart(rt, rec)
    stopped.extend(adopt(rt, new.id))
    return switched, stopped


def rebuild(rt, rec: Record, info) -> str | None:
    """Rebuild one instance on `info`'s class from its state; the reason when it cannot be."""
    cls = info.cls

    def states():     # the class's own validators may run: a content call, cut at the limit
        return (cls.State.model_validate(_dump(rec.state) or {}),
                {n: cls.State.model_validate(_dump(r.state) or {}) for n, r in rec.runs.items()})
    out = rt.guard.call("%s.State" % rec.iid, states)
    if not out.ok:
        words = " ".join(out.error.split()).split(" For further information")[0]
        return "its state no longer fits %s.State (%s)" % (info.name, words)
    state, runs = out.value
    rec.state = state
    for n, s in runs.items():
        rec.runs[n].state = s
    rt._make_object(rec, info)
    for sub in list(rec.subs):
        if not callable(getattr(cls, sub.method, None)):
            log.warning("%s: its listen for %s names %r, which %s does not have; dropped",
                        rec.iid, sub.kind, sub.method, info.name)
            rec.subs.remove(sub)
    for timer in list(rec.timers):
        if timer.tick:
            continue
        if not callable(getattr(cls, timer.method, None)):
            log.warning("%s: its timer %s names %r, which %s does not have; dropped",
                        rec.iid, timer.id, timer.method, info.name)
            rec.timers.remove(timer)
    _sync_ticks(rt, rec, cls)
    return None


def _sync_ticks(rt, rec, cls):
    """The tick timers follow the class's `every`: the grant's (or the instance's), and each
    fired run's for a triggered ability."""
    every = ticks_every(cls)
    ticks = [t for t in rec.timers if t.tick]
    per_run = rec.kind == "ability" and abilities.triggered(cls)
    want = set()
    if every and rec.started:
        if per_run:
            want = {n for n, r in rec.runs.items() if r.fired}
        else:
            want = {None}
    for t in ticks:
        if t.run not in want:
            rec.timers.remove(t)
        elif t.period != every:
            t.period = every
            t.due = min(t.due, rt.now + every)
    have = {t.run for t in rec.timers if t.tick}
    for run in want - have:
        rt.add_tick_timer(rec, run, every)


def restart(rt, rec: Record):
    """Stop and start again with the new config, keeping its state and its end time."""
    ends = rec.ends
    rt.call(rec, "stop", "reloaded")
    if rec.iid not in rt.records:
        return
    rt.release(rec.iid, rec.owned)
    rec.subs = [s for s in rec.subs if s.run is not None]
    rec.timers = [t for t in rec.timers if t.run is not None]
    rec.started = False
    rt.start(rec)
    if rec.iid in rt.records:
        rec.ends = ends


def unload(rt, cid: str, reason: str) -> list[dict]:
    rt.classes.pop(cid, None)
    stopped = []
    for rec in [r for r in rt.records.values() if r.cid == cid]:
        rt.end_record(rec, reason)
        stopped.append({"instance": rec.iid, "reason": reason})
    log.info("%s unloaded (%s)", cid, reason)
    return stopped


# ---- a restart ---------------------------------------------------------------------------

def attach(rt, docs) -> list[dict]:
    """Restore saved records: each one whose class is loaded is rebuilt from its state, the rest
    are kept aside until their class loads. The ones that cannot be rebuilt stop."""
    stopped = []
    records = [Record.from_dict(d) for d in docs]
    for rec in sorted(records, key=lambda r: KIND_ORDER.get(r.kind, 9)):
        info = rt.classes.get(rec.cid)
        if info is None or info.kind != rec.kind:
            rt.orphans.append(rec.to_dict())
            log.warning("%s is kept aside: its class %s is not loaded", rec.iid, rec.cid)
            continue
        why = _attach_one(rt, rec, info)
        if why:
            stopped.append({"instance": rec.iid, "reason": why})
    return stopped


def adopt(rt, cid: str) -> list[dict]:
    """The records kept aside for `cid`, now that it is loaded."""
    mine = [d for d in rt.orphans if d.get("cid") == cid]
    if not mine:
        return []
    rt.orphans = [d for d in rt.orphans if d.get("cid") != cid]
    stopped = []
    for doc in mine:
        rec = Record.from_dict(doc)
        why = _attach_one(rt, rec, rt.classes[cid])
        if why:
            stopped.append({"instance": rec.iid, "reason": why})
        else:
            log.info("%s is back: its class %s loaded", rec.iid, cid)
    return stopped


def _attach_one(rt, rec: Record, info) -> str | None:
    rt.records[rec.iid] = rec
    if rec.kind == "actor":
        rt.actors[rec.user] = body_of(rt, rec)
        rt.confirmed.add(rec.user)
    why = rebuild(rt, rec, info)
    if why:
        rt.end_record(rec, "restart: %s" % why)
        return why
    if rec.kind == "ability":
        being = rt.being(rec.user)
        if being is None:
            if "-" in str(rec.user):        # an actor's, and that actor is not here
                rt.end_record(rec, "restart: its actor %s is not here" % rec.user)
                return "its actor %s is not here" % rec.user
            being = rt.ensure_player(rec.user)
        being.grants[rec.cid] = rec.iid
    return None
