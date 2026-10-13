"""The launch preset against a real Minecraft 26.3 server: a Carpet test player thrown by wind
charges and by a ride, a husk by its Motion, the fall guard, and the launch_pad example.

    integration/realmc/up pl --fresh && eval "$(integration/realmc/env pl)"
    uv run --no-project --python 3.12 --with pydantic python scripts/_test/live_presets_launch.py

It changes blocks, so it runs only against a realmc test server (REALMC_NAME set, and
VFG_MC_PROPERTIES inside that server's folder), never against the box. Everything happens on a
stone floor 40 blocks over the arena (inside its forceloaded chunks) and is taken away again.
A Carpet fake player has no client: the server moves him by the same physics a client runs, so
a wind charge's push, which reaches a real player through his own client, moves him as it would
a person. What it measured on 2026-10-12 is in test_presets_launch.py's flight model, which
these numbers check again: a throw lands within half a block of what was asked.
"""
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
REPO = os.path.dirname(SCRIPTS)
LIB = os.path.join(SCRIPTS, "_lib")
PRESETS = os.path.join(SCRIPTS, "_presets")
sys.path.append(REPO)  # the repo: a preset's values class imports actions._types
sys.path[:0] = [LIB, PRESETS, HERE, os.path.join(REPO, "integration", "realmc")]

PROPS = os.environ.get("VFG_MC_PROPERTIES", "")
if not os.environ.get("REALMC_NAME") or "realmc" not in os.path.realpath(PROPS):
    sys.exit("live_presets_launch: point it at a realmc server first: eval \"$(integration/realmc/env pl)\"")
os.environ["VFG_INSTANCE"] = "livelaunch"
TMP = tempfile.mkdtemp(prefix="vfg-live-launch-")
os.environ["VFG_RUN_DIR"] = TMP

import mc  # noqa: E402
from kit import aim, area, values, world  # noqa: E402
from test_kit import values_meta  # noqa: E402
import launch as L  # noqa: E402
import players as fake_players  # noqa: E402

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)

POS = re.compile(r"\[(-?[\d.eE-]+)d, (-?[\d.eE-]+)d, (-?[\d.eE-]+)d\]")
r = aim.cast((0.5, 300.0, 0.5), (0.0, -1.0, 0.0), 400)
FLOOR = int(r.hit_block[1])
SKY = FLOOR + 40                # the floor the tests stand on is at SKY - 1
SKYBOX = (-32, SKY - 1, -32, 31, SKY + 30, 31)
PLAYERS = fake_players.on()
G, AIR, DOWN, MOD = L._launch_tags()


def vals(**given):
    return values(meta=values_meta(L, given=given), run_dir=TMP, instance="livelaunch")


def where(sel):
    m = POS.search(mc.rcon("data get entity %s Pos" % sel))
    return tuple(float(c) for c in m.groups()) if m else None


def health(sel):
    m = re.search(r"(-?[\d.]+)f", mc.rcon("data get entity %s Health" % sel))
    return float(m.group(1)) if m else None


def flight(sel, seconds=10.0):
    """(where it came to rest, blocks it rose above where it started, blocks along the ground)."""
    start = last = where(sel)
    top, still, end = start[1], 0, time.time() + seconds
    while time.time() < end:
        time.sleep(0.05)
        now = where(sel)
        if now is None:
            return None, top - start[1], 0.0
        top = max(top, now[1])
        still = still + 1 if max(abs(a - b) for a, b in zip(now, last)) < 1e-3 else 0
        last = now
        if still >= 8:
            break
    return last, top - start[1], ((last[0] - start[0]) ** 2 + (last[2] - start[2]) ** 2) ** 0.5


class Live(unittest.TestCase):
    def setUp(self):
        area.fill((-31, SKY - 1, -31, 31, SKY - 1, 31), "stone")
        PLAYERS.spawn("Steve", at=(-20.5, SKY, -20.5), facing=(-90, 0))
        mc.rcon("effect clear Steve")                   # a fake player keeps his effects when he comes back
        mc.rcon("effect give Steve instant_health 1 10 true")
        time.sleep(1.0)

    def tp(self, x, y, z, yaw=-90):
        """Steve moved, then a moment to stand: a throw in the tick he lands from a teleport
        leaves from the air (he is not on the ground for a tick), so it goes further."""
        PLAYERS.tp("Steve", x, y, z, yaw, 0)
        time.sleep(0.5)

    def tearDown(self):
        L._launch_cleanup()
        PLAYERS.kill_all()
        mc.rcon("kill @e[tag=lt]")
        mc.rcon("kill @e[tag=vfg_livelaunch]")
        area.fill(SKYBOX, "air", most_mspt=0)
        self.assertIn("Test failed", mc.rcon("execute if entity @e[type=wind_charge]"))

    def throw(self, sel, **kw):
        n = L.launch(sel if sel.isalnum() else aim.Hit(1.0, world.Pos(*where(sel)), "Husk", False),
                     kw.pop("v", None) or vals(), **kw)
        self.assertEqual(n, 1)
        return flight(sel)

    def unguarded(self, sel):
        """The guard has come off once it landed: no modifier, no tag."""
        end = time.time() + 3
        while time.time() < end and "is 1.0" not in mc.rcon("attribute %s minecraft:fall_damage_multiplier get" % sel):
            time.sleep(0.1)
        self.assertIn("is 1.0", mc.rcon("attribute %s minecraft:fall_damage_multiplier get" % sel))
        self.assertNotIn(G, mc.rcon("data get entity %s Tags" % sel))


