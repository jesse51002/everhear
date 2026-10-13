"""vfg engine: every op against a fake engine's control socket (fake_engine.py), the content's deploy
and reload, a Config rewritten (set, refused and put back, undone), engine bindings beside the
action incantations, and the menu's engine actions through vfg action run. No EC2, no network, no
model (the alias model is off).

    python3 -m unittest discover -s toolkit/tests

harness.py's box: vfg's main() in this process, its remote programs too, remote/engine.py speaking
to the fake engine's socket at <vfg>/engine/engine.sock. The rewrite is the engine's own,
engine/vfg_engine/rewrite.py, as vfg loads it: its static checks are the ones a Config is held to.
"""
import contextlib
import json
import os
import shutil
import sys
import threading
import types
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import REPO, BoxCase, vfg_module, write  # noqa: E402
from fake_engine import FakeEngine  # noqa: E402
import standins  # noqa: E402

TESTS = os.path.dirname(os.path.abspath(__file__))

FIREBALL = '''"""Fireball: an ability (a test's)."""
from vfg_engine import Ability, AbilityConfig, Field


class Fireball(Ability):
    """Burns, then explodes."""

    class Config(AbilityConfig):
        # what the menu says of it
        summary = "Burns for burn_s seconds, then explodes in a radius."
        burn_s: float = Field(3, ge=0, le=10)          # how long it burns
        explosion_base: float = Field(8, ge=0, le=30)  # the blast at power 0
        radius: float = Field(3, ge=1, le=8)
        cooldown_s = 5

        def damage(self, ctx):                         # required: the total
            return self.explosion_base

    def on_cast(self, ctx, ev):
        self.explode(at=ctx.user.looking_at(), radius=self.config.radius, damage=self.config.damage(ctx))
'''
STAFF = '''"""The Fire Staff (a test's)."""
from vfg_engine import Item, ItemConfig


class FireStaff(Item):
    class Config(ItemConfig):
        summary = "Right-click to cast Fireball."
        base = "carrot_on_a_stick"
        name = "Fire Staff"
        active_in = "mainhand"
'''


def bigger(config, radius="5", extra=""):
    """The Config of FIREBALL with its radius changed, comments kept, as the brain would send it."""
    return config.replace("radius: float = Field(3, ge=1, le=8)",
                          "radius: float = Field(%s, ge=1, le=8)%s" % (radius, extra))


class Case(BoxCase):
    def setUp(self):
        super().setUp()
        self.vfg_module = vfg_module()
        self.rw = self.vfg_module.rewrite_module()   # engine/vfg_engine/rewrite.py
        self.engine = FakeEngine(self.box.rvfg, rewrite=self.rw).start()
        self.addCleanup(self.engine.stop)
        repo = self.box.repo
        standins.install(repo)
        for name in os.listdir(os.path.join(REPO, "actions")):
            if name.startswith("engine_") and name.endswith(".py"):
                shutil.copy(os.path.join(REPO, "actions", name), os.path.join(repo, "actions", name))
        self.content = os.path.join(repo, "made", "main", "content")
        self.boxed = os.path.join(self.box.rvfg, "made", "main", "content")
        write(os.path.join(self.content, "fireball.py"), FIREBALL)
        write(os.path.join(self.content, "fire_staff.py"), STAFF)

    def engine_json(self, *args, **kw):
        r = self.vfg("engine", *args, "--json", **kw)
        self.assertOk(r)
        return json.loads(r.stdout)

    def deployed(self, name="fireball.py"):
        with open(os.path.join(self.boxed, name)) as f:
            return f.read()

    def pc(self, name="fireball.py"):
        with open(os.path.join(self.content, name)) as f:
            return f.read()

    def action(self, *words):
        r = self.vfg("action", "run", *words)
        lines = r.stdout.strip().splitlines()
        return r, json.loads(lines[-1]) if lines else None


class TestReads(Case):
    def test_status_in_words_and_as_it_came(self):
        r = self.vfg("engine", "status")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines(), [
            "engine     running since 2026-10-12T18:40:00.000Z", "tick       4821 at 10 a second; p50 1.2 ms, p95 3.4 ms",
            "players    2: Alex, Steve", "instances  0", "classes    6", "slow       0", "orphans    0"])
        self.assertOk(self.vfg("engine", "grant", "Steve", "fireball"))
        self.assertOk(self.vfg("engine", "start", "horde"))
        self.assertIn("instances  2: ability 1, scenario 1\n", self.vfg("engine", "status").stdout)
        got = self.engine_json("status")
        self.assertEqual((got["ok"], got["tick"]), (True, 4821))
        sent = self.box.programs()[-1]
        self.assertEqual((sent["op"], sent["requests"], sent["timeout"]), ("ask", [{"op": "status", "args": {}}], 10.0))

    def test_an_engine_that_is_not_running_is_exit_6_and_one_that_does_not_answer_4(self):
        self.engine.stop()
        r = self.vfg("engine", "status")
        self.assertOneLine(r, 6, r"^vfg: the engine is not running: no control socket at .*engine\.sock "
                                 r"\(vfg up starts vfg-engine\)$")
        self.engine.start()
        self.engine.hold.add("classes")
        r = self.vfg("engine", "classes")
        self.assertOneLine(r, 4, r"^vfg: the engine did not answer classes within 10 s; it was taken off the queue")

    def test_a_box_folder_deeper_than_a_socket_address_holds_still_reaches_the_engine(self):
        # A realmc stand-in's <vfg> is deep in the PC's folders: <vfg>/engine/engine.sock is over the
        # 107 bytes a Unix socket's address holds, so the engine's socket is elsewhere and linked
        # there (engine_e2e.py); remote/engine.py reaches it through its folder.
        deep = os.path.join(self.box.root, "d" * 60, "e" * 40, "vfg")
        os.makedirs(os.path.join(deep, "engine"))
        os.symlink(self.engine.path, os.path.join(deep, "engine", "engine.sock"))
        self.assertGreater(len(os.path.join(deep, "engine", "engine.sock")), 107)
        r = self.vfg("engine", "status", VFG_REMOTE_VFG=deep)
        self.assertOk(r)
        self.assertIn("tick       4821 at 10 a second", r.stdout)
        os.remove(os.path.join(deep, "engine", "engine.sock"))
        self.assertOneLine(self.vfg("engine", "status", VFG_REMOTE_VFG=deep), 6,
                           r"^vfg: the engine is not running: no control socket at .*engine\.sock ")

    def test_classes_by_kind_with_their_files_and_args(self):
        r = self.vfg("engine", "classes")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines(), [
            "ability   fireball    Burns for burn_s seconds, then explodes in a radius.  (made/main/content/fireball.py)",
            "item      fire_staff  Right-click to cast Fireball. Holding it adds 20% speed.  "
            "(made/main/content/fire_staff.py)",
            "mark      giant       Twice the size for a while.  (/opt/vfg/engine/vfg_engine/examples/marks.py)",
            "                       factor (float 1..16; default 2.0): how much bigger",
            "actor     ice_queen   A boss of ice.  (made/main/content/ice_queen.py)",
            "scenario  command     Runs Minecraft commands, at once.  (/opt/vfg/engine/library/command.py)",
            "                       commands (list[Command]; default []): the commands",
            "scenario  horde       Waves of zombies round every player.  (made/main/content/horde.py)",
            "                       count (int 1..100; default 10): how many",
            "                       at (Place; default null): where"])
        self.engine.classes = []
        self.assertEqual(self.vfg("engine", "classes").stdout, "none\n")

    def test_overview_is_the_classes_instances_places_and_incantations_in_one_call(self):
        self.assertOk(self.vfg("engine", "grant", "Steve", "fireball"))
        self.engine.places = {"home": {"dim": "minecraft:overworld", "pos": [1.0, 64.0, 2.0], "yaw": 0.0,
                                       "by": "home_steve", "at": 1791830460.0}}
        self.assertOk(self.vfg("engine", "bind", "Steve", "fire ball", "fireball"))
        before = len(self.box.programs())
        got = self.engine_json("overview")
        self.assertEqual(len(self.box.programs()), before + 1)       # one SSH call
        self.assertEqual([x["op"] for x in self.box.programs()[-1]["requests"]],
                         ["classes", "instances", "places", "incantations"])
        self.assertEqual((got["ok"], [c["id"] for c in got["classes"]], [i["id"] for i in got["instances"]]),
                         (True, ["fireball", "fire_staff", "giant", "horde", "command", "ice_queen"], ["fireball"]))
        self.assertEqual(got["places"], [{"name": "home", "dim": "minecraft:overworld", "pos": [1.0, 64.0, 2.0],
                                          "yaw": 0.0, "by": "home_steve", "at": 1791830460.0}])   # a list, by name
        self.assertEqual([(e["phrase"], e["ability"], e["scenario"]) for e in got["incantations"]],
                         [("fire ball", "fireball", None)])
        self.assertEqual(self.vfg("engine", "places").stdout, "home 1 64 2 overworld (home_steve)\n")
        self.assertEqual(self.engine_json("places")["places"], got["places"])      # --json: rows by name too
        r = self.vfg("engine", "overview")
        parts = r.stdout.rstrip("\n").split("\n\n")
        self.assertEqual(parts[0], self.vfg("engine", "classes").stdout.rstrip("\n"))
        self.assertTrue(parts[1].startswith("instance            kind     id"), parts[1])
        self.assertEqual(parts[2:], [self.vfg("engine", "places").stdout.rstrip("\n"),
                                     self.vfg("engine", "incantations").stdout.rstrip("\n")])
        self.engine.refuse["instances"] = "the engine failed on instances: KeyError"
        r = self.vfg("engine", "overview", "--json")
        self.assertEqual(r.returncode, 1)
        got = json.loads(r.stdout)
        self.assertEqual((got["ok"], len(got["classes"]), "instances" in got, len(got["places"]), got["error"]),
                         (False, 6, False, 1, "instances: the engine failed on instances: KeyError"))
        self.engine.stop()
        self.assertOneLine(self.vfg("engine", "overview", "--json"), 6, "the engine is not running")

    def test_meta_world_instances_and_log(self):
        r = self.vfg("engine", "meta", "fireball")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines(), [
            "kind     id        damage@0  damage@10  damage@20  duration_s  cooldown_s  own",
            "ability  fireball  8         12         16         4           5           radius=3 burn_s=3"])
        self.assertEqual(self.engine.ops("meta"), [{"id": "fireball"}])
        self.assertOneLine(self.vfg("engine", "meta", "nope"), 1, "^vfg: 'nope' is not a loaded class$")
        self.assertOneLine(self.vfg("engine", "meta", "Fire Ball"), 2, "is not an engine class's name")
        r = self.vfg("engine", "world")
        self.assertEqual(r.stdout.splitlines(), [
            "mob_griefing = false (it was true)",
            "  held by scenario:horde#1: false at priority 10 since 2026-10-12T18:41:00.000Z"])
        self.assertOk(self.vfg("engine", "grant", "Steve", "fireball"))
        r = self.vfg("engine", "instances", "--kind", "ability", "--user", "Steve")
        self.assertEqual(r.stdout.splitlines()[1].split(), ["ability:fireball#1", "ability", "fireball", "Steve",
                                                            "2026-10-12T18:41:00.000Z", "-"])
        self.assertEqual(self.engine.ops("instances")[-1], {"kind": "ability", "user": "Steve"})
        self.assertOneLine(self.vfg("engine", "instances", "--kind", "boss"), 2, "--kind is one of ability")
        r = self.vfg("engine", "log", "-n", "5")
        self.assertEqual(r.stdout.splitlines(), ['2026-10-12T18:41:03.000Z info loaded fireball {"file": "fireball.py"}',
                                                 "a line of text"])
        self.assertEqual(self.engine.ops("log"), [{"n": 5}])
        self.assertOneLine(self.vfg("engine", "log", "-n", "0"), 2, "-n is 1 to 1000")


