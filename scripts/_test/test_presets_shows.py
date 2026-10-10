"""Tests for the "shows" presets (theme_music, charge_up, sneak, projectile_lands, countdown,
on_death) against the fake RCON server and fake_world, under the Python EC2 has, with
pydantic for a preset's values class:

    uv run --no-project --python 3.12 --with pydantic python scripts/_test/test_presets_shows.py

Each preset: its function sends the commands it should, its values class clamps or refuses
values outside its limits, and, copied into a script the way `vfg script new --preset` copies
it and stopped with SIGTERM, nothing it made is left. The kit's harness (test_kit.KitCase) points
mc at a fake whose world each test makes afresh.
"""
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
LIB = os.path.join(SCRIPTS, "_lib")
PRESETS = os.path.join(SCRIPTS, "_presets")
sys.path.append(os.path.dirname(os.path.dirname(HERE)))  # the repo: a preset's values class imports actions._types
TOOLKIT_LIB = os.path.join(os.path.dirname(SCRIPTS), "toolkit", "lib")
sys.path.insert(0, HERE)

import test_kit  # noqa: E402  (also points mc at a fake)
from test_kit import KitCase, collect, TMP, _properties  # noqa: E402
from fake_rcon import FakeRcon  # noqa: E402
from fake_world import World  # noqa: E402
import mc  # noqa: E402
from kit import fx, values, world  # noqa: E402


