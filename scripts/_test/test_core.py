"""The scripts under the dispatcher's six core actions — command, teleport, summon_around,
save_place, stop and edit — each run as a copy of its action, as EC2 runs it, against a fake
26.3 world: values checked by lib/py/vfg_actions.py as `vfg action run` checks them, written
into the copy's meta file in a stand-in run/, then `python3 -u main.py` in the script's folder
with PYTHONPATH=_lib, and its answer read from run/<copy>.result.json.

    uv run --no-project --python 3.12 --with pydantic python scripts/_test/test_core.py

The world is fake_places_world's, which answers with 26.3's recorded replies (mc_26_3.py). The
stop and edit scripts act on a copy of a script that only sleeps, started by instances.py in
the same stand-in. These are the old teleport and summon_around handlers' tests and the old
remote/action.py's, moved to the scripts that replace them.
"""
import ast
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
LIB = os.path.join(SCRIPTS, "_lib")
REPO = os.path.dirname(SCRIPTS)
for p in (HERE, LIB, os.path.join(REPO, "lib", "py")):
    if p not in sys.path:
        sys.path.insert(0, p)

import commands  # noqa: E402
import instances  # noqa: E402
import mc_26_3 as R  # noqa: E402
from fake_places_world import FakeWorld  # noqa: E402
from fake_rcon import FakeRcon  # noqa: E402

TMP = tempfile.mkdtemp(prefix="vfg-core-test-")
OW, NETHER = "minecraft:overworld", "minecraft:the_nether"
NAP = '''import json, os, time
meta = json.load(open(os.path.join(os.environ["VFG_RUN_DIR"], os.environ["VFG_SCRIPT"] + ".json")))
print("values", json.dumps(meta["values"], sort_keys=True), flush=True)
time.sleep(60)
'''

_catalog = []


def catalog():
    """(vfg_actions, the library's actions), loaded once."""
    if not _catalog:
        import vfg_actions
        _catalog.append((vfg_actions, vfg_actions.Catalog(REPO, None)))
    return _catalog[0]


class Ran(object):
    def __init__(self, code, result, out):
        self.code, self.out = code, out
        self.summary = (result or {}).get("summary")
        self.facts = (result or {}).get("facts") or {}

    def __repr__(self):
        return "Ran(%d, %r, %r)" % (self.code, self.summary, self.facts)


