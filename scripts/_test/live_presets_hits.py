"""The hits presets against a real Minecraft 26.3 server: damage_hits, freeze, push, around,
lock_on, lightning, explosion and crater, live.

    integration/realmc/up ph && eval "$(integration/realmc/env ph)"
    uv run --no-project --python 3.12 --with pydantic python scripts/_test/live_presets_hits.py

It changes blocks, so it runs only against a realmc test server (REALMC_NAME set, and
VFG_MC_PROPERTIES inside that server's folder), never against the box. Everything happens
in the sky over the arena (40 blocks above its floor, inside its forceloaded chunks) or 400
blocks out for the crater that loads its own chunks, and is taken away again. The presets
run in this process as the scripts that copy them would, with their values checked as vfg
action run checks a copy's (test_kit.values_meta), and once more copied into one script by
library.add_preset (what `vfg script new` does), started with the meta file a copy gets, and
stopped with SIGTERM. The player parts need Carpet's fake players and are skipped without it.
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
sys.path.append(os.path.dirname(os.path.dirname(HERE)))  # the repo: a preset's values class imports actions._types
sys.path[:0] = [LIB, PRESETS, os.path.join(REPO, "toolkit", "lib"), HERE]

PROPS = os.environ.get("VFG_MC_PROPERTIES", "")
if not os.environ.get("REALMC_NAME") or "realmc" not in os.path.realpath(PROPS):
    sys.exit("live_presets_hits: point it at a realmc server first: eval \"$(integration/realmc/env ph)\"")
os.environ["VFG_INSTANCE"] = "livehits"
TMP = tempfile.mkdtemp(prefix="vfg-live-hits-")
os.environ["VFG_RUN_DIR"] = TMP

import mc  # noqa: E402
from kit import aim, area, values, world  # noqa: E402
import library  # noqa: E402
from test_kit import values_meta  # noqa: E402
import around as around_preset  # noqa: E402
import crater as crater_preset  # noqa: E402
import damage_hits as damage_preset  # noqa: E402
import explosion as explosion_preset  # noqa: E402
import freeze as freeze_preset  # noqa: E402
import lightning as lightning_preset  # noqa: E402
import lock_on as lock_preset  # noqa: E402
import push as push_preset  # noqa: E402
import launch as launch_preset  # noqa: E402

push_preset.launch = launch_preset.launch       # push throws a player by launch: a script holds both

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)

POS = re.compile(r"\[(-?[\d.eE-]+)d, (-?[\d.eE-]+)d, (-?[\d.eE-]+)d\]")


def find_floor():
    """The arena's floor under its middle: the top solid block at 0, 0."""
    r = aim.cast((0.5, 300.0, 0.5), (0.0, -1.0, 0.0), 400)
    assert r.why == "block", "no floor under the arena: %s" % r.why
    return int(r.hit_block[1])


FLOOR = find_floor()
SKY = FLOOR + 40            # the tests build up here, inside the arena's forceloaded chunks (x, z -32..31)
SKYBOX = (-32, SKY - 3, -32, 31, SKY + 26, 31)
TAG = "lh"                  # every test mob carries it


def vals(preset, **given):
    """A preset's values as a script would have them: its defaults, then these, checked."""
    return values(meta=values_meta(preset, given=given), run_dir=TMP, instance="livehits")


def push_vals(**given):
    """push's values and launch's, which push reads to throw a player."""
    return values(meta=values_meta(push_preset, launch_preset, given=given), run_dir=TMP, instance="livehits")


def landed(sel, seconds=8.0):
    """Where `sel` comes to rest after a throw (two reads 0.25 s apart the same), and the top it reached."""
    last, top, end = where(sel), -1e9, time.time() + seconds
    while time.time() < end:
        time.sleep(0.25)
        now = where(sel)
        top = max(top, now[1])
        if max(abs(a - b) for a, b in zip(now, last)) < 1e-3:
            return now, top
        last = now
    return last, top


