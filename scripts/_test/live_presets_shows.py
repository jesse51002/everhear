"""The "shows" presets against a real Minecraft 26.3 server: theme_music, charge_up, sneak,
projectile_lands, countdown and on_death, each run for real and the world asked after.

    integration/realmc/up ps && eval "$(integration/realmc/env ps)"
    uv run --no-project --python 3.12 --with pydantic python scripts/_test/live_presets_shows.py

Like live_kit.py it runs only against a realmc test server, never the box. Every command a
preset sends is recorded with the server's answer, and no answer may be a parse error
("Unknown", "Incorrect argument", "<--[HERE]"...): that is how a title or a sound, which
nobody hears here, is checked. What needs a player (a crouch, a player's arrow, a death)
runs with Carpet's fake players and is skipped without them.
"""
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
sys.path.insert(0, HERE)

from live_kit import X, FLOOR, Z, ok  # noqa: E402  (refuses anything but a realmc server)
from test_kit import values_meta  # noqa: E402
import mc  # noqa: E402
from kit import items, values, world  # noqa: E402

sys.path.insert(0, PRESETS)
import theme_music as theme_mod  # noqa: E402
import charge_up as charge_mod  # noqa: E402
import sneak as sneak_mod  # noqa: E402
import projectile_lands as proj_mod  # noqa: E402
import countdown as count_mod  # noqa: E402
import on_death as death_mod  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPTS), "toolkit", "lib"))
import library  # noqa: E402

TAG = "vfg_ps_live"
SPOT = world.Pos(X + 0.5, FLOOR + 1, Z + 0.5)

# Every command and its answer, from every thread.
SENT = []
_rcon = mc.rcon


def _recording(cmd):
    reply = _rcon(cmd)
    SENT.append((cmd, reply))
    return reply


mc.rcon = _recording


def vals(mod, *argv):
    """A preset's values with key=value words given, checked as vfg action run checks them."""
    return values(meta=values_meta(mod, given=argv), run_dir=tempfile.mkdtemp(), instance="t", poll=0.05)


def refused(since=0):
    """Commands sent since index `since` that the server did not understand."""
    return [(c, r) for c, r in SENT[since:] if not ok(r)]


def wait_for(cond, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def collect(gen, seconds=60):
    """A generator run in a thread: a list of what it yielded so far."""
    got = []

    def go():
        for x in gen:
            got.append(x)
    th = threading.Thread(target=go, daemon=True)
    th.start()
    return got


def stop_generators():
    """End every collected generator: they all stop on mc.stopping()."""
    mc._stop["thread"] = threading.get_ident()
    mc._stopping.set()
    time.sleep(1.2)
    mc._stopping.clear()


sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "integration", "realmc"))
try:
    import players as fake_players  # noqa: E402
    PLAYERS = fake_players.on()
    CARPET = "Unknown or incomplete" not in PLAYERS.cmd("carpet")
except Exception:   # no Carpet on this server
    CARPET = False


class Live(unittest.TestCase):
    def setUp(self):
        self.since = len(SENT)

    def tearDown(self):
        stop_generators()
        mc.rcon("kill @e[tag=%s]" % TAG)
        # What a script's stop would take away, for the presets run in this process.
        for name in ("deaths", "kills"):
            mc.rcon("scoreboard objectives remove %s_%s" % (mc.TAG, name))
        mc.rcon("tag @e[tag=%s_seen] remove %s_seen" % (mc.TAG, mc.TAG))

    def assertUnderstood(self):
        self.assertEqual(refused(self.since), [])


# ---- theme_music -------------------------------------------------------------------------------

