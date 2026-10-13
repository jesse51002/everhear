"""A fake game engine for the toolkit's tests: the control socket a box's vfg-engine serves
(engine/vfg_engine/server.py's protocol: one connection a request, one JSON line in, {"op", "args",
"timeout_s"}, one line out, then closed), answering the design's control ops from a little state
of its own, its replies shaped as the engine's (engine/vfg_engine/ops.py). No pydantic, no Minecraft.

    engine = FakeEngine(box.rvfg).start()     # <vfg>/engine/engine.sock
    engine.requests                           # every (op, args) it was sent
    engine.refuse["grant"] = "no such ability"  # the next grants answer ok false, with that error
    engine.hold.add("status")                 # answer as the socket does past timeout_s
    engine.stop()

What it holds: a registry (`classes`, each {id, kind, file, summary}; a content class's file is in
<vfg>/made/main/content/), grants, gives, scenarios started, bindings (the export written to
<vfg>/engine/incantations.json on every change, as the engine writes it). A reload reads the files
it is given (every file in the content dir when none): one holding the line `# REFUSE <check>:
<error>` fails that check, the rest load. config_get reads the deployed file through the rewrite
module the test hands it (the engine's own, engine/vfg_engine/rewrite.py).
"""
import json
import os
import socketserver
import threading

FIREBALL_ID = "fireball"


