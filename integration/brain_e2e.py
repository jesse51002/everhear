#!/usr/bin/env python3
"""The brain end to end on this PC, with no EC2: the spec's smoke test, on stand-ins.

    python3 integration/brain_e2e.py [--keep]

Real:  the brain (`vfg brain start` runs brain/run), its model calls through OpenRouter
       (OPEN_ROUTER_API: from the environment, else the first .env above this repo, or its
       Internal/.env, that holds it; passed to the brain's process only, never printed or
       written), the vfg toolkit (toolkit/vfg, under uv; its remote programs run through the
       fake ssh on the box's Python 3.12), actions/ and the scripts they run, the bridge's feed
       writer (bridge/vfg_feed.py, the box's lib/vfg_feed.py) and vfg.db, made by the bridge.
Stand-ins: SSH (toolkit/tests/fake_ssh, which runs the remote command here), Minecraft
       (scripts/_test/fake_places_world.py over scripts/_test/fake_rcon.py, in this process: 26.3's
       recorded replies, a world that keeps its players, effects and places, and here each
       player's items by slot), Jev (each player's line is put into the feed as a LINE row
       through the box's vfg_feed, as Jev puts it there), the command references (refs/26.3/
       seeded from brain/tests/fixtures/refs: the server's real command tree and a few wiki
       pages; nothing is fetched).

Nothing touches ~/.vfg, ~/.ssh or any remote host but OpenRouter: HOME, VFG_HOME and VFG_REPO
point into a temp folder. The table at the end times each line from the moment it was fed to
the moment its reply was said. Prints PASS/FAIL per check; exits non-zero when anything failed.
A full run makes nine model calls, about $0.09 (the first, uncached, about $0.06 of it). Not
part of any test suite.
"""
import argparse
import datetime as dt
import gzip
import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOOLKIT = os.path.join(REPO, "toolkit")
VFG = os.path.join(TOOLKIT, "vfg")
SCRIPTS_TEST = os.path.join(REPO, "scripts", "_test")
FIXTURES = os.path.join(REPO, "brain", "tests", "fixtures", "refs")
UTC = dt.timezone.utc
KEY = "OPEN_ROUTER_API"
BUDGET = 0.10        # dollars of OpenRouter a full run may spend
WIKI = "https://minecraft.wiki/w/"

FEED_RE = re.compile(r"^([A-Z]+)#(\d+) (\d\d:\d\d:\d\d) (?:([^|]*?) )?\| (.*)$")
LOG_CALL = re.compile(r"^(\S+) (brain|respond) (#\d+|T\d+) (\d+) ms \$([\d.]+), (\d+) in")

# Each player's items as 26.3 prints them (`data get entity <p> Inventory`, equipment,
# SelectedItemSlot): Steve holds a diamond sword in hotbar.4, carries an iron sword and diamond
# boots, and wears iron boots.
ITEMS = {
    "Steve": {"slots": {0: "iron_sword", 4: "diamond_sword", 14: "diamond_boots"}, "selected": 4,
              "worn": {"feet": "iron_boots"}},
    "Alex": {"slots": {0: "bow", 1: "arrow"}, "selected": 0, "worn": {}},
}
WORN_SLOTS = {"armor.head": "head", "armor.chest": "chest", "armor.legs": "legs", "armor.feet": "feet",
              "weapon.offhand": "offhand"}


def parse_iso(s):
    return dt.datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


def wait_for(fn, timeout, step=0.1):
    end = time.monotonic() + timeout
    while True:
        v = fn()
        if v or time.monotonic() >= end:
            return v
        time.sleep(step)


def short(text, n=300):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + " …"


def scripts_test(name):
    """scripts/_test/<name>.py; fake_places_world imports mc_26_3 beside it."""
    if SCRIPTS_TEST not in sys.path:
        sys.path.insert(0, SCRIPTS_TEST)
    return __import__(name)