class LiveWind(Live):
    """Wind charges at his feet: the push a real player's client gets; this server moves the
    Carpet player by it too."""

    def test_one_charge_at_his_feet_six_up(self):
        mc.rcon("summon wind_charge -20.5 %.1f -20.5 {Motion:[0.0,-1.0,0.0]}" % (SKY + 0.3))
        _, rise, along = flight("Steve")
        self.assertAlmostEqual(rise, 6.08, delta=0.3)
        self.assertLess(along, 0.1)

    def test_up_forward_away_and_toward(self):
        _, rise, along = self.throw("Steve", way="up", strength=12)
        self.assertAlmostEqual(rise, 12.0, delta=0.5)
        self.assertLess(along, 0.1)
        self.unguarded("Steve")
        at, rise, along = self.throw("Steve", way="forward", strength=15)
        self.assertAlmostEqual(along, 15.0, delta=0.5)
        self.assertAlmostEqual(at[0], -5.5, delta=0.5)                  # he faces +x
        self.assertEqual(health("Steve"), 20.0)
        self.unguarded("Steve")
        self.tp(-20.5, SKY, -20.5)
        at, _, along = self.throw("Steve", way="away", strength=8, frm=world.Pos(-20.5, SKY, -26.5))
        self.assertAlmostEqual(at[2], -12.5, delta=0.5)                 # away from it: +z
        time.sleep(0.5)
        at, _, along = self.throw("Steve", way="toward", strength=30, frm=world.Pos(-20.5, SKY, -26.5))
        self.assertAlmostEqual(at[2], -25.5, delta=0.5)                 # toward it, one block short

    def test_safe_lands_a_long_fall_whole_and_hurt_does_not(self):
        area.fill((-24, SKY + 15, -24, -18, SKY + 15, -18), "stone")    # a ledge 16 over the floor
        time.sleep(3.0)                                                 # who just joined is not hurt for 3 s
        for landing, hurt in (("safe", False), ("hurt", True)):
            self.tp(-20.5, SKY + 16, -20.5)
            mc.rcon("effect give Steve instant_health 1 10 true")
            time.sleep(0.6)
            at, _, along = self.throw("Steve", way="forward", strength=10, v=vals(launch_landing=landing))
            self.assertAlmostEqual(at[1], SKY, delta=0.01)
            self.assertEqual(health("Steve") < 20.0, hurt, landing)
            self.unguarded("Steve")

    def test_float_comes_down_slowly(self):
        start = time.time()
        self.throw("Steve", way="up", strength=10)
        plain = time.time() - start
        self.unguarded("Steve")
        start = time.time()
        self.throw("Steve", way="up", strength=10, v=vals(launch_landing="float"))
        self.assertGreater(time.time() - start, plain + 1.0)            # down slowly
        self.assertEqual(health("Steve"), 20.0)


class LiveRide(Live):
    def test_a_ride_up_and_along_then_set_down_and_the_stand_gone(self):
        _, rise, _ = self.throw("Steve", way="up", strength=10, v=vals(launch_method="ride"))
        self.assertAlmostEqual(rise, 10.4, delta=0.5)                   # he sits 0.4 over the stand
        self.unguarded("Steve")
        _, _, along = self.throw("Steve", way="forward", strength=12, v=vals(launch_method="ride"))
        self.assertAlmostEqual(along, 12.0, delta=0.6)
        self.assertIn("Test failed", mc.rcon("execute if entity @e[type=armor_stand]"))
        self.assertEqual(health("Steve"), 20.0)

    def test_in_the_air_he_is_carried(self):
        PLAYERS.tp("Steve", -20.5, SKY + 6, -20.5, -90, 0)
        time.sleep(0.1)
        self.assertEqual(L.launch("Steve", vals(), way="forward", strength=8), 1)
        self.assertIn("Test passed", mc.rcon("execute if entity @e[type=armor_stand]"))
        at, _, _ = flight("Steve")
        self.assertAlmostEqual(at[1], SKY, delta=0.01)
        self.assertEqual(health("Steve"), 20.0)