class TestOps(Case):
    def test_grant_and_revoke(self):
        r = self.vfg("engine", "grant", "Steve", "fireball")
        self.assertEqual((r.returncode, r.stdout), (0, "granted fireball to Steve\n"))
        self.assertEqual(self.engine.ops("grant"), [{"who": "Steve", "ability": "fireball"}])
        self.assertEqual(self.engine.grants, [("Steve", "fireball")])
        r = self.vfg("engine", "grant", "Steve", "fireball")
        self.assertEqual(r.stdout, "granted fireball to Steve (Steve already has fireball)\n")
        r = self.vfg("engine", "revoke", "Steve", "fireball")
        self.assertEqual((r.returncode, r.stdout), (0, "took fireball away from Steve\n"))
        r = self.vfg("engine", "revoke", "Steve", "fireball")
        self.assertOneLine(r, 1, "^vfg: Steve does not have fireball$")
        r = self.vfg("engine", "grant", "Steve", "lightning", "--json")
        self.assertEqual((r.returncode, json.loads(r.stdout)), (1, {"ok": False, "error": "no ability lightning"}))
        self.assertOk(self.vfg("engine", "grant", "0e5b7c1a-3d2f-4a6b-9c8d-7e6f5a4b3c2d", "fireball"))
        before = len(self.box.programs())
        for bad in (("@a", "fireball"), ("Steve", "Fire-Ball"), ("Steve Alex", "fireball")):
            self.assertEqual(self.vfg("engine", "grant", *bad).returncode, 2, bad)
        self.assertEqual(len(self.box.programs()), before)   # refused here: no SSH call

    def test_give_take_apply_clear(self):
        self.assertEqual(self.vfg("engine", "give", "Alex", "fire_staff").stdout, "gave Alex fire_staff\n")
        r = self.vfg("engine", "give", "Alex", "fire_staff", "--base", "stick")
        self.assertOneLine(r, 1, "^vfg: fire_staff is given on minecraft:carrot_on_a_stick, not stick$")
        self.assertEqual(self.engine.ops("give")[-1], {"player": "Alex", "item": "fire_staff", "base": "stick"})
        self.assertEqual(self.vfg("engine", "take", "Alex", "fire_staff").stdout, "took every fire_staff from Alex\n")
        self.assertEqual(self.vfg("engine", "apply", "Steve", "giant", "--seconds", "30").stdout,
                         "put giant on Steve for 30 s\n")
        self.assertEqual(self.engine.marks, [("Steve", "giant", 30.0)])
        self.assertOneLine(self.vfg("engine", "apply", "Steve", "giant", "--seconds", "0"), 2, "--seconds is more than 0")
        self.assertEqual(self.vfg("engine", "clear", "Steve", "giant").stdout, "took giant off Steve\n")

    def test_start_stop_and_spawn(self):
        r = self.vfg("engine", "start", "horde", "count=3", "mobs=[\"zombie\", \"husk\"]")
        self.assertEqual((r.returncode, r.stdout), (0, "horde is running as scenario:horde#1\n"))
        # a word is the engine's to read as its arg's type; a list or an object is JSON
        self.assertEqual(self.engine.ops("start"), [{"scenario": "horde", "args": {"count": "3",
                                                                                   "mobs": ["zombie", "husk"]},
                                                     "wait_s": 15.0}])
        self.assertOneLine(self.vfg("engine", "start", "horde", "count"), 2, "is not key=value")
        self.assertOneLine(self.vfg("engine", "start", "horde", "mobs=[zombie"), 2, "reads as JSON but is not")
        r = self.vfg("engine", "start", "command", "commands=[\"say hi\"]", "--json")      # a one-off: its result
        self.assertEqual(json.loads(r.stdout)["result"], {"summary": "ran say hi", "facts": {"replies": ["ran say hi"]}})
        self.assertEqual(self.vfg("engine", "start", "command", "commands=[\"say hi\"]").stdout, "ran say hi\n")
        self.vfg("engine", "start", "command", "--wait", "0")
        self.assertNotIn("wait_s", self.engine.ops("start")[-1])                         # no wait: not sent
        self.assertOneLine(self.vfg("engine", "start", "command", "--wait", "31"), 2, "--wait is 0 to 30")
        r = self.vfg("engine", "stop", "--instance", "scenario:horde#1")
        self.assertEqual((r.returncode, r.stdout), (0, "stopped scenario:horde#1\n"))
        self.assertOk(self.vfg("engine", "start", "horde"))
        self.assertEqual(self.vfg("engine", "stop", "horde").stdout, "stopped horde (scenario:horde#1)\n")
        self.assertOneLine(self.vfg("engine", "stop", "horde"), 1, "^vfg: nothing runs as horde$")
        self.assertOneLine(self.vfg("engine", "stop"), 2, "stop what")
        r = self.vfg("engine", "spawn", "ice_queen", "--at", "10", "64", "-20")
        self.assertEqual(r.stdout, "spawned ice_queen as 0e5b7c1a-3d2f-4a6b-9c8d-7e6f5a4b3c2d\n")
        self.assertEqual(self.engine.ops("spawn")[-1], {"actor": "ice_queen", "at": [10.0, 64.0, -20.0]})
        self.vfg("engine", "spawn", "ice_queen", "--near", "Steve")
        self.assertEqual(self.engine.ops("spawn")[-1], {"actor": "ice_queen", "near": "Steve"})
        self.assertOneLine(self.vfg("engine", "spawn", "ice_queen", "--at", "1", "x", "2"), 2, "three numbers")


