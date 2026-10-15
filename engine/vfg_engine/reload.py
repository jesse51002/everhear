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

A full reload (no paths) also unloads classes whose files are gone, ending their instances. A
reload of some paths loads only content files: a path is refused in words unless, its symlinks and
`..` resolved, it is a *.py directly in one of the content folders (a file anywhere else would
otherwise rank above every folder and shadow the library's and the server's version of its id).

**A server's version shadows the library's.** The content folders are ordered (VFG_ENGINE_CONTENT:
the library first, then made/<server>/content): a class in a later folder with the id of one in
an earlier folder replaces it, logged ("made/main/content/fireball.py shadows the library's
fireball"), and its live instances switch as on any swap. The same id twice in one folder is
refused. When the shadowing file goes (a full reload), the library's version runs again; a
reload of a shadowed file alone changes nothing, and its reply says why (`notes`).
A restart restores the saved records the same way (`attach`); a record whose class did not load
is kept aside (an orphan) and comes back when its class loads.
"""

from __future__ import annotations

import logging
import os
import sys

from . import abilities
from .actors import body_of
from .marks import UUID_RE, mob_body
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
    errors, touched, notes = [], [], []
    if full:
        files = content_files(rt.settings.content)
    else:
        files = []
        for p in paths:
            path, why = content_path(rt, p)
            if path is None:
                errors.append({"file": str(p), "check": "path", "error": why})
            elif path not in files:
                files.append(path)
    for path in files:
        res = load_file(path, rt.guard, rt.now)
        errors.extend(res.errors)
        if res.module is None:
            continue
        defined = {e.get("id") for e in res.errors if e.get("id")}
        rank = folder_rank(rt, path)
        keep = []
        for info in res.classes:
            others = {p: c for p, c in rt.candidates.get(info.id, {}).items() if p != path}
            twin = next((p for p in others if folder_rank(rt, p) == rank), None)
            if twin is not None:
                errors.append({"file": path, "class": info.name, "id": info.id, "check": "base",
                               "error": "the id %r is already taken by %s, in the same folder" % (info.id, twin)})
                continue
            other_kind = next((c for c in others.values() if c.kind != info.kind), None)
            if other_kind is not None:
                errors.append({"file": path, "class": info.name, "id": info.id, "check": "base",
                               "error": "%r is %s in %s; %s cannot take its id"
                                        % (info.id, _a(other_kind.kind), other_kind.file, _a(info.kind))})
                continue
            keep.append(info)
        before = rt.files.get(path, set())
        for info in keep:
            rt.candidates.setdefault(info.id, {})[path] = info
        for cid in before - {i.id for i in keep} - defined:
            rt.candidates.get(cid, {}).pop(path, None)          # no longer in this file
        rt.files[path] = {i.id for i in keep} | (before & defined)
        rt.modules.add(res.module)
        touched.extend(cid for cid in [i.id for i in keep] + sorted(before) if cid not in touched)
    if full:
        for path in [p for p in rt.files if p not in files]:
            for cid in rt.files.pop(path):
                rt.candidates.get(cid, {}).pop(path, None)       # its file is gone
                if cid not in touched:
                    touched.append(cid)
    loaded, switched, stopped = [], 0, []
    for cid in touched:
        n, st, outcome = settle(rt, cid)
        switched += n
        stopped.extend(st)
        if outcome == "loaded":
            loaded.append(cid)
        elif outcome:
            notes.append(outcome)
    forget_modules(rt)
    rt.export_incantations()
    for e in errors:
        log.warning("reload refused %s%s: %s (%s); the old version, if any, keeps running",
                    os.path.basename(e["file"]), " " + e["class"] if e.get("class") else "", e["error"], e["check"])
    if loaded:
        log.info("reload: %s loaded; %d instances switched, %d stopped", ", ".join(loaded), switched, len(stopped))
    return {"ok": not errors, "loaded": loaded, "errors": errors, "switched": switched, "stopped": stopped,
            **({"notes": notes} if notes else {}),
            **({"error": "%d check%s failed" % (len(errors), "" if len(errors) == 1 else "s")} if errors else {})}


def _a(kind: str) -> str:
    return ("an " if kind[0] in "aeiou" else "a ") + kind


def content_path(rt, path) -> tuple[str | None, str]:
    """A path asked to be reloaded, as the content file a full reload would name it, or None and
    why not in words: its folder (symlinks and `..` resolved) must be one of the content folders,
    and it a *.py not starting with _ or . (vfg_engine.loader.content_files)."""
    if not isinstance(path, str) or not path.strip():
        return None, "%r is not a path" % (path,)
    here = os.path.realpath(os.path.dirname(os.path.abspath(path)))
    name = os.path.basename(path)
    for folder in rt.settings.content:
        if os.path.realpath(folder) == here:
            if not name.endswith(".py") or name.startswith(("_", ".")):
                return None, ("%s is not a content file (content is the *.py not starting with _ or . "
                              "in a content folder); nothing was loaded from it" % path)
            return os.path.abspath(os.path.join(folder, name)), ""
    return None, ("%s is not in a content folder (%s); only files directly in one of them are "
                  "content, so nothing was loaded from it"
                  % (path, ", ".join(rt.settings.content) or "none set"))


def folder_rank(rt, path: str) -> int:
    """Which content folder a file is in (0 the first, the library); a later one shadows an earlier.
    -1 for a file in none of them (reload refuses those): it never shadows anything."""
    here = os.path.realpath(os.path.dirname(os.path.abspath(path)))
    for i, folder in enumerate(rt.settings.content):
        if os.path.realpath(folder) == here:
            return i
    return -1


def effective(rt, cid):
    """The class an id runs as: its version in the latest content folder."""
    cands = rt.candidates.get(cid) or {}
    if not cands:
        return None
    return cands[max(cands, key=lambda p: folder_rank(rt, p))]


def shadows(rt, cid, over: str | None = None) -> str | None:
    """The file whose version of `cid` the running one (or the one in `over`) shadows: the next
    folder down, if any."""
    if over is None:
        running = rt.classes.get(cid)
        over = running.file if running is not None else None
    lower = [p for p in (rt.candidates.get(cid) or {}) if p != over]
    return max(lower, key=lambda p: folder_rank(rt, p)) if lower else None


def settle(rt, cid) -> tuple[int, list[dict], str | None]:
    """Run `cid` as its effective version: swapped in if it changed, unloaded if none is left.
    (switched, stopped, "loaded" or a note or None)."""
    best = effective(rt, cid)
    running = rt.classes.get(cid)
    if best is None:
        if running is None:
            return 0, [], None
        return 0, unload(rt, cid, "its file %s is gone" % os.path.basename(running.file)), None
    if best is running:
        lower = [c for p, c in (rt.candidates.get(cid) or {}).items() if p != best.file]
        fresh = [c for c in lower if c.loaded_at == rt.now]
        if fresh:
            note = ("%s in %s is shadowed by %s: loaded, not running" % (cid, fresh[0].file, best.file))
            log.info(note)
            return 0, [], note
        return 0, [], None
    under = shadows(rt, cid, best.file)
    if under:
        log.info("%s shadows %s's %s", best.file, under, cid)
    elif running is not None and running.file != best.file:
        log.info("%s's %s runs again: the version that shadowed it is gone", best.file, cid)
    n, st = swap(rt, best)
    return n, st, "loaded"


def forget_modules(rt):
    """Take every content module no loaded class comes from any more out of sys.modules. One a
    class still runs from (its file's newer version failed a check), or a shadowed version that may
    run again, stays."""
    in_use = {info.module for info in rt.classes.values()}
    in_use |= {info.module for cands in rt.candidates.values() for info in cands.values()}
    for name in list(rt.modules):
        if name not in in_use:
            sys.modules.pop(name, None)
            rt.modules.discard(name)


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
    if rec.kind in ("scenario", "mark") and getattr(cls, "Args", None) is not None:
        fits = rt.guard.call("%s.Args" % rec.iid, lambda: cls.Args.model_validate(rec.args or {}))
        if not fits.ok:
            words = " ".join(fits.error.split()).split(" For further information")[0]
            return "its args no longer fit %s.Args (%s)" % (info.name, words)
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
    rt.release(rec.iid, rec.owned, stopped=True)
    rec.subs = [s for s in rec.subs if s.run is not None]
    rec.timers = [t for t in rec.timers if t.run is not None]
    rec.started = False
    rt.start(rec)
    if rec.iid in rt.records:
        rec.ends = ends


def unload(rt, cid: str, reason: str) -> list[dict]:
    rt.classes.pop(cid, None)
    if not rt.candidates.get(cid):
        rt.candidates.pop(cid, None)
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
    if rec.kind == "mark" and rec.user and rec.user not in rt.actors and UUID_RE.match(rec.user):
        mob_body(rt, rec.user)               # a mark on a vanilla mob: track it again
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