class Box(object):
    """A fake world behind a fake RCON, and a stand-in for /opt/mc and /opt/vfg (run/, and
    scripts/nap, a script that only sleeps) round it."""

    def __init__(self, world):
        self.world = world
        self.password = secrets.token_hex(8)
        self.rcon = FakeRcon(self.password, responder=world.respond).start()
        self.base = tempfile.mkdtemp(prefix="box-", dir=TMP)
        self.run_dir = os.path.join(self.base, "run")
        os.makedirs(self.run_dir)
        os.makedirs(os.path.join(self.base, "mc"))
        self.props = os.path.join(self.base, "mc", "server.properties")
        with open(self.props, "w") as f:
            f.write("enable-rcon=true\nrcon.port=%d\nrcon.password=%s\n" % (self.rcon.port, self.password))
        nap = os.path.join(self.base, "scripts", "nap")
        os.makedirs(nap)
        with open(os.path.join(nap, "main.py"), "w") as f:
            f.write(NAP)
        self.box = instances.Box(self.base, os.path.join(self.base, "mc"))
        self.procs = []

    def close(self):
        for name in self.box.names():
            self.box.stop(name)
        for proc in self.procs:
            proc.wait()
        self.rcon.stop()
        shutil.rmtree(self.base, ignore_errors=True)

    def nap(self, values=None, live=("cooldown",), as_=None):
        """A copy of nap, running: its name."""
        meta = {"action": "nap", "script": "nap", "source": "library", "values": dict(values or {}),
                "live": list(live), "runs": "until_stopped", "task": "T4"}
        name, pid, proc = self.box.start(meta, as_)
        self.procs.append(proc)
        self.box.wait(name, proc, 0.3)
        return name

    def run(self, action, given=None, raw=None, task="T7", extra=None, timeout=60):
        """A copy of `action`, run to its end: its values checked as vfg action run checks
        them (`given`), or sent as they are (`raw`, as from a vfg that checked nothing), with
        `extra` added (what vfg adds to an edit's values)."""
        va, cat = catalog()
        cls = cat.get(action)
        if raw is None:
            checked = va.check_values(cls, dict(given or {}))
            values, live = dict(checked.values), list(checked.live)
        else:
            values, live = dict(raw), []
        values.update(extra or {})
        meta = {"copy": action, "action": action, "script": cls.script, "source": "library", "values": values,
                "live": live, "runs": cls.runs, "task": task, "started": None}
        with open(os.path.join(self.run_dir, action + ".json"), "w") as f:
            json.dump(meta, f)
        result = os.path.join(self.run_dir, action + ".result.json")
        if os.path.exists(result):
            os.remove(result)
        env = dict(os.environ, PYTHONPATH=LIB, PYTHONDONTWRITEBYTECODE="1", VFG_MC_PROPERTIES=self.props,
                   VFG_RCON_HOST="127.0.0.1", VFG_RCON_LOCK=os.path.join(self.base, "rcon.lock"),
                   VFG_SCRIPT=action, VFG_INSTANCE=action, VFG_TASK=task or "", VFG_RUN_DIR=self.run_dir,
                   VFG_LIVE_POLL_S="0.5")
        env.pop("VFG_OWNER", None)
        p = subprocess.run([sys.executable, "-u", "main.py"], cwd=os.path.join(SCRIPTS, cls.script), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        out = p.stdout.decode("utf-8", "replace")
        assert "Traceback" not in out, out
        try:
            with open(result, encoding="utf-8") as f:
                got = json.load(f)
        except (OSError, ValueError):
            got = None
        # No core script places or removes a block, or sends the owner's commands.
        for c in list(self.world.sent):
            why = commands.command_problem(c) or ""
            assert "blocks" not in why and "owner" not in why, c
        return Ran(p.returncode, got, out)


class BoxTest(unittest.TestCase):
    def box(self, world):
        b = Box(world)
        self.addCleanup(b.close)
        return b


def sent(w, start):
    return [c for c in w.sent if c.startswith(start) or (" run " + start) in c]


# ------------------------------------------------------------------ command

def two():
    return FakeWorld(players={"Alex": (197.5, 68.0, 251.5)})


class CommandTests(BoxTest):
    def test_commands_run_in_order_and_report_the_replies(self):
        b = self.box(two())
        r = b.run("command", {"commands": ["/time set day", "give Alex minecraft:golden_apple 64", "weather rain"]})
        self.assertEqual(r.code, 0, r.out)
        self.assertEqual(r.summary, "Set minecraft:overworld to time marker minecraft:day; Gave 64 [Golden Apple] "
                                    "to Alex; Set the weather to rain")
        self.assertEqual(r.facts, {"replies": ["Set minecraft:overworld to time marker minecraft:day",
                                               "Gave 64 [Golden Apple] to Alex", "Set the weather to rain"],
                                   "failed": None, "ran": ["time set day", "give Alex minecraft:golden_apple 64",
                                                           "weather rain"], "partial": False})
        self.assertEqual(b.world.sent, ["time set day", "give Alex minecraft:golden_apple 64", "weather rain"])

    def test_a_minecraft_error_stops_the_list(self):
        b = self.box(two())
        r = b.run("command", {"commands": ["time set day", "give @a minecraft:nonsense", "weather rain"]})
        self.assertEqual(r.code, 1)
        self.assertEqual(r.facts["failed"], "give @a minecraft:nonsense")
        self.assertEqual(r.facts["ran"], ["time set day"])   # took effect: a retry must not send it again
        self.assertIs(r.facts["partial"], False)               # Minecraft refused it: none of it happened
        self.assertTrue(r.summary.startswith("Set minecraft:overworld to time marker minecraft:day; Unknown item "))
        self.assertNotIn("weather rain", b.world.sent)

    def test_rcon_dropping_mid_list_is_partial(self):
        b = self.box(two())

        def drops_on_weather(cmd):
            if cmd.startswith("weather"):
                raise OSError("the connection drops")   # the fake server closes it, as a crash would
            return b.world.respond(cmd)

        b.rcon.responder = drops_on_weather
        r = b.run("command", {"commands": ["time set day", "weather rain", "give Alex minecraft:diamond 1"]})
        self.assertEqual(r.code, 1)
        self.assertIs(r.facts["partial"], True)
        self.assertEqual((r.facts["ran"], r.facts["failed"]), (["time set day"], "weather rain"))
        self.assertIn("'weather rain' was sent: it may or may not have happened", r.summary)
        self.assertNotIn("give Alex minecraft:diamond 1", b.world.sent)
        self.assertEqual(b.rcon.cmds().count("weather rain"), 1)   # never sent again

    def test_minecraft_down_before_the_first_is_not_partial(self):
        b = self.box(two())
        b.rcon.stop()
        r = b.run("command", {"commands": ["time set day"]})
        self.assertEqual(r.code, 1)
        self.assertIs(r.facts["partial"], False)
        self.assertEqual(r.summary, "Minecraft is not answering RCON: 'time set day' was not sent")

    def test_an_offline_player_stops_the_list(self):
        b = self.box(two())
        r = b.run("command", {"commands": ["give Steve minecraft:diamond 1", "time set night"]})
        self.assertEqual((r.code, r.summary), (1, "No player was found"))
        self.assertEqual(r.facts, {"replies": ["No player was found"], "failed": "give Steve minecraft:diamond 1",
                                   "ran": [], "partial": False})
        self.assertEqual(b.world.sent, ["give Steve minecraft:diamond 1"])

    def test_answers_that_change_nothing_are_not_errors(self):
        b = self.box(two())
        r = b.run("command", {"commands": ["difficulty easy", "say hello", "data get storage everhear:places"]})
        self.assertEqual(r.code, 0, r.out)
        self.assertEqual(r.summary, "The difficulty did not change; it is already set to Easy; `say hello` gave no "
                                    "reply; Storage everhear:places has the following contents: {}")
        self.assertEqual(r.facts["replies"][1], "(no reply)")

    def test_an_execute_without_a_reply_may_not_have_happened(self):
        """A failing `execute as|at ... run`, or one aimed at nobody online, replies "": never called done."""
        b = self.box(two())
        r = b.run("command", {"commands": ["time set day", "execute at Steve run summon minecraft:zombie ~ ~ ~",
                                           "say hello"]})
        self.assertEqual(r.code, 0)
        self.assertEqual(r.summary, "Set minecraft:overworld to time marker minecraft:day; `execute at Steve run "
                                    "summon minecraft:zombie ~ ~ ~` gave no reply, so it may not have happened; "
                                    "`say hello` gave no reply")
        self.assertEqual(r.facts["replies"], ["Set minecraft:overworld to time marker minecraft:day", "(no reply)",
                                              "(no reply)"])

    def test_refused_by_the_action_before_it_is_sent(self):
        va, cat = catalog()
        for bad in ("op Alex", "fill ~ ~ ~ ~5 ~5 ~5 stone", "execute as @a at @s run setblock ~ ~ ~ tnt"):
            with self.assertRaises(ValueError):
                va.check_values(cat.get("command"), {"commands": ["time set day", bad]})

    def test_refused_by_the_script_before_anything_is_sent(self):
        """What a vfg that checked nothing would send: the script refuses it too, and sends none."""
        for bad in ("op Alex", "execute as @a run kick Alex", "/stop", "whitelist add Bob", "fill ~ ~ ~ ~5 ~5 ~5 stone",
                    "execute as @a at @s run setblock ~ ~ ~ tnt", "return run clone 0 0 0 1 1 1 5 5 5",
                    "say " + "a" * 1400, "say one\nop Alex", ""):
            b = self.box(two())
            r = b.run("command", raw={"commands": ["time set day", bad]})
            self.assertEqual(r.code, 1, bad)
            self.assertTrue(r.summary.startswith("refused: "), r.summary)
            self.assertEqual((r.facts["replies"], r.facts["ran"]), ([], []))
            self.assertEqual(b.world.sent, [])

    def test_none_or_too_many(self):
        b = self.box(two())
        self.assertEqual(b.run("command", {"commands": []}).summary, "no commands given")
        r = b.run("command", raw={"commands": ["say hi"] * 6})
        self.assertEqual((r.code, r.summary), (1, "at most 5 commands at a time, not 6"))
        self.assertEqual(b.world.sent, [])

    def test_mc_error_on_every_recorded_reply(self):
        ns = script_functions("command", "mc_error", ("ERROR_STARTS", "ERROR_PARTS", "ANSWERS"))
        for command, reply, is_error in R.COMMANDS:
            self.assertIs(ns["mc_error"](reply), is_error, (command, reply))
        # A name that happens to start like an error is still an answer.
        self.assertIs(ns["mc_error"]("Invalid Bob has the following entity data: 20.0f"), False)
        self.assertIs(ns["mc_error"]("Bob is not holding any item"), True)


def script_functions(script, fn, names):
    """A script's function and the constants it reads, without running the script."""
    path = os.path.join(SCRIPTS, script, "main.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == fn)
            or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets))]
    ns = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), path, "exec"), ns)
    return ns