class TestReload(Case):
    def test_the_content_is_deployed_then_reloaded(self):
        r = self.vfg("engine", "reload")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines(), [
            "deployed made/main/content: 2 files; changed fire_staff.py, fireball.py",
            "loaded: fire_staff, fireball", "switched: 2 running instances"])
        self.assertEqual(self.engine.ops("reload"), [{}])   # none named: every content dir
        self.assertEqual(self.deployed(), FIREBALL)
        r = self.vfg("engine", "reload", "fireball")
        self.assertEqual(r.stdout.splitlines()[0], "deployed made/main/content: unchanged, 2 files")
        self.assertEqual(self.engine.ops("reload")[-1], {"paths": [os.path.join(self.boxed, "fireball.py")]})

    def test_a_failed_check_says_which_and_why_and_exits_1(self):
        write(os.path.join(self.content, "fireball.py"), FIREBALL + "# REFUSE in bounds: radius=12 is over its max 8\n")
        os.remove(os.path.join(self.content, "fire_staff.py"))
        self.engine.stopped = [{"instance": "ability:fireball#1", "reason": "its state no longer fits"}]
        r = self.vfg("engine", "reload", "fireball")
        self.assertEqual(r.returncode, 1, r)
        self.assertEqual(r.stdout.splitlines(), [
            "deployed made/main/content: 1 files; changed fireball.py",
            "loaded: nothing",
            "refused made/main/content/fireball.py: in bounds failed: radius=12 is over its max 8 — the old version "
            "keeps running",
            "switched: 0 running instances",
            "stopped ability:fireball#1: its state no longer fits"])
        self.assertEqual(r.stderr, "vfg: in bounds failed: radius=12 is over its max 8\n")
        self.assertFalse(os.path.exists(os.path.join(self.boxed, "fire_staff.py")))   # the folder is swapped whole

    def test_what_cannot_be_reloaded(self):
        self.assertOneLine(self.vfg("engine", "reload", "nope"), 2, "there is no made/main/content/nope.py")
        self.assertOneLine(self.vfg("engine", "reload", "../x"), 2, "is not a content file's name")
        shutil.rmtree(self.content)
        self.assertOneLine(self.vfg("engine", "reload"), 1, "no made/main/content/ in")
        write(os.path.join(self.content, "fireball.py"), FIREBALL)
        self.engine.stop()
        r = self.vfg("engine", "reload")
        self.assertOneLine(r, 6, "^vfg: deployed made/main/content: 1 files; changed fireball.py, but the engine is "
                                 "not running")
        self.assertEqual(self.deployed(), FIREBALL)