class LiveTheme(Live):
    def test_every_tune_plays_and_a_record_is_cut_off(self):
        lengths = {}
        for i, tune in enumerate(theme_mod._THEME_MUSIC_TUNES):
            lengths[tune] = theme_mod.theme_music(SPOT.moved(i * 3, 0, 0), vals(theme_mod, "theme_tune=" + tune))
        theme_mod.theme_music("@a", vals(theme_mod, "theme_tune=ice_beam", "theme_to=everyone"))
        time.sleep(6.5)                                             # the longest tune but the record is 6 s
        self.assertEqual(lengths["boss"], 148.0)
        self.assertEqual(lengths["galick_gun"], 2.0)
        played = [c for c, r in SENT[self.since:] if "playsound" in c]
        want = sum(len(n) for t, n in theme_mod._THEME_MUSIC_TUNES.items() if t != "boss") + 1 + 14
        self.assertGreaterEqual(len(played), want)
        self.assertTrue([c for c in played if "minecraft:music_disc.pigstep" in c])
        theme_mod.theme_music(SPOT.moved(18, 0, 0), vals(theme_mod, "theme_tune=galick_gun"))
        self.assertIn("stopsound @a * minecraft:music_disc.pigstep", [c for c, r in SENT[self.since:]])
        time.sleep(2.0)
        self.assertUnderstood()


# ---- charge_up -----------------------------------------------------------------------------------

class LiveCharge(Live):
    """A charge on an armor stand standing in for a player: it is read and drawn on as one is."""

    def setUp(self):
        Live.setUp(self)
        mc.rcon('summon armor_stand %.1f %d %.1f {Tags:["%s","ps_eye"],NoGravity:1b,Rotation:[-90f,0f]}'
                % (SPOT[0], FLOOR + 1, SPOT[2], TAG))
        self.eye = "@e[tag=ps_eye,limit=1]"

    def test_each_style(self):
        for style, want in (("sphere", (SPOT[0], FLOOR + 1 + 6 + 2.5, SPOT[2])),
                            ("orbs", (SPOT[0] + 3, FLOOR + 1 + 1.62 - 0.3, SPOT[2])),
                            ("aura", (SPOT[0], FLOOR + 2, SPOT[2]))):
            n = len(SENT)
            t = time.time()
            at = charge_mod.charge_up(self.eye, vals(charge_mod, "charge_style=" + style, "charge_seconds=1",
                                                     "charge_size=6"))
            took = time.time() - t
            self.assertTrue(0.9 <= took < 2.5, (style, took))
            self.assertEqual(tuple(round(c, 2) for c in at), tuple(round(c, 2) for c in want), style)
            drawn = [c for c, r in SENT[n:] if " particle " in c]
            self.assertGreater(len(drawn), 10, style)
            print("\n  charge_up %s: %d commands in %.2f s, ends at %s" % (style, len(SENT) - n, took, at))
        self.assertUnderstood()

    def test_nobody_to_charge(self):
        self.assertIsNone(charge_mod.charge_up("NobodyAtAll", vals(charge_mod)))


# ---- countdown -------------------------------------------------------------------------------------

class LiveCountdown(Live):
    def test_three_seconds(self):
        t = time.time()
        self.assertTrue(count_mod.countdown(vals(count_mod, "countdown_seconds=3", "countdown_text=Nuke")))
        self.assertTrue(2.9 <= time.time() - t < 3.8)
        sent = [c for c, r in SENT[self.since:]]
        self.assertEqual(len([c for c in sent if c.startswith("title @a title")]), 3)
        self.assertTrue([c for c in sent if c.startswith("tellraw @a")])
        self.assertUnderstood()


# ---- projectile_lands ---------------------------------------------------------------------------

def _uuid(ints):
    h = "".join("%08x" % (n & 0xffffffff) for n in ints)
    return "%s-%s-%s-%s-%s" % (h[:8], h[8:12], h[12:16], h[16:20], h[20:])


