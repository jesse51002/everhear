"""The build examples on a real Minecraft 26.3 server, with Carpet's fake players: world_replace,
chunk_changer, ice_walk, meteor, lava_rain, castle, ice_castle, sniper_nest, nuke, hellscape
and sky_platform.

    integration/realmc/up e3 --fresh && eval "$(integration/realmc/env e3)"
    uv run --no-project --python 3.12 python scripts/_test/live_examples_build.py [-k castle]

Each example is a copy of its action, started as the box starts it, with `vfg action run`
(its values checked here, then deployed to the realmc stand-in and run there on Python 3.12),
with small values near the arena (0 85 0, its grass floor at y 84, x and z -16..15) so a big
one stays small. A once action's run answers when the copy has ended; the meteor and the nuke,
changed while they run, are started in the background. Steve walks, falls or stands where the
example needs him; then the world is asked with RCON what it did: blocks (execute if block),
health of husks and of a tamed wolf that must be spared, a sign's text, Steve's respawn. The
copies that keep running get one live value changed with `vfg script set` and are seen to
take it; the ones that build and end are checked once they end. After each, nothing tagged
vfg_<copy> is left: no entity, no forceload of its own. It changes the world, so it runs
only against a realmc test server, never the box: `up e3 --fresh` puts the world back after.
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "_lib")
REALMC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "integration", "realmc")
sys.path.insert(0, LIB)
sys.path.insert(0, REALMC)

PROPS = os.environ.get("VFG_MC_PROPERTIES", "")
if not os.environ.get("REALMC_NAME") or "realmc" not in os.path.realpath(PROPS):
    sys.exit("live_examples_build: point it at a realmc server first: eval \"$(integration/realmc/env e3)\"")
os.environ["VFG_INSTANCE"] = "lexbuild"

import mc  # noqa: E402
import players as fake_players  # noqa: E402
from kit import world  # noqa: E402

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)

PLAYERS = fake_players.on()
PLAYERS.cmd = mc.rcon               # every command through mc, which holds the RCON lock the scripts hold
ARENA = PLAYERS.arena()
X, Y, Z = int(ARENA["x"]), int(ARENA["y"]), int(ARENA["z"])     # a player stands at X Y Z
F = Y - 1                                                       # the arena's grass floor
TAG = "lexb"                                                    # what this test summons
NETHER = "minecraft:the_nether"


def cmd(c):
    return mc.rcon(c)


def vfg(*args):
    """`vfg ...` against this server; its output. Fails the test on a non-zero exit."""
    p = subprocess.run(["vfg"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode:
        raise AssertionError("vfg %s exited %d:\n%s" % (" ".join(args), p.returncode, out))
    return out


def last_json(out):
    """The JSON line vfg printed last (a warning on stderr may come before it)."""
    for line in reversed(out.strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError("no JSON line from vfg:\n%s" % out)


def answer(p, copy, seconds=60):
    """The JSON line of a run started in the background, once it answers: ok, for the copy. A
    once action's run answers when the copy has ended (running false, its result), or when its
    wait is over (running true, "still running as <copy>")."""
    try:
        out = p.communicate(timeout=seconds)[0].decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise AssertionError("vfg action run for %s did not answer within %d s" % (copy, seconds))
    line = last_json(out)
    if p.returncode or not line["ok"] or line["copy"] != copy:
        raise AssertionError("vfg action run for %s exited %d:\n%s" % (copy, p.returncode, out))
    return line


def set_live(copy, *values):
    """vfg script set <copy> k=v …, each a live value: the edit lands in the running copy, no
    restart, and says so, `<copy>: k=v …, live`. That line."""
    said = vfg("script", "set", copy, *values).strip().splitlines()[-1]
    if not (said.startswith(copy + ": ") and said.endswith(", live")) or \
            any(v.split("=", 1)[0] + "=" not in said for v in values):
        raise AssertionError("vfg script set %s %s: %s" % (copy, " ".join(values), said))
    return said


def logs(copy, n=200):
    return vfg("script", "logs", copy, "-n", str(n))


def running(copy):
    """True while the box runs the copy: vfg script list --json --fresh asks it now."""
    return copy in last_json(vfg("script", "list", "--json", "--fresh"))["copies"]


def until(test, seconds=10.0, every=0.25):
    end = time.time() + seconds
    got = test()
    while not got and time.time() < end:
        time.sleep(every)
        got = test()
    return got


def is_(x, y, z, b, dim=None):
    """True if the block at x y z is b (an id or #tag), in a dimension."""
    return "passed" in cmd(world.run(dim, "execute if block %d %d %d %s" % (x, y, z, b))).lower()


def what(x, y, z, choices, dim=None):
    """Which of the choices the block at x y z is, or None: for a message that says what it was."""
    return next((c for c in choices if is_(x, y, z, c, dim)), None)


def count(blocks, b, dim=None):
    """How many of a list of (x, y, z) are b."""
    return sum(world.passes(["if block %d %d %d %s" % (x, y, z, b) for x, y, z in blocks], dim))


def fill(x1, y1, z1, x2, y2, z2, b, dim=None):
    return cmd(world.run(dim, "fill %d %d %d %d %d %d %s" % (x1, y1, z1, x2, y2, z2, b)))


def setblock(x, y, z, b, dim=None):
    return cmd(world.run(dim, "setblock %d %d %d %s" % (x, y, z, b)))


def health(name):
    """A summoned target's health (tag lexb_<name>), or a player's; None when it is gone."""
    who = name if name in ("Steve", "Alex") else "@e[tag=%s_%s,limit=1]" % (TAG, name)
    m = re.search(r"(-?[\d.]+)f\s*$", cmd("data get entity %s Health" % who).strip())
    return float(m.group(1)) if m else None


def target(name, x, y, z, mob="husk", hp=40):
    """A target that stands still: a husk (it does not burn at noon) with hp health."""
    attrs = 'attributes:[{id:"max_health",base:%d}],Health:%df,' % (hp, hp) if hp else ""
    cmd('summon %s %.1f %.1f %.1f {NoAI:1b,PersistenceRequired:1b,Silent:1b,%sTags:["%s","%s_%s"]}'
        % (mob, x, y, z, attrs, TAG, TAG, name))


def tamed_wolf(name, x, y, z, owner="Steve"):
    target(name, x, y, z, mob="wolf", hp=0)
    cmd("data modify entity @e[tag=%s_%s,limit=1] Owner set from entity %s UUID" % (TAG, name, owner))


def forceloads():
    return set(re.findall(r"\[(-?\d+), (-?\d+)\]", cmd("forceload query")))


def tagged(copy):
    """How many entities are tagged vfg_<copy>."""
    m = re.search(r"Count: (\d+)", cmd("execute if entity @e[tag=vfg_%s]" % copy))
    return int(m.group(1)) if m else 0


def respawn(p):
    """A player's respawn point as (x, y, z), or None."""
    m = re.search(r"pos: \[I; (-?\d+), (-?\d+), (-?\d+)\]", cmd("data get entity %s respawn" % p))
    return tuple(int(v) for v in m.groups()) if m else None


def sign_text(x, y, z):
    return cmd("data get block %d %d %d front_text.messages" % (x, y, z))


def arena_back():
    """The arena as the template has it: grass on dirt, air for 30 blocks over it."""
    fill(X - 16, F - 3, Z - 16, X + 15, F - 1, Z + 15, "dirt")
    fill(X - 16, F, Z - 16, X + 15, F, Z + 15, "grass_block")
    for y in range(F + 1, F + 31, 10):
        fill(X - 16, y, Z - 16, X + 15, y + 9, Z + 15, "air")


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            PLAYERS.player("Steve", "stop")
        except fake_players.NoCarpet:
            raise unittest.SkipTest("no Carpet on this server: integration/realmc/realmc.py carpet, then up --fresh")
        except fake_players.Refused:
            pass
        cls.loaded = forceloads()

    def setUp(self):
        self.started = []
        self.runs = {}                                          # copy: its run, started in the background
        cmd("kill @e[tag=%s]" % TAG)
        cmd("time set noon")
        cmd("gamerule fire_spread_radius_around_player 0")     # fire stays where a script put it

    def tearDown(self):
        for copy in self.started:
            subprocess.run(["vfg", "script", "stop", copy], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for p in self.runs.values():                            # a run the test never read
            if p.poll() is None:
                p.kill()
            p.communicate()
        for p in PLAYERS.online():
            try:
                PLAYERS.stop(p)
                PLAYERS.sneak(p, False)
            except fake_players.Refused:
                pass
        cmd("kill @e[tag=%s]" % TAG)
        cmd("kill @e[type=item,x=%d,y=%d,z=%d,distance=..120]" % (X, Y, Z))
        cmd("kill @e[type=tnt,x=%d,y=%d,z=%d,distance=..120]" % (X, Y, Z))
        cmd("effect clear @a")

    # -- helpers
    def player(self, name, at, yaw=0.0, pitch=0.0, gamemode="survival"):
        """A fake player at a place, facing (yaw, pitch), full health, nothing in hand."""
        PLAYERS.spawn(name, at, facing=(yaw, pitch), gamemode=gamemode)
        PLAYERS.look(name, yaw, pitch)
        PLAYERS.clear(name)
        cmd("effect give %s instant_health 1 10 true" % name)
        cmd("effect clear %s" % name)
        return name

    def run_(self, action, copy, *values):
        """vfg action run <action> as=<copy> …: its JSON line, ok, for that copy. An
        until-stopped action answers once its copy is ready, a once action when its copy has
        ended (or its wait is over)."""
        self.started.append(copy)
        line = last_json(vfg("action", "run", action, "as=" + copy, *values))
        self.assertTrue(line["ok"] and line["copy"] == copy, line)
        return line

    def start(self, action, copy, *values):
        """vfg action run in the background, for a once action the test changes while its run
        waits for it to end; ends(copy) reads its answer. Back once the copy runs."""
        self.started.append(copy)
        p = self.runs[copy] = subprocess.Popen(["vfg", "action", "run", action, "as=" + copy] + list(values),
                                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertTrue(until(lambda: p.poll() is not None or running(copy), 30, 0.5), "%s did not start" % copy)
        if p.poll() is not None:
            answer(p, copy)                                     # ended already: ok, or why not
        return p

    def ends(self, copy, seconds=60):
        """Wait until a copy that ends by itself has ended (its run in the background answers
        first, ok); its log."""
        if copy in self.runs:
            answer(self.runs.pop(copy), copy, seconds)
        self.assertTrue(until(lambda: not running(copy), seconds, 0.5), "%s did not end:\n%s"
                        % (copy, logs(copy)))
        self.started.remove(copy)
        out = logs(copy)
        self.assertNotIn("Traceback", out, out)
        self.assertNotIn("error", out.lower(), out)
        self.assertEqual(tagged(copy), 0, "entities tagged vfg_%s left" % copy)
        self.assertEqual(forceloads() - self.loaded, set(), "forceloads left by %s" % copy)
        return out

    def stop_clean(self, copy):
        """vfg script stop, then nothing tagged vfg_<copy> is left, no forceload of its own."""
        self.assertIn("stopped " + copy, vfg("script", "stop", copy))
        self.started.remove(copy)
        if copy in self.runs:                                   # its run answers now the copy has ended
            answer(self.runs.pop(copy), copy)
        out = logs(copy)
        self.assertNotIn("Traceback", out, out)
        self.assertTrue(until(lambda: tagged(copy) == 0, 5), "entities tagged vfg_%s left" % copy)
        self.assertEqual(forceloads() - self.loaded, set(), "forceloads left by %s" % copy)
        return out


# ------------------------------------------------------------------------------- the world

class WorldReplace(Build):
    def test_obsidian_goes_round_a_player_full_height_and_not_past_the_radius(self):
        steve = self.player("Steve", (X + 0.5, Y, Z + 0.5))
        inside = [(X + 5, F, Z + 5), (X - 9, F + 20, Z + 9), (X + 9, F - 25, Z - 9), (X - 3, F + 60, Z)]
        outside = [(X + 13, F, Z), (X, F, Z - 13)]
        for x, y, z in inside + outside:
            setblock(x, y, z, "obsidian")
        setblock(X + 2, F, Z + 2, "crying_obsidian")          # not obsidian: stays
        try:
            self.run_("world_replace", "wr", "player=Steve", "radius=10", "dims=overworld")
            out = self.ends("wr")
            print("\n  " + "\n  ".join(l for l in out.splitlines() if "round" in l or "done" in l))
            self.assertEqual(count(inside, "air"), len(inside), [what(*b, ["air", "obsidian"]) for b in inside])
            self.assertEqual(count(outside, "obsidian"), len(outside))
            self.assertTrue(is_(X + 2, F, Z + 2, "crying_obsidian"))
            self.assertIn("4 blocks changed", out)
            # another rule, the run's end stone ask, kept to a band round his feet
            fill(X - 4, F, Z - 4, X + 4, F, Z + 4, "end_stone")
            setblock(X, F - 8, Z, "end_stone")                 # under the band: stays
            self.run_("world_replace", "wr2", "player=Steve", "radius=4", "height=3", "replace_rules=end_stone > bedrock")
            self.ends("wr2")
            ring = [(X + dx, F, Z + dz) for dx in range(-4, 5) for dz in range(-4, 5)]
            self.assertEqual(count(ring, "bedrock"), 81)
            self.assertTrue(is_(X, F - 8, Z, "end_stone"))
            # dims leaves the overworld out: nothing is done
            setblock(X + 1, F, Z + 1, "obsidian")
            self.run_("world_replace", "wr3", "player=Steve", "radius=4", "dims=the_nether")
            self.assertIn("skipped Steve", self.ends("wr3"))
            self.assertTrue(is_(X + 1, F, Z + 1, "obsidian"))
        finally:
            for x, y, z in inside + outside:
                setblock(x, y, z, "dirt" if y < F else "air" if y > F else "grass_block")
            setblock(X, F - 8, Z, "dirt")
            arena_back()

    def test_in_the_nether_it_changes_the_nether_not_the_overworld(self):
        """Steve in the Nether, in spectator so the netherrack round him cannot hurt him: the
        box round him changes there, and the overworld at the same place is left alone."""
        PLAYERS.spawn("Steve", (X + 0.5, Y, Z + 0.5), gamemode="spectator")
        cmd("execute in %s run tp Steve 8.5 70 8.5" % NETHER)
        self.assertTrue(until(lambda: NETHER in cmd("data get entity Steve Dimension"), 10), "Steve is not in the Nether")
        self.assertTrue(until(lambda: "passed" in cmd("execute in %s if loaded 8 70 8" % NETHER).lower(), 20),
                        "the Nether round him did not load")
        fill(5, 67, 5, 11, 73, 11, "netherrack", NETHER)
        fill(5, 67, 5, 11, 73, 11, "netherrack")                # the overworld, the same box
        try:
            self.run_("world_replace", "wrn", "player=Steve", "radius=3", "height=3", "replace_rules=netherrack > glowstone")
            self.ends("wrn")
            box = [(x, y, z) for x in range(5, 12) for y in range(67, 74) for z in range(5, 12)]
            self.assertEqual(count(box, "glowstone", NETHER), 7 * 7 * 7)
            self.assertEqual(count(box, "netherrack"), 7 * 7 * 7)
        finally:
            fill(5, 67, 5, 11, 73, 11, "stone")
            fill(5, 67, 5, 11, 73, 11, "air", NETHER)
            PLAYERS.spawn("Steve", (X + 0.5, Y, Z + 0.5))
            cmd("execute in minecraft:overworld run tp Steve %.1f %d %.1f" % (X + 0.5, Y, Z + 0.5))
            cmd("gamemode survival Steve")
            arena_back()


class ChunkChanger(Build):
    def test_snow_turns_to_random_blocks_chunk_by_chunk_as_he_walks_and_a_new_rule_takes(self):
        """Snow on the arena floor in chunks (0, 0) and (-1, 0). Steve walks west from chunk
        (0, 0) into (-1, 0): the snow there turns to the run's random blocks, grass stays. Then
        the rules change live to grass > moss, he walks north into (-1, -1), and the grass
        there turns to moss while the snow-free grass behind him stays."""
        steve = self.player("Steve", (X + 3.5, Y, Z + 8.5), yaw=90)            # facing west
        fill(X + 2, Y, Z + 2, X + 6, Y, Z + 6, "snow")                            # chunk 0 0: layers
        fill(X - 12, F, Z + 2, X - 8, F, Z + 6, "snow_block")                     # chunk -1 0: blocks
        snow0 = [(x, Y, z) for x in range(X + 2, X + 7) for z in range(Z + 2, Z + 7)]
        snow1 = [(x, F, z) for x in range(X - 12, X - 7) for z in range(Z + 2, Z + 7)]
        try:
            self.run_("chunk_changer", "cc", "player=Steve", "around=0", "enters_below=1", "enters_above=1",
                      "replace_cell=2")
            self.assertTrue(until(lambda: count(snow0, "#minecraft:snow") == 0, 20), "the snow by him stayed")
            PLAYERS.move(steve, "forward")
            time.sleep(3.0)
            PLAYERS.move(steve, None)
            x, _, z = PLAYERS.pos(steve)
            self.assertLess(x, X - 1, "Steve did not walk into chunk -1")
            self.assertTrue(until(lambda: count(snow1, "#minecraft:snow") == 0, 20), "the snow in chunk -1 0 stayed")
            kinds = set(what(bx, by, bz, ["white_wool", "red_wool", "stone", "glass", "oak_planks", "quartz_block",
                                         "terracotta", "deepslate", "andesite", "granite", "diorite"])
                        for bx, by, bz in snow0 + snow1)
            print("\n  some of what the snow became:", sorted(k for k in kinds if k))
            self.assertTrue(is_(X - 14, F, Z + 4, "grass_block"), "grass changed under the snow rule")
            # a live value: the rules
            set_live("cc", "replace_rules=grass_block > moss_block")
            time.sleep(1.0)
            PLAYERS.look(steve, 180, 0)                                             # facing north
            PLAYERS.move(steve, "forward")
            time.sleep(3.0)
            PLAYERS.move(steve, None)
            x, _, z = PLAYERS.pos(steve)
            self.assertLess(z, Z - 1, "Steve did not walk into chunk -1 -1")
            self.assertTrue(until(lambda: is_(X - 10, F, Z - 8, "moss_block"), 20), "no moss in chunk -1 -1")
            self.assertTrue(is_(X - 14, F, Z + 4, "grass_block"), "a chunk was done twice")
            self.assertTrue(is_(X + 10, F, Z - 8, "grass_block"), "chunk 0 -1 changed, he never went in")
            out = self.stop_clean("cc")
            self.assertIn("live: replace_rules", out)
            print("  " + "\n  ".join(l for l in out.splitlines() if "chunk " in l))
        finally:
            arena_back()


class IceWalk(Build):
    def test_a_box_of_ice_round_each_step_buried_too_never_air_bedrock_and_a_flash_on_landing(self):
        steve = self.player("Steve", (X - 10.5, Y, Z + 0.5), yaw=-90)          # facing east
        setblock(X - 6, F - 1, Z, "bedrock")                                    # under his path: stays
        setblock(X - 4, Y + 2, Z + 1, "oak_log")                                # over his head: freezes
        setblock(X - 2, F, Z + 2, "water")                                      # beside his path: freezes
        setblock(X - 2, Y, Z - 2, "short_grass")
        try:
            self.run_("ice_walk", "iw", "player=Steve", "under_radius=2", "under_depth=3", "under_above=4",
                      "walk_every=1")
            time.sleep(1.0)
            PLAYERS.move(steve, "forward")
            time.sleep(2.2)
            PLAYERS.move(steve, None)
            time.sleep(1.0)
            x = int(PLAYERS.pos(steve)[0] // 1)
            self.assertGreater(x, X - 4)
            path = [(xx, F, Z) for xx in range(X - 10, x + 1)]
            self.assertEqual(count(path, "packed_ice"), len(path), "the floor under his path")
            under = [(xx, F - 2, z) for xx in range(X - 9, x) for z in (Z - 2, Z + 2)]
            self.assertEqual(count(under, "packed_ice"), len(under), "the buried dirt two down and two out")
            self.assertTrue(is_(X - 6, F - 1, Z, "bedrock"), "bedrock froze")
            self.assertTrue(is_(X - 4, Y + 2, Z + 1, "packed_ice"), "the log over his head")
            self.assertTrue(is_(X - 2, F, Z + 2, "packed_ice"), "the water")
            self.assertTrue(is_(X - 2, Y, Z - 2, "packed_ice"), "the grass")
            self.assertTrue(is_(X - 6, Y, Z, "air") and is_(X - 6, Y + 1, Z + 1, "air"), "air was filled")
            self.assertTrue(is_(X - 6, F, Z + 3, "grass_block"), "radius 2 reached 3")
            self.assertFalse(is_(X - 6, F - 3, Z, "packed_ice"), "depth 3 reached 4 down")
            # a live value: a narrower path from here on
            set_live("iw", "under_radius=0")
            time.sleep(1.0)
            PLAYERS.move(steve, "forward")
            time.sleep(1.5)
            PLAYERS.move(steve, None)
            time.sleep(1.0)
            x2 = int(PLAYERS.pos(steve)[0] // 1)
            self.assertGreater(x2, x + 3)
            self.assertTrue(is_(x2 - 1, F, Z, "packed_ice"))
            self.assertTrue(is_(x2 - 1, F, Z + 1, "grass_block"), "under_radius=0 still froze a block out")
            # a fall of 5, off a pillar he stood on: a flash where he lands
            fill(x2 + 3, Y, Z, x2 + 3, Y + 4, Z, "stone")
            cmd("tp Steve %d.5 %d %d.5" % (x2 + 3, Y + 5, Z))
            time.sleep(1.0)
            self.assertNotIn("landing flash", logs("iw"))
            cmd("tp Steve %d.5 %d %d.5" % (x2 + 5, Y + 5, Z))
            self.assertTrue(until(lambda: "landing flash for Steve at %d %d %d" % (x2 + 5, Y, Z) in logs("iw"), 10),
                            logs("iw"))
            out = self.stop_clean("iw")
            self.assertIn("live: under_radius=0", out)
        finally:
            arena_back()
            fill(X - 16, F - 4, Z - 16, X + 15, F - 4, Z + 15, "stone")


# ------------------------------------------------------------------------------- the sky

class Meteor(Build):
    def test_it_falls_on_him_a_blast_carves_a_crater_spares_the_wolf_and_a_live_layout_takes(self):
        """Steve, with Resistance V, is the target, sky_radius 0, so it lands on the floor at
        his feet. While it falls the layout changes live from one blast to a plus of five."""
        steve = self.player("Steve", (X + 0.5, Y, Z + 0.5))
        cmd("effect give Steve resistance 30 4 true")
        tamed_wolf("wolf", X + 2.5, Y, Z + 0.5)
        target("near", X + 0.5, Y, Z + 3.5)
        self.start("meteor", "mt", "player=Steve", "sky_radius=0", "meteor_size=3", "meteor_height=30",
                   "meteor_seconds=5", "explosion_power=3", "explosion_keep=0", "explosion_layout=one")
        try:
            self.assertTrue(until(lambda: tagged("mt") > 0, 10), "no meteor in the sky")
            shows = cmd("data get entity @e[tag=vfg_mt,type=block_display,limit=1] block_state")
            self.assertRegex(shows, r'"minecraft:(magma_block|blackstone|obsidian)"', "the meteor is not seen: " + shows)
            time.sleep(1.0)
            set_live("mt", "explosion_layout=plus", "explosion_spread=6")
            out = self.ends("mt", 40)
            print("\n  " + "\n  ".join(l for l in out.splitlines() if "impact" in l or "live" in l))
            self.assertIn("5 blasts", out)
            self.assertTrue(is_(X, F, Z, "air"), "no crater under him")
            self.assertTrue(is_(X + 6, F, Z, "air") and is_(X - 6, F, Z, "air"), "no blasts of the plus")
            self.assertIsNotNone(health("wolf"), "the tamed wolf died")
            self.assertTrue(health("near") is None or health("near") < 40, "the husk by him was not hurt")
            self.assertGreater(health("Steve"), 0)
        finally:
            arena_back()


class LavaRain(Build):
    def test_drops_over_him_change_nothing_and_a_live_rate_takes(self):
        steve = self.player("Steve", (X + 0.5, Y, Z + 0.5))
        for name in ("falling_lava", "dripping_lava", "lava", "flame"):          # 26.3 takes each
            reply = cmd("particle %s %d %d %d 4 1 4 0 25 force" % (name, X, Y + 15, Z))
            self.assertNotIn("<--[HERE]", reply, reply)
        self.run_("lava_rain", "lr", "player=Steve")
        time.sleep(4.0)
        floor = [(x, F + 1, z) for x in range(X - 14, X + 15, 2) for z in range(Z - 14, Z + 15, 2)]
        self.assertEqual(count(floor, "air"), len(floor), "it put blocks down")
        self.assertEqual(count([(X, Y, Z)], "fire"), 0)
        self.assertEqual(health("Steve"), 20.0)
        set_live("lr", "every=2", "drops=5")
        self.assertTrue(until(lambda: "live: every=2" in logs("lr"), 5), logs("lr"))
        self.assertTrue(running("lr"))
        out = self.stop_clean("lr")
        self.assertNotIn("refused", out)


class SkyPlatform(Build):
    def test_obsidian_in_the_sky_with_a_rim_and_lights_he_is_on_it_and_spawns_there(self):
        steve = self.player("Steve", (X + 2.5, Y, Z + 3.5))
        try:
            self.run_("sky_platform", "sp", "player=Steve", "platform_y=120", "platform_size=5")
            out = self.ends("sp")
            self.assertIn("sky platform for Steve at %d 121 %d" % (X + 2, Z + 3), out)
            floor = [(x, 120, z) for x in range(X, X + 5) for z in range(Z + 1, Z + 6)]
            self.assertEqual(count(floor, "obsidian"), 25)
            self.assertTrue(is_(X, 121, Z + 2, "obsidian") and is_(X + 2, 121, Z + 1, "obsidian"), "no rim")
            self.assertTrue(is_(X, 121, Z + 1, "glowstone") and is_(X + 4, 121, Z + 5, "glowstone"), "no lights")
            self.assertTrue(is_(X + 2, 121, Z + 3, "air"), "the middle is not open")
            self.assertTrue(until(lambda: abs(PLAYERS.pos(steve)[1] - 121) < 0.1, 5), PLAYERS.pos(steve))
            self.assertEqual(respawn("Steve"), (X + 2, 121, Z + 3))
        finally:
            fill(X - 1, 119, Z, X + 5, 122, Z + 6, "air")
            PLAYERS.spawn("Steve", (X + 0.5, Y, Z + 0.5))
            cmd("spawnpoint Steve %d %d %d" % (X, Y, Z))


# ------------------------------------------------------------------------------- the builds

class Castle(Build):
    def test_an_obsidian_castle_with_his_name_on_it_gate_facing_him(self):
        """Steve at the north end of the arena facing south: an obsidian castle 13 wide from 6
        blocks in front of him (z -6..6), crying obsidian towers, its gate towards him and a
        sign over it with his name."""
        steve = self.player("Steve", (X + 0.5, Y, Z - 11.5), yaw=0)            # facing south
        try:
            self.run_("castle", "cs", "player=Steve", "castle_size=13", "castle_height=6",
                      "shape_blocks=obsidian", "castle_tower_blocks=crying_obsidian",
                      "castle_sign={player}'s Castle,better than,the stone one")
            out = self.ends("cs", 90)
            print("\n  " + out.strip().splitlines()[-1])
            z0, z1 = Z - 6, Z + 6                                                # front and back walls
            for x, y, z in ((X - 3, Y + 2, z0), (X + 3, Y + 4, z1), (X - 6, Y + 1, Z), (X + 6, Y + 5, Z + 2)):
                self.assertTrue(is_(x, y, z, "obsidian"), ("wall", x, y, z, what(x, y, z, ["air", "crying_obsidian"])))
            self.assertTrue(is_(X - 2, Y + 6, z0, "obsidian") and is_(X - 3, Y + 6, z0, "air"), "no battlements")
            self.assertTrue(is_(X, Y, z0, "air") and is_(X + 2, Y + 2, z0, "air"), "no gate")
            self.assertTrue(is_(X, Y + 3, z0, "iron_bars"), "no bars over the gate")
            self.assertTrue(is_(X - 6, Y + 3, z0 - 2, "crying_obsidian"), "no tower on the corner")
            self.assertTrue(is_(X - 6, Y + 3, z0, "air"), "the tower is not hollow")
            self.assertTrue(is_(X + 6, Y + 11, z1, "crying_obsidian"), "no tower roof")
            self.assertTrue(is_(X, Y, Z, "air") and is_(X, F, Z, "obsidian"), "the yard")
            self.assertTrue(is_(X + 3, Y + 1, Z + 4, "obsidian") and is_(X, Y, Z + 2, "air"), "the keep and its door")
            text = sign_text(X, Y + 4, z0 - 1)
            print("  sign:", text)
            self.assertIn("Steve's Castle", text)
            self.assertIn("better than", text)
            self.assertTrue(is_(X, Y + 4, z0 - 1, "oak_wall_sign[facing=north]"), "the sign does not face him")
        finally:
            fill(X - 12, F, Z - 10, X + 12, F, Z + 10, "grass_block")
            for y in range(Y, Y + 20, 5):
                fill(X - 12, y, Z - 10, X + 12, y + 4, Z + 10, "air")
            arena_back()

    def test_an_ice_castle_with_stairs_down_carved_through_the_ground(self):
        """The run's ice castle, small: packed ice walls, blue ice towers (ice_castle's own
        blocks, fixed), a staircase 8 deep from the yard down and ahead, three blocks of
        headroom over each step."""
        steve = self.player("Steve", (X + 0.5, Y, Z - 13.5), yaw=0)            # facing south
        try:
            self.run_("ice_castle", "ic", "player=Steve", "castle_size=21", "castle_height=8", "castle_ahead=2",
                      "castle_stairs=8")
            self.ends("ic", 90)
            z0 = Z - 12                                                          # its front wall
            self.assertTrue(is_(X + 4, Y + 3, z0, "packed_ice") and is_(X - 10, Y + 3, z0 - 2, "blue_ice"))
            for k in (0, 3, 7):                                                  # step k: k + 1 down
                x, y, z = X, F - k, z0 + 2 + k
                self.assertTrue(is_(x, y, z, "packed_ice"), ("step", k, what(x, y, z, ["air", "dirt", "stone"])))
                self.assertTrue(is_(x, y + 1, z, "air") and is_(x, y + 3, z, "air"), ("headroom", k))
        finally:
            for y in range(F - 10, Y + 20, 5):
                fill(X - 14, y, Z - 14, X + 14, y + 4, Z + 14, "air")
            fill(X - 14, F - 10, Z - 14, X + 14, F - 4, Z + 14, "stone")
            arena_back()


class SniperNest(Build):
    def test_a_tower_with_a_ladder_a_lookout_his_name_and_his_spawn_on_top(self):
        steve = self.player("Steve", (X + 0.5, Y, Z - 8.5), yaw=0)             # facing south
        try:
            self.run_("sniper_nest", "sn", "player=Steve", "nest_height=8", "nest_ahead=3")
            out = self.ends("sn")
            c = Z - 9 + 3 + 2                                                    # the tower's middle, z
            self.assertIn("lookout at %d %d %d" % (X, Y + 8, c), out)
            self.assertTrue(is_(X - 2, Y + 3, c, "stone_bricks") and is_(X, Y + 3, c, "air"), "the tower")
            self.assertTrue(is_(X, Y, c - 2, "air") and is_(X, Y + 1, c - 2, "air"), "no door facing him")
            self.assertTrue(is_(X, Y + 2, c + 1, "ladder[facing=north]") and is_(X, Y + 7, c + 1, "ladder"), "no ladder")
            self.assertTrue(is_(X - 1, Y + 7, c, "stone_bricks"), "no floor up top")
            self.assertTrue(is_(X + 2, Y + 8, c, "stone_bricks") and is_(X, Y + 9, c + 2, "air"), "no walls or slits")
            self.assertTrue(is_(X + 3, Y + 11, c + 3, "stone_bricks"), "no roof")
            self.assertTrue(is_(X + 1, Y + 8, c - 1, "lantern"))          # its right is west, -x
            self.assertTrue(is_(X, Y - 5, c, "stone_bricks"), "no footing")
            text = sign_text(X, Y + 2, c - 3)
            self.assertIn('"Steve"', text)
            self.assertIn("Sniper Nest", text)
            self.assertEqual(respawn("Steve"), (X, Y + 8, c))
        finally:
            for y in range(F - 10, Y + 15, 5):
                fill(X - 4, y, Z - 9, X + 4, y + 4, Z + 2, "air")
            fill(X - 4, F - 10, Z - 9, X + 4, F - 4, Z + 2, "stone")
            cmd("spawnpoint Steve %d %d %d" % (X, Y, Z))
            arena_back()


class Nuke(Build):
    def test_a_ten_second_countdown_kills_in_the_radius_carves_a_crater_and_a_live_radius_takes(self):
        """The nuke at nuke_at, a place given as x y z, 8 blocks from Steve: radius 5 at the
        start, set live to 3 during the countdown. A husk 2 from the middle dies, one 7 off lives."""
        steve = self.player("Steve", (X - 8.5, Y, Z + 0.5))
        target("in", X + 2.5, Y, Z + 0.5)
        target("out", X + 7.5, Y, Z + 0.5)
        try:
            self.start("nuke", "nk", "player=Steve", "nuke_at=%d %d %d" % (X, F, Z), "countdown_seconds=10",
                       "crater_radius=5")
            time.sleep(2.0)
            set_live("nk", "crater_radius=3")
            self.assertTrue(until(lambda: health("in") is None, 20), "the husk in the radius lived")
            out = self.ends("nk", 60)
            print("\n  " + "\n  ".join(out.strip().splitlines()[-4:]))
            self.assertIsNotNone(health("out"), "the husk out of the radius died")
            self.assertGreater(health("Steve") or 0, 0)
            self.assertTrue(is_(X, F, Z, "air") and is_(X, F - 3, Z, "air"), "no crater")
            self.assertTrue(is_(X + 4, F, Z, "grass_block") and not is_(X, F - 4, Z, "air"),
                            "the crater is bigger than radius 3")
        finally:
            fill(X - 5, F - 5, Z - 5, X + 5, F - 4, Z + 5, "stone")
            arena_back()

    def test_a_stop_before_zero_calls_it_off(self):
        steve = self.player("Steve", (X + 0.5, Y, Z + 0.5))
        self.start("nuke", "nk2", "player=Steve", "countdown_seconds=20", "crater_radius=4")
        time.sleep(3.0)
        out = self.stop_clean("nk2")
        self.assertIn("stopping", out)
        self.assertTrue(is_(X, F, Z, "grass_block"))
        self.assertEqual(health("Steve"), 20.0)


class Hellscape(Build):
    def test_the_land_round_him_turns_to_the_nether(self):
        steve = self.player("Steve", (X + 0.5, Y, Z + 0.5))
        setblock(X + 3, F, Z, "water")
        setblock(X - 3, Y, Z, "oak_log")
        setblock(X - 3, Y + 1, Z, "oak_leaves[persistent=true]")
        setblock(X, Y, Z + 3, "short_grass")
        setblock(X + 3, F, Z + 3, "sand")
        setblock(X - 3, F, Z - 3, "gravel")
        setblock(X + 2, Y, Z - 3, "snow")
        setblock(X - 2, F, Z + 2, "ice")
        setblock(X + 10, F, Z, "sand")                         # outside the radius: stays
        try:
            self.run_("hellscape", "hs", "player=Steve", "radius=6", "height=3")
            out = self.ends("hs", 60)
            print("\n  " + out.strip().splitlines()[-1])
            checks = [((X + 3, F, Z), "lava"), ((X - 3, Y, Z), "basalt"), ((X - 3, Y + 1, Z), "air"),
                      ((X, Y, Z + 3), "fire"), ((X + 3, F, Z + 3), "soul_sand"), ((X - 3, F, Z - 3), "soul_soil"),
                      ((X + 2, Y, Z - 3), "air"), ((X - 2, F, Z + 2), "lava"), ((X + 10, F, Z), "sand"),
                      ((X + 10, F, Z + 1), "grass_block")]
            for (x, y, z), b in checks:
                self.assertTrue(is_(x, y, z, b), (b, x, y, z, what(x, y, z, ["air", "fire", "netherrack", "lava",
                                                                              "magma_block", "blackstone"])))
            ground = [(x, F - d, z) for x in range(X - 6, X + 7) for z in range(Z - 6, Z + 7) for d in (0, 1)]
            nether = sum(count(ground, b) for b in ("netherrack", "magma_block", "blackstone", "lava", "soul_sand",
                                                     "soul_soil"))
            self.assertEqual(nether, len(ground), "some ground in the radius is not the Nether's")
            print("  magma and blackstone in the ground:", count(ground, "magma_block"), count(ground, "blackstone"))
        finally:
            cmd("fill %d %d %d %d %d %d air replace fire" % (X - 8, Y, Z - 8, X + 8, Y + 2, Z + 8))
            arena_back()


if __name__ == "__main__":
    unittest.main(verbosity=2)
