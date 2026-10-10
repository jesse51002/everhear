"""The power examples on a real Minecraft 26.3 server, with Carpet's fake players: galick_gun,
hollow_purple, spirit_bomb, ice_beam, super_saiyan, meteor_staff and boom_trident.

    integration/realmc/up e2 --fresh && eval "$(integration/realmc/env e2)"
    uv run --python 3.9 python scripts/_test/live_examples_powers.py [-k galick]

Each example is started as the box starts it, with `vfg script run` (deployed to the realmc
stand-in, run there on Python 3.9), with a few values that are not its defaults. Steve and Alex
hold its item and use it, sneak or throw as a person does; then the world is asked with RCON
what it did: the item given, health of the husks it aimed at (and of a villager and a tamed
wolf, which must be spared), blocks carved or frozen, effects given. One live value is changed
with `vfg script set` and seen to take effect, and after `vfg script stop` nothing tagged
vfg_<instance> is left: no entity, no forceload, no item it gave. Big ones run small. It runs
only against a realmc test server, never the box; `up e2 --fresh` puts the world back after.
"""
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
    sys.exit("live_examples_powers: point it at a realmc server first: eval \"$(integration/realmc/env e2)\"")
os.environ["VFG_INSTANCE"] = "lexpowers"

import mc  # noqa: E402
import players as fake_players  # noqa: E402

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)

PLAYERS = fake_players.on()
PLAYERS.cmd = mc.rcon               # every command through mc, which holds the RCON lock the scripts hold
ARENA = PLAYERS.arena()
X, Y, Z = int(ARENA["x"]), int(ARENA["y"]), int(ARENA["z"])     # a player stands at X Y Z; the floor is Y-1
TAG = "lexp"                                                    # what this test summons


def cmd(c):
    return mc.rcon(c)