class FakeEngine(object):
    def __init__(self, vfg_dir, server="main", rewrite=None):
        self.vfg = vfg_dir
        self.path = os.path.join(vfg_dir, "engine", "engine.sock")
        self.content = os.path.join(vfg_dir, "made", server, "content")
        self.export = os.path.join(vfg_dir, "engine", "incantations.json")
        self.rewrite = rewrite
        self.requests = []
        self.refuse = {}
        self.hold = set()
        self.switched = 2
        self.stopped = []
        self.alias_cap = 24     # the aliases a binding keeps, as the engine stores them
        self.classes = [
            {"id": "fireball", "kind": "ability", "file": os.path.join(self.content, "fireball.py"),
             "summary": "Burns for burn_s seconds, then explodes in a radius."},
            {"id": "fire_staff", "kind": "item", "file": os.path.join(self.content, "fire_staff.py"),
             "summary": "Right-click to cast Fireball. Holding it adds 20% speed.", "base": "minecraft:carrot_on_a_stick"},
            {"id": "giant", "kind": "mark", "file": "/opt/vfg/engine/vfg_engine/examples/marks.py",
             "summary": "Twice the size for a while."},
            {"id": "horde", "kind": "scenario", "file": os.path.join(self.content, "horde.py"),
             "summary": "Waves of zombies round every player."},
            {"id": "ice_queen", "kind": "actor", "file": os.path.join(self.content, "ice_queen.py"),
             "summary": "A boss of ice."},
        ]
        self.grants = []        # [(who, ability)]
        self.gives = []         # [(player, item)]
        self.marks = []         # [(who, mark, seconds)]
        self.running = []       # scenario instances
        self.bindings = []
        self.version = 0
        self.lock = threading.Lock()
        self.server = None

    # ---- the socket

    def start(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        engine = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                line = self.rfile.readline(1 << 20)
                try:
                    req = json.loads(line.decode("utf-8"))
                    op, args = req["op"], req.get("args") or {}
                except (ValueError, KeyError, TypeError):
                    reply = {"ok": False, "error": "the request is not JSON", "reason": "bad_request"}
                else:
                    reply = engine.answer(op, args, req.get("timeout_s"))
                self.wfile.write((json.dumps(reply) + "\n").encode("utf-8"))

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        if os.path.exists(self.path):
            os.remove(self.path)
        self.server = Server(self.path, Handler)
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        return self

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        try:
            os.remove(self.path)
        except OSError:
            pass

    def ops(self, op=None):
        """The (op, args) it was sent; with op, the args of those."""
        return [args for o, args in self.requests if o == op] if op else list(self.requests)

    # ---- the ops

    def answer(self, op, args, timeout_s):
        with self.lock:
            self.requests.append((op, args))
            if op in self.hold:
                return {"ok": False, "reason": "timeout", "cancelled": True,
                        "error": "the engine did not answer %s within %g s; it was taken off the queue and will "
                                 "not be carried out" % (op, timeout_s or 10)}
            if op in self.refuse:
                return {"ok": False, "error": self.refuse[op]}
            fn = getattr(self, "op_" + op, None)
            if fn is None:
                return {"ok": False, "error": "unknown op %s" % op}
            try:
                return dict({"ok": True}, **fn(**args))
            except Refused as e:
                return {"ok": False, "error": str(e)}

    def find(self, wanted, kind=None):
        for c in self.classes:
            if c["id"] == wanted and (kind is None or c["kind"] == kind):
                return c
        raise Refused("no %s %s" % (kind or "class", wanted))

    def op_status(self):
        kinds = {}
        for _ in self.grants:
            kinds["ability"] = kinds.get("ability", 0) + 1
        for _ in self.running:
            kinds["scenario"] = kinds.get("scenario", 0) + 1
        return {"tick": 4821, "tick_hz": 10, "tick_ms": {"p50": 1.2, "p95": 3.4}, "players": ["Alex", "Steve"],
                "instances": kinds, "classes": len(self.classes), "slow": [], "orphans": 0,
                "started_at": 1791830400.0}

    def op_classes(self):
        return {"classes": [dict(c, **{"class": "".join(w.title() for w in c["id"].split("_"))}) for c in self.classes]}

    def op_meta(self, id=None):
        rows = [{"id": c["id"], "kind": c["kind"], "class": c["id"].title(), "summary": c["summary"],
                 "required": {"summary": c["summary"], "damage": 8.0, "duration_s": 4.0, "cooldown_s": 5},
                 "damage": {"0": 8.0, "10": 12.0, "20": 16.0}, "fields": {"radius": 3, "burn_s": 3.0}}
                for c in self.classes if id in (None, c["id"])]
        if id and not rows:
            raise Refused("%r is not a loaded class" % id)
        return {"rows": rows}

    def op_world(self):
        return {"settings": [{"name": "mob_griefing", "value": "false", "original": "true",
                              "holds": [{"holder": "scenario:horde#1", "value": "false", "priority": 10,
                                         "since": 1791830460.0}]}]}

    def op_instances(self, kind=None, user=None):
        rows = [{"instance": "ability:%s#%d" % (ab, i + 1), "kind": "ability", "id": ab, "user": who,
                 "started": 1791830460.0} for i, (who, ab) in enumerate(self.grants)]
        rows += [dict(r) for r in self.running]
        return {"instances": [r for r in rows if kind in (None, r["kind"]) and user in (None, r["user"])]}

    def op_grant(self, who, ability):
        self.find(ability, "ability")
        if (who, ability) in self.grants:
            return {"instance": "ability:%s#1" % ability, "note": "%s already has %s" % (who, ability)}
        self.grants.append((who, ability))
        return {"instance": "ability:%s#%d" % (ability, len(self.grants))}

    def op_revoke(self, who, ability):
        if (who, ability) not in self.grants:
            raise Refused("%s does not have %s" % (who, ability))
        self.grants.remove((who, ability))
        return {}

    def op_give(self, player, item, base=None):
        c = self.find(item, "item")
        if base and base.split(":")[-1] != c["base"].split(":")[-1]:
            raise Refused("%s is given on %s, not %s" % (item, c["base"], base))
        self.gives.append((player, item))
        return {}

    def op_take(self, player, item):
        self.find(item, "item")
        return {}

    def op_apply(self, who, mark, seconds=None):
        self.find(mark, "mark")
        self.marks.append((who, mark, seconds))
        return {}

    def op_clear(self, who, mark):
        return {}

    def op_start(self, scenario, args=None):
        self.find(scenario, "scenario")
        inst = "scenario:%s#%d" % (scenario, len(self.running) + 1)
        self.running.append({"instance": inst, "kind": "scenario", "id": scenario, "user": None,
                             "started": 1791830460.0, "ends": 1791830580.0, "args": args or {}})
        return {"instance": inst}

    def op_stop(self, instance=None, scenario=None):
        hit = [r for r in self.running if r["instance"] == instance or r["id"] == scenario]
        if not hit:
            raise Refused("nothing runs as %s" % (instance or scenario))
        self.running = [r for r in self.running if r not in hit]
        return {"stopped": [r["instance"] for r in hit]}

    def op_spawn(self, actor, at=None, near=None):
        self.find(actor, "actor")
        return {"uuid": "0e5b7c1a-3d2f-4a6b-9c8d-7e6f5a4b3c2d"}

    def op_bind(self, player, phrase, ability, aliases=(), everyday_aliases=(), by=None):
        self.find(ability, "ability")
        if player.startswith("@"):
            raise Refused("incantations are per player")
        kept = list(aliases)[:self.alias_cap]
        entry = {"id": "%s.%s" % (player.lower(), phrase.replace(" ", "_")), "player": player, "phrase": phrase,
                 "aliases": kept, "everyday_aliases": [x for x in everyday_aliases if x in kept], "ability": ability,
                 "cooldown_s": 5.0, "by": by}
        self.bindings = [e for e in self.bindings if e["id"] != entry["id"]] + [entry]
        self.write_export()
        row = next(e for e in self.listing(player) if e["id"] == entry["id"])
        return {"id": entry["id"], "version": self.version, "entry": row}

    def op_unbind(self, player, phrase):
        hit = [e for e in self.bindings if e["player"].lower() == player.lower() and e["phrase"] == phrase]
        if not hit:
            raise Refused("%s has no incantation %r" % (player, phrase))
        self.bindings = [e for e in self.bindings if e not in hit]
        self.write_export()
        return {"id": hit[0]["id"], "version": self.version}

    def listing(self, player=None):
        return [dict((k, e[k]) for k in ("id", "player", "phrase", "aliases", "everyday_aliases", "ability",
                                         "cooldown_s"))
                for e in self.bindings if player is None or e["player"].lower() == player.lower()]

    def op_incantations(self, player=None):
        return {"version": self.version, "incantations": self.listing(player)}

    def write_export(self):
        self.version += 1
        os.makedirs(os.path.dirname(self.export), exist_ok=True)
        tmp = self.export + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"version": self.version, "incantations": self.listing()}, f)
        os.replace(tmp, self.export)

    def op_log(self, n=40):
        return {"lines": [{"t": 1791830463.0, "level": "info", "msg": "loaded fireball", "file": "fireball.py"},
                          "a line of text"][-n:]}

    def class_name(self, path, wanted):
        import ast
        tree = ast.parse(open(path).read())
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name.lower().replace("_", "") == wanted.replace("_", ""):
                return node.name
        raise Refused("no class %s in %s" % (wanted, path))

    def op_config_get(self, **args):
        wanted = args["class"]
        c = self.find(wanted)
        if not c["file"].startswith(self.content):   # the engine's own examples: on the box, not made here
            return {"file": c["file"], "start": 4, "end": 6, "source": "    class Config(MarkConfig):\n        ...\n"}
        if not os.path.isfile(c["file"]):
            raise Refused("%s's file %s is not there" % (wanted, c["file"]))
        source = open(c["file"]).read()
        name = self.class_name(c["file"], wanted)
        start, end = self.rewrite.find_config(source, name)
        return {"id": c["id"], "class": name, "file": c["file"], "start": start, "end": end,
                "source": "".join(source.splitlines(True)[start - 1:end])}

    def op_reload(self, paths=None):
        if paths is None:
            paths = sorted(os.path.join(self.content, n) for n in os.listdir(self.content) if n.endswith(".py"))
        loaded, errors = [], []
        for p in paths:
            try:
                text = open(p).read()
            except OSError as e:
                errors.append({"file": p, "check": "imports", "error": str(e)})
                continue
            refusal = [l.split("# REFUSE ", 1)[1] for l in text.splitlines() if "# REFUSE " in l]
            if refusal:
                check, _, error = refusal[0].partition(":")
                errors.append({"file": p, "check": check.strip(), "error": error.strip()})
            else:
                loaded.append(os.path.splitext(os.path.basename(p))[0])
        return {"loaded": loaded, "errors": errors, "switched": self.switched if loaded else 0,
                "stopped": list(self.stopped)}


class Refused(Exception):
    pass