def _load(name):
    """A preset as a module from its file, left out of sys.modules: imported by its bare name,
    inspect would take its file for that module's, and the class preset() reads from the same
    file for an action run in this process (run_tests.py's) would lose its descriptions."""
    spec = importlib.util.spec_from_file_location("preset_" + name, os.path.join(PRESETS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


theme_mod, charge_mod, sneak_mod, proj_mod, count_mod, death_mod = (
    _load(n) for n in ("theme_music", "charge_up", "sneak", "projectile_lands", "countdown", "on_death"))

sys.path.insert(0, TOOLKIT_LIB)
import library  # noqa: E402

MODS = [theme_mod, charge_mod, sneak_mod, proj_mod, count_mod, death_mod]


def vals(mod, *argv):
    """A preset's values as a script sees them, with key=value words given, checked as vfg
    action run checks them."""
    return values(meta=test_kit.values_meta(mod, given=argv), run_dir=tempfile.mkdtemp(dir=TMP), instance="t",
                  poll=0.05)


def wait_for(cond, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


# ---- worlds that know a little more than fake_world ------------------------------------------

class ShowWorld(World):
    """fake_world, plus projectiles (uuids, who shot them, in ground, dealt damage, the
    weapon they came from) and players' LastDeathLocation."""

    def __init__(self, ground=64):
        World.__init__(self, ground)
        self.died_at = {}

    def projectile(self, type_, pos, origin, weapon=None):
        e = self.add_entity(type_, pos, health=None)
        e.update(uuid=(e["id"], 1, 2, -3), origin=origin, in_ground=False, dealt=False, weapon=weapon)
        return e

    @staticmethod
    def uuid_of(e):
        h = "".join("%08x" % (n & 0xffffffff) for n in e["uuid"])
        return "%s-%s-%s-%s-%s" % (h[:8], h[8:12], h[12:16], h[16:20], h[20:])

    def _by_uuid(self, u):
        return next((e for e in self.entities if e.get("uuid") and self.uuid_of(e) == u), None)

    def respond(self, cmd):
        with self.lock:
            got = self._more(cmd)
            if got is None:
                return World.respond(self, cmd)
            self.commands.append(cmd)
            return got

    def _more(self, cmd):
        m = re.match(r"execute as @a at @s as @e\[type=([^,\]]+),distance=\.\.(\d+),tag=!(\w+)(?:,nbt=\{(\w+):"
                     r"\{components:\{\"minecraft:custom_data\":\{vfg_item:\"(\w+)\"\}\}\}\})?\] run data get entity @s UUID$",
                     cmd)
        if m:
            types = ("arrow", "spectral_arrow") if m.group(1) == "#minecraft:arrows" else (m.group(1),)
            out = ""
            for p in self.players.values():
                for e in self.entities:
                    if e["type"] in types and m.group(3) not in e["tags"] and e["dim"] == p["dim"] \
                            and sum((e["pos"][i] - p["pos"][i]) ** 2 for i in range(3)) <= int(m.group(2)) ** 2 \
                            and (not m.group(5) or e.get("weapon") == m.group(5)):
                        out += "%s has the following entity data: [I; %d, %d, %d, %d]" % ((e["name"],) + e["uuid"])
            return out
        m = re.match(r"execute as ([0-9a-f-]{36}) on origin run data get entity @s UUID$", cmd)
        if m:
            e = self._by_uuid(m.group(1))
            if not e or not e.get("origin"):
                return ""
            who = self.players.get(e["origin"])
            name = who["prefix"] + who["name"] if who else e["origin"]
            return "%s has the following entity data: [I; 9, 9, 9, 9]" % name
        m = re.match(r"(tag|kill|data get entity) ([0-9a-f-]{36})(?: (.*))?$", cmd)
        if m:
            e = self._by_uuid(m.group(2))
            if e is None:
                return "No entity was found"
            rest = m.group(3) or ""
            if m.group(1) == "kill":
                self.entities.remove(e)
                return "Killed %s" % e["name"]
            if m.group(1) == "tag":
                op, tag = rest.split()
                (e["tags"].add if op == "add" else e["tags"].discard)(tag)
                return "Added tag '%s' to %s" % (tag, e["name"])
            if rest == "Pos":
                return "%s has the following entity data: [%rd, %rd, %rd]" % ((e["name"],) + tuple(e["pos"]))
            if rest in ("inGround", "DealtDamage"):
                return "%s has the following entity data: %db" % (e["name"], e["in_ground" if rest == "inGround" else "dealt"])
            if rest.endswith('.components."minecraft:custom_data".vfg_item'):
                if not e.get("weapon"):
                    return "Found no elements matching %s" % rest
                return '%s has the following entity data: "%s"' % (e["name"], e["weapon"])
            if rest == "life":
                return "%s has the following entity data: %ds" % (e["name"], e.get("life", 0) if e["in_ground"] else 0)
            return ""
        m = re.match(r"tag @e\[tag=(\w+)\] remove (\w+)$", cmd)
        if m:
            n = 0
            for e in self.entities:
                if m.group(1) in e["tags"]:
                    e["tags"].discard(m.group(2))
                    n += 1
            return "Removed tag '%s' from %d entities" % (m.group(2), n) if n else "No entity was found"
        m = re.match(r"scoreboard players add @a (\w+) 0$", cmd)
        if m and m.group(1) in self.objectives:
            for name in self.players:
                self.scores.setdefault((m.group(1), name), 0)
            return "Added 0 to [%s] for %d entities" % (m.group(1), len(self.players))
        m = re.match(r"data get entity (\w+) LastDeathLocation$", cmd)
        if m:
            p = self.players.get(m.group(1))
            if not p:
                return "No entity was found"
            if p["name"] not in self.died_at:
                return "Found no elements matching LastDeathLocation"
            d, (x, y, z) = self.died_at[p["name"]]
            return ('%s has the following entity data: {dimension: "%s", pos: [I; %d, %d, %d]}'
                    % (p["prefix"] + p["name"], d, x, y, z))
        return None

    def die(self, name, at, dim="minecraft:overworld"):
        """A player dies at a block: every deathCount objective goes up."""
        with self.lock:
            self.died_at[name] = (dim, at)
            self.bump(name, "deathCount")

    def bump(self, name, criterion, n=1):
        with self.lock:
            for obj, crit in self.objectives.items():
                if crit == criterion:
                    self.scores[(obj, name)] = self.scores.get((obj, name), 0) + n


class ShowCase(KitCase):
    def setUp(self):
        KitCase.setUp(self)
        type(self).current = self.world = ShowWorld()
        self.world.add_player("Steve", (10.5, 64.0, -3.5), rot=(-90.0, 0.0), prefix="[Owner] ")


# ---- values ----------------------------------------------------------------------------------

class PresetValuesTests(unittest.TestCase):
    def test_every_value_is_clamped_or_refused_outside_its_limits(self):
        va = test_kit._vfg_actions()
        T = va._types()
        for mod in MODS:
            test_kit.assert_checked_on_the_pc(self, mod)
            cls = test_kit.values_class(mod)
            for key, f in cls.model_fields.items():
                for o in T.choices(f.annotation, f.metadata) or []:
                    if T.kind(f.annotation, f.metadata) == "enum":     # an option in any case
                        self.assertEqual(va.check_values(cls, {key: o.upper()}, allow_fixed=True).values[key], o)
                if T.kind(f.annotation, f.metadata) == "particle":
                    self.assertRaises(ValueError, va.check_values, cls, {key: "Not A Particle"}, allow_fixed=True)

    def test_every_preset_passes_vfg_script_check(self):
        lib = library.Library(os.path.dirname(SCRIPTS))
        for mod in MODS:
            item = library.parse(mod.__file__, "preset", source="library", rel="scripts/_presets/" +
                                 os.path.basename(mod.__file__))
            self.assertEqual(library.check(item, lib), [], mod.__name__)


# ---- theme_music -----------------------------------------------------------------------------

class ThemeMusicTests(ShowCase):
    def setUp(self):
        for key in list(fx._tunes):          # an earlier test's tune, still sleeping to its next note, is cut
            fx.stop_melody(key)
        time.sleep(0.1)
        ShowCase.setUp(self)

    def tearDown(self):
        for key in list(fx._tunes):
            fx.stop_melody(key)
        ShowCase.tearDown(self)

    def sounds(self):
        return [c for c in self.world.commands if "playsound" in c]

    def test_near_a_player_every_note_of_the_tune(self):
        took = theme_mod.theme_music("Steve", vals(theme_mod, "theme_tune=galick_gun"))
        self.assertEqual(took, 2.0)
        notes = theme_mod._THEME_MUSIC_TUNES["galick_gun"]
        self.assertTrue(wait_for(lambda: len(self.sounds()) == len(notes), 4))
        s = self.sounds()
        self.assertEqual(s[0], "execute at Steve run playsound minecraft:block.note_block.guitar master "
                               "@a[distance=..48] ~ ~ ~ 3 0.500")
        self.assertIn("execute at Steve run playsound minecraft:block.beacon.activate master @a[distance=..48] "
                      "~ ~ ~ 3 1.000", s)

    def test_to_the_player_alone_and_to_everyone(self):
        theme_mod.theme_music("Steve", vals(theme_mod, "theme_tune=spirit_bomb_throw", "theme_to=player"))
        self.assertTrue(wait_for(lambda: len(self.sounds()) == 11, 3))
        self.assertTrue(all(c.startswith("execute as Steve at @s run playsound ") and " @s ~ ~ ~ " in c
                            for c in self.sounds()), self.sounds())
        del self.world.commands[:]
        theme_mod.theme_music("Steve", vals(theme_mod, "theme_tune=spirit_bomb_throw", "theme_to=everyone"))
        self.assertTrue(wait_for(lambda: len(self.sounds()) == 11, 3))
        self.assertTrue(all(c.startswith("execute as @a at @s run playsound ") for c in self.sounds()))

    def test_a_newer_theme_cuts_the_older_off(self):
        theme_mod.theme_music("Steve", vals(theme_mod, "theme_tune=hollow_purple"))
        time.sleep(0.5)
        theme_mod.theme_music("Steve", vals(theme_mod, "theme_tune=ice_beam", "theme_reach=20"))
        time.sleep(3.2)
        s = self.sounds()
        self.assertFalse([c for c in s if "didgeridoo" in c and "0.561" in c], "hollow purple played on")
        self.assertEqual(len([c for c in s if "@a[distance=..20]" in c]), len(theme_mod._THEME_MUSIC_TUNES["ice_beam"]))

    def test_a_record_is_stopped_by_the_next_theme(self):
        took = theme_mod.theme_music("Steve", vals(theme_mod, "theme_tune=boss", "theme_to=everyone"))
        self.assertEqual(took, 148.0)
        self.assertTrue(wait_for(lambda: any("music_disc.pigstep" in c for c in self.sounds()), 2))
        theme_mod.theme_music("Steve", vals(theme_mod, "theme_tune=galick_gun", "theme_to=everyone"))
        self.assertIn("stopsound @a * minecraft:music_disc.pigstep", self.world.commands)

    def test_another_tune_for_one_call_none_and_a_loop(self):
        took = theme_mod.theme_music("Steve", vals(theme_mod, "theme_tune=spirit_bomb"), tune="spirit_bomb_throw")
        self.assertEqual(took, 1.4)
        self.assertTrue(wait_for(lambda: len(self.sounds()) == 11, 3))
        self.assertEqual(theme_mod.theme_music("Steve", vals(theme_mod, "theme_tune=none")), 0)
        del self.world.commands[:]
        theme_mod.theme_music("Steve", vals(theme_mod, "theme_tune=spirit_bomb_throw", "theme_loop=true"))
        self.assertTrue(wait_for(lambda: len(self.sounds()) >= 22, 4), len(self.sounds()))      # it came round again
        theme_mod.theme_music("Steve", vals(theme_mod), tune="none")                              # and is cut off
        time.sleep(0.2)
        n = len(self.sounds())
        time.sleep(1.8)
        self.assertEqual(len(self.sounds()), n)

    def test_at_a_place_for_the_player_alone_falls_back_to_near(self):
        theme_mod.theme_music(world.Pos(1, 70, 2, "minecraft:the_nether"),
                              vals(theme_mod, "theme_tune=spirit_bomb_throw", "theme_to=player"))
        self.assertTrue(wait_for(lambda: len(self.sounds()) == 11, 3), self.sounds())
        self.assertTrue(all(c.startswith("execute in minecraft:the_nether positioned 1.00 70.00 2.00 run playsound ")
                            and "@a[distance=..48]" in c for c in self.sounds()), self.sounds())


# ---- charge_up -------------------------------------------------------------------------------

class ChargeUpTests(ShowCase):
    def particles(self):
        return [c for c in self.world.commands if " particle " in c]

    def test_a_sphere_grows_over_the_head(self):
        t = time.time()
        at = charge_mod.charge_up("Steve", vals(charge_mod, "charge_seconds=0.6", "charge_size=6"))
        took = time.time() - t
        self.assertTrue(0.5 <= took < 1.5, took)
        self.assertEqual(tuple(round(c, 2) for c in at), (10.5, 72.5, -3.5))     # feet + size + 2.5
        ps = self.particles()
        self.assertTrue([c for c in ps if "particle dust{color:[0.3,0.7,1.0],scale:2.5}" in c])
        self.assertTrue([c for c in ps if "particle end_rod" in c and "0.3 26 0.3" in c], "no beam from the sky")
        self.assertIn("execute at Steve run playsound minecraft:block.beacon.activate master @a ~ ~ ~ 3 1.5",
                      self.world.commands)
        self.assertTrue([c for c in self.world.commands if "block.beacon.ambient" in c])

    def test_orbs_meet_three_blocks_ahead(self):
        at = charge_mod.charge_up("Steve", vals(charge_mod, "charge_style=orbs", "charge_seconds=0.5",
                                                "charge_particle=witch"))
        self.assertEqual(tuple(round(c, 2) for c in at), (13.5, 65.32, -3.5))     # Steve looks along +x
        ps = self.particles()
        self.assertTrue([c for c in ps if "particle soul_fire_flame" in c] and [c for c in ps if "particle flame" in c])
        pitches = [float(c.split()[-1]) for c in self.world.commands if "block.amethyst_block.chime" in c]
        self.assertEqual(pitches, sorted(pitches))                                  # it rises
        self.assertGreater(pitches[-1], pitches[0])

    def test_two_orbs_in_two_colours(self):
        blue, red = "dust{color:[0.2,0.4,1.0],scale:1.6}", "dust{color:[1.0,0.1,0.1],scale:1.6}"
        charge_mod.charge_up("Steve", vals(charge_mod, "charge_style=orbs", "charge_seconds=0.3",
                                           'charge_orbs=["%s", "%s"]' % (blue, red)))
        p = self.particles()
        self.assertTrue([c for c in p if blue in c] and [c for c in p if red in c])

    def test_an_aura_drawn_where_the_player_stands(self):
        at = charge_mod.charge_up("Steve", vals(charge_mod, "charge_style=aura", "charge_seconds=0.4"))
        self.assertEqual(tuple(round(c, 2) for c in at), (10.5, 65.0, -3.5))
        self.assertTrue([c for c in self.particles() if c.startswith("execute at Steve positioned ~ ~1 ~ run particle dust")])

    def test_nobody_to_charge(self):
        t = time.time()
        self.assertIsNone(charge_mod.charge_up("Alex", vals(charge_mod)))
        self.assertLess(time.time() - t, 0.5)
        self.assertEqual(self.particles(), [])


# ---- sneak -----------------------------------------------------------------------------------

class SneakTests(ShowCase):
    def crouch(self, seconds, name="Steve"):
        end = time.time() + seconds
        while time.time() < end:
            self.world.sneak(name, 1)
            time.sleep(0.05)

    def tap(self, n, gap=0.3, name="Steve"):
        for _ in range(n):
            self.crouch(0.2, name)
            time.sleep(gap)

    def test_while_crouched_and_not_after(self):
        got, _ = collect(sneak_mod.sneak("@a", vals(sneak_mod, "sneak_mode=while")), 30)
        time.sleep(0.4)
        self.crouch(1.0)
        n = len(got)
        self.assertTrue(6 <= n <= 12, n)
        time.sleep(0.6)
        self.assertLessEqual(len(got), n + 1)
        self.assertTrue(all(p == "Steve" for p, _ in got))

    def test_each_tap_once(self):
        got, _ = collect(sneak_mod.sneak("Steve", vals(sneak_mod, "sneak_mode=tap")), 30)
        time.sleep(0.4)
        self.tap(3)
        self.crouch(1.0)                    # a long crouch is one tap
        time.sleep(0.3)
        self.assertEqual(len(got), 4, got)

    def test_three_taps_within_the_window(self):
        got, _ = collect(sneak_mod.sneak("@a", vals(sneak_mod, "sneak_mode=three_taps", "sneak_seconds=1.5")), 30)
        time.sleep(0.4)
        self.tap(2)
        time.sleep(1.6)                     # two, then too late for a third
        self.tap(1)
        time.sleep(0.3)
        self.assertEqual(got, [])
        self.tap(3)
        self.assertEqual([p for p, _ in got], ["Steve"])

    def test_clicks_never_count_and_only_with_the_item_held(self):
        got, _ = collect(sneak_mod.sneak("@a", vals(sneak_mod, "sneak_mode=tap", "sneak_item=Sniper Rifle")), 30)
        time.sleep(0.4)
        self.world.hold("Steve", main="plain")
        for _ in range(5):
            self.world.click("Steve")
            time.sleep(0.15)
        self.tap(2)                         # crouching without the rifle
        self.assertEqual(got, [])
        self.world.hold("Steve", main="sniper_rifle")
        time.sleep(0.3)
        self.tap(2)
        self.assertEqual(len(got), 2, got)
        self.assertIn("execute as @a if items entity @s weapon.* *[custom_data~{vfg_item:\"sniper_rifle\"}] run "
                      "scoreboard players get @s vfg_sneak", self.world.commands)

    def test_a_player_who_never_crouched_does_not_hide_the_next(self):
        self.world.add_player("Alex", (20.5, 64.0, 5.5))     # after Steve in every reply; Steve never crouches
        got, _ = collect(sneak_mod.sneak("@a", vals(sneak_mod, "sneak_mode=tap")), 30)
        time.sleep(0.4)
        self.tap(2, name="Alex")
        self.assertEqual([p for p, _ in got], ["Alex", "Alex"])

    def test_each_player_on_their_own(self):
        self.world.add_player("Alex", (20.5, 64.0, 5.5))
        got, _ = collect(sneak_mod.sneak("Steve,Alex", vals(sneak_mod, "sneak_mode=three_taps")), 30)
        time.sleep(0.4)
        t = threading.Thread(target=self.tap, args=(2,), kwargs={"name": "Alex"})
        t.start()
        self.tap(3)
        t.join()
        time.sleep(0.3)
        self.assertEqual([p for p, _ in got], ["Steve"])


# ---- projectile_lands -------------------------------------------------------------------------

class ProjectileLandsTests(ShowCase):
    def start(self, *argv):
        got, _ = collect(proj_mod.projectile_lands("@a", vals(proj_mod, *argv)), 30)
        return got

    def test_where_an_arrow_lands_and_who_shot_it(self):
        got = self.start()
        arrow = self.world.projectile("arrow", [12.0, 66.0, -3.5], "Steve")
        skel = self.world.projectile("arrow", [14.0, 66.0, -3.5], "Skeleton")
        time.sleep(0.4)
        arrow["pos"] = [30.0, 64.2, -3.5]
        skel["in_ground"] = True
        time.sleep(0.3)
        self.assertEqual(got, [])
        arrow["in_ground"] = True
        self.assertTrue(wait_for(lambda: got, 2))
        (at, p, weapon), _ = got[0]
        self.assertEqual((p, tuple(at), at.dim, weapon), ("Steve", (30.0, 64.2, -3.5), None, ""))
        time.sleep(0.4)
        self.assertEqual(len(got), 1)                                   # once
        self.assertIn("vfg_%s_seen" % mc.INSTANCE, arrow["tags"])

    def test_a_trident_that_hits_something_and_an_arrow_that_vanishes_into_a_mob(self):
        got = self.start()
        tri = self.world.projectile("trident", [12.0, 66.0, -3.5], "Steve")
        arrow = self.world.projectile("arrow", [11.0, 66.0, -3.5], "Steve")
        time.sleep(0.3)
        for _ in range(16):                                             # it flies along +x, a block every 0.025 s
            arrow["pos"][0] += 1.0
            time.sleep(0.025)
        tri["dealt"] = True
        self.world.entities.remove(arrow)                               # it hit a mob: gone
        self.assertTrue(wait_for(lambda: len(got) == 2, 2))
        spots = sorted(tuple(round(c, 1) for c in at) for (at, p, weapon), _ in got)
        self.assertEqual(spots[0], (12.0, 66.0, -3.5))
        x, y, z = spots[1]                                              # a little past where it was last seen
        self.assertTrue(24.0 < x <= 30.0 and (y, z) == (66.0, -3.5), spots)

    def test_only_the_named_weapon_and_a_cooldown_and_removed_where_it_lands(self):
        got = self.start("projectile_lands_named=Spear Bomb", "projectile_lands_cooldown=5",
                         "projectile_lands_remove=true", "projectile_lands_kind=trident")
        plain = self.world.projectile("trident", [12.0, 66.0, -3.5], "Steve")
        bomb = self.world.projectile("trident", [12.0, 66.0, -2.5], "Steve", weapon="spear_bomb")
        dud = self.world.projectile("trident", [12.0, 66.0, -1.5], "Steve", weapon="spear_bomb")
        arrow = self.world.projectile("arrow", [12.0, 66.0, -0.5], "Steve", weapon="spear_bomb")
        time.sleep(0.4)
        for e in (plain, bomb, arrow):
            e["in_ground"] = True
        self.assertTrue(wait_for(lambda: got, 2))
        dud["in_ground"] = True
        time.sleep(0.5)
        self.assertEqual([(tuple(at), weapon) for (at, p, weapon), _ in got], [((12.0, 66.0, -2.5), "spear_bomb")])
        self.assertNotIn(bomb, self.world.entities)                     # removed
        self.assertIn(dud, self.world.entities)                         # too soon: it lands as it is
        self.assertIn(plain, self.world.entities)
        self.assertTrue([c for c in self.world.commands if c.endswith('item.components."minecraft:custom_data".vfg_item')])
        self.assertFalse([c for c in self.world.commands if "#minecraft:arrows" in c])

    def test_several_named_weapons_in_one_and_which_it_was(self):
        got = self.start("projectile_lands_named=[\"Boom Bow\", \"Spear Bomb\"]")
        bow = self.world.projectile("arrow", [12.0, 66.0, -3.5], "Steve", weapon="boom_bow")
        spear = self.world.projectile("trident", [12.0, 66.0, -2.5], "Steve", weapon="spear_bomb")
        plain = self.world.projectile("arrow", [12.0, 66.0, -1.5], "Steve")
        time.sleep(0.4)
        for e in (bow, spear, plain):
            e["in_ground"] = True
        self.assertTrue(wait_for(lambda: len(got) == 2, 2))
        time.sleep(0.3)
        self.assertEqual(sorted(weapon for (at, p, weapon), _ in got), ["boom_bow", "spear_bomb"])

    def test_one_lying_in_the_ground_from_before_is_left_alone(self):
        old = self.world.projectile("arrow", [12.0, 66.0, -3.5], "Steve")
        old.update(in_ground=True, life=600)                            # shot half a minute before it started
        got = self.start()
        new = self.world.projectile("arrow", [12.0, 66.0, -2.5], "Steve")
        new.update(in_ground=True, life=2)                              # a point-blank shot, down at once
        self.assertTrue(wait_for(lambda: got, 2))
        time.sleep(0.4)
        self.assertEqual([tuple(at) for (at, p, weapon), _ in got], [(12.0, 66.0, -2.5)])

    def test_nobody_online_asks_nothing(self):
        del self.world.players["Steve"]
        got = self.start()
        time.sleep(0.5)
        self.assertEqual([c for c in self.world.commands if "UUID" in c], [])


# ---- countdown -------------------------------------------------------------------------------

class CountdownTests(ShowCase):
    def test_chat_then_a_title_each_second_then_on(self):
        t = time.time()
        self.assertTrue(count_mod.countdown(vals(count_mod, "countdown_seconds=3", "countdown_text=Nuke")))
        self.assertTrue(2.9 <= time.time() - t < 3.6)
        c = self.world.commands
        self.assertEqual(c[0], 'tellraw @a {"text": "Nuke in 3s.", "color": "red", "bold": true}')
        titles = [x for x in c if x.startswith("title @a title")]
        self.assertEqual([re.search(r'"text": "(\d+)"', x).group(1) for x in titles], ["3", "2", "1"])
        self.assertIn('title @a subtitle {"text": "Nuke", "color": "gray"}', c)
        self.assertEqual(len([x for x in c if "block.note_block.bass" in x]), 3)

    def test_for_named_players_and_the_marks_on_the_way(self):
        self.world.add_player("Alex", (20.5, 64.0, 5.5))
        v = vals(count_mod, "countdown_seconds=12", "countdown_to=Alex")
        count_mod.countdown.__globals__["time"] = _Fast(4.0)            # 12 s in about 3 s
        try:
            self.assertTrue(count_mod.countdown(v))
        finally:
            count_mod.countdown.__globals__["time"] = time
        said = [x for x in self.world.commands if x.startswith("tellraw")]
        self.assertEqual([re.search(r'"text": "([^"]+)"', x).group(1) for x in said],
                         ["Detonation in 12s.", "Detonation in 10s."])
        self.assertTrue(all(x.startswith("tellraw Alex ") for x in said))
        self.assertEqual(len([x for x in self.world.commands if x.startswith("title Alex title")]), 10)


class _Fast(object):
    """time, running `k` times as fast (for the countdown's clock only)."""

    def __init__(self, k):
        self.k, self.t0 = k, time.time()

    def time(self):
        return self.t0 + (time.time() - self.t0) * self.k

    def sleep(self, s):
        time.sleep(s / self.k)


# ---- on_death --------------------------------------------------------------------------------

class OnDeathTests(ShowCase):
    def test_where_a_player_died_and_where_they_killed(self):
        got, _ = collect(death_mod.on_death("@a", vals(death_mod, "on_death_event=both")), 30)
        time.sleep(0.5)
        self.world.die("Steve", (40, 70, -12), "minecraft:the_nether")
        self.assertTrue(wait_for(lambda: got, 2))
        (at, p, what), _ = got[0]
        self.assertEqual((tuple(at), at.dim, p, what), ((40.5, 70.0, -11.5), "minecraft:the_nether", "Steve", "death"))
        self.world.bump("Steve", "totalKillCount", 2)
        self.assertTrue(wait_for(lambda: len(got) == 3, 2))
        self.assertEqual([(tuple(a), w) for (a, p, w), _ in got[1:]], [((10.5, 64.0, -3.5), "kill")] * 2)
        self.assertIn("vfg_%s_deaths" % mc.INSTANCE, self.world.objectives)

    def test_a_player_who_dies_and_leaves_at_once_is_counted_when_back(self):
        got, _ = collect(death_mod.on_death("@a", vals(death_mod)), 30)
        time.sleep(0.5)
        steve = self.world.players.pop("Steve")              # gone the moment they died
        self.world.die("Steve", (7, 64, 7))
        time.sleep(1.5)
        self.assertEqual(got, [])
        self.world.players["Steve"] = steve
        self.assertTrue(wait_for(lambda: got, 3))
        self.assertEqual([(tuple(a), p, w) for (a, p, w), _ in got], [((7.5, 64.0, 7.5), "Steve", "death")])

    def test_a_player_who_never_died_does_not_hide_the_next(self):
        self.world.add_player("Alex", (20.5, 64.0, 5.5))
        got, _ = collect(death_mod.on_death("@a", vals(death_mod)), 30)
        time.sleep(0.5)
        self.world.die("Alex", (2, 64, 2))
        self.assertTrue(wait_for(lambda: got, 2))
        self.assertEqual([(p, w) for (a, p, w), _ in got], [("Alex", "death")])

    def test_deaths_only_and_only_the_players_named(self):
        self.world.add_player("Alex", (20.5, 64.0, 5.5))
        got, _ = collect(death_mod.on_death("Alex", vals(death_mod)), 30)
        time.sleep(0.5)
        self.world.die("Steve", (1, 64, 1))
        self.world.bump("Alex", "totalKillCount")
        time.sleep(0.6)
        self.assertEqual(got, [])
        self.world.die("Alex", (2, 64, 2))
        self.assertTrue(wait_for(lambda: got, 2))
        self.assertEqual([(p, w) for (a, p, w), _ in got], [("Alex", "death")])


# ---- copied into a script and stopped -----------------------------------------------------------

SCRIPT = '''"""Probe %(name)s: the preset copied in and run until stopped."""
ASKS = ["probe"]
from kit import values, loop
import mc

v = values()
%(wiring)s
'''


def make_script(preset, wiring):
    """A script with the preset copied in as `vfg script new --preset` copies it."""
    item = library.parse(os.path.join(PRESETS, preset + ".py"), "preset")
    text, _keys, _imports = library.add_preset(SCRIPT % {"name": preset, "wiring": wiring}, item, "main")
    return text


def start(name, source, fake, password, preset, *argv):
    """The script started as the box starts a copy: its meta file holds player=@a and every value
    of the preset's class, with the key=value words given."""
    folder = os.path.join(TMP, name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "main.py"), "w") as f:
        f.write(source)
    run_dir = tempfile.mkdtemp(dir=TMP)
    meta = test_kit.values_meta(preset, given=argv)
    meta["values"]["player"] = "@a"
    with open(os.path.join(run_dir, name + ".json"), "w") as f:
        json.dump(meta, f)
    env = dict(os.environ, PYTHONPATH=LIB, VFG_MC_PROPERTIES=_properties(password, fake.port),
               VFG_RCON_HOST="127.0.0.1", PYTHONDONTWRITEBYTECODE="1", VFG_INSTANCE=name,
               VFG_RUN_DIR=run_dir, VFG_PROTECTED=os.path.join(TMP, "none.json"))
    return subprocess.Popen([sys.executable, "-u", "main.py"], cwd=folder, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


class StopTests(unittest.TestCase):
    """Each preset in a script of its own, stopped with SIGTERM: out within 2 s, no
    Traceback, and nothing it made left behind."""

    def setUp(self):
        self.world = ShowWorld()
        self.world.add_player("Steve", (10.5, 64.0, -3.5), rot=(-90.0, 0.0), prefix="[Owner] ")
        self.password = "p" + str(os.getpid())
        self.fake = FakeRcon(self.password, responder=self.world.respond).start()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
        self.fake.stop()

    def run_and_stop(self, name, preset, wiring, ready, *argv):
        proc = start(name, make_script(preset, wiring), self.fake, self.password, preset, *argv)
        self.procs.append(proc)
        self.assertTrue(wait_for(lambda: ready(self.world), 6), "never got going")
        t = time.time()
        proc.send_signal(signal.SIGTERM)
        out = proc.communicate(timeout=4)[0].decode()
        self.assertLess(time.time() - t, 2.0, out)
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("Traceback", out)
        tag = "vfg_" + name
        self.assertEqual([e for e in self.world.entities if any(t == tag or t.startswith(tag + "_") for t in e["tags"])], [])
        self.assertEqual([o for o in self.world.objectives if o == tag or o.startswith(tag + "_")], [])
        self.assertEqual(self.world.commands[-1], "kill @e[tag=%s]" % tag)
        return out

    def test_theme_music(self):
        self.run_and_stop("probetheme", "theme_music", 'theme_music("Steve", v)\nfor n in loop(every=0.1):\n    pass',
                          lambda w: any("pigstep" in c for c in w.commands), "theme_tune=boss")
        self.assertIn("stopsound @a * minecraft:music_disc.pigstep", self.world.commands)

    def test_charge_up(self):
        self.run_and_stop("probecharge", "charge_up", 'for n in loop(every=0.1):\n    charge_up("Steve", v)',
                          lambda w: len([c for c in w.commands if "particle" in c]) > 20, "charge_seconds=10")

    def test_sneak(self):
        self.run_and_stop("probesneak", "sneak", 'for p in loop(sneak(v["player"], v)):\n    mc.log("sneak", p)',
                          lambda w: "vfg_sneak" in w.objectives)
        self.assertIn("vfg_sneak", self.world.objectives)          # shared: stays

    def test_projectile_lands(self):
        arrow = self.world.projectile("arrow", [12.0, 66.0, -3.5], "Steve")
        self.run_and_stop("probeproj", "projectile_lands",
                          'for at, p in loop(projectile_lands(v["player"], v)):\n    mc.log("landed", p)',
                          lambda w: "vfg_probeproj_seen" in arrow["tags"])
        self.assertEqual(arrow["tags"], set())                      # looked at, and the mark taken off again

    def test_countdown(self):
        out = self.run_and_stop("probecount", "countdown", 'countdown(v)',
                                lambda w: any(c.startswith("tellraw @a") for c in w.commands), "countdown_seconds=30")
        self.assertIn('tellraw @a {"text": "Detonation called off.", "color": "green", "bold": true}',
                      self.world.commands)

    def test_on_death(self):
        self.run_and_stop("probedeath", "on_death",
                          'for at, p, what in loop(on_death(v["player"], v)):\n    mc.log(what, p)',
                          lambda w: "vfg_probedeath_deaths" in w.objectives and "vfg_probedeath_kills" in w.objectives,
                          "on_death_event=both")


if __name__ == "__main__":
    unittest.main(verbosity=2)