class TestConfig(Case):
    def setUp(self):
        super().setUp()
        self.assertOk(self.vfg("engine", "reload"))
        start, end = self.rw.find_config(FIREBALL, "Fireball")
        self.config = "".join(FIREBALL.splitlines(True)[start - 1:end])
        self.lines = (start, end)

    def history(self):
        folder = os.path.join(self.content, ".history", "fireball")
        return sorted(n for n in os.listdir(folder) if n.endswith(".py")) if os.path.isdir(folder) else []

    def log_rows(self):
        path = os.path.join(self.content, ".history", "log.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(l) for l in f]

    def test_get_prints_it_as_written_with_its_file_and_lines(self):
        r = self.vfg("engine", "config", "get", "fireball")
        self.assertOk(r)
        self.assertEqual(r.stdout, "made/main/content/fireball.py, lines %d to %d: the Config of Fireball, as "
                                   "written\n%s" % (self.lines[0], self.lines[1], self.config))
        got = self.engine_json("config", "get", "fireball")
        self.assertEqual((got["class_name"], got["start"], got["end"], got["source"]),
                         ("Fireball", self.lines[0], self.lines[1], self.config))
        write(os.path.join(self.content, "fireball.py"), FIREBALL.replace("burn_s: float = Field(3", "burn_s: float = Field(4"))
        r = self.vfg("engine", "config", "get", "fireball")
        self.assertIn("Field(4", r.stdout)
        self.assertRegex(r.stderr, "^vfg: the box runs another version of this Config than made/main/content/fireball.py")
        self.assertOneLine(self.vfg("engine", "config", "get", "nope"), 1, "^vfg: no class nope$")
        self.assertOneLine(self.vfg("engine", "config", "get", "giant"), 1, "giant's Config is in "
                           "/opt/vfg/engine/vfg_engine/examples/marks.py, not in made/main/content/")

    def test_set_splices_it_in_keeps_the_old_file_and_reloads(self):
        new = bigger(self.config)
        r = self.vfg("engine", "config", "set", "fireball", input=new)
        self.assertOk(r)
        self.assertEqual(r.stdout, "fireball's new Config is in, and reloaded for everyone; 2 running instances "
                                   "switched\n")
        want = FIREBALL.replace("Field(3, ge=1, le=8)", "Field(5, ge=1, le=8)")
        self.assertEqual((self.pc(), self.deployed()), (want, want))  # only that line, comments and all
        self.assertEqual(self.engine.ops("reload")[-1], {"paths": [os.path.join(self.boxed, "fireball.py")]})
        kept = self.history()
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0].endswith("-Fireball.py"))
        with open(os.path.join(self.content, ".history", "fireball", kept[0])) as f:
            self.assertEqual(f.read(), FIREBALL)
        self.assertEqual([(x["op"], x["class"], x["ok"]) for x in self.log_rows()], [("set", "fireball", True)])
        self.assertEqual(self.vfg("engine", "reload").stdout.splitlines()[0],
                         "deployed made/main/content: unchanged, 2 files")  # .history/ is never deployed
        # the Config sent with no indentation is put back indented as the old one was
        dedented = "\n".join(l[4:] for l in bigger(self.config, "6").splitlines()) + "\n"
        self.assertOk(self.vfg("engine", "config", "set", "fireball", input=dedented))
        self.assertEqual(self.pc(), FIREBALL.replace("Field(3, ge=1, le=8)", "Field(6, ge=1, le=8)"))
        r = self.vfg("engine", "config", "set", "fireball", input=dedented)
        self.assertEqual((r.returncode, r.stdout), (0, "fireball's Config is already as written: nothing changed\n"))
        self.assertEqual(len(self.history()), 2)

    def test_a_static_check_refuses_it_before_anything_is_sent(self):
        dropped = "".join(l for l in self.config.splitlines(True) if "radius" not in l)
        before = len(self.box.programs())
        r = self.vfg("engine", "config", "set", "fireball", input=dropped)
        self.assertOneLine(r, 1, r"^vfg: fireball's new Config was refused before it was sent: .*radius.*; nothing "
                                 r"changed, the old version keeps running$")
        self.assertEqual((self.pc(), self.deployed(), self.history()), (FIREBALL, FIREBALL, []))
        self.assertEqual([p["op"] for p in self.box.programs()[before:]], ["ask"])   # config_get, no deploy
        self.assertEqual(self.log_rows()[-1]["ok"], False)
        self.assertOneLine(self.vfg("engine", "config", "set", "fireball", input="  \n"), 2, "the new Config is empty")

    def test_the_static_checks_words_come_back_whole_for_set_and_undo(self):
        words = ["Config.damage changed: a rewrite changes values, never a formula's body",
                 "radius = 3 * 2 is not a value: write a literal, or Field(literal, ...)"]
        with mock.patch.object(self.rw, "static_checks", return_value=words):
            r = self.vfg("engine", "config", "set", "fireball", input=bigger(self.config))
            r2, line = self.action("engine_config", "class_id=fireball", "config=" + bigger(self.config))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr, "vfg: fireball's new Config was refused before it was sent: (1) %s; (2) %s; nothing "
                                   "changed, the old version keeps running\n" % tuple(words))
        self.assertEqual((self.pc(), self.deployed(), self.history()), (FIREBALL, FIREBALL, []))
        self.assertEqual(r2.returncode, 1)   # the action: the same words, as its error
        self.assertTrue(line["error"].endswith("nothing changed, the old version keeps running"), line)
        self.assertIn(words[0], line["error"])
        self.assertOk(self.vfg("engine", "config", "set", "fireball", input=bigger(self.config)))
        with mock.patch.object(self.rw, "static_checks", return_value=words[:1]):
            r = self.vfg("engine", "config", "undo", "fireball")
        self.assertOneLine(r, 1, r"^vfg: fireball's kept Config \(fireball/\d{8}-\d{6}-\d{6}-Fireball\.py\) no longer "
                                 r"fits its class as it is now: Config\.damage changed: .*; nothing changed$")
        self.assertIn("Field(5, ge=1, le=8)", self.pc())
        self.assertEqual(len(self.history()), 1)

    def test_a_changed_formula_or_a_computed_value_is_refused_before_it_is_sent(self):
        """engine/vfg_engine/rewrite.py's own words: a rewrite changes values, never code."""
        for changed in (self.config.replace("return self.explosion_base", "return self.explosion_base * 100"),
                        self.config.replace("Field(3, ge=1, le=8)", "Field(3 * 2, ge=1, le=8)")):
            words = self.rw.static_checks(FIREBALL, FIREBALL.replace(self.config, changed), "Fireball")
            self.assertTrue(words, changed)
            before = len(self.box.programs())
            r = self.vfg("engine", "config", "set", "fireball", input=changed)
            self.assertEqual(r.stderr, "vfg: fireball's new Config was refused before it was sent: %s; nothing changed, "
                                       "the old version keeps running\n" % self.vfg_module.problems_text(words))
            self.assertEqual([p["op"] for p in self.box.programs()[before:]], ["ask"])   # config_get, no deploy
            self.assertEqual((self.pc(), self.history()), (FIREBALL, []))

    def test_refused_by_the_engine_the_pc_file_goes_back_and_is_deployed_again(self):
        new = bigger(self.config, "12", "  # REFUSE in bounds: radius=12 is over its max 8")
        r = self.vfg("engine", "config", "set", "fireball", input=new)
        self.assertOneLine(r, 1, r"^vfg: the engine refused fireball's new Config: in bounds failed: radius=12 is over "
                                 r"its max 8; made/main/content/fireball.py is put back on this PC as it was, and on "
                                 r"the box: the old version keeps running$")
        self.assertEqual((self.pc(), self.deployed(), self.history()), (FIREBALL, FIREBALL, []))
        self.assertEqual(self.engine.ops("reload")[-1], {"paths": [os.path.join(self.boxed, "fireball.py")]})
        self.assertEqual(len(self.engine.ops("reload")), 2)   # the new file's; the old one deployed with no reload
        self.assertEqual(self.log_rows()[-1]["error"].split(":")[0], "the engine refused fireball's new Config")

    def test_an_engine_that_stops_answering_leaves_the_old_file_everywhere(self):
        self.engine.hold.add("reload")
        r = self.vfg("engine", "config", "set", "fireball", input=bigger(self.config))
        self.assertEqual(r.returncode, 4, r)
        self.assertRegex(r.stderr, "deployed, but the engine did not answer reload .* is put back on this PC as it "
                                   "was, but could not be deployed again")
        self.assertEqual((self.pc(), self.history()), (FIREBALL, []))
        self.engine.hold.clear()
        self.engine.stop()
        r = self.vfg("engine", "config", "set", "fireball", input=bigger(self.config))
        self.assertOneLine(r, 6, "the engine is not running")
        self.assertEqual(self.pc(), FIREBALL)

    def test_undo_puts_the_last_kept_config_back_one_at_a_time(self):
        self.assertOneLine(self.vfg("engine", "config", "undo", "fireball"), 1,
                           "no kept version of fireball's Config to put back")
        self.assertOk(self.vfg("engine", "config", "set", "fireball", input=bigger(self.config, "5")))
        five = self.pc()
        self.assertOk(self.vfg("engine", "config", "set", "fireball", input=bigger(self.config, "7")))
        r = self.vfg("engine", "config", "undo", "fireball")
        self.assertOk(r)
        self.assertRegex(r.stdout, r"^fireball's Config is back as it was \(\d{8}-\d{6}-\d{6}-Fireball\.py\), and "
                                   r"reloaded for everyone; 2 running instances switched\n$")
        self.assertEqual((self.pc(), self.deployed()), (five, five))
        self.assertOk(self.vfg("engine", "config", "undo", "fireball"))
        self.assertEqual((self.pc(), self.deployed()), (FIREBALL, FIREBALL))
        self.assertOneLine(self.vfg("engine", "config", "undo", "fireball"), 1, "no kept version")
        self.assertEqual(len(os.listdir(os.path.join(self.content, ".history", "fireball", "undone"))), 2)
        self.assertEqual([x["op"] for x in self.log_rows()], ["set", "set", "undo", "undo"])

    def test_an_undo_the_engine_refuses_changes_nothing_and_keeps_the_version(self):
        self.assertOk(self.vfg("engine", "config", "set", "fireball", input=bigger(self.config, "5")))
        changed = self.pc()
        write(os.path.join(self.content, "fireball.py"), changed + "# REFUSE imports: no module named numpy\n")
        r = self.vfg("engine", "config", "undo", "fireball")
        self.assertEqual(r.returncode, 1, r)
        self.assertIn("no module named numpy", r.stderr)
        self.assertEqual(self.pc(), changed + "# REFUSE imports: no module named numpy\n")
        self.assertEqual(len(self.history()), 1)