# ------------------------------------------------------------------ teleport

def one_player(name="Steve", **more):
    w = FakeWorld()
    w.add(name, (10.5, 64.0, -3.5))
    for n, pos in more.items():
        w.add(n, pos)
    return w


class TeleportTests(BoxTest):
    def tp(self, w, **given):
        r = self.box(w).run("teleport", given)
        self.assertIn(r.code, (0, 1), r.out)
        return r

    def test_biome_found_lands_and_reports(self):
        w = one_player()
        w.located[("biome", "minecraft:snowy_taiga")] = (-368, 64, 912)
        w.landings = [(-368, 70, 912)]
        w.biomes[(-368, 912)] = "minecraft:snowy_taiga"
        r = self.tp(w, player="Steve", to="biome", target="snowy_taiga")
        self.assertEqual(r.code, 0)
        self.assertEqual(r.summary, "teleported Steve to a snowy taiga at -368 70 912")
        self.assertEqual((r.facts["pos"], r.facts["ring"], r.facts["dim"], r.facts["players"]),
                         ([-368, 70, 912], 8, OW, ["Steve"]))
        self.assertEqual(sent(w, "locate"), ["execute in minecraft:overworld positioned 10 64 -4 run locate biome "
                                             "minecraft:snowy_taiga"])
        self.assertEqual(sent(w, "effect"), ["effect give Steve minecraft:resistance 5 4 true",
                                             "effect give Steve minecraft:slow_falling 5 0 true"])
        self.assertEqual(sent(w, "spreadplayers"), ["execute in minecraft:overworld run spreadplayers -368 912 0 8 "
                                                    "under 320 false Steve"])
        # The cushion comes before the move.
        self.assertLess(w.sent.index("effect give Steve minecraft:slow_falling 5 0 true"),
                        w.sent.index(sent(w, "spreadplayers")[0]))
        self.assertFalse(sent(w, "spawnpoint"))   # 991 blocks: under 1,000

    def test_a_biome_found_at_its_edge_is_landed_in_further_on(self):
        """locate finds a biome's nearest edge: the landing moves up to 48 blocks further in, 16
        at a time, while `execute if biome` still says it is there."""
        w = one_player()
        w.located[("biome", "minecraft:snowy_taiga")] = (-368, 64, 912)
        d = math.hypot(-368 - 10, 912 + 4)
        steps = [(int(round(-368 + (-368 - 10) * k / d)), int(round(912 + (912 + 4) * k / d))) for k in (16, 32, 48)]
        for x, z in steps[:2]:
            w.biomes[(x, z)] = "minecraft:snowy_taiga"   # and not at 48
        r = self.tp(w, player="Steve", to="biome", target="snowy_taiga")
        self.assertEqual(r.code, 0, r.out)
        self.assertEqual(sent(w, "spreadplayers"), ["execute in minecraft:overworld run spreadplayers %d %d 0 8 under "
                                                    "320 false Steve" % steps[1]])
        # Three steps further in (the third is not in it), then where he landed: in it.
        self.assertEqual(len([c for c in w.sent if " if biome " in c]), 4)

    def test_a_landing_outside_the_biome_lands_again_from_there(self):
        """locate looks at one height and the chunks round its spot are not loaded to look
        further in: when they land outside the biome, it is looked for again from where they
        stand, and they land once more, with no second cushion; the spawn point is set once,
        where they end."""
        w = one_player()
        w.located[("biome", "minecraft:snowy_taiga")] = (-1368, 64, 912)
        w.landings = [(-1368, 64, 912), (-1360, 70, 920)]
        r = self.tp(w, player="Steve", to="biome", target="snowy_taiga")
        self.assertEqual(r.code, 0, r.out)
        self.assertEqual(r.summary, "teleported Steve to a snowy taiga at -1360 70 920; spawn point set there")
        self.assertEqual(len(sent(w, "locate")), 2)
        self.assertEqual(len(sent(w, "spreadplayers")), 2)
        self.assertEqual(len(sent(w, "effect")), 2)
        self.assertEqual(sent(w, "spawnpoint"), ["execute in minecraft:overworld run spawnpoint Steve -1360 70 920"])

    def test_a_tagged_structure(self):
        w = one_player()
        w.located[("structure", "#minecraft:village")] = (-544, 0, 320, "minecraft:village_plains")
        r = self.tp(w, player="Steve", to="structure", target="#village")
        self.assertEqual(r.summary, "teleported Steve to a plains village at -544 64 320")

    def test_locate_finding_nothing_fails_before_anything_moves(self):
        w = one_player()
        r = self.tp(w, player="Steve", to="biome", target="cherry_grove")
        self.assertEqual((r.code, r.summary, r.facts), (1, "no cherry grove within reach of Steve", {}))
        self.assertFalse(sent(w, "effect") or sent(w, "spreadplayers"))

    def test_a_structure_id_that_is_a_tag_is_tried_once_as_the_tag(self):
        w = one_player()
        w.located[("structure", "minecraft:village")] = "invalid"   # village is a tag of villages
        w.located[("structure", "#minecraft:village")] = (-544, 0, 320, "minecraft:village_plains")
        r = self.tp(w, player="Steve", to="structure", target="village")
        self.assertEqual((r.code, r.summary), (0, "teleported Steve to a plains village at -544 64 320"))
        self.assertEqual([c.split(" run ")[1] for c in sent(w, "locate")],
                         ["locate structure minecraft:village", "locate structure #minecraft:village"])

    def test_an_unknown_id(self):
        w = one_player()
        w.located[("structure", "minecraft:castle")] = "invalid"
        w.located[("structure", "#minecraft:castle")] = "invalid"   # no tag of that name either
        w.located[("biome", "minecraft:nonsense")] = "invalid"
        self.assertEqual(self.tp(w, player="Steve", to="structure", target="castle").summary,
                         "Minecraft has no structure called minecraft:castle")
        self.assertEqual(len(sent(w, "locate")), 2)   # the tag tried once, then the failure
        self.assertEqual(self.tp(w, player="Steve", to="biome", target="nonsense").summary,
                         "Minecraft has no biome called minecraft:nonsense")

    def test_target_is_checked_against_to(self):
        w = one_player()
        for to, target, said in (("biome", "", "'' is not a biome id"),
                                 ("structure", "a village", "'a village' is not a structure id"),
                                 ("place", "my castle!", "'my castle!' is not a place name"),
                                 ("coords", "100", "coordinates are 'x z' or 'x y z', not '100'"),
                                 ("far", "very", "far takes a distance in blocks, not 'very'"),
                                 ("far", "2000000", "far takes a distance from 1 to 1000000 blocks")):
            r = self.tp(w, player="Steve", to=to, target=target)
            self.assertEqual((r.code, r.summary), (1, said))
        self.assertFalse(sent(w, "effect") or sent(w, "spreadplayers"))

    def test_a_landing_in_water_lands_again_at_16(self):
        w = one_player()
        w.located[("biome", "minecraft:cherry_grove")] = (812, 70, -340)
        w.landings = [(812, 63, -340), (820, 71, -330)]
        w.biomes[(820, -330)] = "minecraft:cherry_grove"
        w.block(OW, 812, 62, -340, "minecraft:water")
        r = self.tp(w, player="Steve", to="biome", target="cherry_grove")
        self.assertEqual((r.code, r.facts["ring"]), (0, 16))
        self.assertEqual(r.summary, "teleported Steve to a cherry grove at 820 71 -330")
        self.assertEqual([re.search(r" 0 (\d+) under", c).group(1) for c in sent(w, "spreadplayers")], ["8", "16"])
        self.assertEqual(len([c for c in w.sent if " unless block ~ ~-1 ~ minecraft:water" in c]), 2)

    def test_every_unsafe_ground_lands_again(self):
        for bad in ("minecraft:magma_block", "minecraft:cactus", "minecraft:powder_snow", "minecraft:soul_campfire",
                    "minecraft:sweet_berry_bush", "minecraft:pointed_dripstone", "minecraft:lava"):
            w = one_player()
            w.landings = [(100, 64, 100), (130, 64, 130)]
            w.block(OW, 100, 63, 100, bad)
            r = self.tp(w, player="Steve", to="coords", target="100 100")
            self.assertEqual((r.code, r.facts["ring"], r.facts["pos"]), (0, 16, [130, 64, 130]), bad)

    def test_four_bad_rings_fail_and_put_the_player_back(self):
        w = one_player()
        w.located[("biome", "minecraft:cherry_grove")] = (812, 70, -340)
        w.landings = [(812 + i, 64, -340) for i in range(4)]
        for i in range(4):
            w.block(OW, 812 + i, 63, -340, "minecraft:magma_block")
        r = self.tp(w, player="Steve", to="biome", target="cherry_grove")
        self.assertEqual((r.code, r.summary), (1, "no safe spot within 4 rings of a cherry grove at 812 -340; "
                                                  "Steve is still where they were"))
        self.assertEqual([re.search(r" 0 (\d+) under", c).group(1) for c in sent(w, "spreadplayers")],
                         ["8", "16", "32", "64"])
        self.assertEqual(sent(w, "tp "), ["execute in minecraft:overworld run tp Steve 10.500 64.000 -3.500"])
        self.assertEqual(w.players["Steve"].pos, [10.5, 64.0, -3.5])

    def test_no_room_at_all_fails_without_moving(self):
        w = one_player()
        w.landings = ["fail"] * 4
        r = self.tp(w, player="Steve", to="coords", target="-96 -320")
        self.assertEqual((r.code, r.summary), (1, "no safe spot within 4 rings of -96 -320; Steve is still where "
                                                  "they were"))
        self.assertFalse(sent(w, "tp "))

    def test_a_saved_place_is_reached_exactly_when_the_spot_is_safe(self):
        w = one_player()
        w.save_place("castle", [197, 68, 251], by="T23")
        r = self.tp(w, player="Steve", to="place", target="castle")
        self.assertEqual(r.summary, "teleported Steve to castle at 197 68 251")
        self.assertEqual((r.facts["target"], r.facts["ring"]), ("castle", 0))
        self.assertEqual(w.players["Steve"].pos, [197.5, 68.0, 251.5])   # the block's centre, not 8 blocks off
        [check] = [c for c in w.sent if " positioned 197 68 251 " in c]
        for part in ("unless block ~ ~-1 ~ #minecraft:replaceable", "unless block ~ ~-1 ~ minecraft:magma_block",
                     "if block ~ ~ ~ #minecraft:replaceable", "if block ~ ~1 ~ #minecraft:replaceable",
                     "unless block ~ ~1 ~ minecraft:water"):
            self.assertIn(part, check)
        self.assertEqual(sent(w, "tp "), ["execute in minecraft:overworld run tp Steve 197.5 68.0 251.5"])
        self.assertFalse(sent(w, "spreadplayers"))
        r = self.tp(w, player="Steve", to="place", target="nowhere")
        self.assertEqual((r.code, r.summary), (1, "there is no saved place called nowhere"))

    def test_an_unsafe_saved_place_falls_back_to_the_rings(self):
        for block, at in (("minecraft:lava", 0), ("minecraft:water", 1), ("minecraft:magma_block", -1),
                          ("minecraft:air", -1)):
            w = one_player()
            w.save_place("castle", [197, 68, 251], by="T23")
            w.block(OW, 197, 68 + at, 251, block)
            w.block(OW, 197, 69 + at, 251, block)   # the spot one block up is no better
            w.landings = [(199, 68, 253)]
            r = self.tp(w, player="Steve", to="place", target="castle")
            self.assertEqual((r.summary, r.facts["ring"]), ("teleported Steve to castle at 199 68 253", 8), block)
            # A known height: land at or below it, not on the roof.
            self.assertEqual(sent(w, "spreadplayers"), ["execute in minecraft:overworld run spreadplayers 197 251 0 8 "
                                                        "under 70 false Steve"])
            self.assertFalse([c for c in sent(w, "tp ") if " tp Steve 197.5" in c])

    def test_a_saved_place_on_a_slab_lands_on_top_of_it(self):
        w = one_player()
        w.save_place("hall", [10, 64, 10])
        w.block(OW, 10, 64, 10, "minecraft:oak_slab")   # where the player stood: half a block up
        r = self.tp(w, player="Steve", to="place", target="hall")
        self.assertEqual((w.players["Steve"].pos, r.facts["ring"]), ([10.5, 65.0, 10.5], 0))
        self.assertFalse(sent(w, "spreadplayers"))

    def test_a_saved_place_in_the_nether(self):
        w = one_player()
        w.save_place("fortress", [-400, 70, -96], dim=NETHER)
        r = self.tp(w, player="Steve", to="place", target="fortress")
        self.assertEqual(r.summary, "teleported Steve to fortress at -400 70 -96 in the nether")
        self.assertEqual(sent(w, "tp "), ["execute in minecraft:the_nether run tp Steve -399.5 70.0 -95.5"])
        self.assertEqual((r.facts["dim"], w.players["Steve"].dim), (NETHER, NETHER))
        self.assertFalse(sent(w, "spawnpoint"))   # another dimension: the spawn point stays

    def test_everyone(self):
        w = one_player("Sam", Steve=(0.5, 70.0, 0.5), Alex=(30.5, 65.0, 30.5))
        w.located[("biome", "minecraft:snowy_taiga")] = (-1408, 64, 1408)
        w.landings = [(-1408, 68, 1410)]
        w.biomes[(-1408, 1410)] = "minecraft:snowy_taiga"
        r = self.tp(w, player="@a", to="biome", target="snowy_taiga")
        self.assertEqual(r.code, 0, r.out)
        self.assertEqual(r.summary, "teleported everyone (Sam, Steve, Alex) to a snowy taiga at -1408 68 1410; "
                                    "spawn point set there")
        self.assertEqual(r.facts["players"], ["Sam", "Steve", "Alex"])
        self.assertEqual(len(sent(w, "effect")), 6)   # Resistance and Slow Falling for each
        self.assertEqual(len(sent(w, "spreadplayers")), 1)
        self.assertEqual(sent(w, "tp "), ["tp Steve Sam", "tp Alex Sam"])
        for p in w.players.values():
            self.assertEqual(p.pos, [-1407.5, 68.0, 1410.5])
            self.assertEqual(p.spawnpoint, (-1408, 68, 1410, OW))
        self.assertEqual(sorted(r.facts["spawn_set"]), ["Alex", "Sam", "Steve"])

    def test_two_named_players_one_of_them_offline(self):
        w = one_player("Steve", Alex=(30.5, 65.0, 30.5))
        r = self.tp(w, player="steve,Bob,Alex", to="coords", target="100 -200")
        self.assertEqual(r.code, 0, r.out)
        self.assertEqual(r.summary, "teleported Steve and Alex to 100 64 -200; Bob is not online")
        self.assertEqual((r.facts["players"], r.facts["offline"]), (["Steve", "Alex"], ["Bob"]))

    def test_nobody_online(self):
        r = self.tp(FakeWorld(), player="@a", to="far", target="1000")
        self.assertEqual((r.code, r.summary, r.facts), (1, "nobody is online", {}))

    def test_coords(self):
        w = one_player()
        r = self.tp(w, player="Steve", to="coords", target="100 -200")
        self.assertEqual(r.summary, "teleported Steve to 100 64 -200")
        self.assertTrue(sent(w, "spreadplayers")[0].endswith("spreadplayers 100 -200 0 8 under 320 false Steve"))
        w2 = one_player()
        self.tp(w2, player="Steve", to="coords", target="100, 30, -200")
        self.assertIn("under 32 ", sent(w2, "spreadplayers")[0])

    def test_far_lands_that_far_away_and_sets_the_spawn_point(self):
        w = one_player()
        r = self.tp(w, player="Steve", to="far", target="5000")
        self.assertEqual(r.code, 0)
        m = re.search(r"spreadplayers (-?\d+) (-?\d+) 0 8 under 320", sent(w, "spreadplayers")[0])
        self.assertLess(abs(math.hypot(int(m.group(1)) - 10.5, int(m.group(2)) + 3.5) - 5000), 2)
        self.assertTrue(r.summary.startswith("teleported Steve 5000 blocks away, to "))
        self.assertTrue(r.summary.endswith("; spawn point set there"))
        x, y, z = r.facts["pos"]
        self.assertEqual(sent(w, "spawnpoint"), ["execute in minecraft:overworld run spawnpoint Steve %d %d %d"
                                                 % (x, y, z)])

    def test_far_in_the_nether_looks_below_the_roof(self):
        w = one_player()
        self.tp(w, player="Steve", to="far", target="200", dimension="the_nether")
        self.assertRegex(sent(w, "spreadplayers")[0],
                         r"^execute in minecraft:the_nether run spreadplayers -?\d+ -?\d+ 0 8 under 120 false Steve$")

    def test_a_nether_biome_is_looked_for_from_the_scaled_position(self):
        w = FakeWorld()
        w.add("Steve", (800.5, 64.0, -1600.5))
        w.located[("biome", "minecraft:crimson_forest")] = (288, 1, 288)
        w.biomes[(288, 288)] = "minecraft:crimson_forest"
        r = self.tp(w, player="Steve", to="biome", target="crimson_forest", dimension="the_nether")
        self.assertEqual(sent(w, "locate"), ["execute in minecraft:the_nether positioned 100 64 -201 run locate biome "
                                             "minecraft:crimson_forest"])
        self.assertIn("under 120", sent(w, "spreadplayers")[0])
        self.assertEqual(r.summary, "teleported Steve to a crimson forest at 288 64 288 in the nether")

    def test_spawn_point_with_and_without_a_bed(self):
        w = one_player()
        w.players["Steve"].respawn = (50, 70, 60, OW)
        r = self.tp(w, player="Steve", to="spawn_point")
        self.assertEqual(r.summary, "teleported Steve to their spawn point at 50 70 60")   # exactly
        self.assertFalse(sent(w, "spreadplayers"))
        w2 = one_player()
        w2.world_spawn = (20, 80, -20)
        r = self.tp(w2, player="Steve", to="spawn_point")
        self.assertEqual(r.summary, "teleported Steve to the world spawn at 20 64 -20")
        # RCON's commands run at the world spawn: spreadplayers centres on `~ ~`.
        self.assertEqual(sent(w2, "spreadplayers"), ["spreadplayers ~ ~ 0 8 under 320 false Steve"])

    def test_spawn_point_never_moves_the_spawn_point(self):
        w = one_player()
        w.players["Steve"].respawn = (5000, 70, 5000, OW)
        r = self.tp(w, player="Steve", to="spawn_point")
        self.assertEqual(r.code, 0)
        self.assertFalse(sent(w, "spawnpoint"))

    def test_last_death_with_and_without_one(self):
        w = one_player()
        w.players["Steve"].death = (-20, 30, 40, NETHER)
        w.block(NETHER, -20, 30, 40, "minecraft:lava")   # died in lava: not there, round it
        r = self.tp(w, player="Steve", to="last_death")
        self.assertEqual(r.summary, "teleported Steve to where they last died, at -20 64 40 in the nether")
        self.assertEqual(sent(w, "spreadplayers"), ["execute in minecraft:the_nether run spreadplayers -20 40 0 8 "
                                                    "under 32 false Steve"])
        w2 = one_player()
        r = self.tp(w2, player="Steve", to="last_death")
        self.assertEqual((r.code, r.summary), (1, "Steve has no death on record"))
        self.assertFalse(sent(w2, "spreadplayers"))

    def test_an_offline_player(self):
        w = one_player("Alex")   # Steve is not in the world
        r = self.tp(w, player="Steve", to="coords", target="0 0")
        # `offline` tells the dispatcher to drop the pick at once, with no planner call
        self.assertEqual((r.code, r.summary, r.facts), (1, "Steve is not online", {"offline": ["Steve"]}))
        self.assertEqual(w.sent, ["list"])

    def test_a_tampered_place_is_not_used(self):
        w = one_player()
        w.storage["evil"] = {"pos": [1, 2, 3], "by": "", "dim": "minecraft:overworld run fill ~ ~ ~ ~9 ~9 ~9 tnt"}
        r = self.tp(w, player="Steve", to="place", target="evil")
        self.assertEqual(r.code, 1)
        self.assertFalse(sent(w, "spreadplayers"))