def vfg(*args):
    """`vfg ...` against this server; its output. Fails the test on a non-zero exit."""
    p = subprocess.run(["vfg"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode:
        raise AssertionError("vfg %s exited %d:\n%s" % (" ".join(args), p.returncode, out))
    return out


def logs(instance, n=80):
    return vfg("script", "logs", instance, "-n", str(n))


def until(test, seconds=10.0, every=0.2):
    end = time.time() + seconds
    got = test()
    while not got and time.time() < end:
        time.sleep(every)
        got = test()
    return got


def passed(c):
    return "passed" in cmd(c).lower()


def air(x, y, z):
    return passed("execute if block %d %d %d air" % (x, y, z))


def block(x, y, z, b):
    return passed("execute if block %d %d %d %s" % (x, y, z, b))


def health(name):
    """A summoned target's health (tag lexp_<name>), or a player's; None when it is gone."""
    who = name if name in ("Steve", "Alex") else "@e[tag=%s_%s,limit=1]" % (TAG, name)
    m = re.search(r"(-?[\d.]+)f\s*$", cmd("data get entity %s Health" % who).strip())
    return float(m.group(1)) if m else None


def target(name, x, y, z, mob="husk", hp=100):
    """A target that stands still: a husk (it does not burn at noon) with hp health, or a villager."""
    attrs = 'attributes:[{id:"max_health",base:%d}],Health:%df,' % (hp, hp) if hp else ""
    cmd('summon %s %.1f %.1f %.1f {NoAI:1b,PersistenceRequired:1b,Silent:1b,%sTags:["%s","%s_%s"]}'
        % (mob, x, y, z, attrs, TAG, TAG, name))


def tamed_wolf(name, x, y, z, owner="Steve"):
    target(name, x, y, z, mob="wolf", hp=0)
    cmd("data modify entity @e[tag=%s_%s,limit=1] Owner set from entity %s UUID" % (TAG, name, owner))
    assert passed("execute if data entity @e[tag=%s_%s,limit=1] Owner" % (TAG, name)), "the wolf is not tamed"


def has_item(p, item_id, where="container.*"):
    return passed('execute if items entity %s %s *[custom_data~{vfg_item:"%s"}]' % (p, where, item_id))


def holds_item(p, item_id):
    return has_item(p, item_id, "weapon.mainhand")


def effect(p, name):
    """True if a player or a target (by name) has an effect now."""
    who = p if p in ("Steve", "Alex") else "@e[tag=%s_%s,limit=1]" % (TAG, p)
    return '"minecraft:%s"' % name in cmd("data get entity %s active_effects" % who)


def amplifier(p, name):
    """The amplifier of an effect a player has now (0 is level I; 26.3 leaves amplifier: 0b out),
    or None when they do not have it."""
    for e in re.findall(r"\{[^{}]*\}", cmd("data get entity %s active_effects" % p)):
        if '"minecraft:%s"' % name in e:
            m = re.search(r"amplifier: (-?\d+)b", e)
            return int(m.group(1)) if m else 0
    return None


def gravity(p):
    """A player's gravity attribute: 0.08 normally, 0.0 while flight holds them up."""
    return float(cmd("attribute %s minecraft:gravity get" % p).split(" is ")[-1])


def display(instance):
    """The block a display of an instance shows, or ""."""
    return cmd("execute as @e[tag=vfg_%s,type=block_display,limit=1] run data get entity @s block_state" % instance)


def forceloads():
    return set(re.findall(r"\[(-?\d+), (-?\d+)\]", cmd("forceload query")))


def tagged(instance):
    """How many entities are tagged vfg_<instance> (or with a tag of its own, vfg_<instance>_...)."""
    m = re.search(r"Count: (\d+)", cmd("execute if entity @e[tag=vfg_%s]" % instance))
    return int(m.group(1)) if m else 0


def fill(x1, y1, z1, x2, y2, z2, b):
    return cmd("fill %d %d %d %d %d %d %s" % (x1, y1, z1, x2, y2, z2, b))


def lane():
    """Flat ground for a test, the same every time: grass on dirt from X-16 to X+40 along x and
    Z-8 to Z+8, with 14 blocks of air over it."""
    fill(X - 16, Y, Z - 8, X + 40, Y + 13, Z + 8, "air")
    fill(X - 16, Y - 1, Z - 8, X + 40, Y - 1, Z + 8, "grass_block")
    fill(X - 16, Y - 7, Z - 8, X + 40, Y - 2, Z + 8, "dirt")


class Powers(unittest.TestCase):
    instances = ()

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
        cmd("kill @e[tag=%s]" % TAG)
        cmd("kill @e[type=trident]")
        cmd("kill @e[type=tnt]")
        lane()
        for p in ("Steve", "Alex"):
            if p in PLAYERS.online():
                PLAYERS.stop(p)
                PLAYERS.sneak(p, False)
        cmd("time set noon")
        cmd("effect clear @a")

    def tearDown(self):
        for inst in self.started:
            subprocess.run(["vfg", "script", "stop", inst], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for p in ("Steve", "Alex"):
            if p in PLAYERS.online():
                PLAYERS.stop(p)
                PLAYERS.sneak(p, False)
        cmd("kill @e[tag=%s]" % TAG)
        cmd("kill @e[type=item,x=%d,y=%d,z=%d,distance=..200]" % (X, Y, Z))
        cmd("kill @e[type=experience_orb,x=%d,y=%d,z=%d,distance=..200]" % (X, Y, Z))

    # -- helpers
    def player(self, name, at, yaw, pitch=0.0, gamemode="survival"):
        """A fake player at a place, facing (yaw, pitch), with an empty inventory and full health."""
        PLAYERS.spawn(name, at, facing=(yaw, pitch), gamemode=gamemode)
        PLAYERS.look(name, yaw, pitch)
        PLAYERS.clear(name)
        PLAYERS.hotbar(name, 1)
        cmd("effect clear %s" % name)
        cmd("effect give %s instant_health 1 5 true" % name)
        cmd("effect give %s saturation 1 10 true" % name)
        self.assertTrue(until(lambda: health(name) == 20.0, 3), "%s is not at full health" % name)
        return name

    def run_(self, script, instance, *values):
        out = vfg("script", "run", script, "as=" + instance, *values)
        self.started.append(instance)
        return out

    def stop_clean(self, instance, item_id=None, holders=()):
        """vfg script stop, then nothing tagged vfg_<instance> is left, no forceload of its own,
        and the item it gave is gone from everyone it gave it to."""
        vfg("script", "stop", instance)
        self.started.remove(instance)
        self.assertTrue(until(lambda: tagged(instance) == 0, 5), "entities tagged vfg_%s left" % instance)
        self.assertEqual(forceloads() - self.loaded, set(), "forceloads left by %s" % instance)
        for p in holders:
            self.assertFalse(has_item(p, item_id) or has_item(p, item_id, "weapon.*"),
                             "%s still has the %s it was given" % (p, item_id))

    def given(self, p, item_id):
        self.assertTrue(until(lambda: holds_item(p, item_id), 10), "%s was not given %s" % (p, item_id))


# ---------------------------------------------------------------------------------- the beams

class GalickGun(Powers):
    def test_beam_through_a_wall_hurts_what_it_hits_spares_pets_and_carves(self):
        """Steve faces +x along the arena: husks and a villager and a tamed wolf in the line, a
        stone wall it goes through, a stone block under its end where the crater goes."""
        steve = self.player("Steve", (X + 0.5, Y, Z + 0.5), -90)
        fill(X + 20, Y - 1, Z - 2, X + 20, Y + 3, Z + 2, "stone")
        fill(X + 26, Y - 6, Z - 4, X + 34, Y - 1, Z + 4, "stone")
        fill(X + 2, Y + 2, Z, X + 24, Y + 3, Z, "stone")       # along the path: the tunnel takes Y+2, not Y+3
        target("a", X + 8.5, Y, Z + 0.5)
        target("b", X + 14.5, Y, Z + 0.5)
        target("end", X + 30.5, Y, Z + 4.5)             # 4 off the line, inside the end blast
        target("off", X + 8.5, Y, Z + 6.5)              # 6 off the line: missed
        target("villager", X + 11.5, Y, Z + 0.5, mob="villager", hp=0)
        tamed_wolf("wolf", X + 5.5, Y, Z + 0.5)
        before = {n: health(n) for n in ("villager", "wolf")}
        self.run_("galick_gun", "gg-steve", "player=Steve", "length=30", "charge_seconds=1")
        self.given(steve, "galick_gun")
        PLAYERS.use(steve)
        self.assertTrue(until(lambda: health("a") < 100 and health("b") < 100, 12), "the beam hurt nothing")
        print("\n  husks a, b, end, off:", health("a"), health("b"), health("end"), health("off"))
        self.assertLess(health("a"), 50)                # 60 damage, a husk's armour takes a little
        self.assertEqual(health("off"), 100)
        self.assertEqual({n: health(n) for n in before}, before, "the villager or the wolf was hit")
        self.assertTrue(until(lambda: health("end") < 100, 5), "the end blast hurt nothing")
        self.assertTrue(until(lambda: air(X + 20, Y + 1, Z) and air(X + 20, Y, Z), 15), "no tunnel through the wall")
        self.assertTrue(block(X + 20, Y + 3, Z, "stone") and block(X + 20, Y - 1, Z, "stone"), "the tunnel is too wide")
        self.assertTrue(until(lambda: air(X + 30, Y - 3, Z), 20), "no crater at the end")       # carved after the tunnel
        self.assertEqual([x for x in range(X + 2, X + 25) if not air(x, Y + 2, Z)], [], "the tunnel along the path has gaps")
        self.assertEqual([x for x in range(X + 2, X + 23) if air(x, Y + 3, Z)], [], "the tunnel along the path is too tall")
        self.assertTrue(block(X + 34, Y - 6, Z + 4, "stone"), "the crater is too big")
        # a live value: less damage on the next shot
        a = health("a")
        vfg("script", "set", "gg-steve", "damage_amount=10")
        time.sleep(1.0)
        PLAYERS.use(steve)
        self.assertTrue(until(lambda: health("a") < a, 10), "the second shot missed")
        print("  after damage_amount=10:", a, "->", health("a"))
        self.assertGreater(health("a"), a - 12)
        self.assertIn("fired", logs("gg-steve"))
        self.stop_clean("gg-steve", "galick_gun", [steve])

    def test_two_players_fire_at_each_other_and_the_beams_clash(self):
        """One instance for everyone (player=@a), so each shot runs on its own; Steve and Alex
        face each other 22 blocks apart and fire; the beams clash and nobody is hit."""
        steve = self.player("Steve", (X - 10.5, Y, Z + 0.5), -90)
        alex = self.player("Alex", (X + 11.5, Y, Z + 0.5), 90)
        fill(X, Y, Z - 2, X, Y + 3, Z + 2, "glass")          # what a beam that was not stopped would carve
        self.run_("galick_gun", "gg-all", "beam_clash_seconds=3", "beam_clash_blast=0")
        self.given(steve, "galick_gun")
        self.given(alex, "galick_gun")
        PLAYERS.use(steve)
        time.sleep(0.5)
        PLAYERS.use(alex)
        self.assertTrue(until(lambda: "beam clash won by" in logs("gg-all"), 20), logs("gg-all"))
        out = logs("gg-all")
        print("\n" + "\n".join(l for l in out.splitlines() if "clash" in l))
        self.assertEqual(out.count("beam clash:"), 1)
        self.assertNotIn(" fired: ", out)                # neither beam hit anything: the clash was the shot
        self.assertEqual((health("Steve"), health("Alex")), (20.0, 20.0))
        self.assertTrue(block(X, Y + 1, Z, "glass"), "a beam carved through")
        self.stop_clean("gg-all", "galick_gun", [steve, alex])


class Themes(Powers):
    def test_every_note_of_their_themes_is_a_sound_26_3_plays(self):
        """Each tune these examples play (their theme_tune, and the Spirit Bomb's throw), note
        by note, sent the way fx.melody sends it near a player: the server must play each one."""
        import ast
        steve = self.player("Steve", (X + 0.5, Y, Z + 0.5), -90)
        tunes = {}
        for name in ("galick_gun", "hollow_purple", "spirit_bomb", "ice_beam", "super_saiyan"):
            tree = ast.parse(open(os.path.join(os.path.dirname(HERE), name, "main.py")).read())
            body = {n.targets[0].id: n.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
            values, all_tunes = ast.literal_eval(body["VALUES"]), ast.literal_eval(body["_THEME_MUSIC_TUNES"])
            for t in [values["theme_tune"]["default"]] + (["spirit_bomb_throw"] if name == "spirit_bomb" else []):
                tunes[t] = all_tunes[t]
        refused = []
        for tune, notes in sorted(tunes.items()):
            for _, inst, note, vol in notes:
                sid = inst if "." in inst else "block.note_block." + inst
                reply = cmd("execute at %s run playsound minecraft:%s master @a[distance=..48] ~ ~ ~ %g %.3f"
                            % (steve, sid, vol, max(0.5, min(2.0, 2 ** ((note - 12) / 12.0)))))
                if not reply.startswith("Played sound"):
                    refused.append((tune, sid, reply[:80]))
        print("\n  tunes:", ", ".join("%s (%d notes)" % (t, len(n)) for t, n in sorted(tunes.items())))
        self.assertEqual(refused, [])


class HollowPurple(Powers):
    def test_kills_along_the_beam_and_round_its_end_spares_pets_and_erases_a_tunnel(self):
        """Steve faces +x: husks, a villager and a tamed wolf in the line, Alex in the line too,
        a stone wall, and a husk beside where the beam ends. Mobs only first; then, changed
        live, everything, and Alex dies."""
        steve = self.player("Steve", (X + 0.5, Y, Z + 0.5), -90)
        alex = self.player("Alex", (X + 18.5, Y, Z + 0.5), 90)
        fill(X + 24, Y - 1, Z - 3, X + 24, Y + 3, Z + 3, "stone")
        fill(X + 3, Y + 2, Z, X + 27, Y + 3, Z, "stone")       # along the path: crater_tunnel=1 takes Y+2, not Y+3
        target("a", X + 8.5, Y, Z + 0.5)
        target("b", X + 14.5, Y, Z + 0.5)
        target("end", X + 30.5, Y, Z + 3.5)             # 3 off the line, inside the blast round the end
        target("off", X + 8.5, Y, Z + 6.5)
        target("villager", X + 11.5, Y, Z + 0.5, mob="villager", hp=0)
        tamed_wolf("wolf", X + 5.5, Y, Z + 0.5)
        before = {n: health(n) for n in ("villager", "wolf")}
        for n in (1, 2, 3):                 # as an earlier run leaves on a player it killed mid-shot
            cmd("tag Alex add vfg_hp-steve_hit%d" % n)
        self.run_("hollow_purple", "hp-steve", "player=Steve", "length=30", "width=2", "around_radius=4",
                  "around_what=mobs", "crater_radius=3", "crater_tunnel=1")
        self.given(steve, "hollow_purple")                  # the run's never gave it
        PLAYERS.use(steve)
        self.assertTrue(until(lambda: health("a") is None and health("b") is None, 10), "the beam killed nothing")
        self.assertTrue(until(lambda: health("end") is None, 5), "the blast round its end killed nothing")
        self.assertEqual(health("off"), 100)
        self.assertEqual({n: health(n) for n in before}, before, "the villager or the wolf was hit")
        self.assertEqual(health(alex), 20.0, "mobs only, and Alex was hit")
        self.assertTrue(until(lambda: air(X + 24, Y + 1, Z) and air(X + 24, Y, Z), 15), "no tunnel through the wall")
        self.assertTrue(block(X + 24, Y + 3, Z, "stone"), "the tunnel is too wide")
        self.assertTrue(until(lambda: air(X + 30, Y - 1, Z), 15), "no sphere erased at its end")  # carved after the tunnel
        self.assertEqual([x for x in range(X + 4, X + 28) if not air(x, Y + 2, Z)], [], "the tunnel along the path has gaps")
        self.assertEqual([x for x in range(X + 4, X + 26) if air(x, Y + 3, Z)], [], "the tunnel along the path is too tall")
        vfg("script", "set", "hp-steve", "around_what=everything")
        time.sleep(1.0)
        PLAYERS.use(steve)
        self.assertTrue(until(lambda: alex not in PLAYERS.online(), 8), "everything, and Alex was not hit")
        print("\n  " + "\n  ".join(l for l in logs("hp-steve").splitlines() if "fired" in l))
        self.stop_clean("hp-steve", "hollow_purple", [steve])

    def test_a_hollow_purple_and_a_galick_gun_fired_at_each_other_clash(self):
        """Two scripts, two instances: Steve's Hollow Purple and Alex's Galick Gun, fired at once."""
        steve = self.player("Steve", (X - 10.5, Y, Z + 0.5), -90)
        alex = self.player("Alex", (X + 11.5, Y, Z + 0.5), 90)
        fill(X, Y, Z - 2, X, Y + 3, Z + 2, "glass")
        self.run_("hollow_purple", "hp-clash", "player=Steve", "length=40", "beam_clash_seconds=3", "beam_clash_blast=0")
        self.run_("galick_gun", "gg-clash", "player=Alex", "length=40", "beam_clash_seconds=3", "beam_clash_blast=0")
        self.given(steve, "hollow_purple")
        self.given(alex, "galick_gun")
        PLAYERS.use(steve)
        PLAYERS.use(alex)
        both = lambda: logs("hp-clash") + logs("gg-clash")
        self.assertTrue(until(lambda: "beam clash won by" in both(), 20), both())
        out = both()
        print("\n" + "\n".join(l for l in out.splitlines() if "clash" in l and "---" not in l))
        self.assertEqual(out.count("beam clash:"), 1)
        self.assertNotIn(" fired: ", out)
        self.assertEqual((health(steve), health(alex)), (20.0, 20.0))
        self.assertTrue(block(X, Y + 1, Z, "glass"), "a beam carved through")
        self.stop_clean("hp-clash", "hollow_purple", [steve])
        self.stop_clean("gg-clash", "galick_gun", [alex])


class SpiritBomb(Powers):
    def test_held_it_gathers_overhead_then_flies_blows_up_and_carves(self):
        """Steve holds right-click facing a stone pillar 15 out: the bomb gathers, flies to the
        pillar and blows up; husks round it are hurt, the villager and the wolf are not, nor is
        Steve; a crater is carved. Then, with a cooldown set live, holding again does nothing."""
        steve = self.player("Steve", (X + 0.5, Y, Z + 0.5), -90)
        fill(X + 15, Y, Z - 1, X + 15, Y + 4, Z + 1, "stone")
        target("a", X + 12.5, Y, Z + 3.5)               # the pillar's face is at X+15: 4 to 5 from the blast
        target("b", X + 11.5, Y, Z - 1.5)
        target("villager", X + 11.5, Y, Z - 3.5, mob="villager", hp=0)
        tamed_wolf("wolf", X + 12.5, Y, Z - 1.5)
        before = {n: health(n) for n in ("villager", "wolf")}
        self.run_("spirit_bomb", "sb-steve", "player=Steve", "charge=0.5", "charge_seconds=1.5", "crater_radius=4",
                  "explosion_power=3", "explosion_layout=one", "reach=40")
        self.given(steve, "spirit_bomb")
        PLAYERS.hold(steve)
        time.sleep(1.2)
        PLAYERS.stop(steve)
        self.assertTrue(until(lambda: "threw it" in logs("sb-steve"), 10), logs("sb-steve"))
        self.assertTrue(until(lambda: health("a") < 100 and health("b") < 100, 5), "the blast hurt nothing")
        print("\n  husks a, b:", health("a"), health("b"), " Steve:", health(steve))
        self.assertEqual({n: health(n) for n in before}, before, "the villager or the wolf was hit")
        self.assertEqual(health(steve), 20.0, "it hurt the one who threw it")
        self.assertTrue(until(lambda: air(X + 15, Y + 1, Z) and air(X + 14, Y - 2, Z), 20), "no crater")
        self.assertTrue(block(X + 15, Y - 6, Z, "dirt"), "the crater is too deep")
        vfg("script", "set", "sb-steve", "cooldown=60")
        PLAYERS.hold(steve)
        time.sleep(2.5)
        PLAYERS.stop(steve)
        time.sleep(2.5)
        self.assertEqual(logs("sb-steve").count("threw it"), 1, "a second bomb within the cooldown")
        self.stop_clean("sb-steve", "spirit_bomb", [steve])


class IceBeam(Powers):
    def test_freezes_and_blinds_what_it_hits_ices_a_tunnel_and_lays_a_path(self):
        """Steve faces +x: a husk, Alex, a villager and a tamed wolf in the line; a stone wall, a
        water block beside the line and a hole in the floor under it."""
        steve = self.player("Steve", (X + 0.5, Y, Z + 0.5), -90)
        alex = self.player("Alex", (X + 14.5, Y, Z + 0.5), 90)
        fill(X + 16, Y - 1, Z - 3, X + 16, Y + 4, Z + 3, "stone")
        fill(X + 18, Y, Z + 2, X + 18, Y, Z + 2, "water")
        fill(X + 6, Y - 3, Z - 1, X + 7, Y - 1, Z + 1, "air")
        fill(X + 2, Y + 2, Z, X + 15, Y + 3, Z, "stone")       # along the path: Y+2 is the tunnel, Y+3 its ice
        target("a", X + 8.5, Y, Z + 0.5)
        target("villager", X + 11.5, Y, Z + 0.5, mob="villager", hp=0)
        tamed_wolf("wolf", X + 4.5, Y, Z + 0.5)
        self.run_("ice_beam", "ib-steve", "player=Steve", "length=20", "cooldown=1")
        self.given(steve, "ice_beam_staff")
        PLAYERS.use(steve)
        self.assertTrue(until(lambda: effect("a", "slowness") and effect("a", "blindness"), 8), "the husk was not frozen")
        self.assertTrue(effect(alex, "slowness") and effect(alex, "blindness"), "Alex was not frozen")
        self.assertFalse(effect("villager", "slowness") or effect("wolf", "slowness"), "a villager or the wolf was frozen")
        self.assertEqual(health("a"), 100, "it hurt the husk: the ice beam does no damage")
        self.assertTrue(until(lambda: air(X + 16, Y + 1, Z) and air(X + 16, Y, Z), 10), "no tunnel")
        self.assertTrue(until(lambda: block(X + 16, Y + 3, Z, "packed_ice"), 5), "no ice round the tunnel")
        self.assertTrue(block(X + 16, Y + 4, Z, "stone"), "too much ice")
        self.assertTrue(block(X + 18, Y, Z + 2, "packed_ice"), "the water did not freeze")
        self.assertTrue(until(lambda: block(X + 6, Y - 1, Z, "packed_ice"), 5), "no ice path over the hole")
        self.assertTrue(air(X + 6, Y - 2, Z), "the path is too deep")
        along = range(X + 3, X + 15)
        self.assertEqual([x for x in along if not air(x, Y + 2, Z)], [], "the tunnel along the path has gaps")
        self.assertEqual([x for x in along if not block(x, Y + 3, Z, "packed_ice")], [], "no ice along the path")
        self.assertEqual([x for x in along if x not in (X + 6, X + 7) and not block(x, Y - 1, Z, "packed_ice")], [],
                         "the floor along the path is not ice")
        print("\n  " + "\n  ".join(l for l in logs("ib-steve").splitlines() if "fired" in l))
        vfg("script", "set", "ib-steve", "freeze_blindness=false")
        target("c", X + 9.5, Y, Z + 0.5)
        time.sleep(1.5)
        PLAYERS.use(steve)
        self.assertTrue(until(lambda: effect("c", "slowness"), 8), "the second shot froze nothing")
        self.assertFalse(effect("c", "blindness"), "freeze_blindness=false, and it was blinded")
        self.stop_clean("ib-steve", "ice_beam_staff", [steve])


class SuperSaiyan(Powers):
    def test_three_taps_power_up_fly_and_power_down_and_a_plain_carrot_does_nothing(self):
        steve = self.player("Steve", (X + 0.5, Y, Z + 0.5), -90)
        self.run_("super_saiyan", "ss-steve", "player=Steve", "charge_seconds=1", "seconds=30")
        self.assertTrue(until(lambda: has_item(steve, "super_saiyan"), 10), "no star given")
        # the run's bug: any carrot on a stick set it off
        PLAYERS.give(steve, "carrot_on_a_stick")
        PLAYERS.hotbar(steve, 2)
        self.assertTrue(passed("execute if items entity Steve weapon.mainhand minecraft:carrot_on_a_stick[!custom_data]"))
        PLAYERS.use(steve)
        time.sleep(3.0)
        self.assertFalse(effect(steve, "speed"), "a plain carrot on a stick powered him up")
        PLAYERS.hotbar(steve, 1)
        for _ in range(3):
            PLAYERS.sneak(steve)
            time.sleep(0.25)
            PLAYERS.sneak(steve, False)
            time.sleep(0.25)
        self.assertTrue(until(lambda: effect(steve, "speed") and effect(steve, "strength"), 6), "three taps did nothing")
        self.assertEqual(gravity(steve), 0.0, "no flight")
        y0 = PLAYERS.pos(steve)[1]
        PLAYERS.look(steve, -90, -60)
        self.assertTrue(until(lambda: PLAYERS.pos(steve)[1] > y0 + 2, 5), "looking up did not lift him")
        print("\n  rose from %.1f to %.1f" % (y0, PLAYERS.pos(steve)[1]))
        PLAYERS.look(steve, -90, 0)
        self.assertEqual(amplifier(steve, "speed"), 2, "Blue is Speed III")
        vfg("script", "set", "ss-steve", "level=1")
        self.assertTrue(until(lambda: amplifier(steve, "speed") == 0, 8), "level=1 did not take")
        for _ in range(3):
            PLAYERS.sneak(steve)
            time.sleep(0.25)
            PLAYERS.sneak(steve, False)
            time.sleep(0.25)
        self.assertTrue(until(lambda: not effect(steve, "speed"), 6), "three taps again did not power down")
        self.assertTrue(effect(steve, "slow_falling"), "no soft landing")
        self.assertEqual(gravity(steve), 0.08)
        # the star powers up too
        PLAYERS.use(steve)
        self.assertTrue(until(lambda: effect(steve, "speed"), 6), "the star did nothing")
        self.stop_clean("ss-steve", "super_saiyan", [steve])
        self.assertFalse(effect(steve, "speed"), "a stop left him powered up")
        self.assertEqual(gravity(steve), 0.08, "a stop left him floating")


class MeteorStaff(Powers):
    def test_a_meteor_falls_where_he_looks_and_explodes_sparing_him_and_pets(self):
        steve = self.player("Steve", (X + 0.5, Y, Z + 0.5), -90)
        fill(X + 12, Y, Z - 1, X + 12, Y + 3, Z + 1, "stone")
        target("a", X + 13.5, Y, Z + 2.5)
        target("villager", X + 10.5, Y, Z + 2.5, mob="villager", hp=0)
        tamed_wolf("wolf", X + 10.5, Y, Z - 1.5)
        before = {n: health(n) for n in ("villager", "wolf")}
        self.run_("meteor_staff", "ms-steve", "player=Steve", "meteor_seconds=1.5", "meteor_height=25",
                  "explosion_power=3", "cooldown=1")
        self.given(steve, "meteor_staff")
        PLAYERS.use(steve)
        self.assertTrue(until(lambda: "magma_block" in display("ms-steve"), 4), "no meteor in the sky: " + display("ms-steve"))
        self.assertTrue(until(lambda: "landed" in logs("ms-steve"), 8), logs("ms-steve"))
        self.assertTrue(until(lambda: tagged("ms-steve") == 0, 4), "the meteor's blocks stayed")
        self.assertTrue(until(lambda: health("a") is None or health("a") < 100, 5), "the blast hurt nothing")
        self.assertEqual({n: health(n) for n in before}, before, "the villager or the wolf was hit")
        self.assertEqual(health(steve), 20.0)
        self.assertTrue(air(X + 11, Y - 1, Z) or air(X + 11, Y - 2, Z), "no crater where it landed")
        print("\n  " + "\n  ".join(l for l in logs("ms-steve").splitlines() if "landed" in l))
        vfg("script", "set", "ms-steve", "meteor_blocks=obsidian")
        time.sleep(1.0)
        PLAYERS.look(steve, -90, 15)
        PLAYERS.use(steve)
        self.assertTrue(until(lambda: "obsidian" in display("ms-steve"), 4), "meteor_blocks=obsidian did not take")
        self.assertTrue(until(lambda: logs("ms-steve").count("landed") == 2, 8))
        self.stop_clean("ms-steve", "meteor_staff", [steve])


class BoomTrident(Powers):
    def test_a_thrown_trident_explodes_where_it_lands_then_with_a_crater(self):
        steve = self.player("Steve", (X + 0.5, Y, Z + 0.5), -90, -5)
        fill(X + 12, Y, Z - 3, X + 12, Y + 6, Z + 3, "stone")
        target("a", X + 10.5, Y, Z + 2.5)
        tamed_wolf("wolf", X + 10.5, Y, Z - 2.5)
        before = health("wolf")
        self.run_("boom_trident", "bt-steve", "player=Steve", "explosion_power=3")
        self.assertTrue(until(lambda: has_item(steve, "boom_trident"), 6), "no trident given")
        self.assertTrue(holds_item(steve, "boom_trident"))
        PLAYERS.use(steve, "continuous")
        time.sleep(1.0)
        PLAYERS.stop(steve)
        self.assertTrue(until(lambda: "went off" in logs("bt-steve"), 8), logs("bt-steve"))
        self.assertTrue(until(lambda: health("a") is None or health("a") < 100, 4), "the blast hurt nothing")
        self.assertEqual(health("wolf"), before, "the wolf was hit")
        self.assertEqual(health(steve), 20.0)
        holes = [(y, z) for y in range(Y, Y + 7) for z in range(Z - 3, Z + 4) if air(X + 12, y, z)]
        self.assertTrue(holes, "the wall is whole")
        print("\n  wall holes:", len(holes), "|", [l for l in logs("bt-steve").splitlines() if "went off" in l])
        vfg("script", "set", "bt-steve", "crater=true", "crater_radius=3")
        self.assertTrue(until(lambda: holds_item(steve, "boom_trident"), 10), "the trident did not come back")
        PLAYERS.use(steve, "continuous")
        time.sleep(1.0)
        PLAYERS.stop(steve)
        self.assertTrue(until(lambda: "crater r=3" in logs("bt-steve"), 15), logs("bt-steve"))
        self.stop_clean("bt-steve", "boom_trident", [steve])


if __name__ == "__main__":
    unittest.main(verbosity=2)