def find_key():
    """The OpenRouter key and where it came from (a path or "the environment"); never printed.
    The first .env above this repo that holds it, or the Internal/.env beside one of them (the
    company's repo, whose scripts read it)."""
    if os.environ.get(KEY, "").strip():
        return os.environ[KEY].strip(), "the environment"
    dirs, d = [], REPO
    while True:
        dirs.append(d)
        if os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)
    for path in [os.path.join(d, ".env") for d in dirs] + [os.path.join(d, "Internal", ".env") for d in dirs]:
        try:
            with open(path, encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if line.startswith("export "):
                        line = line[7:].lstrip()
                    k, sep, v = line.partition("=")
                    if sep and k.strip() == KEY:
                        v = v.strip()
                        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                            v = v[1:-1]
                        if v:
                            return v, path
        except OSError:
            pass
    return None, None


def seed_refs(brain_dir, version="26.3"):
    """refs/<version>/ as refs.load() takes a finished cache (complete.json, commands.json and
    index.json present): the fixture's command tree and its wiki pages (raw/<name>.txt is the
    page's URL, then the API's plain text). Every other command is marked not fetched, so it has its
    syntax alone. complete.json carries format 0, so the brain rebuilds usage.json, menu.txt and
    wiki/ from these with its own parser on its first load. Returns the folder."""
    d = os.path.join(brain_dir, "refs", version)
    os.makedirs(os.path.join(d, "raw"), exist_ok=True)
    with gzip.open(os.path.join(FIXTURES, "commands.json.gz")) as f:
        tree = json.load(f)
    with open(os.path.join(d, "commands.json"), "w") as f:
        json.dump(tree, f)
    index = {}
    for name in sorted(tree["children"]):
        index[name] = {"status": "failed", "why": "not in the e2e fixtures", "title": "Commands/" + name,
                       "url": WIKI + "Commands/" + name}
    for fn in sorted(os.listdir(os.path.join(FIXTURES, "wiki"))):
        if not fn.endswith(".json"):  # the folder's README.md (the pages' licence)
            continue
        name = fn[:-len(".json")]
        with open(os.path.join(FIXTURES, "wiki", fn)) as f:
            answer = json.load(f)
        page = answer["query"]["pages"][0]
        url = WIKI + page["title"].replace(" ", "_")
        with open(os.path.join(d, "raw", name + ".txt"), "w") as f:
            f.write(url + "\n" + page["extract"])
        index[name] = {"status": "ok", "title": page["title"], "url": url,
                       "redirected": bool(answer["query"].get("redirects")), "why": ""}
    with open(os.path.join(d, "index.json"), "w") as f:
        json.dump(index, f, indent=1, sort_keys=True)
    with open(os.path.join(d, "complete.json"), "w") as f:
        json.dump({"format": 0, "version": version, "seeded_by": "integration/brain_e2e.py"}, f)
    return d


def server_jar(mc_dir, version="26.3"):
    """A server.jar holding only version.json: all `vfg action refs --version` reads."""
    with zipfile.ZipFile(os.path.join(mc_dir, "server.jar"), "w") as z:
        z.writestr("version.json", json.dumps({"id": version, "name": version}))


def world_class():
    """fake_places_world's FakeWorld, with what the brain's calls also ask of a 26.3 server:
    tellraw, every player's items by slot, `item modify`, the time and the weather."""
    fpw = scripts_test("fake_places_world")
    fpw.ITEMS.update({"diamond_pickaxe": "Diamond Pickaxe", "bread": "Bread", "arrow": "Arrow",
                      "iron_ingot": "Iron Ingot", "emerald": "Emerald", "torch": "Torch"})

    def item(slot, name):
        return '{Slot: %db, id: "minecraft:%s", count: 1}' % (slot, name)

    class World(fpw.FakeWorld):
        def __init__(self, players):
            fpw.FakeWorld.__init__(self, players=players)
            self.items = json.loads(json.dumps(ITEMS))
            for p in self.items.values():
                p["slots"] = {int(k): v for k, v in p["slots"].items()}
            self.modified = []          # (player, slot, modifier) of every item modify that landed
            self.daytime = 6000

        def _respond(self, c, dim, at, full):
            m = re.match(r"^tellraw (\S+) ", c)
            if m:
                return "" if m.group(1).startswith("@") or m.group(1) in self.players else "No player was found"
            m = re.match(r"^data get entity (\w+) (Inventory|SelectedItemSlot)$", c)
            if m and m.group(1) in self.items:
                p = self.items[m.group(1)]
                if m.group(2) == "SelectedItemSlot":
                    return "%s has the following entity data: %d" % (m.group(1), p["selected"])
                return "%s has the following entity data: [%s]" % (
                    m.group(1), ", ".join(item(n, v) for n, v in sorted(p["slots"].items())))
            m = re.match(r"^execute if data entity (\w+) equipment run data get entity \1 equipment$", c)
            if m:
                worn = (self.items.get(m.group(1)) or {}).get("worn") or {}
                if not worn:
                    return ""
                return "%s has the following entity data: {%s}" % (m.group(1), ", ".join(
                    '%s: {id: "minecraft:%s", count: 1}' % (k, v) for k, v in worn.items()))
            m = re.match(r"^item modify entity (\w+) (\S+) (.+)$", c)
            if m:
                return self.modify(m.group(1), m.group(2), m.group(3))
            if c == "time query day":
                return "Timeline minecraft:day is at %d tick(s)" % self.daytime
            if c == "time query time":
                return "Clock minecraft:overworld is at %d tick(s)" % (2 * 24000 + self.daytime)
            if re.match(r'^execute if predicate \{"type":"minecraft:weather_check","(raining|thundering)":true\}$', c):
                return "Test failed"
            return fpw.FakeWorld._respond(self, c, dim, at, full)

        def modify(self, who, slot, modifier):
            p = self.items.get(who)
            if p is None:
                return "No entity was found"
            held = None
            m = re.match(r"^(hotbar|inventory)\.(\d+)$", slot)
            if m:
                held = p["slots"].get(int(m.group(2)) + (9 if m.group(1) == "inventory" else 0))
            elif slot == "weapon.mainhand":
                held = p["slots"].get(p["selected"])
            elif slot in WORN_SLOTS:
                held = p["worn"].get(WORN_SLOTS[slot])
            else:
                return "Unknown slot '%s'item modify entity %s %s<--[HERE]" % (slot, who, slot)
            if held is None:
                return "Nothing changed. Targets either have no item in their slot or the modifier did not apply"
            self.modified.append((who, slot, modifier))
            return "Modified 1 slot(s) on %s" % who

    return World


# ----------------------------------------------------------------- the report

class Report:
    def __init__(self):
        self.checks = []

    def head(self, title):
        print("\n== %s" % title, flush=True)

    def check(self, ok, name, detail=""):
        ok = bool(ok)
        self.checks.append((ok, name))
        print("  %s  %s" % ("PASS" if ok else "FAIL", name), flush=True)
        if detail and not ok:
            print("          " + short(detail, 1500), flush=True)
        return ok

    def note(self, text):
        print("  note  " + text, flush=True)

    def failed(self):
        return [n for ok, n in self.checks if not ok]


# --------------------------------------------------------------------- the box

class Box:
    """The temp folder: HOME, VFG_HOME and the brain's folder in it, the repo, the box's /opt/vfg
    and /opt/mc, and the fake world on the box's RCON port."""

    def __init__(self, root, box_py, key=None):
        j = lambda *p: os.path.join(root, *p)  # noqa: E731
        self.root, self.box_py, self.key = root, box_py, key
        self.home, self.vhome, self.brain = j("home"), j("home", ".vfg"), j("home", ".vfg", "brain")
        self.repo, self.rvfg, self.rmc = j("repo"), j("box", "vfg"), j("box", "mc")
        self.bin, self.units, self.ssh_key = j("bin"), j("units"), j("fake_ssh_key")
        self.db, self.feed = j("box", "vfg", "vfg.db"), j("box", "vfg", "feed.log")
        self.password = secrets.token_hex(8)
        self.world = self.rcon = self.writer = None
        self.started = False
        # uv for brain/run keeps using the owner's cache and Pythons under the temp HOME.
        self.uv_env = {}
        for k, cmd in (("UV_CACHE_DIR", ["uv", "cache", "dir"]), ("UV_PYTHON_INSTALL_DIR", ["uv", "python", "dir"])):
            try:
                self.uv_env[k] = os.environ.get(k) or subprocess.run(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True).stdout.strip()
            except OSError:
                pass

    def deploy(self, players):
        for d in (self.vhome, self.brain, self.repo, os.path.join(self.rvfg, "lib"),
                  os.path.join(self.rvfg, "scripts", "_lib"), self.rmc, self.bin, self.units):
            os.makedirs(d, exist_ok=True)
        for d in (self.vhome, self.brain):
            os.chmod(d, 0o700)
        # The repo vfg brain start and vfg action run read: the real actions, scripts and brain.
        for name in ("actions", "scripts", "brain"):
            os.symlink(os.path.join(REPO, name), os.path.join(self.repo, name))
        # The box: the bridge's feed writer and vfg.db, mc.py (as vfg script deploy leaves it),
        # RCON's settings, the jar.
        shutil.copy(os.path.join(REPO, "bridge", "vfg_feed.py"), os.path.join(self.rvfg, "lib", "vfg_feed.py"))
        shutil.copy(os.path.join(REPO, "scripts", "_lib", "mc.py"), os.path.join(self.rvfg, "scripts", "_lib", "mc.py"))
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1", "VFG_DB": self.db,
               "PYTHONPATH": os.path.join(REPO, "bridge")}
        subprocess.run([self.box_py, "-m", "vfg_bridge", "--init-db"], env=env, cwd=self.rvfg, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        fake_rcon = scripts_test("fake_rcon")
        self.world = world_class()(players)
        self.rcon = fake_rcon.FakeRcon(self.password, responder=self.world.respond).start()
        with open(os.path.join(self.rmc, "server.properties"), "w") as f:
            f.write("#Minecraft server properties\nenable-rcon=true\nrcon.port=%d\nrcon.password=%s\n"
                    "server-port=25565\n" % (self.rcon.port, self.password))
        server_jar(self.rmc)
        for u in ("minecraft", "vfg-bridge", "vfg-jev"):
            with open(os.path.join(self.units, u), "w") as f:
                f.write("active\n")
        # python3 is the box's 3.12, for fake_ssh's remote side and the scripts; vfg is this repo's,
        # under uv as on the PC, with that PATH again in front.
        os.symlink(self.box_py, os.path.join(self.bin, "python3"))
        os.makedirs(os.path.join(self.rvfg, "python", "bin"), exist_ok=True)
        os.symlink(self.box_py, os.path.join(self.rvfg, "python", "bin", "python3"))  # the box's Python, at the box's path
        with open(os.path.join(self.bin, "vfg"), "w") as f:
            f.write("#!/bin/sh\nPATH='%s':\"$PATH\"; export PATH\nexec '%s' \"$@\"\n" % (self.bin, VFG))
        os.chmod(os.path.join(self.bin, "vfg"), 0o755)
        with open(self.ssh_key, "w") as f:
            f.write("not a key: fake_ssh never reads it\n")
        cfg = os.path.join(self.vhome, "config")
        with open(cfg, "w") as f:
            f.write("# brain_e2e: everything points into %s\n" % self.root)
            f.write("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\nVFG_SSH=%s\nVFG_REMOTE_VFG=%s\nVFG_REMOTE_MC=%s\n"
                    "VFG_REPO=%s\nVFG_OWNER=Steve\n" % (self.ssh_key, os.path.join(TOOLKIT, "tests", "fake_ssh"),
                                                       self.rvfg, self.rmc, self.repo))
        os.chmod(cfg, 0o600)
        self.refs = seed_refs(self.brain)
        # The box's feed writer, in this process, as the Jev filter imports it.
        os.environ["VFG_DB"], os.environ["VFG_FEED"] = self.db, self.feed
        spec = importlib.util.spec_from_file_location("vfg_feed", os.path.join(self.rvfg, "lib", "vfg_feed.py"))
        self.writer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.writer)

    # -- environments
    def env(self, with_key=False):
        drop = ("VFG_", "FAKE_", "CLAUDE", "TYPESAFE_", "ANTHROPIC_", "UV_", KEY)
        venv_bin = os.path.join(os.environ.get("VIRTUAL_ENV", "\0"), "bin")
        path = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and p != venv_bin]
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(drop) and k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
        env.update(self.uv_env)
        env.update({
            "HOME": self.home, "VFG_HOME": self.vhome, "VFG_REPO": self.repo, "FAKE_UNITS": self.units,
            "PYTHONDONTWRITEBYTECODE": "1", "VFG_RUNNING_TTL_S": "0",
            "PATH": os.pathsep.join([self.bin, os.path.join(TOOLKIT, "tests", "bin")] + path),
        })
        if with_key:
            env[KEY] = self.key
        return env

    def vfg(self, *args, timeout=60, with_key=False):
        return subprocess.run([os.path.join(self.bin, "vfg")] + list(args), env=self.env(with_key),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=timeout)

    # -- the brain
    def start(self):
        """`vfg brain start`, the key in its environment only: the brain inherits it."""
        r = self.vfg("brain", "start", with_key=True, timeout=60)
        self.started = True
        return r

    def stop(self):
        if not self.started:
            return None
        self.started = False
        return self.vfg("brain", "stop", timeout=60)

    def log(self):
        try:
            with open(os.path.join(self.brain, "brain.log"), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def tasks(self):
        try:
            with open(os.path.join(self.brain, "tasks.json")) as f:
                return json.load(f).get("tasks") or {}
        except (OSError, ValueError):
            return {}

    # -- the box's database
    def sql(self, query, params=()):
        con = sqlite3.connect("file:%s?mode=ro" % self.db, uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(query, params).fetchall()]
        finally:
            con.close()

    def rows(self, after=0, kind=None):
        """Feed rows after `after`: {seq, at, line, processed_at, type, fields, text}."""
        out = []
        for r in self.sql("SELECT seq, at, line, processed_at FROM feed WHERE seq > ? ORDER BY seq", (after,)):
            m = FEED_RE.match(r["line"])
            r.update(type=m.group(1) if m else "", fields=(m.group(4) or "") if m else "", text=m.group(5) if m else "")
            if kind is None or r["type"] == kind:
                out.append(r)
        return out

    def said(self, after=0):
        """The brain's chat lines since `after`: AI say rows."""
        return [r for r in self.rows(after, "AI") if r["fields"].startswith("say ")]

    def last_seq(self):
        rows = self.sql("SELECT MAX(seq) AS s FROM feed")
        return rows[0]["s"] or 0

    def usage(self, ref=None):
        if ref is None:
            return self.sql("SELECT * FROM usage WHERE component IN ('brain', 'respond') ORDER BY id")
        return self.sql("SELECT * FROM usage WHERE ref = ? ORDER BY id", (ref,))

    def lines(self, *said):
        """Players' lines into the feed in one write, as the Jev filter feeds a batch:
        (player, class, text) each; returns [(seq, fed at)]."""
        entries = []
        for who, cls, text in said:
            conf = "0.93" if cls == "action_oriented" else "0.88"
            entries.append(("LINE", "%s voice %s %s -9dB" % (who, cls, conf), text, None))
        seqs = self.writer.append_batch(entries)
        at = time.time()
        return [(s, at) for s in seqs]

    def close(self):
        try:
            if self.started:
                self.stop()
        except Exception:
            pass
        try:
            with open(os.path.join(self.brain, "brain.pid")) as f:
                os.killpg(int(f.read().strip()), signal.SIGKILL)
        except (OSError, ValueError):
            pass
        if self.rcon:
            self.rcon.stop()


# ======================================================================= the run

class Run:
    def __init__(self, box, rep):
        self.box, self.rep = box, rep
        self.asks = []  # {"seq", "at", "who", "text", "reply_at", "reply"}

    def line(self, who, cls, text):
        mark = len(self.box.world.sent)
        (seq, at), = self.box.lines((who, cls, text))
        ask = {"seq": seq, "at": at, "who": who, "text": text, "cls": cls, "mark": mark, "reply": None}
        self.asks.append(ask)
        self.rep.note("#%d %s (%s): %s" % (seq, who, cls, text))
        return ask

    def sent(self, ask):
        """The commands the fake world got since the line was fed."""
        return self.box.world.sent[ask["mark"]:]

    def reply(self, ask, timeout=40, task=None):
        """The first chat line after the line (for an action ask, the one under its task); its
        time is kept for the table."""
        def got():
            for r in self.box.said(ask["seq"]):
                if task is None or r["fields"] == "say %s" % task:
                    return r
            return None
        r = wait_for(got, timeout, 0.05)
        if r:
            ask["reply"] = r["text"]
            ask["reply_at"] = parse_iso(r["at"]).timestamp()
        return r

    def task_of(self, ask, timeout=40):
        """The task the brain opened for the line, from tasks.json."""
        def got():
            for tid, t in self.box.tasks().items():
                if t.get("line") == ask["text"]:
                    return tid
            return None
        return wait_for(got, timeout, 0.05)

    def call_ms(self, ref):
        """The brain's and Respond's calls for `ref` in the log: [(area, ms, cost, ended at)]."""
        out = []
        for line in self.box.log().splitlines():
            m = LOG_CALL.match(line)
            if m and m.group(3) == ref:
                out.append((m.group(2), int(m.group(4)), float(m.group(5)), parse_iso(m.group(1)).timestamp()))
        return out

    def table(self):
        print("\nTIMING: a line fed -> its reply said (the feed rows' times)")
        print("  %-5s %-6s %-16s %8s  %s" % ("line", "who", "class", "reply", "calls (model ms, $)"))
        for a in self.asks:
            took = "%.1f s" % (a["reply_at"] - a["at"]) if a.get("reply_at") else "-"
            calls = ", ".join("%s %d ms $%.4f" % (area, ms, cost) for area, ms, cost, _ in self.call_ms("#%d" % a["seq"]))
            print("  #%-4d %-6s %-16s %8s  %s" % (a["seq"], a["who"], a["cls"], took, calls or "-"))
            print("        %s  ->  %s" % (short(a["text"], 60), short(a.get("reply") or "(no reply)", 80)))


def one_reply(run, ask, settle=3.0):
    """After the reply, a moment more: every chat line since the line (one, when it got one)."""
    time.sleep(settle)
    return [r["text"] for r in run.box.said(ask["seq"])]


def start(run):
    rep, box = run.rep, run.box
    rep.head("start: vfg brain start")
    r = box.start()
    up = wait_for(lambda: re.search(r"^\S+ start pid \d+;", box.log(), re.M), 120, 0.2)
    rep.check(r.returncode == 0 and up, "vfg brain start: the brain is up and following the feed",
              "%s %s; log %s" % (r.stdout, r.stderr, short(box.log()[-1500:])))
    if not up:
        return False
    m = re.search(r"^\S+ refs server (\S+): (\d+) commands", box.log(), re.M)
    rep.check(wait_for(lambda: re.search(r"^\S+ refs server 26\.3: (\d+) commands", box.log(), re.M), 30),
              "the command references load from the seeded refs/26.3/", short(box.log()[-1500:]))
    greet = wait_for(lambda: box.said(), 20)
    rep.check(greet and greet[0]["text"].startswith("AI is on"), "it greets the players: an AI say row",
              "%r" % greet)
    files = [os.path.join(box.rvfg, n) for n in ("watch.json", "lookout.md", "menu.md")]
    ok = wait_for(lambda: all(os.path.exists(p) for p in files), 20)
    watch, menu = {}, ""
    if ok:
        with open(files[0]) as f:
            watch = json.load(f)
        with open(files[2], encoding="utf-8") as f:
            menu = f.read()
    rep.check(ok and watch.get("events") == ["join", "leave", "death", "advancement", "server"]
              and "command — " in menu, "it set the watchers, Jev's lookout and Jev's menu on the box",
              "%r %r" % (watch, menu[:200]))
    m = re.search(r"; (\d+) actions;", box.log())
    rep.note("%s actions on the menu" % (m.group(1) if m else "?"))
    return True


def ask_action(run):
    rep, box = run.rep, run.box
    rep.head("1. An action ask: done in the world, one reply; the rows the call read marked processed")
    # A context line waiting in Jev's batch is fed with the ask, in one write (§5).
    mark = len(box.world.sent)
    (ctx_seq, _), (seq, at) = box.lines(("Alex", "context", "I'm over by the lava pools"),
                                        ("Steve", "action_oriented", "give me 64 firework rockets and speed two"))
    a = {"seq": seq, "at": at, "who": "Steve", "text": "give me 64 firework rockets and speed two",
         "cls": "action_oriented", "mark": mark, "reply": None}
    run.asks.append(a)
    rep.note("#%d Alex (context) and #%d Steve (action_oriented) fed in one write" % (ctx_seq, seq))
    tid = run.task_of(a)
    r = run.reply(a, task=tid)
    sent = run.sent(a)
    gives = [x for x in sent if re.match(r"^(execute .* run )?give Steve (minecraft:)?firework_rocket\S* 64$", x)]
    speed = [x for x in sent if re.match(r"^(execute .* run )?effect give Steve (minecraft:)?speed (\d+|infinite) 1( (true|false))?$", x)]
    rep.check(gives and speed, "64 firework rockets and Speed II given to Steve, in the world's command log",
              "sent %r" % sent)
    rep.check(tid and r, "%s: its reply said under its task: %r" % (tid, r and r["text"]), short(box.log()[-1200:]))
    replies = one_reply(run, a)
    rep.check(len(replies) == 1, "exactly one chat line for the line", "%r" % replies)
    actions = [x for x in box.rows(a["seq"], "AI") if x["fields"] == "action %s" % tid]
    rep.check(len(actions) >= 1 and all(" — ok: " in x["text"] for x in actions),
              "each action it ran is an AI action row, ok", "%r" % [x["text"] for x in actions])
    marks = {x["seq"]: x["processed_at"] for x in box.rows(ctx_seq - 1) if x["seq"] in (ctx_seq, a["seq"])}
    rep.check(len(marks) == 2 and all(marks.values()),
              "the context line waiting before it is marked processed as it came, the line by the call's read",
              "%r" % marks)
    t = box.tasks().get(tid) or {}
    rep.check(t.get("status") == "done" and t.get("player") == "Steve", "%s is done in tasks.json" % tid, "%r" % t)


def ask_item(run):
    rep, box = run.rep, run.box
    rep.head("2. An item ask: boots worn and boots carried; the worn ones changed, by their slot")
    before = len(box.world.modified)
    a = run.line("Steve", "action_oriented", "put depth strider three on my boots")
    tid = run.task_of(a)
    r = run.reply(a, task=tid)
    mods = box.world.modified[before:]
    rep.check(len(mods) == 1 and mods[0][1] == "armor.feet" and "depth_strider" in mods[0][2]
              and "type" in mods[0][2] and "function" not in mods[0][2],
              "item modify entity Steve armor.feet {type:…set_enchantments…depth_strider…}: the boots he wears",
              "modified %r; sent %r" % (mods, run.sent(a)))
    rep.check(r, "one reply: %r" % (r and r["text"]), short(box.log()[-1200:]))
    replies = one_reply(run, a)
    rep.check(len(replies) == 1, "exactly one chat line for the line", "%r" % replies)


def ask_talk(run):
    rep, box = run.rep, run.box
    rep.head("3. A conversational line: Respond replies, the brain is not called, nothing is done")
    a = run.line("Alex", "conversational", "haha you're the best AI, that was hilarious")
    r = run.reply(a, timeout=30)
    rep.check(r and r["fields"] == "say -", "Respond's reply is said: %r" % (r and r["text"]),
              short(box.log()[-1200:]))
    time.sleep(2)
    use = sorted(u["component"] for u in box.usage("#%d" % a["seq"]))
    rep.check(use == ["respond"], "one usage row, component respond, for the line; no brain call", "%r" % use)
    done = [x for x in box.rows(a["seq"], "AI") if x["fields"].startswith(("action", "task"))]
    rep.check(not done, "no action and no task for it", "%r" % done)
    rep.check(len(one_reply(run, a, 0.5)) == 1, "exactly one chat line for the line")


def ask_handed_up(run):
    rep, box = run.rep, run.box
    rep.head("4. A conversational ask: Respond hands it up and says nothing; the brain acts and replies")
    a = run.line("Alex", "conversational", "lol can you make it night already")
    tid = run.task_of(a)
    r = run.reply(a, task=tid) if tid else run.reply(a)
    handed = re.search(r"^\S+ respond #%d handed up" % a["seq"], box.log(), re.M)
    rep.check(handed, "Respond handed it up (brain.log)", short(box.log()[-1500:]))
    use = sorted(u["component"] for u in box.usage("#%d" % a["seq"]))
    rep.check(use == ["brain", "respond"], "a Respond call, then a brain call, for the one line", "%r" % use)
    night = [x for x in run.sent(a) if re.match(r"^(execute .* run )?time set (night|midnight|1[3-8]\d{3})$", x)]
    rep.check(night, "the time set to night in the world", "sent %r" % run.sent(a))
    replies = one_reply(run, a)
    rep.check(r and len(replies) == 1 and r["fields"] == "say %s" % tid,
              "one chat line, the brain's, under %s: %r" % (tid, r and r["text"]), "%r" % replies)


def ask_two_at_once(run):
    rep, box = run.rep, run.box
    rep.head("5. Two lines in the same second: both answered, neither waits on the other")
    mark = len(box.world.sent)
    (s1, at), (s2, _) = box.lines(("Steve", "action_oriented", "give me a golden apple"),
                                  ("Alex", "action_oriented", "can I get sixteen emeralds"))
    a1 = {"seq": s1, "at": at, "who": "Steve", "text": "give me a golden apple", "cls": "action_oriented",
          "mark": mark, "reply": None}
    a2 = {"seq": s2, "at": at, "who": "Alex", "text": "can I get sixteen emeralds", "cls": "action_oriented",
          "mark": mark, "reply": None}
    run.asks += [a1, a2]
    rep.note("#%d and #%d fed in one write" % (s1, s2))
    t1, t2 = run.task_of(a1), run.task_of(a2)
    r1, r2 = run.reply(a1, task=t1), run.reply(a2, task=t2)
    sent = box.world.sent[mark:]
    rep.check(any(re.match(r"^(execute .* run )?give Steve (minecraft:)?golden_apple( 1)?$", x) for x in sent)
              and any(re.match(r"^(execute .* run )?give Alex (minecraft:)?emerald 16$", x) for x in sent),
              "the golden apple to Steve and 16 emeralds to Alex, in the world", "sent %r" % sent)
    rep.check(r1 and r2 and t1 != t2, "both answered, each under its own task (%s, %s)" % (t1, t2),
              "%r %r" % (r1, r2))
    c1, c2 = run.call_ms("#%d" % s1), run.call_ms("#%d" % s2)
    if c1 and c2:
        (_, ms1, _, end1), (_, ms2, _, end2) = c1[0], c2[0]
        start1, start2 = end1 - ms1 / 1000.0, end2 - ms2 / 1000.0
        overlap = min(end1, end2) - max(start1, start2)
        rep.check(overlap > 0, "their brain calls ran at the same time: %.1f s of overlap, started %.2f s apart"
                  % (overlap, abs(start1 - start2)), "%r %r" % (c1, c2))
    else:
        rep.check(False, "both brain calls are in the log", "%r %r" % (c1, c2))


def quiet(run):
    rep, box = run.rep, run.box
    rep.head("6. A context line and a game event: no call for either; the context line marked read at once, "
             "the game event read by the next call")
    time.sleep(1)                                    # the last step's usage rows, written nobody waiting
    calls, before = len(box.usage()), box.last_seq()
    box.writer.append_batch([("LINE", "Steve voice context 0.71 -11dB", "ugh these zombies keep getting me", None),
                             ("GAME", "death Steve", "Steve was slain by Zombie", None)])
    seqs = {x["type"]: x["seq"] for x in box.rows(before)}

    def marked(kind):
        return [x["processed_at"] for x in box.rows(before) if x["seq"] == seqs.get(kind) and x["processed_at"]]
    rep.check(wait_for(lambda: marked("LINE"), 10, 0.2), "the context line is marked processed as it arrives",
              "%r" % box.rows(before))
    time.sleep(3)
    rep.check(len(box.usage()) == calls, "no model call for either: there is no batch call",
              "%r" % box.usage()[calls:])
    rep.check(not marked("GAME"), "the game event waits, unread, for the next call", "%r" % box.rows(before))
    a = run.line("Alex", "conversational", "nice, thanks!")
    run.reply(a, timeout=30)
    rep.check(wait_for(lambda: marked("GAME"), 10, 0.2), "the next call (Respond's) read the game event",
              "%r" % box.rows(before))


def end(run):
    rep, box = run.rep, run.box
    rep.head("the end")
    unread = [x for x in box.rows(0) if x["type"] != "AI" and not x["processed_at"]
              and parse_iso(x["at"]).timestamp() > run.started]
    rep.check(not unread, "every row of the run but the brain's own was read and marked processed",
              "%r" % [(x["seq"], x["line"][:80]) for x in unread])
    r = box.stop()
    log = box.log()
    tasks = box.tasks()
    rep.check(r is not None and r.returncode == 0 and "stopped pid" in r.stdout
              and re.search(r"^\S+ stop stopped$", log, re.M),
              "vfg brain stop: the brain stopped cleanly",
              "%s %s; log tail %s" % (r and r.stdout, r and r.stderr, short(log[-800:])))
    rep.check(not os.path.exists(os.path.join(box.brain, "brain.pid")), "brain.pid is gone")
    rep.check(tasks and not [t for t in tasks.values() if t.get("status") == "open"],
              "tasks.json: %d tasks, none left open" % len(tasks), "%r" % tasks)
    rep.check("Traceback" not in log and not re.search(r"^\S+ error ", log, re.M), "no traceback or error in brain.log",
              short(log[max(0, log.find("Traceback")):][:1500]))
    leaks = []
    for d, _, files in os.walk(box.root):
        for fn in files:
            try:
                with open(os.path.join(d, fn), "rb") as f:
                    if box.key.encode() in f.read():
                        leaks.append(os.path.relpath(os.path.join(d, fn), box.root))
            except OSError:
                pass
    rep.check(not leaks, "the OpenRouter key is in no file of the run", "found in %d file(s): %s" % (len(leaks), leaks))
    use = box.usage()
    cost = sum(u["cost_usd"] or 0 for u in use)
    prompt = sum(u["input_tokens"] or 0 for u in use)
    cached = sum(u["cache_read_tokens"] or 0 for u in use)
    rep.check(cost < BUDGET, "the run cost $%.4f of OpenRouter, under $%.2f" % (cost, BUDGET))
    print("\nCOST: %d calls, $%.4f; %d prompt tokens, %.0f%% read from the cache" % (
        len(use), cost, prompt, 100.0 * cached / prompt if prompt else 0))
    for u in use:
        print("  %-8s %-10s %-24s in %6d (%3.0f%% cached) out %4d  $%.5f" % (
            u["component"], u["ref"], u["model"], u["input_tokens"],
            100.0 * (u["cache_read_tokens"] or 0) / u["input_tokens"] if u["input_tokens"] else 0,
            u["output_tokens"], u["cost_usd"] or 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keep", action="store_true", help="keep the temp folder and its logs")
    args = ap.parse_args()
    key, where = find_key()
    if not key:
        print("no %s in the environment or in a .env above %s" % (KEY, REPO), file=sys.stderr)
        return 2
    try:
        box_py = subprocess.run(["uv", "python", "find", "3.12"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                universal_newlines=True).stdout.strip()
    except OSError:
        box_py = ""
    if not box_py:
        box_py = shutil.which("python3")
        print("no Python 3.12 found: the box's side runs on %s" % box_py)
    root = tempfile.mkdtemp(prefix="vfg-brain-e2e-")
    box = Box(root, box_py, key)
    rep = Report()
    run = Run(box, rep)
    run.started = time.time()
    print("brain e2e: temp folder %s\n  key from %s; the box's side on %s; refs/26.3/ seeded from "
          "brain/tests/fixtures/refs" % (root, where, box_py))
    try:
        box.deploy({"Steve": (197.5, 68.0, 251.5), "Alex": (180.5, 70.0, 240.5)})
        if start(run):
            for step in (ask_action, ask_item, ask_talk, ask_handed_up, ask_two_at_once, quiet):
                try:
                    step(run)
                except Exception:
                    rep.check(False, "the step ran to its end", traceback.format_exc()[-1500:])
            run.table()
        end(run)
    finally:
        box.close()
    failed = rep.failed()
    print("\n" + "=" * 72)
    print("%d/%d checks passed" % (len(rep.checks) - len(failed), len(rep.checks)))
    for name in failed:
        print("  x %s" % name)
    print("=" * 72)
    if args.keep or failed:
        print("kept: %s (home/.vfg/brain/brain.log, box/vfg/feed.log, box/vfg/vfg.db)" % root)
    else:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
