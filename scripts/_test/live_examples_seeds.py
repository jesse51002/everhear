"""The 16 seed scripts, and the run's mountains and mountain_trees, on a real Minecraft 26.3
server, run the way a player's ask runs them.

    integration/realmc/up es --fresh && eval "$(integration/realmc/env es)"
    uv run --python 3.9 python scripts/_test/live_examples_seeds.py [-k zeus]

Each script is started with `vfg script run` (deployed to the server's stand-in and run there
on Python 3.9, as on the box) twice: once with its defaults, and once as a second instance
(as=<name>-b) with values that are not; Carpet's fake player Steve does what sets it off (stands
still, throws a trident, shoots, dies, mines a block); the world is then asked over RCON whether
it did what the seed did; one live value is changed with `vfg script set` and the change is
seen; and after `vfg script stop`, or after the script ended by itself, nothing tagged
vfg_<instance> is left: no entity, no objective of its own, no bossbar, no forceload. It runs
only against a realmc test server with Carpet, never the box. Explosions and lightning go off
at sites of their own away from the arena; `up --fresh` puts the world back after.
"""
import math
import os
import re
import signal
import subprocess
import sys
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "scripts", "_lib"))
sys.path.insert(0, os.path.join(REPO, "integration", "realmc"))

PROPS = os.environ.get("VFG_MC_PROPERTIES", "")
if not os.environ.get("REALMC_NAME") or "realmc" not in os.path.realpath(PROPS):
    sys.exit("live_examples_seeds: point it at a realmc server first: eval \"$(integration/realmc/env es)\"")
os.environ["VFG_INSTANCE"] = "lxseeds"          # this process's own tag

import mc  # noqa: E402
import players as fake_players  # noqa: E402

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)

PLAYERS = fake_players.on()
ARENA = PLAYERS.arena()
X, Y, Z = ARENA["x"], ARENA["y"], ARENA["z"]                   # where a player stands at its centre
if "Unknown or incomplete" in PLAYERS.cmd("carpet"):
    sys.exit("live_examples_seeds: the server has no Carpet: integration/realmc/realmc.py carpet, then up --fresh")
# Where the loud ones go off, each on its own ground (spreadplayers finds the surface).
SITES = {"anti_camp": (-140, -150), "boom_bow": (150, 150), "tnt_rain": (150, -150), "zeus": (60, -140)}


def rc(cmd):
    return mc.rcon(cmd)


