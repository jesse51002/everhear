"""The ray examples against a real Minecraft 26.3 server with Carpet's fake players: railgun
(and railgun as a gatling gun), lightning_staff, infinity_gauntlet, death_gaze,
sniper_rifle, ice_staff, gravity_staff and wither_wand, each started with `vfg script run`
as on the box.

    integration/realmc/up e1 --fresh && eval "$(integration/realmc/env e1)"
    uv run --python 3.9 python scripts/_test/live_examples_rays.py [-k railgun]

Each test spawns Steve (and Alex where it needs him) on the arena, gives the script a
non-default value or two, makes Steve aim with rotate and Carpet's look, hold the item the
script gave him and use it (or sneak), then checks the world over RCON: the targets'
health, effects and positions, what was spared. One `vfg script set` on a live value is
seen to take effect while it runs; `vfg script stop` then leaves nothing tagged
vfg_<instance> (entities, tags on mobs, bossbars, forceloads, objectives) and takes the
item back. Targets are husks (they do not burn at noon) with NoAI and a lot of health, so a
hit is read as health lost. It runs only against a realmc test server, never the box;
`up --fresh` puts the world back after.
"""
import os
import re
import shutil
import subprocess
import sys
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(REPO, "integration", "realmc")]

PROPS = os.environ.get("VFG_MC_PROPERTIES", "")
if not os.environ.get("REALMC_NAME") or "realmc" not in os.path.realpath(PROPS) or not shutil.which("vfg"):
    sys.exit("live_examples_rays: point it at a realmc server first: eval \"$(integration/realmc/env e1)\"")

import players  # noqa: E402

P = players.on()
cmd = P.cmd                 # holds the RCON lock file from send to reply, as the scripts and vfg do
TAG = "lr"                  # every target this test summons carries it
NUM = r"(-?\d+(?:\.\d+)?(?:[eE]-?\d+)?)"
A = P.arena()
FLOOR = int(A["y"]) - 1     # the arena's grass floor; players stand on FLOOR + 1
X0, Y0, Z0 = A["x"] + 0.5, A["y"], A["z"] + 0.5
START = (X0, Y0, Z0 - 13)   # where Steve stands, facing south (+z) down the arena