# ------------------------------------------------------------------ summon_around

class SummonAroundTests(BoxTest):
    def summon(self, w, task="T7", **given):
        b = self.box(w)
        r = b.run("summon_around", given, task=task)
        self.assertIn(r.code, (0, 1), r.out)
        return r

    def test_summon_in_batches_of_ten(self):
        w = FakeWorld()
        w.add("Steve", (197.5, 68.0, 251.5))
        r = self.summon(w, players="Steve", mob="zombie", count=25)
        self.assertEqual(r.code, 0, r.out)
        self.assertEqual(r.summary, "summoned 25 zombies round Steve at 197 68 251")
        self.assertEqual((r.facts["summoned"], r.facts["standing"], r.facts["tag"]), (25, 25, "vfg_summon_T7"))
        summons = sent(w, "summon")
        self.assertEqual(len(summons), 25)
        self.assertEqual(summons[0], 'execute in minecraft:overworld positioned 197.500 68.000 251.500 run summon '
                                     'minecraft:zombie ~ ~ ~ {Tags:["vfg","vfg_summon_T7","vfg_summon_around_new"],'
                                     'PersistenceRequired:1b}')
        spreads = sent(w, "spreadplayers")
        self.assertEqual(spreads, ["execute in minecraft:overworld run spreadplayers 197.500 251.500 2 16 under 76 "
                                   "false @e[tag=vfg_summon_around_new]"] * 3)
        # Ten summons, then their spread; the batch tag comes off after each.
        i = w.sent.index(spreads[0])
        self.assertEqual(sum(1 for c in w.sent[:i] if " summon " in c), 10)
        self.assertEqual(sent(w, "tag "), ["tag @e[tag=vfg_summon_around_new] remove vfg_summon_around_new"] * 3)
        # A tag of their own, never the copy's: they outlast it, and a later copy named the same
        # never removes them.
        self.assertTrue(all(e["tags"] == {"vfg", "vfg_summon_T7"} for e in w.entities))

    def test_summon_a_hundred(self):
        w = FakeWorld()
        w.add("Steve", (197.5, 68.0, 251.5))
        r = self.summon(w, players="Steve", mob="minecraft:zombie", count=100)
        self.assertEqual(r.summary, "summoned 100 zombies round Steve at 197 68 251")
        self.assertEqual((len(sent(w, "spreadplayers")), len(w.entities)), (10, 100))

    def test_summon_round_everyone(self):
        w = FakeWorld()
        w.add("Alex", (197.5, 68.0, 251.5))
        w.add("Steve", (10.2, 64.0, -3.7), dim=NETHER)
        r = self.summon(w, players="@a", mob="sheep", count=3)
        self.assertEqual(r.summary, "summoned 3 sheep round each of Alex at 197 68 251 and Steve at 10 64 -4")
        self.assertTrue(any(c.startswith("execute in minecraft:the_nether positioned 10.200 64.000 -3.700 run summon")
                            for c in w.sent))
        self.assertEqual(r.facts["standing"], 6)
        self.assertEqual([x["dim"] for x in r.facts["rounds"]], [OW, NETHER])

    def test_summon_options(self):
        w = FakeWorld()
        w.add("Steve", (0.5, 64.0, 0.5))
        r = self.summon(w, players="Steve", mob="wolf", count=2, radius=4, scale=2.0, name='Bob "Fang"',
                        glowing=True)
        self.assertEqual(r.summary, 'summoned 2 wolves round Steve at 0 64 0 (named Bob "Fang", glowing, 2x size)')
        nbt = w.entities[0]["nbt"]
        self.assertIn('CustomName:"Bob \\"Fang\\"",CustomNameVisible:1b', nbt)
        self.assertIn("Glowing:1b", nbt)
        self.assertIn('attributes:[{id:"scale",base:2}]', nbt)
        self.assertEqual(w.entities[0]["name"], 'Bob "Fang"')
        self.assertIn(" 1 4 under ", sent(w, "spreadplayers")[0])   # a small radius packs them closer

    def test_summon_spread_that_fails_is_tried_closer(self):
        w = FakeWorld()
        w.add("Steve", (0.5, 64.0, 0.5))
        w.spread_fail = 1
        r = self.summon(w, players="Steve", mob="cow", count=5)
        self.assertEqual(r.code, 0)
        self.assertEqual([c.split()[7] for c in sent(w, "spreadplayers")], ["2", "0"])

    def test_summon_refused_after_some_stand_is_partial(self):
        w = FakeWorld()
        w.add("Steve", (0.5, 64.0, 0.5))
        b = self.box(w)
        calls = {"n": 0}

        def refuses_the_second_batch(cmd):
            if " run summon " in cmd:
                calls["n"] += 1
                if calls["n"] > 10:
                    return "Unable to summon entity"
            return w.respond(cmd)

        b.rcon.responder = refuses_the_second_batch
        r = b.run("summon_around", {"players": "Steve", "mob": "cow", "count": 20})
        self.assertEqual(r.code, 1)
        self.assertIs(r.facts["partial"], True)
        self.assertEqual(r.facts["summoned"], 10)
        self.assertIn("then Minecraft said: Unable to summon entity", r.summary)

    def test_summon_refusals(self):
        w = FakeWorld(peaceful=True)
        w.add("Alex", (0.5, 64.0, 0.5))
        r = self.summon(w, players="Alex", mob="zombie")
        self.assertEqual((r.code, r.summary), (1, "could not summon zombie: Monsters cannot be summoned in Peaceful "
                                                  "difficulty"))
        r = self.summon(w, players="Alex", mob="nonsense")
        self.assertTrue(r.summary.startswith("could not summon nonsense: Can't find element"), r.summary)
        r = self.summon(w, players="Alex", mob="player")
        self.assertEqual(r.summary, "could not summon player: Can't summon entity of type minecraft:player")
        r = self.summon(w, players="Steve", mob="cow")
        self.assertEqual((r.code, r.summary, r.facts), (1, "Steve is not online", {"offline": ["Steve"]}))
        r = self.summon(FakeWorld(), players="@a", mob="cow")
        self.assertEqual((r.code, r.summary), (1, "nobody is online"))
        self.assertFalse(sent(w, "spreadplayers"))

    def test_some_given_are_offline(self):
        w = FakeWorld()
        w.add("Alex", (0.5, 64.0, 0.5))
        r = self.summon(w, players="Alex,Steve", mob="cow", count=2)
        self.assertEqual(r.code, 0)
        self.assertEqual(r.summary, "summoned 2 cows round Alex at 0 64 0; Steve is not online")
        self.assertEqual(r.facts["offline"], ["Steve"])

    def test_with_no_task_the_tag_is_the_copys_name(self):
        w = FakeWorld()
        w.add("Alex", (0.5, 64.0, 0.5))
        r = self.summon(w, task=None, players="Alex", mob="cow", count=1)
        self.assertEqual(r.facts["tag"], "vfg_summon_summon_around")