class TestClassIds(Case):
    def test_a_class_is_found_by_the_id_it_sets_or_its_name_in_snake_case(self):
        vfg = vfg_module()
        source = """from typing import ClassVar
from vfg_engine import Ability, AbilityConfig


class BigBlast(Ability):
    id = "fireball"

    class Config(AbilityConfig):
        summary = "a"


class Fireball(Ability):
    id: ClassVar[str] = "small_fireball"

    class Config(AbilityConfig):
        summary = "b"


class IceQueenBoss(Ability):
    class Config(AbilityConfig):
        summary = "c"
"""
        self.assertEqual(vfg.content_class(source, "fireball"), "BigBlast")        # its id, not Fireball's name
        self.assertEqual(vfg.content_class(source, "small_fireball"), "Fireball")
        self.assertEqual(vfg.content_class(source, "ice_queen_boss"), "IceQueenBoss")
        self.assertEqual(vfg.content_class(source, "Fireball"), "Fireball")        # a class's name too
        self.assertIsNone(vfg.content_class(source, "nothing"))
        self.assertEqual(vfg.snake("FireStaff"), "fire_staff")
        self.assertEqual(vfg.snake("HTTPBeam"), "http_beam")

    def test_an_engine_without_the_class_name_still_finds_it_by_its_id(self):
        write(os.path.join(self.content, "blast.py"), FIREBALL.replace("class Fireball(Ability):",
                                                                      'class BigBlast(Ability):\n    id = "fireball"\n'))
        self.engine.classes[0]["file"] = os.path.join(self.boxed, "blast.py")
        self.assertOk(self.vfg("engine", "reload"))
        real = self.engine.op_config_get

        def old_engine(**args):   # an engine whose config_get names no class
            got = real(**args)
            got.pop("class")
            return got
        self.engine.op_config_get = old_engine
        self.engine.class_name = lambda path, wanted: "BigBlast"
        r = self.vfg("engine", "config", "get", "fireball")
        self.assertOk(r)
        self.assertIn("the Config of BigBlast, as written", r.stdout.splitlines()[0])

    def test_one_rule_for_a_class_id_here_and_in_the_actions(self):
        vfg = vfg_module()
        good = ["fireball", "fire_staff", "a", "a" + "b" * 63, "x9_"]
        bad = ["", "Fireball", "fire-staff", "9lives", "_x", "a" + "b" * 64, "fire ball", "fireball\n"]
        for x in good:
            self.assertTrue(vfg.ENGINE_ID_RE.match(x), x)
        for x in bad:
            self.assertFalse(vfg.ENGINE_ID_RE.match(x), x)
        with self.box.environ():
            va, cat = vfg.catalog(vfg.Config())
            for name, key in (("engine_grant", "ability"), ("engine_give", "item"), ("engine_start", "scenario"),
                              ("engine_bind", "ability"), ("engine_config", "class_id")):
                for x in good:
                    va.check_values(cat.get(name), {key: x})
                for x in bad[1:]:                  # empty is the field's own "none given"
                    with self.assertRaises(ValueError, msg=(name, x)):
                        va.check_values(cat.get(name), {key: x})


class TestBindings(Case):
    def test_bind_makes_its_aliases_warns_and_lists_it(self):
        self.assertOk(self.vfg("engine", "bind", "Steve", "fire bowl", "fireball"))
        r = self.vfg("engine", "bind", "Steve", "Fire Ball!", "fireball")
        self.assertEqual(r.returncode, 0, r)
        self.assertEqual(r.stdout, '"fire ball" for Steve now casts fireball; aliases fireball\n')
        self.assertIn('sounds like "fire bowl"', r.stderr)      # against Steve's other phrases
        self.assertIn("vfg: Steve does not have fireball yet: saying it casts nothing until it is granted", r.stderr)
        self.assertOk(self.vfg("engine", "grant", "Steve", "fireball"))
        r = self.vfg("engine", "bind", "Steve", "Fire Ball!", "fireball", "--by", "fire_steve")
        self.assertNotIn("does not have", r.stderr)
        self.assertIn('replaces Steve\'s "fire ball", which cast fireball', r.stderr)
        self.assertEqual(self.engine.ops("bind")[-1]["by"], "fire_steve")
        self.assertOneLine(self.vfg("engine", "bind", "Steve", "x y", "fireball", "--by", "Bad Task"), 2, "--by")
        bound = self.engine.ops("bind")[1]
        self.assertEqual(self.engine.ops("instances")[0], {"kind": "ability", "user": "Steve"})
        self.assertEqual(bound, {"player": "Steve", "phrase": "fire ball", "ability": "fireball", "aliases": ["fireball"],
                                 "everyday_aliases": [], "by": ""})
        self.assertEqual(self.vfg("engine", "incantations").stdout,
                         'Steve: "fire bowl" → casts fireball cooldown 5s; aliases firebowl\n'
                         'Steve: "fire ball" → casts fireball cooldown 5s; aliases fireball\n')
        r = self.vfg("engine", "unbind", "Steve", "fire ball")
        self.assertEqual(r.stdout, 'removed Steve\'s incantation "fire ball"\n')
        self.assertEqual(len(self.engine_json("incantations")["incantations"]), 1)

    def test_a_phrase_bound_to_a_scenario_with_its_args_caster_for_whoever_says_it(self):
        r = self.vfg("engine", "bind", "Alex", "drop horde", "--scenario", "horde", "at=caster", "count=4",
                     "--cooldown", "10")
        self.assertEqual((r.returncode, r.stdout), (0, '"drop horde" for Alex now starts horde {"at":"caster",'
                                                       '"count":"4"}; aliases drophorde\n'))
        self.assertEqual(self.engine.ops("bind"), [{"player": "Alex", "phrase": "drop horde", "scenario": "horde",
                                                    "args": {"at": "caster", "count": "4"}, "aliases": ["drophorde"],
                                                    "everyday_aliases": [], "by": "", "cooldown_s": 10.0}])
        self.assertEqual(self.vfg("engine", "incantations").stdout,
                         'Alex: "drop horde" → starts horde {"at":"caster","count":"4"} cooldown 10s; '
                         'aliases drophorde\n')
        got = self.engine_json("incantations")["incantations"][0]      # the export's shape: the other one null
        self.assertEqual((got["ability"], got["scenario"], got["args"]), (None, "horde", {"at": "caster", "count": "4"}))
        r = self.vfg("engine", "bind", "Alex", "x y", "--scenario", "horde", "count=caster")
        self.assertOneLine(r, 1, "caster fills a Player or a Place arg, and count is int$")
        r = self.vfg("engine", "bind", "Alex", "x y", "--scenario", "giant")
        self.assertOneLine(r, 1, r"^vfg: the engine has no scenario giant \(its scenarios: command, horde\)")

    def test_the_binding_comes_back_as_the_engine_stored_it(self):
        self.engine.alias_cap = 0                               # this engine keeps no alias
        r = self.vfg("engine", "bind", "Steve", "fire ball", "fireball")
        self.assertEqual(r.stdout, '"fire ball" for Steve now casts fireball; no aliases\n')
        r, line = self.action("engine_bind", "player=Steve", "phrase=ice beam", "ability=fireball")
        self.assertEqual(line["facts"]["entry"], {"id": "steve.ice_beam", "player": "Steve", "phrase": "ice beam",
                                                  "aliases": [], "everyday_aliases": [], "ability": "fireball",
                                                  "cooldown_s": 5.0})

    def test_what_bind_refuses(self):
        before = len(self.box.programs())
        self.assertOneLine(self.vfg("engine", "bind", "@a", "fire ball", "fireball"), 2, "never @a")
        self.assertOneLine(self.vfg("engine", "bind", "Steve", " ! ", "fireball"), 2, "a word at least")
        self.assertOneLine(self.vfg("engine", "bind", "Steve", "zap"), 2, "or a scenario .*, one of them$")
        self.assertOneLine(self.vfg("engine", "bind", "Steve", "zap", "fireball", "--scenario", "horde"), 2,
                           "one of them$")
        # an ability's cooldown is its Config's: --cooldown on one is refused, never dropped in silence
        self.assertOneLine(self.vfg("engine", "bind", "Steve", "zap", "fireball", "--cooldown", "9"), 2,
                           "^vfg: --cooldown is a scenario's incantation's: an ability's cooldown is its Config's")
        self.assertEqual(len(self.box.programs()), before)
        r = self.vfg("engine", "bind", "Steve", "zap", "lightning")
        self.assertOneLine(r, 1, r"^vfg: the engine has no ability lightning \(its abilities: fireball\)")
        self.assertEqual(self.engine.ops("bind"), [])
        self.engine.refuse["bind"] = "Steve has no fireball: grant it first"
        self.assertOneLine(self.vfg("engine", "bind", "Steve", "fire ball", "fireball"), 1, "grant it first$")