class LiveProjectilePieces(Live):
    """The questions projectile_lands asks, of real arrows and tridents, with a pillager as the
    shooter: who shot it, where it is, whether it is down, and that one gone is gone."""

    def setUp(self):
        Live.setUp(self)
        mc.rcon("fill %d %d %d %d %d %d air" % (X - 12, FLOOR + 1, Z - 4, X + 12, FLOOR + 12, Z + 4))
        mc.rcon('summon pillager %.1f %d %.1f {UUID:[I;71,72,73,74],Tags:["%s"],NoAI:1b,PersistenceRequired:1b}'
                % (X - 10.5, FLOOR + 1, Z + 0.5, TAG))

    def arrow(self, ints, at, motion, kind="arrow", extra=""):
        mc.rcon('summon %s %.2f %.2f %.2f {UUID:[I;%d,%d,%d,%d],Owner:[I;71,72,73,74],Motion:[%.2f,%.2f,%.2f],'
                'Tags:["%s"]%s}' % ((kind,) + tuple(at) + tuple(ints) + tuple(motion) + (TAG, extra)))
        return _uuid(ints)

    def test_an_arrow_shot_lands_in_the_ground(self):
        u = self.arrow((81, 1, 1, 1), (X - 8.5, FLOOR + 3, Z + 0.5), (0.6, 0.3, 0.0),
                       extra=',weapon:{id:"minecraft:bow",count:1,components:{"minecraft:custom_data":'
                             '{vfg_item:"boom_bow"}}}')
        self.assertEqual(proj_mod._projectile_lands_thrower(u), "Pillager")
        first = proj_mod._projectile_lands_pos(u, None)
        self.assertIsInstance(first, world.Pos)
        self.assertFalse(proj_mod._projectile_lands_down(u, "arrow"))
        self.assertTrue(wait_for(lambda: proj_mod._projectile_lands_down(u, "arrow"), 5))
        at = proj_mod._projectile_lands_pos(u, None)
        print("\n  arrow from %s landed at %s" % (first, at))
        self.assertGreater(at[0], first[0])
        self.assertLess(abs(at[1] - (FLOOR + 1)), 1.0)                  # on the floor
        named = 'nbt={weapon:{components:{"minecraft:custom_data":{vfg_item:"%s"}}}}'
        self.assertIn("[I; 81, 1, 1, 1]", mc.rcon("execute as @e[type=#minecraft:arrows,%s] run data get entity @s UUID"
                                                   % (named % "boom_bow")))
        self.assertNotIn("[I; 81, 1, 1, 1]", mc.rcon("execute as @e[type=#minecraft:arrows,%s] run data get entity @s "
                                                      "UUID" % (named % "other_bow")))
        self.assertUnderstood()

    def test_a_trident_that_hits_a_mob_and_an_arrow_that_goes_into_one(self):
        mc.rcon('summon zombie %.1f %d %.1f {Tags:["%s"],NoAI:1b,PersistenceRequired:1b,'
                'attributes:[{id:"max_health",base:200}],Health:200f}' % (X + 0.5, FLOOR + 1, Z + 0.5, TAG))
        mc.rcon('summon zombie %.1f %d %.1f {Tags:["%s"],NoAI:1b,PersistenceRequired:1b,'
                'attributes:[{id:"max_health",base:200}],Health:200f}' % (X + 0.5, FLOOR + 1, Z + 3.5, TAG))
        t = self.arrow((82, 1, 1, 1), (X - 5.5, FLOOR + 2, Z + 0.5), (1.5, 0.05, 0.0), kind="trident",
                       extra=',item:{id:"minecraft:trident",count:1}')
        a = self.arrow((83, 1, 1, 1), (X - 5.5, FLOOR + 2, Z + 3.5), (2.0, 0.05, 0.0))
        self.assertTrue(wait_for(lambda: proj_mod._projectile_lands_down(t, "trident"), 4), "the trident never hit")
        self.assertIn("1b", mc.rcon("data get entity %s DealtDamage" % t))
        self.assertTrue(wait_for(lambda: proj_mod._projectile_lands_pos(a, None) is None, 4), "the arrow is still there")
        self.assertUnderstood()

    def test_the_mark_and_its_removal(self):
        u = self.arrow((84, 1, 1, 1), (X + 0.5, FLOOR + 5, Z + 0.5), (0, 0, 0))
        mc.rcon("tag %s add vfg_ps_live_seen" % u)
        self.assertIn("vfg_ps_live_seen", mc.rcon("data get entity %s Tags" % u))
        mc.rcon("tag @e[tag=vfg_ps_live_seen] remove vfg_ps_live_seen")
        self.assertNotIn("vfg_ps_live_seen", mc.rcon("data get entity %s Tags" % u))
        self.assertIn("Killed", mc.rcon("kill %s" % u))
        self.assertIsNone(proj_mod._projectile_lands_pos(u, None))
        self.assertUnderstood()