# ------------------------------------------------------------------ save_place

class SavePlaceTests(BoxTest):
    def test_where_a_player_stands(self):
        w = FakeWorld(players={"Steve": (197.9, 68.0, 251.2)})
        r = self.box(w).run("save_place", {"name": "castle", "player": "Steve"}, task="T23")
        self.assertEqual((r.code, r.summary), (0, "saved castle at 197 68 251 in overworld"))
        self.assertEqual(r.facts, {"name": "castle", "pos": [197, 68, 251], "dim": OW, "by": "T23"})
        self.assertEqual(w.storage["castle"], {"pos": [197, 68, 251], "by": "T23", "dim": OW})

    def test_coordinates_in_the_nether(self):
        w = FakeWorld()
        r = self.box(w).run("save_place", {"name": "base", "at": "1, 5.5, -2 nether"})
        self.assertEqual((r.code, r.summary), (0, "saved base at 1 5 -2 in the_nether"))
        self.assertEqual(w.storage["base"]["dim"], NETHER)

    def test_another_place_or_a_player_as_at(self):
        w = FakeWorld(players={"Alex": (30.5, 65.0, -30.5)})
        w.save_place("castle", [197, 68, 251], by="T23")
        b = self.box(w)
        self.assertEqual(b.run("save_place", {"name": "gate", "at": "castle"}).summary,
                         "saved gate at 197 68 251 in overworld")
        self.assertEqual(b.run("save_place", {"name": "alex", "at": "Alex"}).summary,
                         "saved alex at 30 65 -31 in overworld")

    def test_refusals(self):
        w = FakeWorld(players={"Steve": (0.5, 64.0, 0.5)})
        b = self.box(w)
        for given, said in (({"name": "", "player": "Steve"}, "which name?"),
                            ({"name": "home", "player": "Steve", "at": "1 2 3"}, "give the player or the coordinates, "
                                                                                  "not both"),
                            ({"name": "home"}, "give the player or the coordinates")):
            r = b.run("save_place", given)
            self.assertEqual(r.code, 1)
            self.assertTrue(r.summary.startswith(said), r.summary)
        r = b.run("save_place", {"name": "home", "player": "Bob"})
        self.assertEqual((r.code, r.summary, r.facts), (1, "Bob is not online", {"offline": ["Bob"]}))
        r = b.run("save_place", {"name": "home", "at": "nowhere"})
        self.assertEqual((r.code, r.summary), (1, "nowhere is nowhere: not x y z, a saved place or a player online"))
        self.assertEqual(w.storage, {})


