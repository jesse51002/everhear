"""The control ops (DESIGN, "THE CONTROL OPS"): what `Engine.submit(op, args)` answers.

Each op is handled in the tick after it was submitted, in order with the other inputs, and its
reply is a JSON-able dict with `ok`, and `error` (in words) when not ok. The ones that write to
Minecraft (give, take, spawn) answer once the tick's batch has gone out, with what Minecraft
said; a cast answers once its on_cast has run.
"""

from __future__ import annotations

import collections
import logging
import statistics

from . import abilities, actors, items, marks, meta, rewrite, scenarios
from .reload import reload

log = logging.getLogger("vfg_engine")

CASTS_KEPT = 1000          # cast_ids remembered, so a repeated one answers as the first did


class OpError(Exception):
    """A refusal, in words."""


def _words(exc) -> str:
    """A refusal's message as it was written. A KeyError's str() is the repr of its message, quotes
    added; stripping quotes off any message would also cut the ones it holds ("'nope' is not a
    loaded item" came back as "nope' is not a loaded item")."""
    if isinstance(exc, KeyError) and len(exc.args) == 1 and isinstance(exc.args[0], str):
        return exc.args[0]
    return str(exc)


def _need(args, name):
    value = args.get(name)
    if value is None or value == "":
        raise OpError("%s is required" % name)
    return value