# ---- sneak and on_death without players: what they ask parses ------------------------------------

class LiveQuestions(Live):
    def test_the_sneak_poll_and_the_death_objectives(self):
        stat = items.Stat(*items.SNEAK)
        self.assertEqual(stat.changes("if items entity @s weapon.* %s" % items.match("Sniper Rifle")), {})
        for crit in ("deathCount", "totalKillCount"):
            self.assertIn("Created new objective", mc.rcon("scoreboard objectives add vfg_ps_probe %s" % crit))
            mc.rcon("scoreboard objectives remove vfg_ps_probe")
        self.assertUnderstood()


# ---- with Carpet's fake players ---------------------------------------------------------------------

@unittest.skipUnless(CARPET, "the server has no Carpet: integration/realmc/realmc.py carpet, then up --fresh")
class LiveWithPlayers(Live):
    def setUp(self):
        Live.setUp(self)
        PLAYERS.spawn("Steve")
        PLAYERS.clear("Steve")
        PLAYERS.hotbar("Steve", 1)          # a fake player keeps its slot from one spawn to the next
        PLAYERS.sneak("Steve", False)
        time.sleep(0.5)

    def tearDown(self):
        Live.tearDown(self)
        PLAYERS.kill_all()

    def test_sneak_while_tap_and_three_taps(self):
        got = collect(sneak_mod.sneak("Steve", vals(sneak_mod, "sneak_mode=while")))
        time.sleep(0.5)
        PLAYERS.sneak("Steve")
        time.sleep(1.0)
        PLAYERS.sneak("Steve", False)
        time.sleep(0.5)
        n = len(got)
        print("\n  sneak while: %d passes in about 1 s" % n)
        self.assertTrue(5 <= n <= 14, n)
        time.sleep(0.6)
        self.assertLessEqual(len(got), n + 1)
        stop_generators()
        taps = collect(sneak_mod.sneak("Steve", vals(sneak_mod, "sneak_mode=three_taps")))
        time.sleep(0.5)
        for i in range(3):
            PLAYERS.sneak("Steve")
            time.sleep(0.25)
            PLAYERS.sneak("Steve", False)
            time.sleep(0.25)
            if i == 1:
                self.assertEqual(taps, [])
        self.assertTrue(wait_for(lambda: taps == ["Steve"], 1.0), taps)
        self.assertUnderstood()

    def test_a_players_arrow_lands(self):
        uuid = re.search(r"\[I; (-?\d+), (-?\d+), (-?\d+), (-?\d+)\]", mc.rcon("data get entity Steve UUID")).groups()
        x, y, z = PLAYERS.pos("Steve")
        got = collect(proj_mod.projectile_lands("@a", vals(proj_mod)))
        time.sleep(0.5)
        mc.rcon('summon arrow %.2f %.2f %.2f {Owner:[I;%s],Motion:[0.0,0.4,0.7],Tags:["%s"]}'
                % ((x, y + 1.6, z + 1.0) + (",".join(uuid), TAG)))
        self.assertTrue(wait_for(lambda: got, 6), "never landed")
        at, p, weapon = got[0]
        print("\n  Steve's arrow landed at %s" % (at,))
        self.assertEqual(weapon, "")
        self.assertEqual(p, "Steve")
        self.assertGreater(at[2], z + 1.0)
        self.assertUnderstood()

    def test_steve_shoots_the_named_bow_and_a_plain_one(self):
        mc.rcon('give Steve bow[custom_data={vfg_item:"boom_bow"}]')         # hotbar 1
        mc.rcon("give Steve arrow 16")                                        # hotbar 2
        mc.rcon("give Steve bow")                                             # hotbar 3
        got = collect(proj_mod.projectile_lands("@a", vals(proj_mod, "projectile_lands_named=Boom Bow",
                                                           "projectile_lands_kind=arrow")))
        x, y, z = PLAYERS.pos("Steve")
        for slot in (1, 3):
            PLAYERS.hotbar("Steve", slot)
            PLAYERS.look("Steve", 0, -20)
            time.sleep(0.3)
            PLAYERS.use_continuous("Steve")
            time.sleep(1.3)
            PLAYERS.stop("Steve")
            time.sleep(2.5)
        self.assertEqual(len(got), 1, got)
        at, p, weapon = got[0]
        print("\n  the Boom Bow's arrow landed at %s; the plain bow's did not count" % (at,))
        self.assertEqual((p, weapon), ("Steve", "boom_bow"))
        self.assertGreater(at[2], z + 5)                                      # yaw 0 is +z: in the hill past the arena
        mc.rcon("kill @e[type=arrow,distance=..80,x=%d,y=%d,z=%d]" % (x, y, z))
        self.assertUnderstood()

    def test_an_arrow_lying_in_the_ground_from_before_is_left_alone(self):
        mc.rcon("clear Steve")
        mc.rcon("give Steve bow")
        mc.rcon("give Steve arrow 16")
        PLAYERS.hotbar("Steve", 1)
        PLAYERS.look("Steve", 0, 20)                                          # into the floor, 4 blocks off
        time.sleep(0.3)
        PLAYERS.use_continuous("Steve")
        time.sleep(1.2)
        PLAYERS.stop("Steve")
        time.sleep(3.0)                                                       # it lies there 3 s before anyone watches
        self.assertIn("passed", mc.rcon("execute if entity @e[type=arrow,nbt={inGround:1b}]").lower())
        got = collect(proj_mod.projectile_lands("@a", vals(proj_mod, "projectile_lands_kind=arrow")))
        time.sleep(1.5)
        self.assertEqual(got, [], "it acted on an arrow that was there before it started")
        PLAYERS.use_continuous("Steve")                                       # a new one, down at once
        time.sleep(1.2)
        PLAYERS.stop("Steve")
        self.assertTrue(wait_for(lambda: got, 4), "the new arrow was not seen")
        x, y, z = PLAYERS.pos("Steve")
        mc.rcon("kill @e[type=arrow,distance=..40,x=%d,y=%d,z=%d]" % (x, y, z))
        self.assertUnderstood()

    def test_steve_throws_a_trident_into_a_mob(self):
        x, y, z = PLAYERS.pos("Steve")
        mc.rcon('summon zombie %.1f %.1f %.1f {Tags:["%s"],NoAI:1b,PersistenceRequired:1b,'
                'attributes:[{id:"max_health",base:200}],Health:200f}' % (x, y, z + 8, TAG))
        mc.rcon("give Steve trident")
        got = collect(proj_mod.projectile_lands("Steve", vals(proj_mod, "projectile_lands_kind=trident",
                                                              "projectile_lands_remove=true")))
        PLAYERS.look_at("Steve", x, y + 1.2, z + 8)
        time.sleep(0.3)
        PLAYERS.use_continuous("Steve")
        time.sleep(1.0)
        PLAYERS.stop("Steve")
        self.assertTrue(wait_for(lambda: got, 4), "the trident never came down")
        at, p, weapon = got[0]
        print("\n  Steve's trident hit the zombie at %s" % (at,))
        self.assertEqual(p, "Steve")
        self.assertLess(abs(at[2] - (z + 8)), 2.5)
        time.sleep(0.3)
        self.assertNotIn("passed", mc.rcon("execute if entity @e[type=trident,x=%d,y=%d,z=%d,distance=..30]"
                                           % (x, y, z)).lower())             # taken away where it landed
        self.assertUnderstood()

    def test_sneak_taps_with_the_item_held_and_clicks_never_count(self):
        items.give("Steve", "Sniper Rifle")
        PLAYERS.hotbar("Steve", 1)
        got = collect(sneak_mod.sneak("@a", vals(sneak_mod, "sneak_mode=tap", "sneak_item=Sniper Rifle")))
        time.sleep(0.5)
        for _ in range(4):                                  # right-clicks with it: not a crouch
            PLAYERS.use("Steve")
            time.sleep(0.2)
        self.assertEqual(got, [])
        for _ in range(2):
            PLAYERS.sneak("Steve")
            time.sleep(0.3)
            PLAYERS.sneak("Steve", False)
            time.sleep(0.3)
        self.assertEqual(got, ["Steve", "Steve"])
        PLAYERS.hotbar("Steve", 5)                          # nothing in hand
        time.sleep(0.3)
        PLAYERS.sneak("Steve")
        time.sleep(0.3)
        PLAYERS.sneak("Steve", False)
        time.sleep(0.3)
        self.assertEqual(got, ["Steve", "Steve"])
        self.assertUnderstood()

    def test_a_player_hears_sees_and_reads_them(self):
        theme_mod.theme_music("Steve", vals(theme_mod, "theme_tune=spirit_bomb_throw", "theme_to=player"))
        self.assertTrue(count_mod.countdown(vals(count_mod, "countdown_seconds=2", "countdown_to=Steve")))
        at = charge_mod.charge_up("Steve", vals(charge_mod, "charge_style=orbs", "charge_seconds=0.5"))
        self.assertIsNotNone(at)
        replies = [r for c, r in SENT[self.since:]]
        self.assertEqual(len([r for r in replies if r.startswith("Played sound minecraft:block.note_block")
                              and r.endswith("to Steve")]) >= 9, True)
        self.assertEqual(len([r for r in replies if r == "Showing new title for Steve"]), 2)
        self.assertGreater(len([r for r in replies if r.startswith("Displaying particle")]), 5)
        self.assertUnderstood()

    def test_a_kill_and_where_steve_died(self):
        time.sleep(3.5)                                              # a player who just joined cannot be hurt
        got = collect(death_mod.on_death("Steve", vals(death_mod, "on_death_event=both")))
        time.sleep(0.6)
        x, y, z = PLAYERS.pos("Steve")
        mc.rcon('summon zombie %.1f %.1f %.1f {Tags:["%s"],NoAI:1b}' % (x + 2, y, z, TAG))
        self.assertIn("Applied", mc.rcon("damage @e[type=zombie,tag=%s,limit=1,sort=nearest,x=%.1f,y=%.1f,z=%.1f] 100 "
                                         "minecraft:player_attack by Steve" % (TAG, x + 2, y, z)))
        self.assertTrue(wait_for(lambda: got, 4), "no kill seen")
        self.assertEqual(got[0][1:], ("Steve", "kill"))
        self.assertLess(abs(got[0][0][0] - x) + abs(got[0][0][2] - z), 1.0)       # where the killer stands
        PLAYERS.tp("Steve", x + 3, y, z - 4)
        time.sleep(0.5)
        self.assertIn("Applied", mc.rcon("damage Steve 1000 minecraft:magic"))
        # A fake player leaves when it dies; a real one stays on the death screen. Either
        # way the death is counted when they are next read.
        time.sleep(1.0)
        PLAYERS.spawn("Steve")
        self.assertTrue(wait_for(lambda: len(got) >= 2, 5), "no death seen")
        at, p, what = got[1]
        print("\n  a kill where Steve stood, %s; Steve died at %s" % (got[0][0], at))
        self.assertEqual((p, what), ("Steve", "death"))
        self.assertLess(abs(at[0] - (x + 3)) + abs(at[2] - (z - 4)), 2.0)
        self.assertUnderstood()