# ------------------------------------------------------------------ stop and edit

class StopTests(BoxTest):
    def test_stops_a_running_copy_and_what_carries_its_tag(self):
        w = FakeWorld()
        b = self.box(w)
        name = b.nap({"cooldown": 2})
        pid = b.box.pid(name)
        self.assertTrue(pid)
        w.entities.append({"type": "zombie", "tags": {"vfg", "vfg_nap"}, "pos": [0, 64, 0], "dim": OW, "name": None,
                           "nbt": ""})
        w.entities.append({"type": "zombie", "tags": {"vfg", "vfg_summon_T7"}, "pos": [0, 64, 0], "dim": OW,
                           "name": None, "nbt": ""})
        r = b.run("stop", {"copy": "nap"})
        self.assertEqual((r.code, r.summary), (0, "stopped nap; took away 1 thing it left"))
        self.assertEqual(r.facts, {"copy": "nap", "action": "nap", "was_running": True, "removed": 1})
        self.assertIsNone(b.box.pid("nap"))
        self.assertTrue(instances.gone(pid))
        self.assertEqual([e["tags"] for e in w.entities], [{"vfg", "vfg_summon_T7"}])   # what was made to stay

    def test_one_copy_of_two(self):
        b = self.box(FakeWorld())
        b.nap()
        b.nap()
        r = b.run("stop", {"copy": "nap_2"})
        self.assertEqual((r.code, r.summary), (0, "stopped nap_2"))
        self.assertTrue(b.box.pid("nap"))
        self.assertIsNone(b.box.pid("nap_2"))

    def test_a_copy_that_ended(self):
        b = self.box(FakeWorld())
        b.nap()
        b.box.stop("nap")
        r = b.run("stop", {"copy": "nap"})
        self.assertEqual((r.code, r.summary), (0, "nap had already ended"))
        self.assertIs(r.facts["was_running"], False)

    def test_refusals(self):
        b = self.box(FakeWorld())
        for given, said in (({"copy": "ghost"}, "nothing runs as ghost"), ({}, "which copy?"),
                            ({"copy": "stop"}, "stop is this stop itself")):
            r = b.run("stop", given)
            self.assertEqual(r.code, 1)
            self.assertTrue(r.summary.startswith(said), r.summary)


