"""Tests for the launch preset, and push throwing a player through it, against the fake RCON
server and fake_world, with pydantic for a preset's values class:

    uv run --no-project --python 3.12 --with pydantic python scripts/_test/test_presets_launch.py

The flight model first: it gives back what was measured on a real 26.3 server (a Carpet test
player, a husk, an armor stand; live_presets_launch.py measures them again). Then the preset is
loaded from scripts/_presets/ as it is and run in this process against a fresh fake world
(test_kit's KitCase, with what launch sends besides: OnGround, a block's id, wind charges staged
and sent down, Motion, attribute modifiers, effects, ride and an nbt selector): the charges it
stages and where, the stand it rides, the fall guard it puts on and takes off as they land, and
its cleanup. Last, the launch_pad example run as on EC2 on a slime block, and stopped.
"""
import importlib.util
import math
import os
import re
import secrets
import signal
import sys
import tempfile
import threading
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
PRESETS = os.path.join(SCRIPTS, "_presets")
sys.path.append(os.path.dirname(SCRIPTS))  # the repo: a preset's values class imports actions._types
sys.path.insert(0, HERE)

import test_kit  # noqa: E402
from test_kit import KitCase, start_script, until, TMP  # noqa: E402
from fake_rcon import FakeRcon  # noqa: E402
from fake_world import World, OVERWORLD, Fail, _split_opts, _token  # noqa: E402
import instances  # noqa: E402
import mc  # noqa: E402
from kit import aim, values, world  # noqa: E402