def start(folder, name, presets, **given):
    """A script started as the box starts a copy: its meta file holds every value of the presets'
    classes, with player=@a and these given, checked as vfg action run checks them."""
    meta = values_meta(*presets, given={k: v for k, v in given.items() if k != "player"})
    meta["values"]["player"] = given.get("player", "@a")
    with open(os.path.join(TMP, name + ".json"), "w") as f:
        __import__("json").dump(meta, f)
    env = dict(os.environ, PYTHONPATH=LIB, VFG_INSTANCE=name)
    return subprocess.Popen([sys.executable, "-u", "main.py"], cwd=folder, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def summon(kind, x, y, z, extra=""):
    nbt = 'NoAI:1b,NoGravity:1b,PersistenceRequired:1b,Tags:["%s"],attributes:[{id:"max_health",base:100}],Health:100f'
    reply = mc.rcon("summon %s %.2f %.2f %.2f {%s%s}" % (kind, x, y, z, nbt % TAG, extra))
    assert "Summoned" in reply, reply
    return "@e[tag=%s,x=%.2f,y=%.2f,z=%.2f,distance=..0.3,limit=1]" % (TAG, x, y, z)


def health(sel):
    m = re.search(r"(-?[\d.]+)f", mc.rcon("data get entity %s Health" % sel))
    return float(m.group(1)) if m else None


def where(sel):
    m = POS.search(mc.rcon("data get entity %s Pos" % sel))
    return tuple(float(c) for c in m.groups()) if m else None


def block(x, y, z, kind):
    return "passed" in mc.rcon("execute if block %d %d %d %s" % (x, y, z, kind)).lower()


def eye(x, y, z, yaw=-90.0, pitch=0.0):
    """An invisible armor stand to aim from: aim.look reads its position and rotation."""
    mc.rcon('summon armor_stand %.2f %.2f %.2f {Tags:["%s","lh_eye"],NoGravity:1b,Invisible:1b,Rotation:[%sf,%sf]}'
            % (x, y, z, TAG, yaw, pitch))
    return "@e[tag=lh_eye,limit=1]"


def platform(x1, z1, x2, z2, kind="glass", y=None):
    area.fill((x1, (SKY - 1) if y is None else y, z1, x2, (SKY - 1) if y is None else y, z2), kind)


def leftover_tags():
    """Every tag on anything in the sky that this test's presets would have put there."""
    reply = mc.rcon("execute as @e[x=-32,y=%d,z=-32,dx=64,dy=40,dz=64] run data get entity @s Tags" % (SKY - 3))
    return sorted(set(t for t in re.findall(r'"([^"]+)"', reply) if t.startswith("vfg_livehits_")))



class Live(unittest.TestCase):
    def setUp(self):
        self.forced = mc.rcon("forceload query")

    def tearDown(self):
        mc.rcon("kill @e[tag=%s]" % TAG)
        mc.rcon("kill @e[tag=vfg_livehits]")          # what a stop kills: bolts, TNT
        mc.rcon("kill @e[type=item,x=-32,y=%d,z=-32,dx=64,dy=40,dz=64]" % (SKY - 3))
        area.fill(SKYBOX, "air", most_mspt=0)
        self.assertEqual(leftover_tags(), [])
        self.assertEqual(mc.rcon("forceload query"), self.forced, "a forceload was left behind")


class LiveHostile(Live):
    def test_hostile_mobs_only_and_a_tag_left_from_before_spares_nobody(self):
        z = -20.5
        platform(-30, -24, 10, -17)
        shooter = eye(-27.5, SKY, z)
        husk = summon("husk", -21.5, SKY, z)
        cow = summon("cow", -18.5, SKY, z)
        creeper = summon("creeper", -15.5, SKY, z, ",ExplosionRadius:0b")
        import itertools
        damage_preset._DAMAGE_N = itertools.cycle(range(1, 17))   # a new process: its names from the first,
        aim._cleared.clear()                                      # none of them cleared yet
        mc.rcon("tag %s add vfg_livehits_hit1" % creeper)   # as an earlier process leaves on one it killed mid-shot
        r = aim.ray(aim.look(shooter), 30)
        hits = aim.near_line(r, 1.5, with_players=False)
        self.assertEqual(len(hits), 3, hits)
        n = damage_preset.damage_hits(hits, "", vals(damage_preset, damage_amount=10, damage_what="hostile",
                                                     damage_type="magic"))
        self.assertEqual(n, 2)
        self.assertEqual((health(husk), health(cow), health(creeper)), (90.0, 100.0, 90.0))
        got = around_preset.around((-18.5, SKY, z), vals(around_preset, around_radius=6, around_what="hostile"))
        self.assertEqual(len(got), 2, got)
        self.assertEqual(leftover_tags(), [])


class LiveDamage(Live):
    def test_once_each_sparing_pets_villagers_and_the_spared(self):
        z = -20.5
        platform(-30, -24, 10, -17)
        shooter = eye(-27.5, SKY, z)
        near = summon("husk", -21.5, SKY, z)
        mid = summon("husk", -15.5, SKY, z)
        aside = summon("husk", -15.5, SKY, z + 4)
        villager = summon("villager", -18.5, SKY, z)
        wolf = summon("wolf", -12.5, SKY, z, ",Owner:[I;1,2,3,4]")
        named = summon("husk", -7.5, SKY, z, ',CustomName:"Spared"')
        r = aim.ray(shooter, 30)
        line = aim.near_line(r, 1.5, shooter=shooter)
        self.assertEqual([h.name for h in line], ["Husk", "Husk", "Spared"])
        blast = around_preset.around(r.point(12), vals(around_preset, around_radius=4))
        self.assertEqual(len(blast), 1)                 # the middle husk, listed again
        n = damage_preset.damage_hits(line + blast, shooter, vals(damage_preset, damage_amount=10,
                                                                  damage_spare="Spared"))
        self.assertEqual(n, 2)
        lost = [100 - health(s) for s in (near, mid)]
        self.assertTrue(all(8 < x < 11 for x in lost), lost)     # once each: 10, less a husk's armor
        for s in (aside, villager, wolf, named):
            self.assertEqual(health(s), 100.0, s)

    def test_kill_the_first_only(self):
        z = -12.5
        platform(-30, -15, 10, -10)
        shooter = eye(-27.5, SKY, z)
        a, b = summon("husk", -20.5, SKY, z), summon("husk", -14.5, SKY, z)
        hits = aim.near_line(aim.ray(shooter, 30), 1.5, shooter=shooter)
        self.assertEqual(damage_preset.damage_hits(hits, shooter, vals(damage_preset, damage_kill=True,
                                                                       damage_most=1)), 1)
        self.assertIsNone(health(a))
        self.assertEqual(health(b), 100.0)

    def test_double_on_the_head(self):
        z = -4.5
        platform(-30, -7, 10, -2, y=SKY - 3)
        shooter = eye(-27.5, SKY, z)
        look = aim.look(shooter)
        head = summon("husk", -20.5, look.origin[1] - 1.7, z)     # the look passes its head
        body = summon("husk", -14.5, look.origin[1] - 0.9, z)     # and this one's middle
        hits = aim.near_line(aim.ray(look, 30), 1.0, shooter=shooter)
        self.assertEqual(len(hits), 2)
        damage_preset.damage_hits(hits, look, vals(damage_preset, damage_amount=10, damage_head=2))
        on_head, on_body = 100 - health(head), 100 - health(body)
        self.assertGreater(on_head, 1.8 * on_body, (on_head, on_body))
        self.assertGreater(on_body, 8)

    def test_as_nobody_when_the_shooter_is_gone(self):
        z = 3.5
        platform(-30, 1, 10, 6)
        shooter = eye(-27.5, SKY, z)
        target = summon("husk", -20.5, SKY, z)
        hits = aim.near_line(aim.ray(shooter, 30), 1.5, shooter=shooter)
        self.assertEqual(damage_preset.damage_hits(hits, "Nobody_Here", vals(damage_preset, damage_amount=10)), 1)
        self.assertLess(health(target), 95)


class LiveWhere(Live):
    def test_around_finds_more_than_one_reply_holds(self):
        platform(-30, -30, -10, -10)
        for i in range(10):
            for k in range(9):
                summon("pig", -25.5 + i, SKY, -24.5 + k)
        reply = mc.rcon("execute positioned -21 %d -20 as @e[tag=%s,distance=..9] run data get entity @s Pos" % (SKY, TAG))
        self.assertLess(len(POS.findall(reply)), 90, "the reply was not cut: the test proves nothing")
        hits = around_preset.around(world.Pos(-21.0, SKY, -20.0), vals(around_preset, around_radius=9))
        self.assertEqual(len(hits), 90)
        self.assertEqual(len(set(tuple(h.pos) for h in hits)), 90)
        self.assertEqual([h.t for h in hits], sorted(h.t for h in hits))
        few = around_preset.around((-21.0, SKY, -20.0), vals(around_preset, around_radius=9, around_most=5))
        self.assertEqual([h.t for h in few], [h.t for h in hits[:5]])

    def test_around_a_player_name_that_is_gone(self):
        self.assertEqual(around_preset.around("Nobody_Here", vals(around_preset)), [])

    def test_lock_on_the_nearest_in_the_cone_never_behind_a_wall(self):
        z = 12.5
        platform(-30, 5, 20, 20)
        shooter = eye(-27.5, SKY, z)
        summon("husk", -9.5, SKY, z + 0.8)            # 18 out, a little off the line: in the cone
        summon("husk", -2.5, SKY, z)                  # 25 out, on the line: further
        summon("husk", -21.5, SKY, z + 5)             # 6 out, 40 degrees aside: not in the cone
        summon("villager", -15.5, SKY, z)             # never a target
        got = lock_preset.lock_on(shooter, vals(lock_preset))
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(got[0].pos[0], -9.5, places=1)
        area.fill((-12, SKY, 9, -12, SKY + 3, 16), "stone")
        self.assertEqual(lock_preset.lock_on(shooter, vals(lock_preset)), [])
        self.assertEqual(lock_preset.lock_on("Nobody_Here", vals(lock_preset)), [])


class LiveWhat(Live):
    def test_freeze_stops_wraps_and_melts(self):
        platform(-30, -30, -10, -10, "stone")
        husk = summon("husk", -20.5, SKY, -20.5)
        villager = summon("villager", -14.5, SKY, -20.5)
        hits = around_preset.around((-17.5, SKY, -20.5), vals(around_preset, around_radius=6, around_what="everything"))
        self.assertEqual(len(hits), 1)          # around never gives the villager
        hits.append(aim.Hit(1.0, world.Pos(-14.5, SKY, -20.5), "Villager", False))     # nor will freeze take it
        v = vals(freeze_preset, freeze_seconds=2, freeze_shell=True, freeze_block="packed_ice")
        self.assertEqual(freeze_preset.freeze(hits, v), 1)
        effects = mc.rcon("data get entity %s active_effects" % husk)
        self.assertIn('"minecraft:slowness"', effects)
        self.assertIn('"minecraft:weakness"', effects)
        self.assertGreater(int(re.search(r"(\d+)", mc.rcon("data get entity %s TicksFrozen" % husk).split(":")[-1]).group(1)), 140)
        self.assertNotIn("slowness", mc.rcon("data get entity %s active_effects" % villager))
        x, y, z = -21, SKY, -21
        for c in [(x, y - 1, z), (x, y + 2, z), (x - 1, y, z), (x + 1, y + 1, z), (x, y, z - 1), (x + 1, y + 1, z + 1)]:
            if c != (x, y - 1, z):              # the floor under it was stone, and stays stone
                self.assertTrue(block(*(c + ("packed_ice",))), c)
        self.assertTrue(block(x, y - 1, z, "stone"))
        self.assertTrue(block(x, y, z, "air") and block(x, y + 1, z, "air"))
        self.assertFalse(block(-15, y, -21, "packed_ice"))        # no shell round the villager
        time.sleep(3.0)                                 # 2 s of freeze, then the shell melts
        for c in [(x, y + 2, z), (x - 1, y, z), (x + 1, y + 1, z), (x, y, z - 1), (x + 1, y + 1, z + 1)]:
            self.assertTrue(block(*(c + ("air",))), c)
        self.assertTrue(block(x, y - 1, z, "stone"))

    def test_push_away_toward_and_up(self):
        platform(-31, -31, 31, -9, "stone")
        frm = world.Pos(-20.5, SKY, -20.5)
        pigs = {"away": (-17.5, -20.5), "toward": (-6.5, -20.5), "up": (-20.5, -14.5)}
        sels = {}
        for way, (x, z) in pigs.items():
            mc.rcon('summon pig %.2f %d %.2f {PersistenceRequired:1b,Tags:["%s","lh_%s"]}' % (x, SKY, z, TAG, way))
            sels[way] = "@e[tag=lh_%s,limit=1]" % way
        mc.rcon("effect give @e[tag=%s,type=pig] slowness 30 9 true" % TAG)
        time.sleep(1.0)
        start = dict((w, where(s)) for w, s in sels.items())
        top = start["up"][1]
        for way in ("away", "toward", "up"):
            hits = around_preset.around(start[way], vals(around_preset, around_radius=1))
            self.assertEqual(push_preset.push(hits, frm, vals(push_preset, push_way=way, push_strength=6)), 1, way)
        for _ in range(30):
            time.sleep(0.1)
            top = max(top, where(sels["up"])[1])
        end = dict((w, where(s)) for w, s in sels.items())
        self.assertGreater(end["away"][0] - start["away"][0], 3.5, (start, end))      # about 6 out
        self.assertLess(end["toward"][0] - start["toward"][0], -3.5, (start, end))    # about 6 in
        self.assertGreater(top - start["up"][1], 3.5)                                  # about 6 up
        self.assertEqual(push_preset.push([], "Nobody_Here", vals(push_preset)), 0)

    def test_lightning_ring_one_by_one(self):
        platform(-30, -30, -10, -10, "stone")
        at = world.Pos(-20.5, SKY, -20.5)
        t = time.time()
        n = lightning_preset.lightning(at, "", vals(lightning_preset, lightning_count=5, lightning_layout="ring",
                                                    lightning_spread=3, lightning_every=0.3))
        took = time.time() - t
        self.assertEqual(n, 5)
        self.assertGreaterEqual(took, 1.15)
        self.assertLess(took, 3.0)
        time.sleep(1.5)                                 # the last bolt is gone
        n = lightning_preset.lightning(at, "", vals(lightning_preset, lightning_count=4, lightning_spread=2))
        bolts = re.search(r"Count: (\d+)", mc.rcon("execute if entity @e[type=lightning_bolt,tag=vfg_livehits]"))
        self.assertEqual(n, 4)
        self.assertEqual(int(bolts.group(1)) if bolts else 0, 4)

    def test_explosion_tnt_breaks_blocks_and_spares_pets_and_villagers(self):
        platform(-30, -30, -10, -10, "stone")
        husk = summon("husk", -18.5, SKY, -20.5)
        wolf = summon("wolf", -22.5, SKY, -20.5, ",Owner:[I;1,2,3,4]")
        villager = summon("villager", -20.5, SKY, -17.5)
        self.assertEqual(explosion_preset.explosion(world.Pos(-20.5, SKY, -20.5), "", vals(explosion_preset)), 1)
        time.sleep(1.0)
        self.assertTrue(block(-21, SKY - 1, -21, "air"), "TNT broke nothing")
        h = health(husk)
        self.assertTrue(h is None or h < 80, h)
        self.assertEqual(health(wolf), 100.0)
        self.assertEqual(health(villager), 100.0)

    def test_explosion_that_breaks_nothing_in_a_plus(self):
        platform(-30, -30, -10, -10, "stone")
        husk = summon("husk", -18.5, SKY, -20.5)
        far = summon("husk", -11.5, SKY, -11.5)
        wolf = summon("wolf", -22.5, SKY, -20.5, ",Owner:[I;1,2,3,4]")
        v = vals(explosion_preset, explosion_breaks=False, explosion_layout="plus", explosion_spread=3)
        self.assertEqual(explosion_preset.explosion(world.Pos(-20.5, SKY, -20.5), "", v), 5)
        time.sleep(0.5)
        self.assertEqual(mc.rcon("fill -30 %d -30 -10 %d -10 air replace stone" % (SKY - 1, SKY - 1)),
                         "Successfully filled 441 block(s)", "a blast that breaks nothing broke something")
        self.assertLess(health(husk), 70)
        self.assertEqual(health(far), 100.0)
        self.assertEqual(health(wolf), 100.0)

    def test_crater_leaves_a_lava_pool_or_a_burning_floor(self):
        for cx, leave in ((-20, "lava"), (10, "fire")):
            area.fill((cx - 9, SKY, -29, cx + 9, SKY + 18, -11), "stone")
            mid = world.Pos(cx + 0.5, SKY + 9.5, -19.5)
            n = crater_preset.crater(mid, vals(crater_preset, crater_radius=7, crater_leave=leave))
            self.assertGreater(n, 1000)
            self.assertTrue(block(cx, SKY + 9, -20, "air"))
            self.assertTrue(block(cx + 6, SKY + 9, -20, "air"))
            self.assertTrue(block(cx + 9, SKY + 9, -20, "stone"))
            self.assertTrue(block(cx, SKY + 17, -20, "stone"))
            if leave == "lava":
                self.assertTrue(block(cx, SKY + 2, -20, "lava"))       # the pool at the bottom
                self.assertTrue(block(cx, SKY + 6, -20, "air"))        # and air above it
            else:
                self.assertTrue(block(cx, SKY + 2, -20, "fire"))       # fire on the floor
                self.assertTrue(block(cx, SKY + 5, -20, "air"))        # none in the air over it
        area.fill((-32, SKY, -32, 31, SKY + 20, -9), "air", "replace lava", most_mspt=0)

    def test_a_crater_out_of_loaded_land_loads_it_and_lets_go(self):
        at = world.Pos(400.5, SKY + 9.5, 400.5)
        cube = (390, SKY, 390, 410, SKY + 19, 410)
        area.forceload(cube, wait=20)
        area.fill(cube, "stone")
        area.release()
        self.assertEqual(mc.rcon("forceload query"), self.forced)
        n = crater_preset.crater(at, vals(crater_preset, crater_radius=8))
        self.assertGreater(n, 1500)
        self.assertEqual(mc.rcon("forceload query"), self.forced)
        area.forceload(cube, wait=20)
        try:
            self.assertTrue(block(400, SKY + 9, 400, "air"))
            self.assertTrue(block(407, SKY + 9, 400, "air"))
            self.assertTrue(block(410, SKY + 9, 410, "stone"))
            area.fill(cube, "air")
        finally:
            area.release()


SCRIPT_WIRING = '''
eye = "@e[tag=lh_eye,limit=1]"
r = aim.ray(eye, 30)
hits = aim.near_line(r, 1.5, shooter=eye) + around(r.end, v) + lock_on(eye, v)
mc.log("hurt", damage_hits(hits, eye, v))
mc.log("frozen", freeze(hits, v))
mc.log("pushed", push(hits, r.start, v))
mc.log("struck", lightning(r.point(20), "", v))
mc.log("blasts", explosion(r.point(20), "", v))
mc.log("carved", crater(r.point(20), v))
mc.log("ready")
for n in loop(every=0.5):
    pass
'''


class LiveScript(Live):
    """All eight (and launch, which push throws a player by) copied into one script, as `vfg
    script new` does, run as on EC2 and stopped."""

    def test_eight_presets_in_one_script_run_and_stop_clean(self):
        text = library.SKELETON.replace("from kit import result, values", "from kit import values, aim, loop\nimport mc")
        names = ("damage_hits", "freeze", "push", "around", "lock_on", "lightning", "explosion", "crater", "launch")
        fields = set(["player"])            # its action's: the presets' values and a player
        for name in names:
            item = library.parse(os.path.join(PRESETS, name + ".py"), "preset")
            text, keys, _imports = library.add_preset(text, item, "main")
            fields.update(keys)
        folder = os.path.join(TMP, "hitsprobe")
        os.makedirs(folder)
        with open(os.path.join(folder, "main.py"), "w") as f:
            f.write(text + SCRIPT_WIRING)
        self.assertEqual([p for p in library.check(library.parse(os.path.join(folder, "main.py"), "script"), None, fields)
                          if "TODO" not in p and "ASKS" not in p], [])
        platform(-30, -30, 10, -10, "stone")
        eye(-27.5, SKY, -20.5)
        summon("husk", -20.5, SKY, -20.5)
        summon("husk", -10.5, SKY, -20.5)
        proc = start(folder, "hitsprobe", names, freeze_shell=True, freeze_seconds=60, explosion_breaks=False,
                     crater_radius=3, lightning_count=2, damage_amount=5)
        out = b""
        deadline = time.time() + 30
        while b"ready" not in out and time.time() < deadline and proc.poll() is None:
            out += proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(1)
        text_out = out.decode()
        self.assertIn("ready", text_out)
        self.assertRegex(text_out, r"hurt [12]\b")
        self.assertRegex(text_out, r"frozen [12]\b")
        self.assertTrue(block(-22, SKY + 2, -21, "ice"), "no shell")
        t = time.time()
        proc.send_signal(signal.SIGTERM)
        rest = proc.communicate(timeout=5)[0].decode()
        self.assertLess(time.time() - t, 2.0)
        self.assertEqual(proc.returncode, 0, text_out + rest)
        self.assertNotIn("Traceback", text_out + rest)
        self.assertFalse(block(-22, SKY + 2, -21, "ice"), "the shell was left after the stop")
        self.assertIn("Test failed", mc.rcon("execute if entity @e[tag=vfg_hitsprobe]"))


REALMC = os.path.join(REPO, "integration", "realmc")
sys.path.insert(0, REALMC)
try:
    import players as fake_players  # noqa: E402
    PLAYERS = fake_players.on()
    CARPET = "Unknown or incomplete" not in PLAYERS.cmd("carpet")
except Exception:   # no Carpet on this server, or no realmc beside the repo
    CARPET = False


@unittest.skipUnless(CARPET, "the server has no Carpet: integration/realmc/realmc.py carpet, then up --fresh")
class LivePlayers(Live):
    """Steve shoots and is spared; Alex is hit, frozen, pushed and spared by name."""

    def setUp(self):
        super(LivePlayers, self).setUp()
        platform(-31, -31, 31, -9, "stone")
        PLAYERS.spawn("Steve", at=(-27.5, SKY, -20.5), facing=(-90, 0))
        PLAYERS.spawn("Alex", at=(-17.5, SKY, -20.5), facing=(90, 0))
        time.sleep(3.5)                                 # a player who just joined cannot be hurt for 3 s

    def tearDown(self):
        mc.rcon("effect clear Steve")
        mc.rcon("effect clear Alex")
        PLAYERS.kill_all()
        super(LivePlayers, self).tearDown()

    def test_a_player_hit_along_a_ray_and_one_spared_by_name(self):
        hits = aim.near_line(aim.ray("Steve", 30), 1.5, shooter="Steve")
        self.assertEqual([(h.name, h.player) for h in hits], [("Alex", True)])
        self.assertEqual(damage_preset.damage_hits(hits, "Steve", vals(damage_preset, damage_amount=4)), 1)
        self.assertLess(health("Alex"), 20)
        mc.rcon("effect give Alex instant_health 1 5 true")
        time.sleep(0.6)
        self.assertEqual(damage_preset.damage_hits(hits, "Steve", vals(damage_preset, damage_spare="Alex")), 0)
        self.assertEqual(health("Alex"), 20.0)

    def test_around_a_player_never_holds_that_player(self):
        got = around_preset.around("Steve", vals(around_preset, around_radius=12, around_what="players"))
        self.assertEqual([h.name for h in got], ["Alex"])

    def test_a_player_frozen_and_pushed(self):
        hits = around_preset.around("Steve", vals(around_preset, around_radius=12, around_what="players"))
        self.assertEqual(freeze_preset.freeze(hits, vals(freeze_preset, freeze_seconds=2)), 1)
        self.assertIn('"minecraft:slowness"', mc.rcon("data get entity Alex active_effects"))
        before = where("Alex")
        self.assertEqual(push_preset.push(hits, "Steve", push_vals(push_strength=6)), 1)
        self.assertGreater(landed("Alex")[0][0] - before[0], 2.5)       # thrown by launch, frozen as he is

    def test_lightning_and_tnt_spare_who_set_them_off(self):
        platform(-31, -31, 31, -9, "obsidian")          # TNT leaves the floor under him
        steve = where("Steve")
        self.assertEqual(lightning_preset.lightning(steve, "Steve", vals(lightning_preset)), 0)
        self.assertEqual(lightning_preset.lightning(steve, "", vals(lightning_preset)), 1)
        time.sleep(0.6)
        self.assertLess(health("Steve"), 20.0)          # a bolt at his feet, spared by nobody
        mc.rcon("effect give Steve instant_health 1 5 true")
        mc.rcon("effect give Steve fire_resistance 10 0 true")
        time.sleep(0.6)
        self.assertEqual(health("Steve"), 20.0)
        self.assertEqual(explosion_preset.explosion(world.Pos(steve[0] + 2, steve[1], steve[2]), "Steve",
                                                    vals(explosion_preset)), 1)
        time.sleep(1.0)
        self.assertEqual(health("Steve"), 20.0)         # resistance V for two seconds
        v = vals(explosion_preset, explosion_keep=6)
        self.assertEqual(explosion_preset.explosion(world.Pos(steve[0] + 2, steve[1], steve[2]), "", v), 0)


    def test_lock_on_where_a_player_looks(self):
        target = summon("husk", -9.5, SKY, -19.5)
        summon("husk", -21.5, SKY, -15.5)                # nearer, but off to the side
        PLAYERS.look_at("Steve", -9.5, SKY + 1, -19.5)
        time.sleep(0.5)
        got = lock_preset.lock_on("Steve", vals(lock_preset))
        self.assertEqual([round(h.pos[0], 1) for h in got], [-9.5])
        self.assertEqual(damage_preset.damage_hits(got, "Steve", vals(damage_preset, damage_amount=10)), 1)
        self.assertLess(health(target), 95)

    def test_a_player_lifted_and_pulled_in(self):
        start = where("Alex")
        hits = around_preset.around("Steve", vals(around_preset, around_radius=12, around_what="players"))
        self.assertEqual(push_preset.push(hits, "Steve", push_vals(push_way="up", push_strength=4)), 1)
        at, top = landed("Alex")
        self.assertAlmostEqual(top - start[1], 4.0, delta=0.5)          # launched 4 up, and down again
        hits = around_preset.around("Steve", vals(around_preset, around_radius=12, around_what="players"))
        self.assertEqual(push_preset.push(hits, "Steve", push_vals(push_way="toward", push_strength=5)), 1)
        self.assertLess(landed("Alex")[0][0] - at[0], -3.0)


STAFF = '''
for p in loop(items.clicks(v["player"], "Ice Staff", every=0.05)):
    hits = lock_on(p, v)
    mc.log("frozen", freeze(hits, v), "hurt", damage_hits(hits, p, v))
'''


@unittest.skipUnless(CARPET, "the server has no Carpet: integration/realmc/realmc.py carpet, then up --fresh")
class LiveStaff(Live):
    """The ice staff as a script copies it: a right-click, the nearest mob in the cone where the
    player looks, frozen in a shell and hurt, by Steve himself."""

    def test_a_click_locks_on_freezes_and_hurts(self):
        text = library.SKELETON.replace("from kit import result, values", "from kit import values, items, loop\nimport mc")
        for name in ("lock_on", "freeze", "damage_hits"):
            text = library.add_preset(text, library.parse(os.path.join(PRESETS, name + ".py"), "preset"), "main")[0]
        folder = os.path.join(TMP, "icestaffprobe")
        os.makedirs(folder)
        with open(os.path.join(folder, "main.py"), "w") as f:
            f.write(text + STAFF)
        platform(-31, -31, 31, -9, "stone")
        husk = summon("husk", -12.5, SKY, -20.5)
        PLAYERS.spawn("Steve", at=(-27.5, SKY, -20.5), facing=(-90, 0))
        PLAYERS.clear("Steve")
        proc = start(folder, "icestaffprobe", ("lock_on", "freeze", "damage_hits"), player="Steve",
                     freeze_shell=True, freeze_seconds=30, damage_amount=8)
        try:
            deadline = time.time() + 10
            while "passed" not in mc.rcon('execute if items entity Steve container.* *[custom_data~{vfg_item:"ice_staff"}]').lower() \
                    and time.time() < deadline:
                time.sleep(0.2)
            for slot in range(1, 10):
                PLAYERS.hotbar("Steve", slot)
                time.sleep(0.2)
                if "passed" in mc.rcon('execute if items entity Steve weapon.mainhand *[custom_data~{vfg_item:"ice_staff"}]').lower():
                    break
            PLAYERS.look_at("Steve", -12.5, SKY + 1, -20.5)
            time.sleep(3.0)                             # Steve just joined: he cannot hurt anything yet... nor be
            PLAYERS.use("Steve")
            deadline = time.time() + 5
            while "slowness" not in mc.rcon("data get entity %s active_effects" % husk) and time.time() < deadline:
                time.sleep(0.2)
            frozen = mc.rcon("data get entity %s active_effects" % husk)
            hurt, shell = health(husk), block(-13, SKY + 2, -21, "ice")
        finally:
            proc.send_signal(signal.SIGTERM)
            out = proc.communicate(timeout=5)[0].decode()
            PLAYERS.kill_all()
        self.assertIn('"minecraft:slowness"', frozen, out)
        self.assertLess(hurt, 95)
        self.assertTrue(shell, "no shell")
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("Traceback", out)
        self.assertRegex(out, r"frozen 1 hurt 1")
        self.assertFalse(block(-13, SKY + 2, -21, "ice"), "the shell was left after the stop")


if __name__ == "__main__":
    print("realmc %s: arena floor at y %d, tests at y %d" % (os.environ.get("REALMC_NAME"), FLOOR, SKY))
    unittest.main(verbosity=2)