class Ops:
    def __init__(self, engine):
        self.engine = engine
        self.rt = engine.rt
        self._casts: collections.OrderedDict = collections.OrderedDict()

    def handle(self, op: str, args, reply):
        """Answer one op: reply(doc) now, or later for the ones that wait on Minecraft."""
        fn = getattr(self, "op_" + str(op), None)
        if fn is None:
            reply({"ok": False, "error": "unknown op %r (ops: %s)" % (op, ", ".join(sorted(self.names())))})
            return
        try:
            doc = fn(dict(args or {}), reply)
        except (OpError, ValueError, KeyError, LookupError) as exc:
            reply({"ok": False, "error": _words(exc)})
            return
        except Exception as exc:
            log.exception("op %s failed", op)
            reply({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
            return
        if doc is not None:
            reply({"ok": True, **doc} if "ok" not in doc else doc)

    def names(self):
        return [n[3:] for n in dir(self) if n.startswith("op_")]

    def _after_flush(self, reply, ok_doc):
        """A callback for the op's write: replies with what Minecraft said."""
        def done(op, result):
            if result.ok:
                reply({"ok": True, **ok_doc})
            else:
                reply({"ok": False, "error": result.error or result.reply or "Minecraft refused it"})
        return done

    # ---- reading -------------------------------------------------------------------------

    def op_status(self, args, reply):
        e, rt = self.engine, self.rt
        ms = list(e.tick_ms)
        q = statistics.quantiles(ms, n=20) if len(ms) >= 2 else ms * 19
        kinds = collections.Counter(r.kind for r in rt.records.values())
        return {"tick": e.tick_n, "tick_hz": e.settings.tick_hz,
                "tick_ms": {"p50": round(statistics.median(ms), 2) if ms else None,
                            "p95": round(q[18], 2) if ms else None},
                "players": sorted(n for n, p in rt.players.items() if p.online),
                "instances": dict(kinds), "classes": len(rt.classes),
                "slow": [{"instance": k, "why": v} for k, v in sorted(rt.guard.flagged.items())],
                "orphans": len(rt.orphans), "started_at": e.started_at}

    def op_classes(self, args, reply):
        return {"classes": [{"id": i.id, "kind": i.kind, "class": i.name, "file": i.file,
                             "summary": i.config.summary}
                            for i in sorted(self.rt.classes.values(), key=lambda i: (i.kind, i.id))]}

    def op_meta(self, args, reply):
        cid = args.get("id")
        if cid and cid not in self.rt.classes:
            raise OpError("%r is not a loaded class" % cid)
        return {"rows": meta.rows(self.rt.classes, cid, self.rt.guarded)}

    def op_world(self, args, reply):
        return {"settings": self.rt.world_settings.listing()}

    def op_instances(self, args, reply):
        kind, user = args.get("kind"), args.get("user")
        out = []
        for r in self.rt.records.values():
            if (kind and r.kind != kind) or (user and str(r.user or "").lower() != str(user).lower()):
                continue
            row = {"instance": r.iid, "kind": r.kind, "id": r.cid, "user": r.user, "started": r.created}
            if r.ends is not None:
                row["ends"] = r.ends
            if r.paused_at is not None:
                row["paused"] = True
            if r.runs:
                row["runs"] = [{"run": n, "ends": x.ends, "charging": not x.fired} for n, x in r.runs.items()]
            if r.stack is not None:
                row["stack"] = r.stack
            out.append(row)
        return {"instances": out}

    def op_log(self, args, reply):
        n = int(args.get("n") or 100)
        return {"lines": list(self.engine.log_lines)[-max(1, n):]}

    def op_config_get(self, args, reply):
        name = _need(args, "class")
        info = self.rt.classes.get(name) or next(
            (i for i in self.rt.classes.values() if i.name == name), None)
        if info is None:
            raise OpError("%r is not a loaded class" % name)
        with open(info.file, encoding="utf-8") as f:
            source = f.read()
        start, end = rewrite.find_config(source, info.name)
        return {"id": info.id, "class": info.name, "file": info.file, "start": start, "end": end,
                "source": "\n".join(source.splitlines()[start - 1:end]) + "\n"}

    def op_incantations(self, args, reply):
        rt = self.rt
        return {"version": rt.bindings.version,
                "incantations": rt.bindings.listing(rt.cooldown_of, args.get("player"))}

    # ---- changing ------------------------------------------------------------------------

    def op_reload(self, args, reply):
        paths = args.get("paths")
        if paths is not None and not isinstance(paths, list):
            raise OpError("paths must be a list of files")
        return reload(self.rt, paths or None)

    def op_grant(self, args, reply):
        who, ability = str(_need(args, "who")), str(_need(args, "ability"))
        iid = abilities.grant(self.rt, self.rt.target_of(who), ability)
        return {"instance": iid or self.rt.being(self.rt.target_of(who)).grants.get(ability),
                **({} if iid else {"note": "%s already has %s" % (who, ability)})}

    def op_revoke(self, args, reply):
        who, ability = str(_need(args, "who")), str(_need(args, "ability"))
        if not abilities.revoke(self.rt, self.rt.target_of(who), ability):
            raise OpError("%s does not have %s" % (who, ability))
        return {}

    def op_give(self, args, reply):
        player, item = str(_need(args, "player")), str(_need(args, "item"))
        op = items.give_op(self.rt, self.rt.target_of(player), item, args.get("base"))
        self.rt.queue(op, self._after_flush(reply, {"item": item, "base": op.base}))
        return None

    def op_take(self, args, reply):
        player, item = str(_need(args, "player")), str(_need(args, "item"))
        self.rt.queue(items.take_op(self.rt.target_of(player), item), self._after_flush(reply, {"item": item}))
        return None

    def op_apply(self, args, reply):
        who, mark = str(_need(args, "who")), str(_need(args, "mark"))
        iid = marks.apply(self.rt, self.rt.target_of(who), mark, args.get("seconds"), source=None)
        return {"instance": iid}

    def op_clear(self, args, reply):
        who, mark = str(_need(args, "who")), str(_need(args, "mark"))
        n = marks.clear(self.rt, self.rt.target_of(who), mark)
        if not n:
            raise OpError("%s has no %s" % (who, mark))
        return {"cleared": n}

    def op_start(self, args, reply):
        scenario = str(_need(args, "scenario"))
        return {"instance": scenarios.start(self.rt, scenario, args.get("args") or {})}

    def op_stop(self, args, reply):
        rt = self.rt
        iid, scenario = args.get("instance"), args.get("scenario")
        if iid:
            rec = rt.records.get(iid)
            if rec is None:
                raise OpError("no instance %r is running" % iid)
            rt.end_record(rec, "stopped")
            return {"stopped": [iid]}
        if scenario:
            recs = [r for r in rt.records.values() if r.kind == "scenario" and r.cid == scenario]
            if not recs:
                raise OpError("no %s is running" % scenario)
            for r in recs:
                rt.end_record(r, "stopped")
            return {"stopped": [r.iid for r in recs]}
        raise OpError("stop needs instance or scenario")

    def op_spawn(self, args, reply):
        rt = self.rt
        actor = str(_need(args, "actor"))
        info = rt.classes.get(actor)
        if info is None or info.kind != "actor":
            raise OpError("%r is not a loaded actor" % actor)
        at, near = args.get("at"), args.get("near")
        if at is not None:
            if not (isinstance(at, (list, tuple)) and len(at) == 3):
                raise OpError("at must be [x, y, z]")
            pos, dim = tuple(float(v) for v in at), args.get("dim")
        elif near:
            p = rt.snapshot.players.get(rt.target_of(near))
            if p is None:
                raise OpError("%s is not online" % near)
            fx, _, fz = p.facing
            pos, dim = (p.pos[0] + 3 * fx, p.pos[1], p.pos[2] + 3 * fz), p.dim
        else:
            raise OpError("spawn needs at=[x, y, z] or near=<player>")
        uuid = actors.spawn(rt, actor, pos, dim)
        # its reply waits for the summon: the uuid is already known
        for i in range(len(rt.ops) - 1, -1, -1):
            op, cb = rt.ops[i]
            if getattr(op, "uuid", None) == uuid:
                def both(op, res, cb=cb):
                    cb(op, res)
                    self._after_flush(reply, {"uuid": uuid})(op, res)
                rt.ops[i] = (op, both)
                break
        return None

    def op_bind(self, args, reply):
        rt = self.rt
        player, phrase, ability = _need(args, "player"), _need(args, "phrase"), str(_need(args, "ability"))
        info = rt.classes.get(ability)
        if info is None or info.kind != "ability":
            raise OpError("%r is not a loaded ability: bind it once the ability exists" % ability)
        if not abilities.triggered(info.cls):
            raise OpError("%s is passive: an incantation needs an ability with on_cast" % ability)
        entry = rt.bindings.bind(player, phrase, ability, args.get("aliases") or (),
                                 args.get("everyday_aliases") or (), args.get("by"), rt.now)
        rt.export_incantations()
        row = next(e for e in rt.bindings.listing(rt.cooldown_of, entry["player"]) if e["id"] == entry["id"])
        return {"id": entry["id"], "version": rt.bindings.version, "entry": row}

    def op_unbind(self, args, reply):
        rt = self.rt
        entry = rt.bindings.unbind(_need(args, "player"), _need(args, "phrase"))
        rt.export_incantations()
        return {"id": entry["id"], "version": rt.bindings.version}

    def op_cast(self, args, reply):
        player, phrase = str(_need(args, "player")), _need(args, "phrase")
        cast_id = args.get("cast_id")
        if cast_id and cast_id in self._casts:
            return dict(self._casts[cast_id], repeated=True)
        doc = self._cast(player, phrase, cast_id)
        if cast_id:
            self._casts[cast_id] = doc
            while len(self._casts) > CASTS_KEPT:
                self._casts.popitem(last=False)
        return doc

    def _cast(self, player, phrase, cast_id) -> dict:
        rt = self.rt
        entry = rt.bindings.lookup(player, phrase)
        if entry is None:
            return {"ok": True, "status": "unknown"}
        out = {"ok": True, "incantation": entry["id"], "ability": entry["ability"]}
        being = rt.player(player)
        if entry["ability"] not in rt.classes:
            return {**out, "status": "failed", "error": "%s is not loaded" % entry["ability"]}
        if being is None:
            return {**out, "status": "not_granted"}
        status, extra = abilities.cast(rt, being, entry["ability"], by="incantation",
                                       phrase=entry["phrase"], cast_id=cast_id)
        out["status"] = status
        if "remaining_s" in extra:
            out["remaining_s"] = extra["remaining_s"]
        if status == "failed" and extra.get("error"):
            out["error"] = extra["error"]
        if status == "fired":
            # on_cast has run by now (nothing else was running); an error there ended its run
            why = rt.run_ends.get((extra["instance"], extra["run"]))
            if why and why.startswith(("error:", "cut:")):
                out.update(status="failed", error=why)
        return out