def load(name):
    """The preset as a module, from its file."""
    spec = importlib.util.spec_from_file_location("preset_" + name, os.path.join(PRESETS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = load("launch")
PUSH = load("push")
PUSH.launch = L.launch          # a script holds both, side by side


def vals(*presets, **given):
    """The values of a script holding these presets: their defaults, then these."""
    return values(meta=test_kit.values_meta(*(presets or (L,)), given=given), run_dir=tempfile.mkdtemp(dir=TMP),
                  instance="t")


_NUMS = re.compile(r"-?\d+(?:\.\d+)?")


class LaunchWorld(World):
    """fake_world, and what launch sends besides. Each entity has "ground" (OnGround, True
    unless set), "motion", "mods" (modifier ids on fall_damage_multiplier), "effects", a
    "vehicle" and "scores" (a score stored from `time query gametime`, which answers
    `gametime`; a selector's scores={obj=..N} picks by it); `ids` gives a block an id for
    "if block ... <id>" (slime_block, ice); a wind charge sent down bursts at once: it is gone
    and its x, z is kept in `bursts`."""

    def __init__(self):
        World.__init__(self)
        self.ids = {}
        self.bursts = []
        self.gametime = 100000

    def _run(self, cmd, ctx):
        m = re.match(r"^execute (.*?)store result score (\S+) (\S+) run time query gametime$", cmd.strip())
        if not m:
            return World._run(self, cmd, ctx)
        ctxs = self._execute(m.group(1), [ctx])[1] if m.group(1).strip() else [ctx]
        for c in ctxs:
            for e in self._select(m.group(2), c):
                e.setdefault("scores", {})[m.group(3)] = self.gametime
        return ""

    def _cmd_time(self, args, ctx):
        return "The game time is %d tick(s)" % self.gametime if args.strip() == "query gametime" else ""

    def respond(self, cmd):
        cmd = cmd.replace(" anchored eyes positioned ^ ^ ^ ", " positioned ~ ~1.62 ~ ")
        m = re.match(r"^execute (?:in \S+ )?if block (-?\d+) (-?\d+) (-?\d+) (#?[a-z_:]+)$", cmd)
        if m and not m.group(4).endswith("replaceable"):
            with self.lock:
                self.commands.append(cmd)
                got = self.ids.get((int(m.group(1)), int(m.group(2)), int(m.group(3))), "grass_block")
                want = m.group(4).replace("minecraft:", "")
                ok = got == want or (want == "#ice" and got in ("ice", "packed_ice", "blue_ice"))
                return "Test passed" if ok else "Test failed"
        return World.respond(self, cmd)

    def _select(self, sel, ctx):
        m = re.match(r"^(@[aeprs])\[(.*)\]$", sel)
        if not m or ("nbt=" not in m.group(2) and "scores=" not in m.group(2)):
            return World._select(self, sel, ctx)
        opts = _split_opts(m.group(2))
        rest = [o for o in opts if not o.startswith(("nbt=", "scores="))]
        es = World._select(self, "%s[%s]" % (m.group(1), ",".join(rest)) if rest else m.group(1), ctx)
        for o in opts:
            if o.startswith("nbt="):
                es = [e for e in es if e.get("ground", True) == ("OnGround:1b" in o)]
            s = re.match(r"scores=\{(\w+)=\.\.(-?\d+)\}$", o)
            if s:
                es = [e for e in es if e.get("scores", {}).get(s.group(1), float("inf")) <= int(s.group(2))]
        return es

    def _cmd_data(self, args, ctx):
        m = re.match(r"get entity (\S+) OnGround$", args)
        if m:
            es = self._select(m.group(1), ctx)
            if not es:
                raise Fail("No entity was found")
            e = es[0]
            return "%s has the following entity data: %s" % (e["name"], "1b" if e.get("ground", True) else "0b")
        m = re.match(r"(modify|merge) entity (\S+) (.*)$", args)
        if not m:
            return World._cmd_data(self, args, ctx)
        es = self._select(m.group(2), ctx)
        if not es:
            raise Fail("No entity was found")
        e = es[0]
        if e["type"] == "player":
            raise Fail("Unable to modify player data")
        e["motion"] = tuple(float(x) for x in _NUMS.findall(m.group(3).split("Motion", 1)[1])[:3])
        if e["type"] == "wind_charge" and e["motion"][1] < 0:
            self.bursts.append((e["pos"][0], e["pos"][1], e["pos"][2]))
            self.entities.remove(e)
        return "Modified entity data of %s" % e["name"]

    def _cmd_attribute(self, args, ctx):
        m = re.match(r"(\S+) minecraft:fall_damage_multiplier (?:modifier (add|remove) (\S+)|get)", args)
        es = self._select(m.group(1), ctx)
        if not es:
            raise Fail("No entity was found")
        e = es[0]
        mods = e.setdefault("mods", set())
        if m.group(2) == "add":
            if m.group(3) in mods:
                raise Fail("Entity %s already has modifier %s" % (e["name"], m.group(3)))
            mods.add(m.group(3))
            return "Added modifier %s to attribute Fall Damage Multiplier for entity %s" % (m.group(3), e["name"])
        if m.group(2) == "remove":
            if m.group(3) not in mods:
                raise Fail("Entity %s has no modifier %s" % (e["name"], m.group(3)))
            mods.discard(m.group(3))
            return "Removed modifier %s from attribute Fall Damage Multiplier for entity %s" % (m.group(3), e["name"])
        return "The value of attribute Fall Damage Multiplier for entity %s is %s" % (e["name"], 0.0 if mods else 1.0)

    def _cmd_effect(self, args, ctx):
        m = re.match(r"give (\S+) (\S+) (\d+) (\d+)", args)
        es = self._select(m.group(1), ctx) if m else []
        if not es:
            raise Fail("No entity was found")
        for e in es:
            e.setdefault("effects", {})[m.group(2).replace("minecraft:", "")] = (int(m.group(3)), int(m.group(4)))
        return "Applied effect %s to %s" % (m.group(2), es[0]["name"] if len(es) == 1 else "%d targets" % len(es))

    def _cmd_ride(self, args, ctx):
        who, rest = _token(args)
        op, rest = _token(rest)
        e = self._select(who, ctx)[0]
        if op == "mount":
            es = self._select(rest.strip(), ctx)
            if not es:
                raise Fail("No entity was found")
            e["vehicle"] = es[0]
            return "%s started riding %s" % (e["name"], es[0]["name"])
        if not e.get("vehicle"):
            raise Fail("%s is not riding any vehicle" % e["name"])
        return "%s stopped riding %s" % (e["name"], e.pop("vehicle")["name"])


class LaunchCase(KitCase):
    """KitCase in a LaunchWorld: Steve at 0.5 64 0.5 on the ground, facing east (+x)."""

    def setUp(self):
        KitCase.setUp(self)
        type(self).current = self.world = LaunchWorld()
        self.steve = self.world.add_player("Steve", (0.5, 64.0, 0.5), rot=(-90.0, 0.0))
        L._LAUNCH.update(n=0, busy=0, rides={}, until=0.0, thread=None, cleanup=False)
        L._LAUNCH["idle"].set()

    def tearDown(self):
        mc._stop["thread"] = threading.get_ident()
        mc._stopping.set()
        th = L._LAUNCH["thread"]
        if th is not None:
            th.join(3)
        KitCase.tearDown(self)

    def tags(self, e):
        return sorted(t for t in e["tags"] if t.startswith(mc.TAG))

    def land(self, *es):
        """They leave the ground (the watcher sees them in the air), then land."""
        for e in es:
            e["ground"] = False
        g, air, _, _ = L._launch_tags()
        until(lambda: all(air in e["tags"] for e in es), 3)
        for e in es:
            e["ground"] = True


# ---- the flight model, against flights measured on 26.3 ----------------------------------------

class FlightTests(unittest.TestCase):
    def test_it_gives_back_the_flights_measured_on_a_real_server(self):
        f = L._launch_flight
        # A wind charge let fall at a test player's feet: 6.08 blocks up; four at once: 71.15.
        a, u = L._launch_charge(0.0)
        self.assertAlmostEqual(f(0.0, u - L._LAUNCH_REST)[2], 6.08, delta=0.1)
        self.assertAlmostEqual(f(0.0, 4 * u - L._LAUNCH_REST)[2], 71.15, delta=0.5)
        # Four bursting 1.3 blocks behind him: (1.51, 1.51) a tick read back, 12.26 up, 10.37 along.
        a, u = L._launch_charge(1.3)
        self.assertAlmostEqual(4 * a, 1.51, delta=0.03)
        self.assertAlmostEqual(4 * u - L._LAUNCH_REST, 1.51, delta=0.03)
        along, _, top = f(4 * a, 4 * u - L._LAUNCH_REST)
        self.assertAlmostEqual(along, 10.37, delta=0.3)
        self.assertAlmostEqual(top, 12.26, delta=0.3)
        # Motion (1.2, 0.6): an armor stand carried him 10.96 along; a husk on the ground went 7.3.
        self.assertAlmostEqual(f(1.2, 0.6, L._LAUNCH_AIR)[0], 10.96, delta=0.3)
        self.assertAlmostEqual(f(1.2, 0.6)[0], 7.3, delta=0.4)
        # A stand given a Motion y of 2.0 rose 20.41 with him on it (he sits 0.39 above it).
        self.assertAlmostEqual(f(0.0, 2.0)[2] + 0.39, 20.41, delta=0.3)

    def test_a_plan_reaches_what_was_asked(self):
        for s in (2, 6, 12, 30, 64):
            n, o, ticks = L._launch_plan("up", s, L._LAUNCH_GROUND)
            a, u = L._launch_charge(o)
            self.assertGreaterEqual(n, 2)
            self.assertAlmostEqual(L._launch_flight(0.0, n * u - L._LAUNCH_REST)[2], s, delta=0.05, msg=s)
        for s in (3, 8, 15, 20):
            for first in (L._LAUNCH_GROUND, 0.728):
                n, o, ticks = L._launch_plan("forward", s, first)
                a, u = L._launch_charge(o)
                self.assertTrue(1.0 <= o <= 2.0 and 1 <= n <= L._LAUNCH_MOST, (s, n, o))
                self.assertAlmostEqual(L._launch_flight(n * a, n * u - L._LAUNCH_REST, first)[0], s, delta=0.05)
        for s in (5, 12):
            along, up = L._launch_speed("forward", s, L._LAUNCH_AIR)
            self.assertAlmostEqual(L._launch_flight(along, up, L._LAUNCH_AIR)[0], s, delta=0.05)
            self.assertAlmostEqual(up / along, L._LAUNCH_SLOPE)


# ---- a player: wind, a ride, the guard ------------------------------------------------------------

class WindTests(LaunchCase):
    def test_forward_stages_charges_behind_them_and_sends_them_down_in_one_command(self):
        self.assertEqual(L.launch("Steve", vals(), way="forward", strength=12), 1)
        staged = self.sent("summon wind_charge")
        n, o, _ = L._launch_plan("forward", 12, L._LAUNCH_GROUND)
        self.assertEqual(len(staged), n)
        for c in staged:
            x, y, z = (float(v) for v in c.split()[2:5])
            self.assertAlmostEqual(x, 0.5 - o, places=2)        # behind him: he faces +x
            self.assertAlmostEqual((y, z), (64.6, 0.5), places=2)
            self.assertNotIn("Motion", c)                        # staged still
        release = [c for c in self.sent("execute as @e[type=wind_charge") if "Motion set value [0.0d,-1.0d,0.0d]" in c]
        self.assertEqual(len(release), 1)
        self.assertEqual(len(self.world.bursts), n)
        self.assertEqual([e for e in self.world.entities if e["type"] == "wind_charge"], [])
        g = L._launch_tags()[0]
        self.assertEqual(self.tags(self.steve), [g])
        self.assertEqual(self.steve["mods"], {L._launch_tags()[3]})

    def test_up_is_a_ring_round_the_feet_and_slime_keeps_more_of_a_throw_along(self):
        L.launch("Steve", vals(), way="up", strength=10)
        xs = [tuple(float(v) for v in c.split()[2:5]) for c in self.sent("summon wind_charge")]
        self.assertGreaterEqual(len(xs), 2)
        self.assertAlmostEqual(sum(x for x, _, _ in xs) / len(xs), 0.5, places=3)
        self.assertAlmostEqual(sum(z for _, _, z in xs) / len(xs), 0.5, places=3)
        self.world.ids[(0, 63, 0)] = "slime_block"
        self.world.commands[:] = []
        self.steve["tags"].clear()
        self.steve["mods"].clear()
        L.launch("Steve", vals(), way="forward", strength=12)
        n, o, _ = L._launch_plan("forward", 12, 0.728)
        self.assertEqual(len(self.sent("summon wind_charge")), n)

    def test_away_and_toward_a_place_never_past_it(self):
        L.launch("Steve", vals(), way="away", strength=8, frm=world.Pos(-5.5, 64.0, 0.5))
        self.assertLess(float(self.sent("summon wind_charge")[0].split()[2]), 0.5)      # behind, from -x
        self.steve["tags"].clear()
        self.world.commands[:] = []
        L.launch("Steve", vals(), way="toward", strength=30, frm=world.Pos(-5.5, 64.0, 0.5))
        staged = self.sent("summon wind_charge")
        self.assertGreater(float(staged[0].split()[2]), 0.5)                          # behind, from +x
        n, o, _ = L._launch_plan("forward", 5.0, L._LAUNCH_GROUND)                     # 6 away: 5 at most
        self.assertEqual(len(staged), n)
        self.steve["tags"].clear()
        self.assertEqual(L.launch("Steve", vals(), way="toward", strength=30, frm=world.Pos(1.0, 64.0, 0.5)), 0)

    def test_safe_float_and_hurt(self):
        L.launch("Steve", vals(launch_landing="float"), way="up", strength=8)
        self.assertIn("slow_falling", self.steve["effects"])
        self.assertTrue(self.steve["mods"])
        alex = self.world.add_player("Alex", (5.5, 64.0, 5.5))
        L.launch("Alex", vals(launch_landing="hurt"), way="up", strength=8)
        self.assertEqual(alex.get("mods", set()), set())
        self.assertEqual(self.tags(alex), [L._launch_tags()[0]])                    # still watched

    def test_the_guard_comes_off_as_they_land_and_the_watcher_ends(self):
        alex = self.world.add_player("Alex", (5.5, 64.0, 5.5))
        self.assertEqual(L.launch(["Steve", "Alex", "Steve"], vals(), way="up", strength=8), 2)
        self.land(self.steve, alex)
        self.assertTrue(until(lambda: not self.steve.get("mods") and not alex.get("mods") and not self.tags(self.steve)
                              and not self.tags(alex), 3))
        self.assertTrue(L._LAUNCH["idle"].wait(3))
        self.assertIsNone(L._LAUNCH["thread"])

    def test_one_still_in_the_air_from_a_throw_is_left_to_fly(self):
        L.launch("Steve", vals(), way="up", strength=8)
        self.steve["ground"] = False
        before = len(self.sent("summon"))
        self.assertEqual(L.launch("Steve", vals(), way="up", strength=8), 0)
        self.assertEqual(len(self.sent("summon")), before)

    def test_wait_returns_once_they_have_landed(self):
        done = []
        th = threading.Thread(target=lambda: done.append(L.launch("Steve", vals(), way="up", strength=8, wait=True)))
        th.start()
        time.sleep(0.3)
        self.assertEqual(done, [])
        self.land(self.steve)
        th.join(3)
        self.assertEqual(done, [1])


class RideTests(LaunchCase):
    def stand(self):
        return [e for e in self.world.entities if e["type"] == "armor_stand"]

    def test_a_player_in_the_air_is_carried_and_set_down_where_the_stand_lands(self):
        self.steve["ground"] = False
        self.assertEqual(L.launch("Steve", vals(), way="forward", strength=10), 1)
        self.assertEqual(self.sent("summon wind_charge"), [])
        s = self.stand()[0]
        self.assertIs(self.steve["vehicle"], s)
        along, up = L._launch_speed("forward", 10, L._LAUNCH_AIR)
        self.assertEqual([round(c, 3) for c in s["motion"]], [round(along, 3), round(up, 3), 0.0])
        self.assertIn(mc.TAG, s["tags"])
        s["ground"] = False                     # it flies
        self.assertTrue(test_kit.looked(self.world, "OnGround", "_lr"))
        s["ground"] = True                      # and lands
        self.assertTrue(until(lambda: not self.stand() and "vehicle" not in self.steve, 3))
        self.assertIn("ride Steve dismount", self.world.commands)
        self.steve["ground"] = True
        self.assertTrue(until(lambda: not self.steve.get("mods"), 3))

    def test_ride_by_choice_and_when_there_is_no_open_ground_behind(self):
        L.launch("Steve", vals(launch_method="ride"), way="up", strength=10)
        self.assertEqual(len(self.stand()), 1)
        self.assertEqual(self.sent("summon wind_charge"), [])
        alex = self.world.add_player("Alex", (5.5, 64.0, 5.5), rot=(-90.0, 0.0))
        for y in (64, 65):
            for x in range(1, 5):
                self.world.solid.add((OVERWORLD, x, y, 5))
        L.launch("Alex", vals(), way="forward", strength=8)                       # a wall right behind him
        self.assertEqual(len(self.stand()), 2)
        self.assertIn("vehicle", alex)

    def test_a_head_in_a_block_is_set_down_at_once(self):
        L.launch("Steve", vals(launch_method="ride"), way="up", strength=10)
        self.world.solid.add((OVERWORLD, 0, 65, 0))                                 # his eyes are in it
        self.assertTrue(until(lambda: "vehicle" not in self.steve and not self.stand(), 3))


class MobAndCleanupTests(LaunchCase):
    def test_a_mob_is_thrown_by_its_motion_and_guarded(self):
        z = self.world.add_entity("zombie", (4.5, 64.0, 0.5))
        h = aim.Hit(4.0, world.Pos(4.5, 64.0, 0.5), "Zombie", False)
        self.assertEqual(L.launch([h, h], vals(), way="away", strength=10, frm=world.Pos(0.5, 64.0, 0.5)), 1)
        along, up = L._launch_speed("forward", 10, L._LAUNCH_GROUND)
        self.assertEqual([round(c, 3) for c in z["motion"]], [round(along, 3), round(up, 3), 0.0])
        self.assertEqual(z["mods"], {L._launch_tags()[3]})
        self.land(z)
        self.assertTrue(until(lambda: not z["mods"] and not self.tags(z), 3))

    def test_a_stop_sets_riders_down_and_softens_the_fall_of_who_is_in_the_air(self):
        alex = self.world.add_player("Alex", (5.5, 64.0, 5.5))
        L.launch("Steve", vals(launch_method="ride"), way="up", strength=10)
        L.launch("Alex", vals(), way="up", strength=10)
        alex["ground"] = False
        L._launch_cleanup()
        self.assertNotIn("vehicle", self.steve)
        self.assertEqual([e for e in self.world.entities if e["type"] == "armor_stand"], [])
        self.assertIn("slow_falling", alex["effects"])
        for p in (self.steve, alex):
            self.assertEqual((p.get("mods"), self.tags(p)), (set(), []))


# ---- a guard a dead copy left heals ---------------------------------------------------------------

class HealTests(LaunchCase):
    def test_a_guard_a_stand_or_a_charge_a_dead_copy_left_goes_at_the_next_throw(self):
        w, old, now = self.world, self.world.gametime - 100, self.world.gametime
        alex = w.add_player("Alex", (5.5, 64.0, 5.5))           # landed long ago, the copy that threw him dead
        bob = w.add_player("Bob", (9.5, 70.0, 9.5))             # still in the air
        carl = w.add_player("Carl", (12.5, 64.0, 12.5))         # thrown this moment, not off the ground yet
        for p, stamp in ((alex, old), (bob, old), (carl, now)):
            p.update(mods={instances.LAUNCH_MOD}, scores={instances.LAUNCH_SCORE: stamp})
        bob["ground"] = False
        stand = w.add_entity("armor_stand", (20.5, 64.0, 20.5), tags=(instances.LAUNCH_STAND,))
        stand["scores"] = {instances.LAUNCH_SCORE: old}
        charge = w.add_entity("wind_charge", (22.5, 64.6, 20.5), tags=(instances.LAUNCH_CHARGE,))
        charge["scores"] = {instances.LAUNCH_SCORE: old}
        fresh = w.add_entity("wind_charge", (24.5, 64.6, 20.5), tags=(instances.LAUNCH_CHARGE,))
        fresh["scores"] = {instances.LAUNCH_SCORE: now}
        self.assertEqual(L.launch("Steve", vals(), way="up", strength=8), 1)
        self.assertEqual(alex["mods"], set())
        self.assertEqual((bob["mods"], carl["mods"]), ({instances.LAUNCH_MOD}, {instances.LAUNCH_MOD}))
        self.assertNotIn(stand, w.entities)
        self.assertNotIn(charge, w.entities)
        self.assertIn(fresh, w.entities)
        self.assertEqual(self.steve["scores"][instances.LAUNCH_SCORE], now)      # stamped before the guard went on
        self.assertEqual(self.steve["mods"], {instances.LAUNCH_MOD})
        self.assertTrue(self.sent("scoreboard objectives add vfg_launch dummy"))

    def test_this_copys_own_tags_from_an_earlier_run_come_off_and_its_stands_are_stamped(self):
        alex = self.world.add_player("Alex", (5.5, 64.0, 5.5))
        alex["tags"].update(L._launch_tags()[:2])
        L.launch("Steve", vals(launch_method="ride"), way="up", strength=8)
        self.assertEqual(self.tags(alex), [])
        s = [e for e in self.world.entities if e["type"] == "armor_stand"][0]
        self.assertIn(instances.LAUNCH_STAND, s["tags"])
        self.assertEqual(s["scores"][instances.LAUNCH_SCORE], self.world.gametime)

    def test_the_sweep_after_a_dead_copy_sets_down_what_it_threw(self):
        g, air, down = instances.launch_tags("gone")
        alex = self.world.add_player("Alex", (5.5, 70.0, 5.5))            # in the air when it died
        alex.update(mods={instances.LAUNCH_MOD}, ground=False, scores={instances.LAUNCH_SCORE: self.world.gametime})
        alex["tags"].update((g, air))
        stand = self.world.add_entity("armor_stand", (9.5, 70.0, 9.5), tags=("vfg_gone_lr", instances.LAUNCH_STAND))
        instances.launch_sweep(mc.rcon, "gone")
        self.assertIn("slow_falling", alex["effects"])
        self.assertEqual((alex["mods"], sorted(alex["tags"])), (set(), []))
        self.assertNotIn(stand, self.world.entities)

    def test_the_cleanup_heals_too(self):
        alex = self.world.add_player("Alex", (5.5, 64.0, 5.5))
        alex.update(mods={instances.LAUNCH_MOD}, scores={instances.LAUNCH_SCORE: self.world.gametime - 100})
        L._launch_cleanup()
        self.assertEqual(alex["mods"], set())

# ---- push throws a player through launch ----------------------------------------------------------

class PushTests(LaunchCase):
    def test_push_launches_a_player_and_still_throws_a_mob(self):
        alex = self.world.add_player("Alex", (6.5, 64.0, 0.5))
        z = self.world.add_entity("zombie", (0.5, 64.0, 6.5))
        hits = [aim.Hit(6.0, world.Pos(6.5, 64.0, 0.5), "Alex", True), aim.Hit(6.0, world.Pos(0.5, 64.0, 6.5), "Zombie", False)]
        v = vals(L, PUSH, push_strength=8)
        self.assertEqual(PUSH.push(hits, "Steve", v), 2)
        staged = self.sent("summon wind_charge")
        n, o, _ = L._launch_plan("forward", 8, L._LAUNCH_GROUND)
        self.assertEqual(len(staged), n)
        self.assertAlmostEqual(float(staged[0].split()[2]), 6.5 - o, places=2)    # behind Alex, away from Steve
        self.assertEqual(alex["mods"], {L._launch_tags()[3]})
        self.assertEqual(alex["pos"], [6.5, 64.0, 0.5])                           # never teleported
        self.assertEqual([round(c, 2) for c in z["motion"]], [0.0, 0.4, 1.6])     # a mob: as before
        self.assertFalse(self.sent("tp Alex"))
        alex["tags"].clear()                    # he has landed
        alex["mods"].clear()
        self.world.commands[:] = []
        PUSH.push(hits[:1], "Steve", vals(L, PUSH, push_way="toward", push_strength=20))
        staged = self.sent("summon wind_charge")
        n, o, _ = L._launch_plan("forward", 6.0 - 1.5, L._LAUNCH_GROUND)            # never past Steve
        self.assertEqual(len(staged), n)
        self.assertAlmostEqual(float(staged[0].split()[2]), 6.5 + o, places=2)    # behind Alex, toward Steve


# ---- the launch_pad example, run as on EC2, stopped -----------------------------------------------

class ScriptTests(unittest.TestCase):
    def test_launch_pad_launches_whoever_stands_on_slime_and_leaves_nothing_after_a_stop(self):
        with open(os.path.join(SCRIPTS, "launch_pad", "main.py")) as f:
            text = f.read()
        w = LaunchWorld()
        steve = w.add_player("Steve", (0.5, 64.0, 0.5), rot=(-90.0, 0.0))
        alex = w.add_player("Alex", (8.5, 64.0, 8.5), rot=(0.0, 0.0))
        w.ids[(0, 63, 0)] = "slime_block"
        password = secrets.token_hex(8)
        fake = FakeRcon(password, responder=w.respond).start()
        meta = test_kit.values_meta("under_feet", L, given={"launch_way": "forward", "launch_strength": 12})
        meta["values"].update(player="@a", pad_block="slime_block", cooldown=2.0, at="", under_radius=0)
        try:
            proc = start_script("launchprobe", text, fake, password, run_dir=tempfile.mkdtemp(dir=TMP), vals=meta)
            self.assertTrue(until(lambda: w.bursts, 10), "\n".join(w.commands[-20:]))
            time.sleep(0.5)
            with w.lock:
                self.assertTrue(all(b[0] < 0.5 for b in w.bursts))                 # behind Steve, who faces +x
                self.assertIn("vfg_launchprobe_launch", steve["tags"])
                self.assertEqual(steve["mods"], {"vfg:launch"})
                self.assertNotIn("vfg_launchprobe_launch", alex["tags"])            # not on a pad
                steve["ground"] = False                                            # in the air when it stops
            t = time.time()
            proc.send_signal(signal.SIGTERM)
            out = proc.communicate(timeout=5)[0].decode()
            self.assertLess(time.time() - t, 2.0)
            self.assertEqual(proc.returncode, 0, out)
            self.assertNotIn("Traceback", out)
            self.assertIn("launched Steve forward 12 blocks", out)
            with w.lock:
                self.assertIn("slow_falling", steve["effects"])
                self.assertEqual((steve["mods"], sorted(steve["tags"])), (set(), []))
                self.assertEqual([e for e in w.entities if any(t.startswith("vfg_launchprobe") for t in e["tags"])], [])
                self.assertEqual(w.commands[-1], "kill @e[tag=vfg_launchprobe]")
        finally:
            fake.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