class TestAliases(Case):
    """The phrase's aliases as a bind makes them: the code's (the phrase joined and split), then the
    alias call's variants through the code's checks (bridge/vfg_bridge/sounds.py), while the engine
    is read. The alias model is off unless a test names one, vfg_llm mocked."""

    def model(self, name):
        self.box.write_config("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\nVFG_INCANT_ALIAS_MODEL=%s\n" % (self.box.key, name))

    def bind(self, phrase="fire ball", llm=None, player="Steve", **patch):
        """`vfg engine bind` through its action: its one JSON line."""
        vfg = vfg_module()
        with contextlib.ExitStack() as stack:
            if llm is not None:
                stack.enter_context(mock.patch.dict(sys.modules, {"vfg_llm": llm}))
            flush = stack.enter_context(mock.patch.object(vfg, "flush_usage_later"))
            for k, v in patch.items():
                stack.enter_context(mock.patch.object(vfg, k, v))
            r, line = self.action("engine_bind", "player=" + player, "phrase=" + phrase, "ability=fireball")
        self.assertTrue(line["ok"], line)
        return line, flush

    def aliases(self, phrase):
        return next(e for e in self.engine.bindings if e["phrase"] == phrase)

    def test_the_models_variants_come_while_the_engine_is_read_and_are_merged_after_the_codes(self):
        reading = threading.Event()
        vfg = vfg_module()
        ask = vfg.engine_ask

        def ask_and_say(cfg, a, requests, timeout=None):
            reading.set()
            return ask(cfg, a, requests, timeout) if timeout is not None else ask(cfg, a, requests)
        # It answers only once the engine is being read: the two at once. Thirty that sound like it
        # (fyre bawl, fier boll ...), among them the phrase itself, a repeat, one far longer and one
        # that sounds unlike it.
        alike = [a + " " + b for a in ("fyre", "fier", "fire") for b in ("bawl", "bal", "boll", "bol", "pall",
                                                                          "paul", "bull", "bell", "baul", "balle")]
        llm = fake_llm(["fire bowl", "Fire-Ball", "fireball", "fire bowl", "fire ball now please", "ice cream"] + alike,
                       after=reading)
        self.model("test/m")
        line, flush = self.bind(llm=llm, engine_ask=ask_and_say)
        flush.assert_called_once()                # the call's own usage row, sent nobody waiting
        aliases = self.aliases("fire ball")["aliases"]
        self.assertEqual(aliases[:3], ["fireball", "fire bowl", "fyre bawl"])     # the code's first
        self.assertEqual(len(aliases), 24)
        # What the code's checks dropped is in the facts, never a note.
        self.assertEqual(line["facts"]["alias_call"], {
            "model": "test/m", "cost": 0.00012, "ms": 840,
            "dropped": ["fire ball: the phrase itself", "fire bowl: a repeat",
                        "fire ball now please: 6 speech sounds longer", "ice cream: sounds unlike it (0.82)"]
            + ["%s: past the 24" % x for x in alike[22:]]})
        self.assertEqual([n for n in line["notes"] if "alias" in n or "dropped" in n], [])
        call = llm.calls[0]
        self.assertEqual((call["model"], call["reasoning"], call["timeout"], call.get("providers")),
                         ("test/m", "low", 20.0, None))
        self.assertEqual((call["component"], call["call_id"]), ("alias", "fire ball"))
        prompt = call["prompt"]
        self.assertIn('The incantation: "fire ball"\nHow it sounds (the CMU pronouncing dictionary, ARPAbet, stress '
                      'left out):\n  fire = F AY ER\n  ball = B AO L\n', prompt)
        self.assertIn("Always exactly 20, the likeliest first.", prompt)
        schema = call["output"].model_json_schema()
        self.assertEqual(schema["$defs"]["Variant"]["required"], ["text", "sounds", "common_phrase"])
        self.assertIn("exactly 20", schema["properties"]["variants"]["description"])

    def test_the_model_off_is_no_call_and_the_codes_aliases(self):
        line, flush = self.bind()
        flush.assert_not_called()                 # no call, no row to send
        self.assertNotIn("alias_call", line["facts"])
        self.assertEqual(self.aliases("fire ball")["aliases"], ["fireball"])

    def test_a_made_up_word_is_said_from_its_letters_in_the_prompt(self):
        self.model("test/m")
        llm = fake_llm(["garlic gun", "galic gun"])
        self.bind("galick gun", llm=llm)
        self.assertIn('  galick = not in the dictionary (a made-up word); a guess from its letters: G AE L IH K. '
                      'Think how a player would say it.\n  gun = G AH N\n', llm.calls[0]["prompt"])
        self.assertEqual(self.aliases("galick gun")["aliases"], ["galickgun", "garlic gun", "galic gun"])

    def test_a_failed_alias_call_is_a_note(self):
        self.model("test/m")
        line, flush = self.bind(llm=fake_llm(fail=RuntimeError("no answer from test/m within 8 s")))
        flush.assert_called_once()                # a failed call's row is queued too
        self.assertIn("aliases from code only: the alias call failed (no answer from test/m within 8 s)", line["notes"])
        self.assertEqual(self.aliases("fire ball")["aliases"], ["fireball"])

    def test_a_one_word_phrase_is_bound_with_its_split_form_and_a_short_one_with_a_note(self):
        line, _ = self.bind("Fireball!")
        self.assertEqual([n for n in line["notes"] if "short" in n], [])     # 6 speech sounds, as "fire ball"
        self.assertEqual(self.aliases("fireball")["aliases"], ["fire ball"])
        line, _ = self.bind("heal")
        self.assertIn('"heal" is short (3 speech sounds), so it may fire by accident: a second word helps.',
                      line["notes"])
        self.assertEqual(self.aliases("heal")["aliases"], [])

    def test_everyday_talk_is_a_note_when_the_alias_call_says_so(self):
        self.model("test/m")
        for everyday in (True, False):
            llm = fake_llm(["heel", "heals"], everyday=everyday)
            line, _ = self.bind("heal", llm=llm)
            note = '"heal" is everyday talk, so it will fire whenever it is said in conversation.'
            self.assertEqual(note in line["notes"], everyday, line["notes"])
            self.assertEqual(self.aliases("heal")["aliases"], ["heals"])   # heel: 3 speech sounds
            self.assertIn("everyday talk", llm.calls[0]["prompt"])

    def test_the_variants_marked_everyday_talk_are_kept_as_everyday_aliases(self):
        self.model("test/m")
        llm = fake_llm(["ice been", "Ice cream", "I Scream!", "nice beam", "eyes beam", "icebeam"],
                       everyday_variants=["Ice cream", "I Scream!", "nice beam", "eyes beam", "icebeam"])
        self.bind("ice beam", llm=llm)
        e = self.aliases("ice beam")
        self.assertEqual(e["aliases"], ["icebeam", "ice been", "ice cream", "i scream", "nice beam", "eyes beam"])
        # Among the aliases only, and never the phrase's own words run together (the code's).
        self.assertEqual(e["everyday_aliases"], ["ice cream", "i scream", "nice beam", "eyes beam"])
        self.assertTrue(self.vfg("engine", "incantations").stdout.rstrip().endswith(
            "; everyday, not spotted: ice cream, i scream, nice beam, eyes beam"))
        prompt = llm.calls[0]["prompt"]
        for words in ('"ice cream", "i scream", "nice beam" and "eyes beam" are', '("ice been", "juicy stake"',
                      '"destroyed everything"'):
            self.assertIn(words, prompt)
        self.model("off")                          # the model off: none
        self.bind("ice beam")
        self.assertEqual(self.aliases("ice beam")["everyday_aliases"], [])

    def test_bind_warnings_are_notes_against_the_players_own_phrases_only(self):
        self.bind()
        line, _ = self.bind("fire wall")
        self.assertIn('"fire wall" sounds like "fire ball", so one may fire for the other.', line["notes"])
        line, _ = self.bind("fire wall", player="Alex")            # Alex has no "fire ball": no warning
        self.assertFalse(any("sounds like" in n for n in line["notes"]), line["notes"])
        line, _ = self.bind("go go", player="Alex")
        self.assertTrue(any(n.startswith('"go go" is short') for n in line["notes"]), line["notes"])