class EditTests(BoxTest):
    def edit(self, b, copy, values, changed, live_only):
        return b.run("edit", {"copy": copy}, extra={"values": values, "changed": changed, "live_only": live_only})

    def test_a_live_value_goes_into_the_live_file(self):
        b = self.box(FakeWorld())
        name = b.nap({"cooldown": 2, "player": "@a"})
        pid = b.box.pid(name)
        r = self.edit(b, name, {"cooldown": 0, "player": "@a"}, ["cooldown"], True)
        self.assertEqual((r.code, r.summary), (0, "nap: cooldown=0, live"))
        self.assertEqual(r.facts, {"copy": "nap", "action": "nap", "changed": {"cooldown": 0}, "live": True,
                                   "restarted": False})
        with open(os.path.join(b.run_dir, "nap.values.json")) as f:
            self.assertEqual(json.load(f), {"cooldown": 0})
        self.assertEqual(b.box.pid(name), pid)   # never restarted

    def test_any_other_value_restarts_it_with_the_rest_kept(self):
        b = self.box(FakeWorld())
        name = b.nap({"cooldown": 2, "player": "@a"})
        pid = b.box.pid(name)
        r = self.edit(b, name, {"cooldown": 2, "player": "Alex"}, ["player"], False)
        self.assertEqual((r.code, r.summary), (0, "restarted nap with player=Alex"))
        self.assertEqual((r.facts["live"], r.facts["restarted"], r.facts["running"]), (False, True, True))
        new = b.box.pid(name)
        self.assertTrue(new and new != pid)
        self.assertEqual(b.box.meta(name)["values"], {"cooldown": 2, "player": "Alex"})
        deadline = time.time() + 5
        while time.time() < deadline and "Alex" not in "".join(b.box.tail(name)):
            time.sleep(0.05)
        self.assertIn('"player": "Alex"', "".join(b.box.tail(name)))

    def test_refusals(self):
        b = self.box(FakeWorld())
        r = self.edit(b, "nap", {"cooldown": 0}, ["cooldown"], True)
        self.assertEqual((r.code, r.summary), (1, "nap is not running"))
        r = self.edit(b, "", {"cooldown": 0}, ["cooldown"], True)
        self.assertEqual(r.code, 1)
        self.assertTrue(r.summary.startswith("which copy?"))
        r = b.run("edit", {"copy": "nap", "values": {}})
        self.assertEqual((r.code, r.summary), (1, "no values to change on nap"))

    def test_without_what_vfg_adds_the_box_works_it_out(self):
        b = self.box(FakeWorld())
        name = b.nap({"cooldown": 2, "player": "@a"})
        r = b.run("edit", {"copy": name, "values": {"cooldown": 0}})
        self.assertEqual((r.code, r.summary), (0, "nap: cooldown=0, live"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
