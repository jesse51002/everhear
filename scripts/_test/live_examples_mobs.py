"""The mobs examples on a real Minecraft 26.3 server, run the way a player's ask runs them.

    integration/realmc/up e4 --fresh && eval "$(integration/realmc/env e4)"
    uv run --python 3.9 python scripts/_test/live_examples_mobs.py [-k horde]

Each example is started with `vfg script run` (deployed to the server's stand-in and run there on
Python 3.9, as on the box), with Carpet's fake players Steve and Alex doing what sets it off; the
world is then asked over RCON whether it did what the run's script did; one value is changed with
`vfg script set` while it runs and the change is seen; and after `vfg script stop`, or after the
example ended by itself, nothing tagged vfg_<instance> is left: no entity, bossbar or forceload.
It runs only against a realmc test server with Carpet, never the box. The examples: horde_boss,
dragon_boss, ice_queen, horde, horror_mode, keep_effects, barrier_shield, projectile_shield,
weapon_chest, arena and tether.
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
    sys.exit("live_examples_mobs: point it at a realmc server first: eval \"$(integration/realmc/env e4)\"")
os.environ["VFG_INSTANCE"] = "lxmobs"           # this process's own tag

import mc  # noqa: E402
import players as fake_players  # noqa: E402

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)

PLAYERS = fake_players.on()
ARENA = PLAYERS.arena()
X, Y, Z = ARENA["x"], ARENA["y"], ARENA["z"]                   # where a player stands at its centre
if "Unknown or incomplete" in PLAYERS.cmd("carpet"):
    sys.exit("live_examples_mobs: the server has no Carpet: integration/realmc/realmc.py carpet, then up --fresh")


def rc(cmd):
    return mc.rcon(cmd)


def vfg(*args):
    """vfg as a player's ask runs it; its output. Fails the test when vfg fails."""
    r = subprocess.run(["vfg"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
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


def number(reply):
    m = re.search(r"(-?\d+(?:\.\d+)?)f?\s*$", reply.strip())
    return float(m.group(1)) if m else None


def count(selector):
    m = re.search(r"Count: (\d+)", rc("execute if entity %s" % selector))
    return int(m.group(1)) if m else 0


def health(who):
    return number(rc("data get entity %s Health" % who))


def pos(who):
    v = re.findall(r"(-?[\d.]+(?:E-?\d+)?)d", rc("data get entity %s Pos" % who))
    return tuple(float(c) for c in v[:3]) if len(v) >= 3 else None


def flat(a, b):
    return math.hypot(a[0] - b[0], a[2] - b[2])


def effect(who, name):
    """The amplifier of an effect on a player or mob, or None."""
    for m in re.finditer(r"\{([^{}]*)\}", rc("data get entity %s active_effects" % who)):
        if '"minecraft:%s"' % name in m.group(1):
            a = re.search(r"amplifier: (-?\d+)b", m.group(1))
            return int(a.group(1)) if a else 0
    return None


def running(instance):
    return re.search(r"^%s running" % re.escape(instance), vfg("script", "list"), re.M) is not None


FORCED = rc("forceload query")


def left(instance, tags=()):
    """What an instance left behind: entities tagged vfg_<instance> (or one of its own tags),
    its bossbars, and forceloads that were not there before."""
    out = []
    for t in ("vfg_" + instance,) + tuple("vfg_%s_%s" % (instance, s) for s in tags):
        if count("@e[tag=%s]" % t):
            out.append("entity tagged " + t)
    for k in (1, 2):
        if "No bossbar" not in rc("bossbar get vfg:%s_boss%d max" % (instance, k)):
            out.append("bossbar vfg:%s_boss%d" % (instance, k))
    if rc("forceload query") != FORCED:
        out.append("forceloads: " + rc("forceload query")[:200])
    return out


class Live(unittest.TestCase):
    """Steve and Alex, in creative so nothing kills them unless a test says otherwise."""
    INSTANCES = ()

    @classmethod
    def setUpClass(cls):
        for name, at in (("Steve", (X, Y, Z)), ("Alex", (X + 6, Y, Z))):
            PLAYERS.spawn(name, at, gamemode="creative")

    def setUp(self):
        for inst in self.INSTANCES:
            if running(inst):
                vfg("script", "stop", inst)
        for name, at in (("Steve", (X, Y, Z)), ("Alex", (X + 6, Y, Z))):
            if name not in PLAYERS.online():
                PLAYERS.spawn(name, at, gamemode="creative")
            PLAYERS.stop(name)
            PLAYERS.tp(name, at[0], at[1], at[2], 0, 0)
            rc("gamemode creative %s" % name)
            rc("effect clear %s" % name)
            rc("clear %s" % name)
        rc("kill @e[tag=lxprobe]")

    def tearDown(self):
        for inst in self.INSTANCES:
            if running(inst):
                vfg("script", "stop", inst)
        rc("kill @e[tag=lxprobe]")
        rc("kill @e[type=item,x=%d,y=%d,z=%d,distance=..200]" % (X, Y, Z))
        rc("kill @e[type=experience_orb,x=%d,y=%d,z=%d,distance=..200]" % (X, Y, Z))
        rc("kill @e[type=arrow,x=%d,y=%d,z=%d,distance=..200]" % (X, Y, Z))

    def stopped_clean(self, instance, tags=(), wait=3.0):
        """vfg script stop, then nothing of it left."""
        vfg("script", "stop", instance)
        self.assertFalse(running(instance))
        self.assertEqual(until(lambda: not left(instance, tags), wait) or left(instance, tags), True)

    def probe(self, mob, x, y, z, nbt=""):
        """A mob for a test to watch, tagged lxprobe (never the script's)."""
        rc('summon %s %.2f %.2f %.2f {Tags:["lxprobe"],PersistenceRequired:1b%s}' % (mob, x, y, z, nbt))


# ---- the bosses -------------------------------------------------------------------------------

class Bosses(Live):
    INSTANCES = ("horde_boss", "hb_stop", "dragon_boss", "ice_queen")

    def tearDown(self):
        super().tearDown()
        for block in ("packed_ice", "snow_block"):                   # what a failed queen test left on the arena
            rc("fill %d %d %d %d %d %d air replace %s" % (X - 20, Y, Z - 20, X + 20, Y + 3, Z + 20, block))

    def test_horde_boss_fought_to_the_death(self):
        out = vfg("script", "run", "horde_boss", "boss_health=60", "boss_every=2", "boss_minions=2",
                  "boss_reward_count=3")
        self.assertIn("running horde_boss", out)
        body = "@e[tag=vfg_horde_boss_boss1,limit=1]"
        self.assertTrue(until(lambda: count("@e[tag=vfg_horde_boss_boss1]") == 1, 15), "no Horde King")
        self.assertIn("Horde King", rc("data get entity %s CustomName" % body))
        self.assertIn("is 3.0", rc("execute as %s run attribute @s scale base get" % body))
        self.assertIn("netherite_helmet", rc("data get entity %s equipment" % body))
        self.assertIn("is 12.0", rc("execute as %s run attribute @s attack_damage base get" % body))
        self.assertEqual(number(rc("bossbar get vfg:horde_boss_boss1 max")), 60)
        self.assertTrue(until(lambda: count("@e[tag=vfg_horde_boss_boss1_minion]") >= 1, 8), "no minions")
        self.assertLessEqual(count("@e[tag=vfg_horde_boss_boss1_minion]"), 6)       # three times boss_minions
        self.assertIn("boss_reward_count=5", vfg("script", "set", "horde_boss", "boss_reward_count=5"))
        time.sleep(1.0)
        rc("damage %s 35 minecraft:magic" % body)                                   # 25 of 60: below half
        self.assertTrue(until(lambda: number(rc("bossbar get vfg:horde_boss_boss1 value")) == 25, 5))
        self.assertTrue(until(lambda: effect(body, "speed") is not None, 5), "it did not enrage")
        rc("damage %s 100 minecraft:magic" % body)
        self.assertTrue(until(lambda: not running("horde_boss"), 10), "it did not end when the boss died")
        self.assertIn("is dead", vfg("script", "logs", "horde_boss"))
        self.assertEqual(count('@e[type=item,x=%d,y=%d,z=%d,distance=..40,nbt={Item:{id:"minecraft:diamond",count:5}}]'
                               % (X, Y, Z)), 1, "no reward of 5 (set while it ran) where it died")
        self.assertEqual(left("horde_boss", ("boss1", "boss1_minion")), [])

    def test_horde_boss_stopped_mid_fight(self):
        vfg("script", "run", "horde_boss", "as=hb_stop", "boss_every=1", "boss_mob=husk", "boss_minion=husk")
        self.assertTrue(until(lambda: count("@e[tag=vfg_hb_stop_boss1_minion]") >= 1, 15))
        self.assertEqual(count("@e[type=husk,tag=vfg_hb_stop_boss1]"), 1)
        self.stopped_clean("hb_stop", ("boss1", "boss1_minion"))

    def test_dragon_boss_three_heads_one_bar(self):
        old = rc("gamerule mob_griefing")
        vfg("script", "run", "dragon_boss", "boss_health=50", "height=40")
        heads = "@e[type=ender_dragon,tag=vfg_dragon_boss_boss1]"
        self.assertTrue(until(lambda: count(heads) == 3, 15), "not three dragons")
        y = pos("@e[tag=vfg_dragon_boss_boss1_0,limit=1]")[1]
        self.assertGreater(y, Y + 25, "not up in the sky")
        self.assertEqual(number(rc("bossbar get vfg:dragon_boss_boss1 max")), 150)
        self.assertIn("false", rc("gamerule mob_griefing"))
        self.assertEqual(count("@e[tag=vfg_dragon_boss_boss1_minion]"), 0)           # no ground in the sky
        self.assertIn("Three-Headed Dragon", rc("data get entity @e[tag=vfg_dragon_boss_boss1_1,limit=1] CustomName"))
        rc("kill @e[tag=vfg_dragon_boss_boss1_1]")                                     # one head down
        self.assertTrue(until(lambda: number(rc("bossbar get vfg:dragon_boss_boss1 value")) == 100, 8))
        self.assertIn("boss_reward_count=2", vfg("script", "set", "dragon_boss", "boss_reward_count=2"))
        self.assertTrue(running("dragon_boss"))
        rc("kill %s" % heads)
        self.assertTrue(until(lambda: not running("dragon_boss"), 12), "it did not end when they died")
        log = vfg("script", "logs", "dragon_boss")
        self.assertIn("is dead", log)
        self.assertNotIn("Traceback", log)
        self.assertTrue(until(lambda: count('@e[type=item,nbt={Item:{id:"minecraft:diamond",count:2}}]') == 1, 5),
                        "no reward of 2 (set while they flew) where the last fell")
        self.assertEqual(rc("gamerule mob_griefing"), old)
        self.assertEqual(until(lambda: not left("dragon_boss", ("boss1",)), 15) or left("dragon_boss", ("boss1",)), True)

    def test_ice_queen_freezes_and_calls_servants(self):
        PLAYERS.tp("Alex", X + 30, Y, Z + 30)                       # out of her reach
        rc("gamemode survival Steve")
        rc("effect give Steve resistance infinite 2 true")          # hurt, not killed
        rc("effect give Steve health_boost infinite 4 true")
        rc("effect give Steve instant_health 1 10 true")
        vfg("script", "run", "ice_queen", "boss_health=80", "boss_every=3", "summon_count=2", "cold=4")
        queen = "@e[tag=vfg_ice_queen_boss1,limit=1]"
        self.assertTrue(until(lambda: count("@e[type=stray,tag=vfg_ice_queen_boss1]") == 1, 15), "no Ice Queen")
        self.assertIn("is 2.0", rc("execute as %s run attribute @s scale base get" % queen))
        gear = rc("data get entity %s equipment" % queen)
        self.assertIn("diamond_helmet", gear)
        self.assertIn("8900331", gear)                                 # the pale blue robes
        self.assertEqual(number(rc("bossbar get vfg:ice_queen_boss1 max")), 80)
        full = health("Steve")
        # Her first ability, 3 s in: Steve frozen (slowness X), in a shell of packed ice, hurt by the cold.
        self.assertTrue(until(lambda: effect("Steve", "slowness") == 9, 8), "Steve was not frozen")
        sx, sy, sz = (int(math.floor(c)) for c in pos("Steve"))
        shell = ["%d %d %d" % (sx + dx, sy, sz + dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        self.assertTrue(any("passed" in rc("execute if block %s packed_ice" % b).lower() for b in shell),
                        "no ice shell round Steve")
        self.assertLess(health("Steve"), full, "the cold did not hurt")
        # Her second, 3 s later: frost servants round her.
        self.assertTrue(until(lambda: count("@e[type=stray,tag=vfg_ice_queen_summon]") >= 1, 6), "no servants")
        self.assertIn("Frost Servant", rc("data get entity @e[tag=vfg_ice_queen_summon,limit=1] CustomName"))
        self.assertLessEqual(count("@e[tag=vfg_ice_queen_summon]"), 6)
        # A value changed while she fights: the next shell is snow.
        vfg("script", "set", "ice_queen", "freeze_block=snow_block")
        snow = []

        def snowed():
            x, y, z = (int(math.floor(c)) for c in pos("Steve"))
            snow[:] = ["%d %d %d" % (x + dx, y, z + dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))]
            return any("passed" in rc("execute if block %s snow_block" % b).lower() for b in snow)
        self.assertTrue(until(snowed, 10), "freeze_block=snow_block did not reach the next freeze")
        rc("effect give Steve instant_health 1 10 true")
        rc("damage %s 200 minecraft:magic" % queen)
        self.assertTrue(until(lambda: not running("ice_queen"), 10), "it did not end when she died")
        self.assertEqual(count("@e[tag=vfg_ice_queen_summon]"), 0, "her servants outlived her")
        dropped = count('@e[type=item,x=%d,y=%d,z=%d,distance=..40,nbt={Item:{id:"minecraft:blue_ice"}}]' % (X, Y, Z))
        picked = "Found 16" in rc("clear Steve blue_ice 0")            # she fell at his feet
        self.assertTrue(dropped == 1 or picked, "no blue ice where she fell")
        # Every shell melts once its freeze is over (4 s), her death or not.
        self.assertTrue(until(lambda: all("passed" not in rc("execute if block %s %s" % (b, k)).lower()
                                          for b in shell + snow for k in ("packed_ice", "snow_block")), 6),
                        "a shell was left standing")
        self.assertEqual(left("ice_queen", ("boss1", "summon")), [])


# ---- the hordes and the dark ------------------------------------------------------------------

class Hordes(Live):
    INSTANCES = ("horde", "horde_once", "horror_mode")

    def test_horde_waves_held_at_the_cap(self):
        vfg("script", "run", "horde", "horde_per_player=4", "horde_cap=6", "horde_every=2", "horde_waves=0",
            "horde_spread=10", "horde_mobs=husk,spider")
        tag = "@e[tag=vfg_horde_horde]"
        self.assertTrue(until(lambda: count(tag) == 6, 10), "never reached the cap: %d" % count(tag))
        most = 0
        for _ in range(12):                                            # three more waves come and go
            most = max(most, count(tag))
            time.sleep(0.5)
        self.assertEqual(most, 6, "over the cap")
        rc("kill @e[tag=vfg_horde_horde,limit=3,sort=random]")
        self.assertTrue(until(lambda: count(tag) == 6, 6), "the next wave did not fill it again")
        self.assertIn("horde_cap=10", vfg("script", "set", "horde", "horde_cap=10"))
        self.assertTrue(until(lambda: count(tag) == 10, 8), "horde_cap=10 did not reach it: %d" % count(tag))
        self.assertEqual(count("@e[type=husk,tag=vfg_horde_horde]") + count("@e[type=spider,tag=vfg_horde_horde]"), 10)
        steve, near = pos("Steve"), 0
        for m in re.findall(r"\[(-?[\d.]+)d, (-?[\d.]+)d, (-?[\d.]+)d\]", rc("execute as %s run data get entity @s Pos" % tag)):
            near += flat(steve, tuple(float(c) for c in m)) < 40
        self.assertGreater(near, 0)
        self.stopped_clean("horde", ("horde",))

    def test_one_wave_ends_when_it_is_dead(self):
        vfg("script", "run", "horde", "as=horde_once", "horde_per_player=3", "horde_spread=10")
        tag = "@e[tag=vfg_horde_once_horde]"
        self.assertTrue(until(lambda: count(tag) == 6, 10), "not 3 round each of 2: %d" % count(tag))  # 3 round each of two
        time.sleep(2.5)
        self.assertEqual(count(tag), 6, "a second wave came")
        self.assertTrue(running("horde_once"))
        rc("kill %s" % tag)
        self.assertTrue(until(lambda: not running("horde_once"), 6), "it did not end when they were dead")
        self.assertEqual(left("horde_once", ("horde",)), [])

    def test_horror_mode_scares_and_the_dark_spares_who_asked(self):
        vfg("script", "run", "horror_mode", "director_events=mob,shadow", "director_min_s=1", "director_max_s=1")
        self.assertTrue(until(lambda: count("@e[type=creeper,tag=vfg_horror_mode]") >= 1, 12), "no fake creeper")
        self.assertIn("NoAI: 1b", rc("data get entity @e[type=creeper,tag=vfg_horror_mode,limit=1]"))
        self.assertTrue(until(lambda: count("@e[type=armor_stand,tag=vfg_horror_mode]") >= 1, 12), "no shadow")
        vfg("script", "set", "horror_mode", "director_events=dark")
        self.assertTrue(until(lambda: effect("Steve", "darkness") is not None and effect("Alex", "darkness") is not None, 6),
                        "the dark did not come")
        vfg("script", "set", "horror_mode", "no_dark=Alex")
        self.assertTrue(until(lambda: effect("Alex", "darkness") is None, 4), "the dark did not lift from Alex")
        rc("effect clear Steve darkness")
        self.assertTrue(until(lambda: effect("Steve", "darkness") is not None, 5), "Steve's dark stopped too")
        self.assertIsNone(effect("Alex", "darkness"), "Alex was put in the dark again")
        vfg("script", "stop", "horror_mode")
        self.assertIsNone(effect("Steve", "darkness"), "the dark outlived the stop")
        self.assertEqual(until(lambda: not left("horror_mode"), 3) or left("horror_mode"), True)


# ---- what is kept on players, and round them ----------------------------------------------------

class Round(Live):
    INSTANCES = ("keep_effects", "barrier_shield", "projectile_shield", "weapon_chest", "arena", "tether")

    def test_keep_effects_kept_changed_and_taken_off(self):
        vfg("script", "run", "keep_effects", "player=Steve")
        self.assertTrue(until(lambda: effect("Steve", "speed") == 4, 4), "no Speed V")
        self.assertIsNone(effect("Alex", "speed"))
        self.assertIn("show_particles: 0b", rc("data get entity Steve active_effects"))
        rc("effect clear Steve")                                        # as milk, or a death, does
        self.assertTrue(until(lambda: effect("Steve", "speed") == 4, 3), "not given again")
        vfg("script", "set", "keep_effects", "level=2")                 # "reduce my speed to two"
        self.assertTrue(until(lambda: effect("Steve", "speed") == 1, 3), "level=2 did not lower it")
        vfg("script", "set", "keep_effects", "effect=night_vision", "player=Steve,Alex")
        self.assertTrue(until(lambda: effect("Alex", "night_vision") == 1 and effect("Steve", "night_vision") == 1, 4))
        self.assertIsNone(effect("Steve", "speed"), "the old effect was left on")
        self.stopped_clean("keep_effects")
        self.assertIsNone(effect("Steve", "night_vision"), "the stop left it on Steve")
        self.assertIsNone(effect("Alex", "night_vision"), "the stop left it on Alex")

    def test_barrier_shield_throws_mobs_out(self):
        self.probe("husk", X + 2, Y, Z)                                 # 2 blocks from Steve
        inside = "@e[tag=lxprobe,x=%.1f,y=%.1f,z=%.1f,distance=..%d]"
        vfg("script", "run", "barrier_shield", "player=Steve", "around_radius=5", "push_strength=4")
        self.assertTrue(until(lambda: count(inside % (X, Y, Z, 5)) == 0, 5), "the husk stayed inside")
        self.assertTrue(count("@e[tag=lxprobe]"), "the husk is gone, not thrown")
        self.probe("husk", X - 8, Y, Z)                                 # outside 5, inside 12
        time.sleep(1.5)
        self.assertEqual(count(inside % (X, Y, Z, 4)), 0)
        vfg("script", "set", "barrier_shield", "around_radius=12")
        self.assertTrue(until(lambda: count(inside % (X, Y, Z, 12)) == 0, 6), "around_radius=12 did not reach")
        self.stopped_clean("barrier_shield")

    def test_projectile_shield_turns_an_arrow(self):
        PLAYERS.tp("Alex", X + 30, Y, Z + 30)
        rc("gamemode survival Steve")
        self.assertEqual(health("Steve"), 20)
        vfg("script", "run", "projectile_shield", "player=Steve")
        time.sleep(2.0)
        rc("summon arrow %.2f %.2f %.2f {Motion:[0.0d,0.0d,-1.5d],Tags:[\"lxprobe\"]}" % (X, Y + 1.3, Z + 9))
        self.assertTrue(until(lambda: count("@e[type=arrow,tag=vfg_projectile_shield]") == 1, 3), "the arrow was not turned")
        time.sleep(1.0)
        self.assertEqual(health("Steve"), 20, "the arrow hurt him")
        self.assertGreater(pos("@e[type=arrow,tag=vfg_projectile_shield,limit=1]")[2], Z + 9, "it did not fly back")
        rc('summon fireball %.2f %.2f %.2f {Motion:[0.0d,0.0d,-0.6d],acceleration_power:0.05d,Tags:["lxprobe"]}'
           % (X, Y + 1.3, Z + 14))
        self.assertTrue(until(lambda: count("@e[type=fireball,tag=vfg_projectile_shield]") == 1, 3), "the fireball was not turned")
        time.sleep(1.0)
        self.assertEqual(health("Steve"), 20, "the fireball hurt him")
        # His own arrows are never turned.
        uuid = re.search(r"\[I; [^\]]*\]", rc("data get entity Steve UUID")).group(0).replace(" ", "")
        rc("summon arrow %.2f %.2f %.2f {Motion:[0.0d,0.0d,1.5d],Owner:%s,Tags:[\"lxprobe\",\"lxown\"]}" % (X, Y + 1.3, Z + 1.5, uuid))
        time.sleep(1.0)
        self.assertEqual(count("@e[tag=lxown,tag=vfg_projectile_shield]"), 0, "his own arrow was turned")
        # The shield moved to Alex while it runs: the next arrow at Steve hurts him.
        vfg("script", "set", "projectile_shield", "player=Alex")
        time.sleep(1.0)
        rc("summon arrow %.2f %.2f %.2f {Motion:[0.0d,0.0d,-1.5d],Tags:[\"lxprobe\"]}" % (X, Y + 1.3, Z + 9))
        self.assertTrue(until(lambda: health("Steve") < 20, 3), "Steve is still shielded after player=Alex")
        rc("effect give Steve instant_health 1 5 true")
        self.stopped_clean("projectile_shield")

    def test_weapon_chest_and_its_weapons(self):
        vfg("script", "run", "weapon_chest")
        spots = [(X + dx, Y, Z + dz) for dx, dz in ((2, 0), (0, 2), (-2, 0), (0, -2), (2, 2), (-2, -2))]
        chest = until(lambda: next((s for s in spots if "minecraft:bow" in rc("data get block %d %d %d Items" % tuple(
            int(math.floor(c)) for c in s))), None), 6)
        self.assertTrue(chest, "no chest of weapons beside Steve")
        cx, cy, cz = (int(math.floor(c)) for c in chest)
        items = rc("data get block %d %d %d Items" % (cx, cy, cz))
        for name in ("Hellfire Longbow", "Glacier Bow", "Stormcaller", "Thunderbrand", "Doomcleaver", "Gravebreaker"):
            self.assertIn(name, items)
        self.assertIn('id: "minecraft:arrow"', items)
        # Thunderbrand: a right-click, lightning and damage where he looks. A husk 5 blocks to the
        # side of the spot is out of around_radius 4 until it is set to 8.
        rc("item replace entity Steve weapon.mainhand from block %d %d %d container.3" % (cx, cy, cz))
        PLAYERS.look("Steve", 0, 14)                                    # the ground about 6.5 blocks south
        time.sleep(0.5)
        self.probe("husk", X + 5, Y, Z + 6.5, ",NoAI:1b")
        self.probe("husk", X, Y, Z + 6.5, ",NoAI:1b,Tags:[\"lxprobe\",\"lxnear\"]")
        PLAYERS.use("Steve")
        self.assertTrue(until(lambda: count("@e[tag=lxnear]") == 0 or health("@e[tag=lxnear,limit=1]") < 20, 3),
                        "nothing where he looked was hurt")
        side = "@e[tag=lxprobe,tag=!lxnear,limit=1]"
        self.assertEqual(health(side), 20, "around_radius 4 reached 5 blocks")
        vfg("script", "set", "weapon_chest", "around_radius=8")
        time.sleep(1.5)                                                 # its cooldown is 1 s
        PLAYERS.use("Steve")
        self.assertTrue(until(lambda: not count(side) or health(side) < 15, 3), "around_radius=8 did not reach")  # 10, less armour
        rc("kill @e[tag=lxprobe]")
        # Hellfire Longbow: an arrow that blasts where it lands.
        rc("item replace entity Steve weapon.mainhand from block %d %d %d container.0" % (cx, cy, cz))
        rc("give Steve arrow 4")
        self.probe("husk", X + 2, Y, Z + 7, ",NoAI:1b")
        PLAYERS.look("Steve", 0, 25)
        time.sleep(0.3)
        PLAYERS.use_continuous("Steve")
        time.sleep(1.2)
        PLAYERS.stop("Steve")
        self.assertTrue(until(lambda: count("@e[type=arrow,tag=vfg_weapon_chest_seen]") == 1, 2), "the arrow was not known")
        self.assertTrue(until(lambda: not count("@e[tag=lxprobe]") or health("@e[tag=lxprobe,limit=1]") < 20, 4),
                        "no blast where it landed")
        # Stormcaller: a thrown trident calls lightning where it lands (never within 4 blocks of
        # who threw it, so it is thrown further).
        rc("kill @e[tag=lxprobe]")
        rc("item replace entity Steve weapon.mainhand from block %d %d %d container.2" % (cx, cy, cz))
        PLAYERS.look("Steve", 0, 5)
        time.sleep(0.3)
        PLAYERS.use_continuous("Steve")
        time.sleep(1.0)
        PLAYERS.stop("Steve")
        self.assertTrue(until(lambda: count("@e[type=lightning_bolt,tag=vfg_weapon_chest]") >= 1, 4, 0.05),
                        "no lightning where the trident landed")
        self.stopped_clean("weapon_chest", ("seen",))
        self.assertIn("minecraft:bow", rc("data get block %d %d %d Items" % (cx, cy, cz)), "the chest went with it")
        rc("setblock %d %d %d air" % (cx, cy, cz))

    def test_arena_sends_back_who_leaves(self):
        vfg("script", "run", "arena", "leaves_radius=10", "leaves_center=%g,%g,%g" % (X, Y, Z))
        time.sleep(1.5)
        PLAYERS.tp("Alex", X + 25, Y, Z)
        self.assertTrue(until(lambda: flat(pos("Alex"), (X, Y, Z)) < 1.5, 4), "Alex was not sent back")
        vfg("script", "set", "arena", "leaves_radius=40")
        time.sleep(1.0)
        PLAYERS.tp("Alex", X + 25, Y, Z)
        time.sleep(2.5)
        self.assertGreater(flat(pos("Alex"), (X, Y, Z)), 20, "leaves_radius=40 did not take")
        self.stopped_clean("arena")

    def test_tether_pulls_one_back_to_the_other(self):
        vfg("script", "run", "tether", "player=Alex", "leaves_of_player=Steve", "leaves_radius=8")
        time.sleep(1.5)
        PLAYERS.tp("Alex", X + 25, Y, Z + 5)
        self.assertTrue(until(lambda: flat(pos("Alex"), pos("Steve")) < 1.5, 4), "Alex was not brought back to Steve")
        time.sleep(1.0)                                                # seen back inside, so it can fire again
        PLAYERS.tp("Steve", X - 10, Y, Z - 10)                         # Steve walks off: Alex follows
        self.assertTrue(until(lambda: flat(pos("Alex"), pos("Steve")) < 1.5, 4), "Alex did not follow Steve")
        vfg("script", "set", "tether", "leaves_radius=40")
        time.sleep(1.0)
        PLAYERS.tp("Alex", X + 15, Y, Z + 5)
        time.sleep(2.5)
        self.assertGreater(flat(pos("Alex"), pos("Steve")), 20, "leaves_radius=40 did not take")
        self.stopped_clean("tether")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        try:
            PLAYERS.kill_all()
        except Exception:
            pass