def vfg(*args, timeout=60):
    """vfg as a player's ask runs it; its output. Fails the test when vfg fails."""
    r = subprocess.run(["vfg"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    out = r.stdout.decode()
    if r.returncode != 0:
        raise AssertionError("vfg %s: exit %d\n%s" % (" ".join(args), r.returncode, out))
    return out


def until(test, seconds=10.0, every=0.2):
    end = time.time() + seconds
    got = test()
    while not got and time.time() < end:
        time.sleep(every)
        got = test()
    return got


def most(test, seconds, every=0.05):
    """The highest a count reaches within some seconds."""
    end, top = time.time() + seconds, 0
    while time.time() < end:
        top = max(top, test())
        time.sleep(every)
    return top


def number(reply):
    m = re.search(r"(-?\d+(?:\.\d+)?)[a-z]?\s*$", reply.strip())
    return float(m.group(1)) if m else None


def count(selector):
    m = re.search(r"Count: (\d+)", rc("execute if entity %s" % selector))
    return int(m.group(1)) if m else 0


def passes(cmd):
    return "test passed" in rc(cmd).lower()


def items(what, who="Steve"):
    """How many of an item (with a predicate) a player has."""
    m = re.search(r"Count: (\d+)", rc("execute if items entity %s container.* %s" % (who, what)))
    return int(m.group(1)) if m else 0


def epos(selector):
    """(x, y, z) of an entity's feet, or None."""
    v = re.findall(r"(-?[\d.]+(?:E-?\d+)?)d", rc("data get entity %s Pos" % selector))
    return tuple(float(c) for c in v[:3]) if len(v) >= 3 else None


def attr(selector, name):
    return number(rc("attribute %s minecraft:%s base get" % (selector, name)))


def running(instance):
    return re.search(r"^%s running" % re.escape(instance), vfg("script", "list"), re.M) is not None


def logs(instance):
    """The instance's log since it was last started."""
    out = vfg("script", "logs", instance, "-n", "400")
    return out.rsplit("\n--- ", 1)[-1]


def logged(instance, pattern, seconds=10.0):
    """The last match of a pattern in the instance's log, waiting for it."""
    found = []

    def look():
        found[:] = re.findall(pattern, logs(instance))
        return bool(found)
    until(look, seconds, 0.5)
    return found[-1] if found else None


FORCED = rc("forceload query")


def objectives():
    return re.findall(r"\[([^\]]+)\]", rc("scoreboard objectives list"))


def left(instance, own_tags=(), shared=()):
    """What an instance left behind: entities tagged vfg_<instance> (with the kit's "vfg" tag,
    or one of its own tags on the world's mobs), its objectives (and the seed's shared ones),
    and forceloads that were not there before."""
    out = []
    tag = "vfg_" + instance
    for t in (tag,) + tuple("%s_%s" % (tag, s) for s in own_tags):
        if count("@e[tag=%s]" % t):
            out.append("entity tagged " + t)
    for m in re.finditer(r"\[([^\]]*)\]", rc("execute as @e[tag=vfg] run data get entity @s Tags")):
        tags = re.findall(r'"([^"]+)"', m.group(1))
        if any(t == tag or t.startswith(tag + "_") for t in tags):
            out.append("entity tagged " + ",".join(tags))
    for o in objectives():
        if o == tag or o.startswith(tag + "_") or o in shared:
            out.append("objective " + o)
    if rc("forceload query") != FORCED:
        out.append("forceloads: " + rc("forceload query")[:200])
    return out


def steve(at=None, safe=True):
    """Steve on the arena (or at a site, on its surface), in survival, standing still, with
    Resistance V unless he is to be hurt."""
    rc("kill @e[type=vex]")
    if "Steve" not in PLAYERS.online():
        PLAYERS.spawn("Steve")
    PLAYERS.stop("Steve")
    PLAYERS.sneak("Steve", False)
    if at:
        rc("spreadplayers %d %d 0 1 false Steve" % at)
    else:
        PLAYERS.tp("Steve", X, Y, Z, 0, 0)
    rc("effect clear Steve")
    if safe:
        rc("effect give Steve resistance 900 4 true")
        rc("effect give Steve fire_resistance 900 0 true")
    time.sleep(0.5)
    return PLAYERS.pos("Steve")


def calm():
    """Noon and clear again, and no monster left round the arena (the Demon Lord makes it
    midnight and thundering)."""
    rc("time set noon")
    rc("weather clear")
    for mob in ("zombie", "husk", "skeleton", "stray", "spider", "cave_spider", "creeper", "witch", "phantom",
                "ravager", "evoker", "vindicator", "blaze"):
        rc("kill @e[type=%s,x=%d,y=%d,z=%d,distance=..120]" % (mob, X, Y, Z))
    # The Ravager Warlord's evoker summons vexes, which carry no tag: they outlive the stop.
    rc("kill @e[type=vex]")


def die(at, back):
    """Steve dies for real at a place (Carpet then takes him off), and comes back elsewhere."""
    PLAYERS.tp("Steve", at[0], at[1], at[2])
    time.sleep(0.5)
    rc("damage Steve 1000 minecraft:out_of_world")
    until(lambda: "Steve" not in PLAYERS.online(), 5)
    PLAYERS.spawn("Steve", at=back)


class Seeds(unittest.TestCase):
    def setUp(self):
        self.started = []

    def tearDown(self):
        for inst in self.started:
            try:
                if running(inst):
                    vfg("script", "stop", inst)
            except AssertionError:
                pass

    def run_(self, name, *values, instance=None, ends=False):
        """Start an instance; with ends, wait (up to 60 s) for it to end by itself."""
        args = [name] + (["as=" + instance] if instance else []) + list(values)
        out = vfg("script", "run", *((["--wait", "60"] if ends else []) + args), timeout=120)
        inst = instance or name
        self.started.append(inst)
        if not ends:
            self.assertIn("running %s" % inst, out)
        return inst

    def set_(self, inst, *values):
        out = vfg("script", "set", inst, *values)
        self.assertIn("live", out)
        return out

    def stop(self, inst, own_tags=(), shared=()):
        vfg("script", "stop", inst)
        self.assertFalse(running(inst))
        self.assertTrue(until(lambda: not left(inst, own_tags, shared), 5), left(inst, own_tags, shared))
        self.assertNotIn("Traceback", logs(inst))

    def ended(self, inst, seconds):
        self.assertTrue(until(lambda: not running(inst), seconds, 0.5), "%s did not end by itself" % inst)
        self.assertNotIn("Traceback", logs(inst))

    # ---- at the arena --------------------------------------------------------------------------

    def test_01_attribute_guard(self):
        steve()
        rc("attribute Steve minecraft:scale base set 0.5")
        inst = self.run_("attribute_guard")
        self.assertTrue(until(lambda: attr("Steve", "scale") == 1.0, 8), "scale was not put back")
        self.stop(inst)
        inst = self.run_("attribute_guard", "every=2", instance="attribute_guard-b")
        rc("attribute Steve minecraft:movement_speed base set 0.3")
        self.assertTrue(until(lambda: abs(attr("Steve", "movement_speed") - 0.1) < 1e-6, 4))
        self.set_(inst, "every=1")
        self.assertTrue(logged(inst, r"live: every=1"), "the live every was not read")
        rc("attribute Steve minecraft:gravity base set 0.01")
        self.assertTrue(until(lambda: attr("Steve", "gravity") == 0.08, 2.5), "not put back within every=1")
        self.stop(inst)

    def test_02_chosen_one(self):
        steve()
        rc("clear Steve")
        inst = self.run_("chosen_one")
        self.assertTrue(until(lambda: count("@e[type=mannequin,tag=vfg_chosen_one]") == 1, 6), "no Radiant One")
        self.ended(inst, 30)
        self.assertEqual(items('netherite_sword[item_name="Holy Zombie Slayer"]'), 1)
        self.assertEqual(left(inst), [])
        inst = self.run_("chosen_one", "player=Steve", "blade=Excalibur", instance="chosen_one-b")
        time.sleep(3)
        self.set_(inst, "blade=Doombringer")                  # live: the name given is the one now
        self.ended(inst, 30)
        self.assertEqual(items('netherite_sword[item_name="Doombringer"]'), 1)
        self.assertEqual(items('netherite_sword[item_name="Excalibur"]'), 0)
        self.assertEqual(left(inst), [])
        inst = self.run_("chosen_one", "player=Steve", instance="chosen_one-c")
        self.assertTrue(until(lambda: count("@e[type=mannequin,tag=vfg_chosen_one-c]") == 1, 6))
        self.stop(inst)                                       # stopped mid-cinematic: the giant goes

    def test_03_demon_lord(self):
        x, y, z = steve()
        inst = self.run_("demon_lord")
        self.assertTrue(until(lambda: passes("execute if block %d %d %d chest" % (math.floor(x) + 1, math.floor(y),
                                                                                  math.floor(z))), 6), "no supply chest")
        self.assertEqual(count("@e[tag=vfg_demon_lord_label]"), 1)
        self.assertTrue(until(lambda: count("@e[tag=vfg_demon_lord,tag=!vfg_demon_lord_prop]") >= 1, 40),
                        "round 1 sent no spiders")
        self.stop(inst, shared=("vfg_dl_deaths",))
        self.assertIn("59999968", rc("worldborder get"))
        steve()
        inst = self.run_("demon_lord", "round=12", "break_s=30", instance="demon_lord-b")
        self.assertTrue(until(lambda: "No bossbar" not in rc("bossbar get vfg:demon_lord max"), 70, 1),
                        "round 12 brought no boss")
        self.assertTrue(until(lambda: count("@e[type=ravager,tag=vfg_demon_lord-b]") == 1, 5), "no Ravager Warlord")
        self.assertNotIn("59999968", rc("worldborder get"))     # the arena's border is up
        self.set_(inst, "break_s=5")
        self.stop(inst, shared=("vfg_dl_deaths",))
        self.assertIn("No bossbar", rc("bossbar get vfg:demon_lord max"))
        self.assertIn("59999968", rc("worldborder get"))
        calm()

    def test_04_god_visits(self):
        steve()
        inst = self.run_("god_visits")
        time.sleep(3)
        self.assertTrue(running(inst))
        self.stop(inst)
        rc("clear Steve")
        rc("give Steve bread 20")
        inst = self.run_("god_visits", "director_min_s=10", "director_max_s=10.5", "stay_s=4",
                         "director_weights=1,0", "gifts=diamond 5", instance="god_visits-b")
        self.assertTrue(until(lambda: count("@e[type=mannequin,tag=vfg_god_visits-b]") == 1, 16), "no god came")
        self.assertTrue(until(lambda: items("diamond") == 5, 3), "no gift")
        self.set_(inst, "director_weights=0,1", "take_items=bread")
        self.assertTrue(until(lambda: items("bread") == 12, 20), "the god took no bread")
        self.stop(inst)

    def test_05_grave_marks(self):
        steve(safe=False)
        inst = self.run_("grave_marks")
        die((8.5, Y, 8.5), (X - 5, Y, Z - 5))
        self.assertTrue(until(lambda: passes("execute if block 8 %d 8 skeleton_skull" % Y), 6), "no skull")
        self.assertEqual(count("@e[type=armor_stand,tag=vfg_grave_marks]"), 1)
        self.stop(inst)
        self.assertFalse(passes("execute if block 8 %d 8 skeleton_skull" % Y))
        steve(safe=False)
        inst = self.run_("grave_marks", "seconds=8", instance="grave_marks-b")
        die((8.5, Y, 8.5), (X - 5, Y, Z - 5))
        self.assertTrue(until(lambda: passes("execute if block 8 %d 8 skeleton_skull" % Y), 6))
        self.assertTrue(until(lambda: not passes("execute if block 8 %d 8 skeleton_skull" % Y), 12),
                        "the mark did not fade after 8 s")
        self.set_(inst, "seconds=60")
        die((-8.5, Y, 8.5), (X + 5, Y, Z - 5))
        self.assertTrue(until(lambda: passes("execute if block -9 %d 8 skeleton_skull" % Y), 6))
        time.sleep(10)
        self.assertTrue(passes("execute if block -9 %d 8 skeleton_skull" % Y), "seconds=60 did not keep it")
        PLAYERS.tp("Steve", -7.5, Y, 7.5)                         # back to the grave: it fades
        self.assertTrue(until(lambda: not passes("execute if block -9 %d 8 skeleton_skull" % Y), 4))
        self.assertTrue(logged(inst, r"Steve found their grave", 2))
        self.stop(inst)

    def test_06_gun_chests(self):
        x, y, z = steve()
        rc("kill @e[tag=vfg_gun_chest]")
        inst = self.run_("gun_chests", ends=True)
        self.ended(inst, 30)
        self.assertEqual(count("@e[tag=vfg_gun_chest]"), 4)
        for _ in range(4):
            label = "@e[tag=vfg_gun_chest,limit=1,sort=nearest,x=%d,y=%d,z=%d]" % (x, y, z)
            self.assertTrue(passes("execute as %s at @s if block ~ ~-1 ~ chest" % label), "a tag with no chest under it")
            guns = rc("execute as %s at @s run data get block ~ ~-1 ~ Items" % label)
            self.assertEqual(len(re.findall(r'"minecraft:item_name": "(AK-47|Shotgun|Sniper Rifle|Rocket Launcher|'
                                            r'Railgun|Harpoon Gun)"', guns)), 2, guns)
            at = epos(label)
            far = math.hypot(at[0] - x, at[2] - z)
            self.assertTrue(39 <= far <= 162, far)
            rc("kill %s" % label)
        self.assertEqual(left(inst), [])
        inst = self.run_("gun_chests", "per_player=2", "nearest=20", "farthest=30", instance="gun_chests-b", ends=True)
        self.ended(inst, 30)
        self.assertEqual(count("@e[tag=vfg_gun_chest,x=%d,y=%d,z=%d,distance=..40]" % (x, y, z)), 2)
        self.assertEqual(left(inst), [])
        rc("kill @e[tag=vfg_gun_chest]")

    def test_07_herobrine(self):
        steve()
        inst = self.run_("herobrine")
        time.sleep(3)
        self.assertTrue(running(inst))
        self.stop(inst)
        inst = self.run_("herobrine", "director_min_s=1", "director_max_s=1.5", "director_events=shadow",
                         "director_weights=1", instance="herobrine-b")
        self.assertTrue(until(lambda: count("@e[type=armor_stand,tag=vfg_herobrine-b]") >= 1, 6), "no Herobrine")
        for dx, dz in ((3, 3), (-3, 3), (3, -3)):
            rc("setblock %d %d %d torch" % (X + dx, Y, Z + dz))
        self.set_(inst, "director_events=torches")
        self.assertTrue(until(lambda: not any(passes("execute if block %d %d %d torch" % (X + dx, Y, Z + dz))
                                              for dx, dz in ((3, 3), (-3, 3), (3, -3))), 8), "the torches stayed lit")
        self.stop(inst)
        self.assertNotIn("darkness", rc("data get entity Steve active_effects"))

    def test_08_kill_fx(self):
        steve(safe=False)
        rc("item replace entity Steve weapon.mainhand with diamond_sword")

        def kill():
            rc('summon chicken %d %d %d {NoAI:1b,Tags:["lxseeds_prey"]}' % (X, Y, Z + 2))
            rc("damage @e[tag=lxseeds_prey,limit=1] 100 minecraft:player_attack by Steve")
        inst = self.run_("kill_fx")
        time.sleep(1.5)
        for _ in range(3):
            kill()
            time.sleep(1)
        self.assertTrue(logged(inst, r"Steve kill streak 3", 4), logs(inst))
        self.stop(inst)
        inst = self.run_("kill_fx", "streak_s=20", "volume=0.5", instance="kill_fx-b")
        time.sleep(1.5)
        kill()
        time.sleep(1)
        kill()
        self.assertTrue(logged(inst, r"Steve kill streak 2", 4))
        rc('summon pig %d %d %d {NoAI:1b,Tags:["lxseeds_pig"],attributes:[{id:"max_health",base:100}],Health:100f}'
           % (X, Y, Z + 2))
        PLAYERS.look_at("Steve", X, Y + 0.5, Z + 2)
        time.sleep(0.3)
        PLAYERS.attack("Steve")                                   # a hit: dealt goes up
        rc("damage Steve 2 minecraft:generic")                    # a hurt: taken goes up
        self.assertTrue(until(lambda: mc.score("Steve", "vfg_kill_fx-b_dealt") > 0, 3))
        self.assertTrue(until(lambda: mc.score("Steve", "vfg_kill_fx-b_taken") > 0, 3))
        rc("kill @e[tag=lxseeds_pig]")
        self.set_(inst, "streak_s=1")
        time.sleep(1)
        kill()
        time.sleep(1.6)
        kill()
        time.sleep(1)
        self.assertEqual(re.findall(r"Steve kill streak (\d+)", logs(inst))[-2:], ["1", "1"], "streak_s=1 did not cut it")
        die((X + 3, Y, Z + 3), (X, Y, Z))
        self.assertTrue(logged(inst, r"Steve died", 5))
        self.stop(inst)

    def test_09_lucky_blocks(self):
        steve()
        rc("clear Steve")
        inst = self.run_("lucky_blocks")
        self.assertTrue(until(lambda: items('sponge[item_name="Lucky Block"]') == 3, 5))
        rc("setblock %d %d %d sponge" % (X, Y, Z + 2))
        PLAYERS.look_at("Steve", X, Y + 0.5, Z + 2)
        PLAYERS.attack("Steve", "continuous")                    # he mines it by hand
        self.assertTrue(until(lambda: not passes("execute if block %d %d %d sponge" % (X, Y, Z + 2)), 8))
        PLAYERS.stop("Steve")
        self.assertTrue(logged(inst, r"Steve got (.+)", 4), "breaking it gave nothing")
        self.stop(inst, shared=("vfg_lucky",))
        rc("clear Steve")
        inst = self.run_("lucky_blocks", "give=1", instance="lucky_blocks-b")
        self.assertTrue(until(lambda: items('sponge[item_name="Lucky Block"]') == 1, 5))
        self.stop(inst, shared=("vfg_lucky",))

    def test_10_sneaky_creepers(self):
        steve()
        rc('summon creeper %d %d %d {NoAI:1b,Tags:["lxseeds_c1"]}' % (X, Y, Z + 6))
        inst = self.run_("sneaky_creepers")
        self.assertTrue(until(lambda: number(rc("data get entity @e[tag=lxseeds_c1,limit=1] Fuse")) == 60, 4))
        self.assertTrue(until(lambda: "invisibility" in rc("data get entity @e[tag=lxseeds_c1,limit=1] active_effects"), 3))
        self.stop(inst, own_tags=("fuse",))
        self.assertEqual(number(rc("data get entity @e[tag=lxseeds_c1,limit=1] Fuse")), 30)
        inst = self.run_("sneaky_creepers", "fuse=5", instance="sneaky_creepers-b")
        self.assertTrue(until(lambda: number(rc("data get entity @e[tag=lxseeds_c1,limit=1] Fuse")) == 100, 4))
        self.set_(inst, "fuse=4")
        time.sleep(1)
        rc('summon creeper %d %d %d {NoAI:1b,Tags:["lxseeds_c2"]}' % (X + 3, Y, Z + 6))
        self.assertTrue(until(lambda: number(rc("data get entity @e[tag=lxseeds_c2,limit=1] Fuse")) == 80, 4))
        self.stop(inst, own_tags=("fuse",))
        self.assertEqual(number(rc("data get entity @e[tag=lxseeds_c2,limit=1] Fuse")), 30)
        rc("kill @e[tag=lxseeds_c1]")
        rc("kill @e[tag=lxseeds_c2]")

    def test_11_walking_dead(self):
        steve()
        rc('summon zombie %d %d %d {NoAI:1b,Tags:["lxseeds_near"]}' % (X, Y, Z + 9))
        rc('summon zombie %d %d %d {NoAI:1b,Tags:["lxseeds_far"]}' % (X, Y, Z + 14))
        near, far = "@e[tag=lxseeds_near,limit=1]", "@e[tag=lxseeds_far,limit=1]"
        inst = self.run_("walking_dead")
        self.assertTrue(until(lambda: abs((attr(far, "movement_speed") or 0) - 0.17) < 1e-3, 4))
        self.assertTrue(until(lambda: abs((attr(near, "movement_speed") or 0) - 0.23) < 1e-3, 3))
        self.assertEqual(attr(near, "follow_range"), 16)
        self.stop(inst, own_tags=("w",), shared=("vfg_wd_sneak", "vfg_wd_sprint"))
        self.assertTrue(abs(attr(far, "movement_speed") - 0.23) < 1e-3, "speed not reset")
        inst = self.run_("walking_dead", "walk_speed=0.1", "friends=0", instance="walking_dead-b")
        self.assertTrue(until(lambda: abs((attr(far, "movement_speed") or 0) - 0.1) < 1e-3, 4))
        self.set_(inst, "walk_range=20")
        self.assertTrue(until(lambda: attr(near, "follow_range") == 20, 4))
        self.stop(inst, own_tags=("w",), shared=("vfg_wd_sneak", "vfg_wd_sprint"))
        self.assertEqual(attr(near, "follow_range"), 35, "follow range not reset")
        rc("kill @e[tag=lxseeds_near]")
        rc("kill @e[tag=lxseeds_far]")

    def test_12_zoo(self):
        steve()
        inst = self.run_("zoo")
        self.assertTrue(until(lambda: count("@e[tag=vfg_zoo_horde]") >= 20, 6), "no animals came")
        self.stop(inst, own_tags=("horde",))
        inst = self.run_("zoo", "horde_cap=10", instance="zoo-b")
        self.assertTrue(until(lambda: count("@e[tag=vfg_zoo-b_horde]") == 10, 6))
        time.sleep(2.5)
        self.assertEqual(count("@e[tag=vfg_zoo-b_horde]"), 10, "over the cap")
        self.set_(inst, "horde_cap=25")
        self.assertTrue(until(lambda: count("@e[tag=vfg_zoo-b_horde]") == 25, 6))
        self.stop(inst, own_tags=("horde",))

    # ---- at sites of their own -------------------------------------------------------------------

    def test_13_anti_camp(self):
        steve(SITES["anti_camp"])
        inst = self.run_("anti_camp")
        self.assertTrue(until(lambda: count("@e[type=tnt,tag=vfg_anti_camp]") >= 1, 20, 0.1), "no TNT on a camper")
        self.assertEqual(most(lambda: count("@e[type=tnt,tag=vfg_anti_camp]"), 1), 7)
        self.stop(inst)
        steve(SITES["anti_camp"])
        inst = self.run_("anti_camp", "stands_seconds=6", "tnt=3", instance="anti_camp-b")
        self.assertTrue(until(lambda: count("@e[type=tnt,tag=vfg_anti_camp-b]") >= 1, 10, 0.1))
        self.assertEqual(most(lambda: count("@e[type=tnt,tag=vfg_anti_camp-b]"), 1), 3)
        self.set_(inst, "tnt=5")
        time.sleep(3)                                              # the volley before goes off
        self.assertTrue(until(lambda: count("@e[type=tnt,tag=vfg_anti_camp-b]") >= 1, 10, 0.1))
        self.assertEqual(most(lambda: count("@e[type=tnt,tag=vfg_anti_camp-b]"), 1), 5)
        self.stop(inst)

    def shoot(self, item, yaw, pitch):
        rc("item replace entity Steve weapon.mainhand with %s" % item)
        if item == "bow":
            rc("give Steve arrow 4")                             # beside the bow, never in its slot
        PLAYERS.look("Steve", yaw, pitch)
        PLAYERS.use("Steve", "continuous")
        time.sleep(1.2)
        PLAYERS.stop("Steve")

    def test_14_boom_bow(self):
        steve(SITES["boom_bow"])
        rc("clear Steve")
        inst = self.run_("boom_bow")
        time.sleep(1)
        self.shoot("bow", 0, 10)
        at = logged(inst, r"(\d+) blast\(s\) where Steve's arrow came down: (-?\d+) (-?\d+) (-?\d+)", 8)
        self.assertTrue(at, "the arrow did not blow up")
        n, x, y, z = map(int, at)
        self.assertEqual(n, 1)
        self.assertTrue(passes("execute if block %d %d %d air" % (x, y - 1, z)), "no crater under it")
        self.stop(inst, own_tags=("seen",))
        steve(SITES["boom_bow"])
        inst = self.run_("boom_bow", "explosion_power=3", "cloud=true", instance="boom_bow-b")
        time.sleep(1)
        self.shoot("bow", 90, 10)
        self.assertTrue(logged(inst, r"1 blast\(s\) where Steve's arrow", 8))
        self.set_(inst, "explosion_layout=plus", "explosion_spread=4")
        time.sleep(1)
        self.shoot("bow", 180, 10)
        self.assertTrue(logged(inst, r"5 blast\(s\) where Steve's arrow", 8), "the plus did not go off")
        self.assertEqual(count("@e[type=arrow,tag=vfg_boom_bow-b_seen,nbt={inGround:1b}]"), 0, "a landed arrow stayed")
        self.stop(inst, own_tags=("seen",))

    def test_15_tnt_rain(self):
        steve(SITES["tnt_rain"])
        inst = self.run_("tnt_rain")
        self.assertTrue(until(lambda: count("@e[type=tnt,tag=vfg_tnt_rain]") >= 1, 4, 0.1))
        dropped = logged(inst, r"dropped (\d+) TNT", 3)
        self.assertTrue(dropped and 1 <= int(dropped) <= 10, dropped)
        self.stop(inst)
        steve(SITES["tnt_rain"])
        inst = self.run_("tnt_rain", "every=5", "sky_count=3", "fuse=6", "sky_open=false", instance="tnt_rain-b")
        self.assertTrue(until(lambda: count("@e[type=tnt,tag=vfg_tnt_rain-b]") >= 1, 4, 0.1))
        self.assertEqual(most(lambda: count("@e[type=tnt,tag=vfg_tnt_rain-b]"), 1), 3)
        self.set_(inst, "sky_count=6")
        self.assertTrue(logged(inst, r"live: sky_count=6", 8))
        self.assertTrue(logged(inst, r"dropped 6 TNT", 3), logs(inst))
        self.stop(inst)

    def test_16_zeus(self):
        steve(SITES["zeus"])
        inst = self.run_("zeus")
        time.sleep(1)
        self.shoot("trident", 0, -25)
        self.assertEqual(most(lambda: count("@e[type=lightning_bolt,tag=vfg_zeus]"), 4), 1)
        self.assertTrue(logged(inst, r"lightning x1 where Steve's trident came down", 3))
        self.stop(inst, own_tags=("seen",))
        steve(SITES["zeus"])
        inst = self.run_("zeus", "lightning_count=3", "lightning_layout=ring", "lightning_spread=3", instance="zeus-b")
        time.sleep(1)
        self.shoot("trident", 0, -25)
        self.assertEqual(most(lambda: count("@e[type=lightning_bolt,tag=vfg_zeus-b]"), 4), 3)
        self.set_(inst, "lightning_count=2")
        time.sleep(1)
        self.shoot("trident", 90, -25)
        self.assertTrue(logged(inst, r"lightning x2 where Steve's trident came down", 6), logs(inst))
        self.stop(inst, own_tags=("seen",))

    # ---- the run's two, last: they build terrain beside the arena -----------------------------

    def test_17_mountains(self):
        steve()
        inst = self.run_("mountains", ends=True)
        self.ended(inst, 600)
        self.assertTrue(logged(inst, r"done in \d+s", 1), logs(inst))
        base = int(logged(inst, r"base (\d+)", 1))
        x0, z0 = int(X) + 60, int(Z) - 50                      # away=60 east of Steve, spanning his z
        px, pz = x0 + 70, z0 + 62                                # its highest peak
        rc("forceload add %d %d" % (px, pz))
        try:
            self.assertTrue(until(lambda: passes("execute if loaded %d 64 %d" % (px, pz)), 10))
            self.assertTrue(passes("execute if block %d %d %d stone" % (px, base + 60, pz)), "no peak")
            self.assertTrue(passes("execute if biome %d %d %d minecraft:jagged_peaks" % (px, base + 60, pz)))
        finally:
            rc("forceload remove %d %d" % (px, pz))
        self.assertEqual(left(inst), [])
        inst = self.run_("mountains", "seed=7", "corner=-260,-60", instance="mountains-b")
        self.assertTrue(logged(inst, r"terrain column 10/100", 30), logs(inst))
        self.stop(inst)                                          # stopped part-built: its forceloads go

    def test_18_mountain_trees(self):
        steve()
        # On ground west of the arena, with a seed of its own each time (a run with the same
        # seed over the same ground finds every spot it picks already planted).
        inst = self.run_("mountain_trees", "n=20", "seed=%d" % (time.time() % 100000), "corner=-220,40",
                         instance="mountain_trees-b", ends=True)
        self.ended(inst, 120)
        placed = logged(inst, r"trees placed (\d+) tries \d+", 1)
        self.assertTrue(placed and 1 <= int(placed) <= 20, logs(inst))     # n caps it; tries are n x 8
        self.assertEqual(left(inst), [])
        inst = self.run_("mountain_trees", ends=True)
        self.ended(inst, 300)
        placed = logged(inst, r"trees placed (\d+) tries \d+", 1)
        # Up to 250, in at most 2000 tries: on a fresh world about 200 on the mountains' slopes,
        # fewer each time it runs again over the same ground with the same seed.
        self.assertTrue(placed and 1 <= int(placed) <= 250, logs(inst))
        self.assertEqual(left(inst), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