def fake_llm(variants=None, fail=None, after=None, everyday=False, everyday_variants=()):
    """A stand-in for lib/py/vfg_llm: structured() answers `variants`, those in
    `everyday_variants` marked common_phrase, and `everyday`, or raises `fail`, once `after` (an
    Event) is set, if given; the calls it had in .calls."""
    mod = types.ModuleType("vfg_llm")
    mod.calls = []

    async def structured(prompt, output, **kw):
        mod.calls.append(dict(kw, prompt=prompt, output=output))
        if after is not None and not after.wait(5):
            raise RuntimeError("answered only after the engine was read")
        if fail:
            raise fail
        said = [{"text": v, "sounds": "", "common_phrase": v in everyday_variants} for v in variants or []]
        return types.SimpleNamespace(value=output(variants=said, everyday=everyday), cost=0.00012, ms=840)
    mod.structured = structured
    return mod


class TestActions(Case):
    def test_the_menus_engine_actions_go_to_the_engine(self):
        r, line = self.action("engine_grant", "player=Steve", "ability=fireball")
        self.assertEqual(r.returncode, 0, r)
        self.assertEqual(line, {"ok": True, "action": "engine_grant", "running": False,
                                "summary": "granted fireball to Steve", "facts": {"instance": "ability:fireball#1"},
                                "notes": []})
        self.assertEqual(self.engine.ops("grant"), [{"who": "Steve", "ability": "fireball"}])
        r, line = self.action("engine_give", "player=Alex", "item=fire_staff")
        self.assertEqual((line["ok"], line["summary"]), (True, "gave Alex fire_staff"))
        r, line = self.action("engine_start", "scenario=horde", "args.count=5")
        self.assertEqual((line["summary"], line["facts"], line["running"]),
                         ("horde is running as scenario:horde#1", {"instance": "scenario:horde#1"}, True))
        self.assertEqual(self.engine.ops("start")[-1], {"scenario": "horde", "args": {"count": "5"}, "wait_s": 15.0})
        r, line = self.action("engine_start", "scenario=command", 'args={"commands": ["say hi"]}')
        self.assertEqual((line["summary"], line["facts"], line["running"]),   # a one-off: its result
                         ("ran say hi", {"replies": ["ran say hi"], "instance": "scenario:command#2"}, False))
        r, line = self.action("engine_apply", "player=Steve", "mark=giant", "args.factor=3", "seconds=30")
        self.assertEqual(self.engine.ops("apply")[-1], {"who": "Steve", "mark": "giant", "seconds": 30.0,
                                                        "args": {"factor": "3"}})
        r, line = self.action("engine_stop", "scenario=horde")
        self.assertEqual((line["summary"], line["facts"]), ("stopped horde (scenario:horde#1)",
                                                            {"stopped": ["scenario:horde#1"]}))
        r, line = self.action("engine_revoke", "player=Steve", "ability=fireball")
        self.assertTrue(line["ok"], line)

    def test_a_refusal_is_ok_false_with_the_engines_error(self):
        r, line = self.action("engine_grant", "player=Steve", "ability=lightning")
        self.assertEqual(r.returncode, 1)
        self.assertEqual((line["ok"], line["error"]), (False, "no ability lightning"))
        r, line = self.action("engine_grant", "player=Steve")
        self.assertEqual((r.returncode, line["error"]), (2, "engine_grant needs ability=<its name under ENGINE>"))
        r, line = self.action("engine_grant", "player=@a", "ability=fireball")
        self.assertEqual(r.returncode, 2)
        r, line = self.action("engine_grant", "player=Steve", "ability=fireball", "as=mine")
        self.assertEqual((r.returncode, line["error"]), (2, "engine_grant has no value as; its values: player, ability"))
        r, line = self.action("engine_stop", "scenario=horde", "instance=scenario:horde#1")
        self.assertEqual(r.returncode, 2)
        self.engine.stop()
        r, line = self.action("engine_give", "player=Alex", "item=fire_staff")
        self.assertEqual((r.returncode, line["ok"]), (6, False))
        self.assertIn("the engine is not running", line["error"])

    def test_bind_and_unbind_through_their_actions(self):
        r, line = self.action("engine_bind", "player=Steve", "phrase=fire ball", "ability=fireball", "--task", "fire_steve")
        self.assertEqual(r.returncode, 0, r)
        self.assertEqual((line["summary"], line["facts"]["id"]),
                         ('"fire ball" for Steve now casts fireball; aliases fireball', "steve.fire_ball"))
        self.assertEqual(self.engine.ops("bind")[0]["by"], "fire_steve")
        r, line = self.action("engine_unbind", "player=Steve", "phrase=Fire Ball")
        self.assertEqual((r.returncode, line["summary"]), (0, 'removed Steve\'s incantation "fire ball"'))
        self.assertEqual(self.engine.ops("unbind"), [{"player": "Steve", "phrase": "fire ball"}])

    def test_a_config_rewrite_through_its_action_comments_and_all(self):
        self.assertOk(self.vfg("engine", "reload"))
        start, end = self.rw.find_config(FIREBALL, "Fireball")
        config = "".join(FIREBALL.splitlines(True)[start - 1:end])
        r, line = self.action("engine_config", "class_id=fireball", "config=" + bigger(config))
        self.assertEqual(r.returncode, 0, r)
        self.assertTrue(line["ok"], line)
        self.assertEqual(line["facts"]["changed"], ["fireball"])
        self.assertIn("# how long it burns", self.pc())
        self.assertIn("Field(5, ge=1, le=8)", self.deployed())
        r, line = self.action("engine_config", "class_id=fireball", "config=" + bigger(config, "5"))
        self.assertEqual((line["ok"], line["facts"]), (True, {"changed": []}))   # what it is already: changed nothing
        r, line = self.action("engine_config", "class_id=fireball",
                              "config=" + bigger(config, "12", "  # REFUSE in bounds: radius=12 is over its max 8"))
        self.assertEqual((r.returncode, line["ok"]), (1, False))
        self.assertIn("radius=12 is over its max 8", line["error"])
        self.assertIn("Field(5, ge=1, le=8)", self.pc())

OLD_STORE = {"version": 7, "incantations": [
    {"id": "krownjm.freeze", "player": "KrownJM", "phrase": "freeze", "action": "blizzard_freeze",
     "values": {"radius": 6}, "aliases": ["frieze"], "everyday_aliases": [], "cooldown_s": 3.0, "by": "blizzard_krown"},
    {"id": "krownjm.fireball", "player": "KrownJM", "phrase": "fireball", "action": "fireball_cast", "values": {},
     "aliases": ["fire ball"], "everyday_aliases": [], "cooldown_s": 3.0},
    {"id": "@a.zoo", "player": "@a", "phrase": "zoo time", "action": "old_thing", "values": {}, "aliases": [],
     "everyday_aliases": []}]}
OLD_PLACES = ('Storage everhear:places has the following contents: {base: {pos: [197, 68, 251], by: "castle_steve", '
              'dim: "minecraft:overworld"}, nether_hub: {pos: [10, 70, -3], dim: "minecraft:the_nether"}}')