# ---- each in a script of its own, stopped ---------------------------------------------------------

SCRIPT = '''"""Probe %(name)s: the preset copied in and run until stopped."""
ASKS = ["probe"]
from kit import values, loop
import mc

v = values()
mc.log("ready")
%(wiring)s
'''


class LiveStops(unittest.TestCase):
    """Copied in as `vfg script new --preset` copies it, run as on the box, stopped: out within
    2 s and nothing tagged with its instance left on the server."""

    def run_and_stop(self, name, preset, wiring, ready, *argv):
        item = library.parse(os.path.join(PRESETS, preset + ".py"), "preset")
        text = library.add_preset(SCRIPT % {"name": preset, "wiring": wiring}, item, "main")[0]
        folder = os.path.join(tempfile.mkdtemp(), name)
        os.makedirs(folder)
        with open(os.path.join(folder, "main.py"), "w") as f:
            f.write(text)
        run_dir = tempfile.mkdtemp()
        meta = values_meta(preset, given=argv)       # the meta file a copy gets: the preset's values, and player
        meta["values"]["player"] = "@a"
        with open(os.path.join(run_dir, name + ".json"), "w") as f:
            json.dump(meta, f)
        env = dict(os.environ, PYTHONPATH=LIB, VFG_INSTANCE=name, VFG_RUN_DIR=run_dir)
        proc = subprocess.Popen([sys.executable, "-u", "main.py"], cwd=folder, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        lines = []
        reader = threading.Thread(target=lambda: lines.extend(iter(proc.stdout.readline, b"")), daemon=True)
        reader.start()
        try:
            self.assertTrue(wait_for(lambda: any(b" ready" in l for l in lines) and ready(), 15), "never got going")
            time.sleep(1.0)
            t = time.time()
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
            took = time.time() - t
        finally:
            if proc.poll() is None:
                proc.kill()
        reader.join(2)
        proc.stdout.close()
        out = b"".join(lines).decode()
        print("\n  %s stopped in %.2f s" % (preset, took))
        self.assertLess(took, 2.0, out)
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("Traceback", out)
        self.assertIn("stopping (signal 15)", out)                 # it was running when told to stop
        tag = "vfg_" + name
        self.assertNotIn("passed", mc.rcon("execute if entity @e[tag=%s]" % tag).lower())
        self.assertNotIn(tag, mc.rcon("scoreboard objectives list"))
        return out

    def test_on_death_takes_its_objectives_away(self):
        self.run_and_stop("psdeath", "on_death", 'for at, p, what in loop(on_death(v["player"], v)):\n    pass',
                          lambda: "vfg_psdeath_kills" in mc.rcon("scoreboard objectives list"), "on_death_event=both")

    def test_a_countdown_stopped_half_way(self):
        self.run_and_stop("pscount", "countdown", "countdown(v)", lambda: True, "countdown_seconds=60")

    def test_a_charge_stopped_half_way(self):
        mc.rcon('summon armor_stand %.1f %d %.1f {Tags:["vfg_pscharge"],NoGravity:1b}' % (SPOT[0], FLOOR + 1, SPOT[2]))
        self.run_and_stop("pscharge", "charge_up",
                          'for k in loop(every=0.1):\n    charge_up("@e[tag=vfg_pscharge,limit=1]", v)',
                          lambda: True, "charge_seconds=10")

    def test_a_theme_record_stopped(self):
        self.run_and_stop("pstheme", "theme_music", 'theme_music("@a", v)\nfor k in loop(every=0.2):\n    pass',
                          lambda: True, "theme_tune=boss", "theme_to=everyone")

    def test_projectiles_watched_then_stopped(self):
        self.run_and_stop("psproj", "projectile_lands", 'for at, p, weapon in loop(projectile_lands(v["player"], v)):\n    pass',
                          lambda: True)
        self.assertNotIn("passed", mc.rcon("execute if entity @e[tag=vfg_psproj_seen]").lower())


if __name__ == "__main__":
    print("realmc %s: arena floor at %d %d %d; Carpet %s" % (os.environ.get("REALMC_NAME"), X, FLOOR, Z,
                                                             "yes" if CARPET else "no"))
    unittest.main(verbosity=2)