class LiveMob(Live):
    def test_a_husk_by_its_motion_safe_and_hurt(self):
        PLAYERS.tp("Steve", 20.5, SKY, 20.5)
        mc.rcon("effect give Steve resistance 60 4 true")
        for landing, hurt in (("safe", False), ("hurt", True)):
            mc.rcon('summon husk -20.5 %d -20.5 {Tags:["lt"],PersistenceRequired:1b,Silent:1b,Rotation:[-90f,0f],'
                    'attributes:[{id:"minecraft:movement_speed",base:0.0}]}' % SKY)      # its AI walks it off the throw
            time.sleep(0.5)
            _, rise, _ = self.throw("@e[tag=lt,limit=1]", way="up", strength=20, v=vals(launch_landing=landing))
            self.assertAlmostEqual(rise, 20.0, delta=0.5)
            time.sleep(0.5)
            self.assertEqual(health("@e[tag=lt,limit=1]") < 20.0, hurt, landing)
            mc.rcon("kill @e[tag=lt]")


class LiveHeal(Live):
    """What a copy killed mid-flight leaves: a guard on a player who has landed, a stand. The
    next throw, by any copy, takes them away; what is in the air or new is left."""

    def test_a_guard_and_a_stand_left_by_a_dead_copy_go_at_the_next_throw(self):
        PLAYERS.spawn("Alex", at=(-10.5, SKY, -10.5), facing=(0, 0))
        mc.rcon("scoreboard objectives add vfg_launch dummy")
        for who in ("Steve", "Alex"):
            mc.rcon("attribute %s minecraft:fall_damage_multiplier modifier add vfg:launch -1 add_multiplied_total" % who)
        mc.rcon("scoreboard players set Steve vfg_launch 0")             # thrown long ago
        mc.rcon("execute store result score Alex vfg_launch run time query gametime")   # thrown just now
        mc.rcon('summon armor_stand 0.5 %d 0.5 {Invisible:1b,Small:1b,Tags:["vfg_launch_stand","lt"]}' % SKY)
        mc.rcon("scoreboard players set @e[tag=vfg_launch_stand,limit=1] vfg_launch 0")
        time.sleep(0.5)
        mc.rcon('summon husk 10.5 %d 10.5 {Tags:["lt"],PersistenceRequired:1b,Silent:1b,NoAI:1b}' % SKY)
        self.assertEqual(L.launch(aim.Hit(1.0, world.Pos(10.5, SKY, 10.5), "Husk", False), vals(), way="up",
                                  strength=4), 1)
        self.assertIn("is 1.0", mc.rcon("attribute Steve minecraft:fall_damage_multiplier get"))
        self.assertIn("is 0.0", mc.rcon("attribute Alex minecraft:fall_damage_multiplier get"))   # too new to touch
        self.assertIn("Test failed", mc.rcon("execute if entity @e[tag=vfg_launch_stand]"))
        mc.rcon("attribute Alex minecraft:fall_damage_multiplier modifier remove vfg:launch")


class LivePad(Live):
    """The launch_pad example as the box runs a copy: Steve walks onto a slime block."""

    def test_a_step_onto_slime_throws_him_forward_and_a_stop_leaves_nothing(self):
        area.fill((-17, SKY - 1, -21, -16, SKY - 1, -21), "slime_block")   # two blocks long: he walks onto it
        meta = values_meta("under_feet", L, given={"launch_way": "forward", "launch_strength": 12})
        meta["values"].update(player="@a", pad_block="slime_block", cooldown=2.0, at="", under_radius=0)
        with open(os.path.join(TMP, "padprobe.json"), "w") as f:
            __import__("json").dump(meta, f)
        env = dict(os.environ, PYTHONPATH=LIB, VFG_INSTANCE="padprobe")
        proc = subprocess.Popen([sys.executable, "-u", "main.py"], cwd=os.path.join(SCRIPTS, "launch_pad"), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            time.sleep(1.0)
            PLAYERS.move("Steve", "forward")                            # walks east onto the pad
            end = time.time() + 6
            while time.time() < end and where("Steve")[1] < SKY + 1:
                time.sleep(0.05)
            PLAYERS.move("Steve", None)
            up = where("Steve")[1] - SKY
            at, rise, _ = flight("Steve")
            rise += up
        finally:
            proc.send_signal(signal.SIGTERM)
            out = proc.communicate(timeout=5)[0].decode()
        self.assertIn("launched Steve forward 12 blocks", out)
        self.assertGreater(rise, 4.0)
        self.assertGreater(at[0], -7.0)                                 # about 12 on from the pad
        self.assertEqual(health("Steve"), 20.0)
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("Traceback", out)
        self.assertIn("Test failed", mc.rcon("execute if entity @e[tag=vfg_padprobe]"))
        self.assertNotIn("vfg_padprobe", mc.rcon("data get entity Steve Tags"))
        self.assertIn("is 1.0", mc.rcon("attribute Steve minecraft:fall_damage_multiplier get"))


if __name__ == "__main__":
    print("realmc %s: arena floor at y %d, tests at y %d" % (os.environ.get("REALMC_NAME"), FLOOR, SKY))
    unittest.main(verbosity=2)