class TestMigrate(Case):
    """vfg engine migrate: the old everhear:places and the old incantation store moved into the
    engine once, the bindings through lib/migrate_map.py (KrownJM's three among its rows), what has
    no row left behind and said. The table itself: test_migrate_map.py."""

    def setUp(self):
        super().setUp()
        self.engine.classes += [{"id": "blizzard", "kind": "ability", "file": "/opt/vfg/made/main/content/blizzard.py",
                                 "summary": "A freezing storm round the caster."}]
        self.rcon.responder = lambda cmd: OLD_PLACES if cmd == "data get storage everhear:places" else None
        write(os.path.join(self.box.rvfg, "incantations.json"), json.dumps(OLD_STORE))

    def test_a_dry_run_says_what_would_move_and_moves_nothing(self):
        r = self.vfg("engine", "migrate", "--dry-run")
        self.assertEqual(r.returncode, 1, r)            # old_thing has no row: left behind
        self.assertEqual(r.stdout.splitlines(), [
            "places: 2 found",
            'would move KrownJM "freeze" (blizzard_freeze): KrownJM "freeze" casts blizzard',
            '  note: its values {"radius":6} stay behind: blizzard\'s Config is one version for everyone',
            'would move KrownJM "fireball" (fireball_cast): KrownJM "fireball" casts fireball',
            'left @a "zoo time" (old_thing): old_thing has no engine class in the migration table'])
        self.assertEqual((self.engine.ops("places_import"), self.engine.ops("bind")), ([], []))

    def test_the_places_are_imported_once_and_the_bindings_bound_with_their_aliases(self):
        r = self.vfg("engine", "migrate", "--json")
        self.assertEqual(r.returncode, 1, r)
        got = json.loads(r.stdout)
        self.assertEqual(got["places"], {"found": 2, "imported": ["base", "nether_hub"], "skipped": [], "refused": [],
                                         "version": 1, "world": "w1", "ok": True})
        self.assertEqual(sorted(self.engine.places), ["base", "nether_hub"])
        self.assertEqual(self.engine.places["base"]["by"], "castle_steve")
        self.assertEqual(self.engine.ops("bind"), [
            {"player": "KrownJM", "phrase": "freeze", "ability": "blizzard", "aliases": ["frieze"],
             "everyday_aliases": [], "by": "blizzard_krown"},
            {"player": "KrownJM", "phrase": "fireball", "ability": "fireball", "aliases": ["fire ball"],
             "everyday_aliases": [], "by": ""}])
        self.assertEqual([b["ok"] for b in got["bindings"]], [True, True])
        self.assertEqual(got["left"], [{"from": '@a "zoo time" (old_thing)',
                                        "why": "old_thing has no engine class in the migration table"}])
        r = self.vfg("engine", "migrate")                 # again: the places once only, unless forced
        self.assertIn("places: 2 found, refused: this world's places were imported already", r.stdout)
        self.vfg("engine", "migrate", "--force")
        self.assertEqual(self.engine.ops("places_import")[-1]["force"], True)

    def test_an_everyones_binding_is_split_over_the_players_named(self):
        store = dict(OLD_STORE, incantations=[dict(OLD_STORE["incantations"][1], player="@a")])
        write(os.path.join(self.box.rvfg, "incantations.json"), json.dumps(store))
        r = self.vfg("engine", "migrate", "--dry-run")
        self.assertIn("no player is named to split it over: give --players", r.stdout)
        r = self.vfg("engine", "migrate", "--players", "Steve,Alex")
        self.assertOk(r)
        self.assertEqual([b["player"] for b in self.engine.ops("bind")], ["Steve", "Alex"])
        self.assertOneLine(self.vfg("engine", "migrate", "--players", "@a"), 2)


class TestRealEngine(Case):
    """The same commands against the engine itself, `python -m vfg_engine --fake-io` (the engine
    on its FakeIo, no Minecraft), serving its real control socket where the box's is, its content
    dir the box's made/main/content/: the contract as the engine keeps it. Skipped, saying why, when
    the engine cannot start here (engine/ missing, or not importable yet)."""

    def setUp(self):
        super().setUp()
        self.engine.stop()
        import subprocess
        import time
        engine_dir = os.path.join(REPO, "engine")
        if not os.path.isfile(os.path.join(engine_dir, "vfg_engine", "__main__.py")):
            self.skipTest("no engine/vfg_engine/__main__.py")
        examples = os.path.join(engine_dir, "examples")
        if not os.path.isfile(os.path.join(examples, "fireball.py")):
            self.skipTest("no engine/examples/fireball.py")
        os.remove(os.path.join(self.content, "fire_staff.py"))
        shutil.copy(os.path.join(examples, "fireball.py"), os.path.join(self.content, "fireball.py"))
        os.makedirs(self.boxed, exist_ok=True)
        shutil.copy(os.path.join(examples, "fireball.py"), os.path.join(self.boxed, "fireball.py"))
        here = os.path.join(self.box.rvfg, "engine")
        os.makedirs(here, exist_ok=True)
        env = dict(os.environ, PYTHONPATH=engine_dir, VFG_ENGINE_SOCKET=os.path.join(here, "engine.sock"),
                   VFG_ENGINE_LOG=os.path.join(here, "engine.log"), VFG_ENGINE_CONTENT=self.boxed,
                   VFG_ENGINE_STATE=os.path.join(here, "state.json"),
                   VFG_ENGINE_EXPORT=os.path.join(here, "incantations.json"))
        self.proc = subprocess.Popen([sys.executable, "-m", "vfg_engine", "--fake-io"], cwd=engine_dir, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(self.stop_engine)
        deadline = time.time() + 20
        while time.time() < deadline and self.proc.poll() is None:
            if os.path.exists(env["VFG_ENGINE_SOCKET"]) and self.vfg("engine", "status").returncode == 0:
                return
            time.sleep(0.1)
        why = (self.proc.stderr.read().decode("utf-8", "replace") if self.proc.poll() is not None else "no socket in 20 s")
        self.skipTest("the engine did not start on its FakeIo: %s" % one_line(why))

    def stop_engine(self):
        import signal
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(10)
            except Exception:
                self.proc.kill()
                self.proc.wait()
        self.proc.stderr.close()

    def test_the_engines_own_answers(self):
        r = self.vfg("engine", "classes")
        self.assertOk(r)
        self.assertRegex(r.stdout, r"^ability   fireball  Burns for burn_s seconds.*\(made/main/content/fireball\.py\)\n$")
        r = self.vfg("engine", "meta", "fireball")
        self.assertOk(r)
        self.assertIn("damage@0", r.stdout.splitlines()[0])
        r = self.vfg("engine", "reload", "fireball")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines()[1:], ["loaded: fireball", "switched: 0 running instances"])
        self.assertEqual(self.vfg("engine", "grant", "Steve", "fireball").stdout, "granted fireball to Steve\n")
        r = self.vfg("engine", "bind", "Steve", "fire ball", "fireball")
        self.assertOk(r)
        self.assertEqual(r.stdout, '"fire ball" for Steve now casts fireball; aliases fireball\n')
        listed = self.engine_json("incantations")["incantations"]
        self.assertEqual([(e["phrase"], e["ability"], e["scenario"], e["cooldown_s"]) for e in listed],
                         [("fire ball", "fireball", None, 5.0)])
        self.assertOk(self.vfg("engine", "unbind", "Steve", "fire ball"))
        self.assertOneLine(self.vfg("engine", "grant", "Steve", "nothing_here"), 1)

    def test_a_config_rewritten_refused_and_undone_by_the_engines_checks(self):
        r = self.vfg("engine", "config", "get", "fireball")
        self.assertOk(r)
        head, config = r.stdout.split("\n", 1)
        self.assertRegex(head, r"^made/main/content/fireball\.py, lines \d+ to \d+: the Config of Fireball, as written$")
        original = self.pc()
        r = self.vfg("engine", "config", "set", "fireball", input=config.replace("Field(8, ge=0, le=30)",
                                                                                 "Field(12, ge=0, le=30)"))
        self.assertOk(r)
        self.assertIn("explosion_base: float = Field(12, ge=0, le=30)", self.deployed())
        row = self.vfg("engine", "meta", "fireball").stdout.splitlines()[1].split()
        self.assertEqual((row[2], row[-1]), ("15", "radius=3"))   # damage at power 0: 1 x 3 s burning + the 12 blast
        self.assertIn("explosion_base=12", row)
        r = self.vfg("engine", "config", "set", "fireball", input=config.replace("Field(3, ge=1, le=8)",
                                                                                 "Field(12, ge=1, le=8)"))
        self.assertEqual(r.returncode, 1, r)
        self.assertIn("the old version keeps running", r.stderr)
        self.assertIn("Field(12, ge=0, le=30)", self.pc())      # the last good one, back
        self.assertIn("Field(3, ge=1, le=8)", self.deployed())
        self.assertOk(self.vfg("engine", "config", "undo", "fireball"))
        self.assertEqual((self.pc(), self.deployed()), (original, original))


def one_line(text):
    return " ".join(str(text).split())[-400:]


if __name__ == "__main__":
    import unittest
    unittest.main()