def vfg(*args, ok=True):
    """`vfg <args>` as a person types it; its output. Fails the test on a non-zero exit."""
    p = subprocess.run(["vfg"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, timeout=60)
    if ok and p.returncode != 0:
        raise AssertionError("vfg %s exited %d: %s" % (" ".join(args), p.returncode, p.stdout))
    return p.stdout


_n = [0]


def target(kind, x, y, z, hp=1000, extra="", ai=False):
    """A mob (NoAI unless ai) with `hp` health at a place; its selector, by a tag of its own."""
    _n[0] += 1
    nbt = ('NoAI:%db,PersistenceRequired:1b,Silent:1b,Tags:["%s","%s_%d"],attributes:[{id:"max_health",base:%d}],'
           'Health:%df%s' % (0 if ai else 1, TAG, TAG, _n[0], hp, hp, extra))
    out = cmd("summon %s %.2f %.2f %.2f {%s}" % (kind, x, y, z, nbt))
    assert "Summoned" in out, out
    return "@e[tag=%s_%d,limit=1]" % (TAG, _n[0])


def health(sel):
    """A mob's or player's health, or None when it is dead or gone."""
    m = re.search(NUM + "f", cmd("data get entity %s Health" % sel))
    return float(m.group(1)) if m else None


def where(sel):
    m = re.search(r"\[%sd, %sd, %sd\]" % (NUM, NUM, NUM), cmd("data get entity %s Pos" % sel))
    return tuple(float(c) for c in m.groups()) if m else None


def effects(sel):
    """{effect id: amplifier} of what a mob or player has on it now."""
    out = cmd("data get entity %s active_effects" % sel)
    return {m.group(2): int(m.group(1)) for m in re.finditer(r'amplifier: (-?\d+)b[^}]*?id: "minecraft:([a-z_]+)"', out)}


def wait_for(fn, seconds=5.0, every=0.1):
    """fn() again until it is truthy or the time is up; its last answer."""
    end = time.time() + seconds
    got = fn()
    while not got and time.time() < end:
        time.sleep(every)
        got = fn()
    return got


def has(player, item_id, slots="container.*"):
    return "passed" in cmd('execute if items entity %s %s *[custom_data~{vfg_item:"%s"}]'
                           % (player, slots, item_id)).lower()


def tags_left(instance):
    """Every tag on any entity that starts with vfg_<instance>, and how many entities have
    vfg_<instance> itself."""
    out = cmd("execute as @e[x=-64,y=-64,z=-64,dx=128,dy=400,dz=128] run data get entity @s Tags")
    tag = "vfg_" + instance
    return sorted(set(t for t in re.findall(r'"([^"]+)"', out) if t == tag or t.startswith(tag + "_")))


class Example(unittest.TestCase):
    """Starts each test on a clean arena with Steve at START, facing south, in survival."""
    instance = None

    @classmethod
    def setUpClass(cls):
        cmd("time set noon")
        cmd("gamerule mob_griefing true")

    def setUp(self):
        self.forced = cmd("forceload query")
        self.clear_arena()
        P.spawn("Steve", START, facing=(0, 0))
        P.stop("Steve")
        P.sneak("Steve", False)
        P.clear("Steve")
        P.hotbar("Steve", 1)
        cmd("effect clear Steve")
        cmd("effect give Steve instant_health 1 10 true")
        cmd("effect give Steve saturation 1 10 true")
        self.started = []

    def tearDown(self):
        for inst in self.started:
            vfg("script", "stop", inst, ok=False)
        for name in ("Alex",):
            if name in P.online():
                P.kill(name)
        self.clear_arena()

    def clear_arena(self):
        cmd("kill @e[tag=%s]" % TAG)
        cmd("kill @e[type=item]")
        cmd("kill @e[type=wither]")
        cmd("kill @e[type=wither_skull]")
        self.clear_arena_fire()
        cmd("fill %d %d %d %d %d %d air replace stone" % (X0 - 16, FLOOR + 1, Z0 - 16, X0 + 15, FLOOR + 6, Z0 + 15))

    def clear_arena_fire(self):
        cmd("fill %d %d %d %d %d %d air replace fire" % (X0 - 16, FLOOR + 1, Z0 - 16, X0 + 15, FLOOR + 3, Z0 + 15))

    # -- the script
    def run_script(self, script, *values, item=None, instance=None):
        """vfg script run, then wait until Steve has its item (if it gives one) and holds it."""
        instance = instance or script
        args = ["script", "run", script] + (["as=" + instance] if instance != script else []) + list(values)
        out = vfg(*args)
        self.assertIn("running %s" % instance, out)
        self.started.append(instance)
        if item:
            self.assertTrue(wait_for(lambda: has("Steve", item), 8), "%s never gave Steve its item" % instance)
            self.hold(item)
        return instance

    def hold(self, item):
        """Select the hotbar slot that has the item."""
        for slot in range(1, 10):
            P.hotbar("Steve", slot)
            if has("Steve", item, "weapon.mainhand"):
                return slot
        self.fail("Steve has no %s in his hotbar" % item)

    def set(self, instance, *values):
        out = vfg("script", "set", instance, *values)
        self.assertNotIn("restart", out.lower(), out)
        time.sleep(1.0)             # VFG_LIVE_POLL_S is 0.5: read again within a second
        return out

    def stop_clean(self, instance, item=None):
        """vfg script stop, then nothing it made is left."""
        out = vfg("script", "stop", instance)
        self.assertIn("stopped", out)
        self.started.remove(instance)
        time.sleep(0.5)
        self.assertEqual(tags_left(instance), [], "tagged things left behind by %s" % instance)
        self.assertNotIn("vfg:%s" % instance, cmd("bossbar list"))
        self.assertEqual(cmd("forceload query"), self.forced, "a forceload was left behind")
        objectives = cmd("scoreboard objectives list")
        self.assertNotRegex(objectives, r"\[vfg_%s(_[^\]]*)?\]" % re.escape(instance))
        if item:
            self.assertFalse(has("Steve", item), "%s was not taken back" % item)
        log = vfg("script", "logs", instance, ok=False)
        self.assertNotIn("Traceback", log)
        self.assertNotIn("error in", log)

    def use(self, wait=1.0):
        P.use("Steve")
        time.sleep(wait)

    def aim_at(self, x, y, z):
        P.look_at("Steve", x, y, z)
        time.sleep(0.15)


class Railgun(Example):
    def test_railgun_pierces_a_line_through_a_wall_and_spares_the_rest(self):
        near = target("husk", X0, Y0, Z0 - 6)
        villager = target("villager", X0, Y0, Z0 - 3, hp=20)
        far = target("husk", X0, Y0, Z0 + 2)
        aside = target("husk", X0 + 5, Y0, Z0 - 3)
        cmd("fill %d %d %d %d %d %d stone" % (X0 - 2, Y0, Z0 - 9, X0 + 2, Y0 + 3, Z0 - 9))
        inst = self.run_script("railgun", "player=Steve", "cooldown=1", item="railgun")
        P.look("Steve", 0, 0)
        self.use()
        self.assertEqual(health(near), 970.0)       # 30 magic: armour does not take any of it
        self.assertEqual(health(far), 970.0)        # through the wall, and on through the first
        self.assertEqual(health(villager), 20.0)
        self.assertEqual(health(aside), 1000.0)
        self.assertEqual(health("Steve"), 20.0)
        # live: walls stop it now, and it hurts more
        self.set(inst, "through_walls=false", "damage_amount=50")
        self.use(1.5)
        self.assertEqual((health(near), health(far)), (970.0, 970.0))
        self.set(inst, "through_walls=true")
        self.use(1.5)
        self.assertEqual((health(near), health(far)), (920.0, 920.0))
        self.stop_clean(inst, "railgun")

    def test_railgun_as_a_gatling_gun_fires_while_held(self):
        husk = target("husk", X0, Y0, Z0 + 2)
        inst = self.run_script("railgun", "player=Steve", "fire=hold", "item=Gatling Gun", "cooldown=0.1",
                               "range=24", "particle=crit", "sound=entity.arrow.shoot", "damage_amount=10",
                               "damage_type=player_attack", item="gatling_gun", instance="gatling")
        P.look("Steve", 0, 0)
        self.use(1.0)
        one = 1000.0 - health(husk)
        self.assertGreater(one, 9)                  # one tap, one shot: 10 less its armour's share
        self.assertLess(one, 10.5)
        P.hold("Steve")
        time.sleep(2.0)
        P.stop("Steve")
        time.sleep(0.5)
        lost = 1000.0 - health(husk)
        self.assertGreaterEqual(lost, one * 4 - 0.5, "held 2 s: at least three more shots (a mob takes "
                                                     "one every 0.5 s whatever the rate)")
        # live: out of range now
        self.set(inst, "range=10")
        before = health(husk)
        self.use(1.0)
        self.assertEqual(health(husk), before)
        self.stop_clean(inst, "gatling_gun")


class LightningStaff(Example):
    def test_strikes_where_steve_looks_and_spares_who_it_is_told(self):
        husk = target("husk", X0, Y0, Z0)
        inst = self.run_script("lightning_staff", "player=Steve", "lightning_count=3", "cooldown=1.5",
                               item="lightning_staff")
        self.aim_at(X0, FLOOR + 0.9, Z0 + 2)
        P.use("Steve")
        bolts = wait_for(lambda: int((re.search(r"Count: (\d+)", cmd(
            "execute if entity @e[type=lightning_bolt,tag=vfg_%s]" % inst)) or [0, 0])[1]), 2, 0.05)
        time.sleep(1.0)
        self.assertGreaterEqual(bolts, 1, "no bolt struck")
        lost = 1000.0 - health(husk)
        # 40 of lightning, less the 1.6% a husk's own 2 armour takes off it (39.36), and the
        # bolts' own when one lands near it too
        self.assertGreaterEqual(lost, 39.35, "40 lightning damage to what is within 4 blocks")
        self.assertEqual(health("Steve"), 20.0)
        # live: Alex stands by the target and is spared, bolts and damage alike (the first
        # strike's fire is put out, and he takes none from fire, so a hurt is the staff's)
        self.clear_arena_fire()
        P.spawn("Alex", (X0 + 2, Y0, Z0 + 3), facing=(0, 0))
        cmd("effect give Alex instant_health 1 10 true")
        cmd("effect give Alex fire_resistance 30 0 true")
        self.set(inst, "lightning_spare=Alex", "damage_spare=Alex")
        before = health(husk)
        time.sleep(0.6)                             # the husk's own fire from the first strike
        self.use(1.5)
        self.assertEqual(health("Alex"), 20.0)
        self.assertLessEqual(health(husk), before - 39.35)
        self.stop_clean(inst, "lightning_staff")


class InfinityGauntlet(Example):
    def test_kills_the_first_thing_only_within_its_range(self):
        near = target("husk", X0, Y0, Z0 - 6)
        far = target("husk", X0, Y0, Z0 + 1)
        inst = self.run_script("infinity_gauntlet", "player=Steve", "range=10", item="infinity_gauntlet")
        P.look("Steve", 0, 0)
        self.use()
        self.assertIsNone(health(near), "the first in line is gone")
        self.assertEqual(health(far), 1000.0)
        self.use()
        self.assertEqual(health(far), 1000.0, "14 blocks away, past range=10")
        self.set(inst, "range=30")
        self.use()
        self.assertIsNone(health(far))
        self.assertEqual(health("Steve"), 20.0)
        self.stop_clean(inst, "infinity_gauntlet")


class DeathGaze(Example):
    def test_kills_mobs_it_looks_at_while_sneaking_never_through_a_wall(self):
        P.spawn("Alex", (X0, Y0, Z0 - 8), facing=(0, 0))
        cmd("effect give Alex instant_health 1 10 true")
        villager = target("villager", X0, Y0, Z0 - 4, hp=20)
        husk = target("husk", X0, Y0, Z0)
        cmd("fill %d %d %d %d %d %d stone" % (X0 - 2, Y0, Z0 + 4, X0 + 2, Y0 + 3, Z0 + 4))
        hidden = target("husk", X0, Y0, Z0 + 7)
        inst = self.run_script("death_gaze", "player=Steve", "range=40")
        P.look("Steve", 0, 0)
        time.sleep(1.0)
        self.assertEqual(health(husk), 1000.0, "standing, nothing happens")
        P.sneak("Steve")
        self.assertTrue(wait_for(lambda: health(husk) is None, 3), "the husk it looked at is still alive")
        P.sneak("Steve", False)
        self.assertEqual(health(villager), 20.0)
        self.assertEqual(health(hidden), 1000.0, "it saw through the wall")
        self.assertEqual(health("Alex"), 20.0, "damage_what=mobs: players are never touched")
        # live: players too, hurt rather than killed
        self.set(inst, "damage_what=all", "damage_kill=false", "damage_amount=3")
        P.sneak("Steve")
        time.sleep(1.2)
        P.sneak("Steve", False)
        self.assertLess(health("Alex"), 20.0)
        self.assertEqual(health(hidden), 1000.0)
        self.stop_clean(inst)


class SniperRifle(Example):
    def test_double_on_the_head_and_a_scope_while_sneaking(self):
        husk = target("husk", X0, Y0, Z0)
        inst = self.run_script("sniper_rifle", "player=Steve", "damage_amount=100", item="super_sniper")
        self.aim_at(X0, Y0 + 0.9, Z0)               # its body
        self.use()
        body = 1000.0 - health(husk)
        self.assertTrue(95 <= body <= 100, body)    # 100 player_attack, less a husk's 2 armour
        self.aim_at(X0, Y0 + 1.75, Z0)              # its head
        self.use()
        head = 1000.0 - body - health(husk)
        self.assertAlmostEqual(head, 2 * body, delta=1)
        # the scope: slowness while he crouches holding it, gone when he stands
        P.sneak("Steve")
        self.assertTrue(wait_for(lambda: effects("Steve").get("slowness") == 3, 2), effects("Steve"))
        P.sneak("Steve", False)
        self.assertTrue(wait_for(lambda: "slowness" not in effects("Steve"), 2), "the scope stayed on")
        # a shot through the scope is fired from crouching eyes (from standing ones it would
        # pass 0.35 higher, over the head: a body shot)
        P.sneak("Steve")
        time.sleep(0.5)
        self.aim_at(X0, Y0 + 2.1, Z0)
        before = health(husk)
        self.use()
        P.sneak("Steve", False)
        self.assertAlmostEqual(before - health(husk), 2 * body, delta=1)
        # live: three times on the head
        self.aim_at(X0, Y0 + 1.75, Z0)
        self.set(inst, "damage_head=3")
        before = health(husk)
        self.use()
        self.assertAlmostEqual(before - health(husk), 3 * body, delta=1.5)
        self.stop_clean(inst, "super_sniper")


class IceStaff(Example):
    def test_locks_on_and_freezes_and_hurts_round_the_mob(self):
        locked = target("husk", X0, Y0, Z0)
        beside = target("husk", X0 + 4, Y0, Z0)
        out = target("husk", X0, Y0, Z0 + 9)
        villager = target("villager", X0 - 3, Y0, Z0, hp=20)
        inst = self.run_script("ice_staff", "player=Steve", "damage_amount=100", "around_radius=6",
                               item="ice_staff")
        P.look("Steve", 4, 0)                       # a little off the husk: the cone locks onto it
        self.use()
        self.assertEqual(health(locked), 900.0)
        self.assertEqual(health(beside), 900.0)
        self.assertEqual(health(out), 1000.0)
        self.assertEqual(health(villager), 20.0)
        for sel in (locked, beside):
            self.assertEqual(effects(sel).get("slowness"), 9, effects(sel))
        self.assertNotIn("slowness", effects(out))
        # live: a wider blast reaches the third
        self.set(inst, "around_radius=12")
        self.use(1.5)
        self.assertEqual(health(out), 900.0)
        self.stop_clean(inst, "ice_staff")


class GravityStaff(Example):
    def test_crush_pins_a_flyer_to_the_ground_and_lift_throws_up(self):
        flyer = target("husk", X0, Y0 + 4, Z0, extra=",NoGravity:1b")
        inst = self.run_script("gravity_staff", "player=Steve", "around_radius=6", item="gravity_staff")
        self.aim_at(X0, FLOOR + 0.9, Z0)
        self.use(0.2)
        landed = wait_for(lambda: (where(flyer) or (0, 99, 0))[1] < Y0 + 0.5, 3)
        self.assertTrue(landed, "still up at %s" % (where(flyer),))
        self.assertEqual(effects(flyer).get("slowness"), 9)
        time.sleep(1.0)
        self.assertLess(where(flyer)[1], Y0 + 0.5, "it got back up while pinned")
        # live: lift
        self.set(inst, "mode=lift", "push_strength=8")
        time.sleep(4)                               # the first pin is over
        ground = target("husk", X0 + 2, Y0, Z0 + 2, ai=True)
        y0 = where(ground)[1]
        P.use("Steve")
        top, end = y0, time.time() + 2
        while time.time() < end:
            top = max(top, (where(ground) or (0, y0, 0))[1])
            time.sleep(0.05)
        self.assertGreater(top - y0, 3, "lift threw nothing up")
        self.stop_clean(inst, "gravity_staff")


class WitherWand(Example):
    def test_summons_withers_where_steve_looks_and_takes_them_away(self):
        cmd("gamemode creative Steve")
        try:
            griefing = re.search(r"(true|false)", cmd("gamerule mob_griefing")).group(1)
            # NoAI, so they stay where they were summoned to be looked at (a wither flies off at once)
            inst = self.run_script("wither_wand", "player=Steve", "summon_count=2", "summon_nbt=NoAI:1b",
                                   item="wither_wand")
            self.aim_at(X0, FLOOR + 0.9, Z0 + 6)
            self.use(2.0)
            n = lambda: int((re.search(r"Count: (\d+)", cmd("execute if entity @e[type=wither,tag=vfg_%s]" % inst))
                             or [0, 0])[1])
            self.assertEqual(n(), 2)
            for m in re.finditer(r"\[%sd, %sd, %sd\]" % (NUM, NUM, NUM), cmd(
                    "execute as @e[type=wither,tag=vfg_%s] run data get entity @s Pos" % inst)):
                x, z = float(m.group(1)), float(m.group(3))
                self.assertLess(((x - X0) ** 2 + (z - Z0 - 6) ** 2) ** 0.5, 4.5, "a wither far from where he looked")
            self.assertIn("false", cmd("gamerule mob_griefing"))
            self.set(inst, "summon_count=1")
            time.sleep(3.5)                         # its 5 s cooldown
            self.use(2.0)
            self.assertEqual(n(), 3)
            # none left: mob_griefing goes back while it still runs
            cmd("kill @e[type=wither,tag=vfg_%s]" % inst)
            self.assertTrue(wait_for(lambda: griefing in cmd("gamerule mob_griefing"), 4))
            self.stop_clean(inst, "wither_wand")
            self.assertEqual(cmd("execute if entity @e[type=wither]").startswith("Test passed"), False)
            self.assertIn(griefing, cmd("gamerule mob_griefing"))
        finally:
            cmd("gamemode survival Steve")


if __name__ == "__main__":
    unittest.main(verbosity=2)
