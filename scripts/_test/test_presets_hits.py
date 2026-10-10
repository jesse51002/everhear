"""Tests for the hits presets (damage_hits, freeze, push, around, lock_on, lightning,
explosion, crater) against the fake RCON server and fake_world, with pydantic for a preset's
values class:

    uv run --no-project --python 3.12 --with pydantic python scripts/_test/test_presets_hits.py

Each preset is loaded from scripts/_presets/ as it is and run in this process against a
fresh fake world (test_kit's KitCase, with a few more commands the hits presets send:
effect, data modify and merge, tp, damage by someone who must be there, a tag added twice,
and a volume selector). It sends the commands it should, and a script that copies all eight
leaves nothing behind after a stop (its values class is checked with every preset's, in
test_presets_values.py).
"""
import importlib.util
import math
import os
import re
import secrets
import signal
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
PRESETS = os.path.join(SCRIPTS, "_presets")
sys.path.append(os.path.dirname(os.path.dirname(HERE)))  # the repo: a preset's values class imports actions._types
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPTS), "toolkit", "lib"))

import test_kit  # noqa: E402
from test_kit import KitCase, start_script, TMP  # noqa: E402
from fake_rcon import FakeRcon  # noqa: E402
from fake_world import World, OVERWORLD, _split_opts, _token  # noqa: E402
import library  # noqa: E402
import mc  # noqa: E402
from kit import aim, values, world  # noqa: E402

NAMES = ["damage_hits", "freeze", "push", "around", "lock_on", "lightning", "explosion", "crater"]


def load(name):
    """The preset as a module, from its file."""
    spec = importlib.util.spec_from_file_location("preset_" + name, os.path.join(PRESETS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = {n: load(n) for n in NAMES}


def vals(name, **given):
    """A preset's values: its defaults, then these, checked as vfg action run checks them."""
    return values(meta=test_kit.values_meta(P[name], given=given), run_dir=tempfile.mkdtemp(dir=TMP), instance="t")


class HitsWorld(World):
    """fake_world, and what the hits presets send besides: effects, Motion, TicksFrozen, tp, a
    volume (dx, dy, dz), damage `by` someone who must be there, and a tag already there."""

    def _select(self, sel, ctx):
        m = re.match(r"^(@[aeprs])\[(.*)\]$", sel)
        if not m:
            return World._select(self, sel, ctx)
        opts = _split_opts(m.group(2))
        box = dict(o.split("=", 1) for o in opts if o.split("=")[0] in ("dx", "dy", "dz"))
        rest = [o for o in opts if o.split("=")[0] not in ("dx", "dy", "dz")]
        es = World._select(self, "%s[%s]" % (m.group(1), ",".join(rest)) if rest else m.group(1), ctx)
        if box:
            lo = ctx[2]
            es = [e for e in es if all(lo[i] <= e["pos"][i] < lo[i] + float(box["d" + "xyz"[i]]) + 1 for i in range(3))]
        return es

    def _cmd_tag(self, args, ctx):
        sel, rest = _token(args)
        op, tag = _token(rest)
        es = self._select(sel, ctx)
        if op == "add":
            new = [e for e in es if tag.strip() not in e["tags"]]
            if not new:
                return "Target either already has the tag or has too many tags" if es else "No entity was found"
            for e in new:
                e["tags"].add(tag.strip())
            return "Added tag '%s' to %s" % (tag.strip(), new[0]["name"] if len(new) == 1 else "%d entities" % len(new))
        return World._cmd_tag(self, args, ctx)

    def _cmd_damage(self, args, ctx):
        m = re.search(r" by (\S+)", args)
        if m and not self._select(m.group(1), ctx):
            return ""                                   # 26.3, inside execute: nothing
        return World._cmd_damage(self, args, ctx)

    def _cmd_effect(self, args, ctx):
        m = re.match(r"give (\S+) (\S+) (\d+) (\d+)", args)
        es = self._select(m.group(1), ctx) if m else []
        for e in es:
            e.setdefault("effects", {})[m.group(2).replace("minecraft:", "")] = (int(m.group(3)), int(m.group(4)))
        return "Applied effect %s to %s" % (m.group(2), es[0]["name"]) if es else "No entity was found"

    def _cmd_data(self, args, ctx):
        m = re.match(r"(modify|merge) entity (\S+) (.*)$", args)
        if not m:
            return World._cmd_data(self, args, ctx)
        es = self._select(m.group(2), ctx)
        for e in es:
            if e["type"] == "player":
                return "Unable to modify player data"
            n = re.findall(r"-?[\d.]+", m.group(3))
            if m.group(1) == "modify":
                e["motion"] = tuple(float(x) for x in n)
            else:
                e["frozen"] = int(n[0])
        return "Modified entity data of %s" % es[0]["name"] if es else "No entity was found"

    def _cmd_tp(self, args, ctx):
        who, rest = _token(args)
        xyz, _ = self._coords(rest, ctx[2])
        es = self._select(who, ctx)
        for e in es:
            e["pos"] = list(xyz)
        return "Teleported %s to %.1f, %.1f, %.1f" % ((es[0]["name"],) + tuple(xyz)) if es else "No entity was found"


class HitsCase(KitCase):
    """KitCase in a HitsWorld: Steve at 0.5 64 0.5 looking east (+x), level."""

    def setUp(self):
        KitCase.setUp(self)
        type(self).current = self.world = HitsWorld()
        self.world.add_player("Steve", (0.5, 64.0, 0.5), rot=(-90.0, 0.0), prefix="[Owner] ")

    def mob(self, kind, x, z=0.5, y=64.0, **kw):
        return self.world.add_entity(kind, (x, y, z), **kw)

    def tags_left(self):
        return sorted(t for e in self.world._all() for t in e["tags"] if t.startswith(mc.TAG + "_"))


# ---- values ------------------------------------------------------------------------------

# ---- the hits: around, lock_on, damage_hits ----------------------------------------------------

class AroundTests(HitsCase):
    def test_nearest_first_never_a_pet_or_a_villager_or_the_player_it_is_round(self):
        for x in (3.5, 9.5, 6.5):
            self.mob("zombie", x)
        self.mob("wolf", 2.5, owner="Steve")
        self.mob("villager", 2.0)
        self.mob("zombie", 20.5)
        self.world.add_player("Alex", (4.5, 64.0, 0.5))
        got = P["around"].around("Steve", vals("around", around_radius=10))
        self.assertEqual([(h.name, round(h.t)) for h in got], [("Zombie", 3), ("Zombie", 6), ("Zombie", 9)])
        self.assertTrue(all(isinstance(h, aim.Hit) and isinstance(h.pos, world.Pos) for h in got))
        got = P["around"].around("Steve", vals("around", around_radius=10, around_what="everything", around_most=2))
        self.assertEqual([(h.name, h.player) for h in got], [("Zombie", False), ("Alex", True)])
        got = P["around"].around((4.5, 64.0, 0.5), vals("around", around_what="players"))
        self.assertEqual(sorted(h.name for h in got), ["Alex", "Steve"])      # a place spares nobody
        self.assertEqual(P["around"].around("Nobody", vals("around")), [])

    def test_a_reply_cut_at_4_kb_is_asked_again_in_smaller_boxes(self):
        for i in range(90):
            self.mob("zombie", -4.5 + (i % 10), z=-4.5 + i // 10, name="Zombie Number %02d" % i)
        before = len(self.world.commands)
        got = P["around"].around(world.Pos(0.5, 64.0, 0.5), vals("around", around_radius=8))
        self.assertEqual(len(got), 90)
        asks = self.world.commands[before:]
        self.assertGreater(len(asks), 1)
        self.assertTrue(all("dx=" in c for c in asks[1:]), asks[1])

    def test_in_the_nether_it_asks_the_nether(self):
        self.world.players["Steve"]["dim"] = "minecraft:the_nether"
        self.mob("zombie", 3.5, dim="minecraft:the_nether")
        got = P["around"].around("Steve", vals("around"))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].pos.dim, "minecraft:the_nether")
        self.assertTrue(self.sent("execute in minecraft:the_nether positioned"))


class LockOnTests(HitsCase):
    def test_the_nearest_in_the_cone_up_to_the_first_wall(self):
        self.mob("zombie", 20.5, z=1.6)          # 20 out, 1.1 aside: in a 6 degree cone
        self.mob("zombie", 30.5)                  # further, on the line
        self.mob("zombie", 6.5, z=5.5)            # near, 40 degrees aside
        self.mob("villager", 10.5)
        got = P["lock_on"].lock_on("Steve", vals("lock_on"))
        self.assertEqual([(h.name, round(h.pos[0], 1)) for h in got], [("Zombie", 20.5)])
        self.assertAlmostEqual(got[0].t, 20.0, delta=0.6)
        self.world.solid.update(("minecraft:overworld", 15, y, z) for y in range(60, 70) for z in range(-5, 6))
        self.assertEqual(P["lock_on"].lock_on("Steve", vals("lock_on")), [])
        self.assertEqual(P["lock_on"].lock_on("Nobody", vals("lock_on")), [])

    def test_players_only_when_asked(self):
        self.world.add_player("Alex", (12.5, 64.0, 0.5))
        self.assertEqual(P["lock_on"].lock_on("Steve", vals("lock_on")), [])
        got = P["lock_on"].lock_on("Steve", vals("lock_on", lock_players=True))
        self.assertEqual([(h.name, h.player) for h in got], [("Alex", True)])


class DamageTests(HitsCase):
    def line(self):
        return aim.near_line(aim.ray("Steve", 40), 1.5, shooter="Steve")

    def test_once_each_as_the_player_sparing_pets_villagers_and_the_spared(self):
        a, b = self.mob("zombie", 10.5), self.mob("zombie", 20.5)
        wolf, villager = self.mob("wolf", 15.5, owner="Steve"), self.mob("villager", 25.5)
        named = self.mob("zombie", 30.5, name="Spared")
        self.world.add_player("Alex", (35.5, 64.0, 0.5))
        hits = self.line()
        self.assertEqual([h.name for h in hits], ["Zombie", "Zombie", "Spared", "Alex"])
        hits += P["around"].around(world.Pos(20.5, 64.0, 0.5), vals("around", around_radius=2))   # b again
        n = P["damage_hits"].damage_hits(hits, "Steve", vals("damage_hits", damage_amount=5,
                                                             damage_spare="Spared,Alex"))
        self.assertEqual(n, 2)
        self.assertEqual((a["health"], b["health"]), (15.0, 15.0))           # once each
        self.assertEqual((wolf["health"], villager["health"], named["health"]), (20.0, 20.0, 20.0))
        self.assertEqual(self.world.players["Alex"]["health"], 20.0)
        self.assertEqual(len(self.sent("run damage @s")), 1)
        self.assertRegex(self.sent("run damage @s")[0], r"^execute as @e\[tag=%s_hit\d+\] run damage @s 5.0 "
                         r"minecraft:player_attack by Steve$" % mc.TAG)
        self.assertEqual(self.tags_left(), [])

    def test_a_player_hit_and_the_shooter_never(self):
        self.world.add_player("Alex", (12.5, 64.0, 0.5))
        hits = self.line() + [aim.Hit(0.0, world.Pos(0.5, 64.0, 0.5), "Steve", True)]
        self.assertEqual(P["damage_hits"].damage_hits(hits, "Steve", vals("damage_hits", damage_amount=4)), 1)
        self.assertEqual(self.world.players["Alex"]["health"], 16.0)
        self.assertEqual(self.world.players["Steve"]["health"], 20.0)

    def test_kill_the_first_only(self):
        a, b = self.mob("zombie", 10.5), self.mob("zombie", 20.5)
        n = P["damage_hits"].damage_hits(self.line(), "Steve", vals("damage_hits", damage_kill=True, damage_most=1))
        self.assertEqual(n, 1)
        self.assertEqual((a["health"], b["health"]), (0.0, 20.0))
        self.assertIn("1000000.0 minecraft:generic_kill by Steve", self.sent("run damage")[0])

    def test_double_on_the_head_with_a_look(self):
        lk = aim.look("Steve")
        head = self.mob("zombie", 10.5, y=lk.origin[1] - 1.7)
        body = self.mob("zombie", 20.5, y=lk.origin[1] - 0.9)
        hits = aim.near_line(aim.ray(lk, 40), 1.0, shooter="Steve")
        P["damage_hits"].damage_hits(hits, lk, vals("damage_hits", damage_amount=4, damage_head=2.5))
        self.assertEqual((head["health"], body["health"]), (10.0, 16.0))
        P["damage_hits"].damage_hits(hits, "Steve", vals("damage_hits", damage_amount=1, damage_head=2.5))
        self.assertEqual((head["health"], body["health"]), (9.0, 15.0))    # no look, no head

    def test_as_nobody_when_the_shooter_is_gone(self):
        a = self.mob("zombie", 10.5)
        hits = self.line()
        n = P["damage_hits"].damage_hits(hits, "Nobody", vals("damage_hits", damage_amount=3))
        self.assertEqual((n, a["health"]), (1, 17.0))
        self.assertRegex(self.sent("run damage")[-1], r"^execute as @e\[tag=%s_hit\d+\] run damage @s 3.0 "
                         r"minecraft:player_attack$" % mc.TAG)

    def test_nothing_hit_sends_no_damage(self):
        self.assertEqual(P["damage_hits"].damage_hits([], "Steve", vals("damage_hits")), 0)
        self.assertEqual(self.sent("damage"), [])


class DamageWhatTests(HitsCase):
    """damage_what, and a tag an earlier run left on a player."""

    def test_hostile_mobs_only_then_players_only(self):
        z, cow = self.mob("zombie", 10.5), self.mob("cow", 20.5)
        self.world.add_player("Alex", (15.5, 64.0, 0.5))
        hits = aim.near_line(aim.ray("Steve", 40), 1.5, shooter="Steve")
        self.assertEqual(len(hits), 3)
        n = P["damage_hits"].damage_hits(hits, "Steve", vals("damage_hits", damage_amount=5, damage_what="hostile"))
        self.assertEqual((n, z["health"], cow["health"], self.world.players["Alex"]["health"]), (1, 15.0, 20.0, 20.0))
        n = P["damage_hits"].damage_hits(hits, "Steve", vals("damage_hits", damage_amount=5, damage_what="players"))
        self.assertEqual((n, z["health"], cow["health"], self.world.players["Alex"]["health"]), (1, 15.0, 20.0, 15.0))
        n = P["damage_hits"].damage_hits(hits, "Steve", vals("damage_hits", damage_amount=5, damage_what="mobs"))
        self.assertEqual((n, z["health"], cow["health"], self.world.players["Alex"]["health"]), (2, 10.0, 15.0, 15.0))
        self.assertEqual(self.tags_left(), [])

    def test_a_tag_left_from_an_earlier_run_spares_nobody(self):
        import itertools
        self.world.add_player("Alex", (12.5, 64.0, 0.5))
        P["damage_hits"]._DAMAGE_N = itertools.cycle(range(1, 17))         # a new process: its names from the first,
        aim._cleared.clear()                                                # none of them cleared yet
        self.world.players["Alex"]["tags"].add("%s_hit1" % mc.TAG)         # he died tagged, last run
        hits = aim.near_line(aim.ray("Steve", 40), 1.5, shooter="Steve")
        self.assertEqual(P["damage_hits"].damage_hits(hits, "Steve", vals("damage_hits", damage_amount=4)), 1)
        self.assertEqual(self.world.players["Alex"]["health"], 16.0)
        self.assertEqual(self.tags_left(), [])

    def test_once_its_names_come_round_a_hit_costs_no_untag_first(self):
        import itertools
        P["damage_hits"]._DAMAGE_N = itertools.cycle(range(1, 17))
        aim._cleared.clear()
        z = self.mob("zombie", 10.5, health=1000.0)
        hits = aim.near_line(aim.ray("Steve", 40), 1.5, shooter="Steve")
        for _ in range(16):                                                 # each name once, cleared first
            P["damage_hits"].damage_hits(hits, "Steve", vals("damage_hits", damage_amount=1))
        before = len(self.world.commands)
        self.assertEqual(P["damage_hits"].damage_hits(hits, "Steve", vals("damage_hits", damage_amount=1)), 1)
        sent = self.world.commands[before:]
        self.assertIn("%s_hit1" % mc.TAG, sent[0])                          # the first name again
        self.assertNotIn("remove", sent[0])                                 # tagged at once, no untag first
        self.assertEqual(len([c for c in sent if "remove" in c]), 2)        # only the untag after
        self.assertEqual(z["health"], 1000.0 - 17)
        self.assertEqual(self.tags_left(), [])


# ---- freeze and push ---------------------------------------------------------------------------

class FreezeTests(HitsCase):
    def test_slow_weak_and_frosted_for_its_seconds_never_a_villager(self):
        z, v = self.mob("zombie", 10.5), self.mob("villager", 5.5)
        hits = aim.near_line(aim.ray("Steve", 20), 1.5, shooter="Steve")
        hits.append(aim.Hit(5.0, world.Pos(5.5, 64.0, 0.5), "Villager", False))
        self.assertEqual(P["freeze"].freeze(hits, vals("freeze", freeze_seconds=3.2)), 1)
        self.assertEqual(z["effects"], {"slowness": (4, 9), "weakness": (4, 9)})
        self.assertEqual(z["frozen"], 140 + 160)
        self.assertNotIn("effects", v)
        self.assertEqual(self.world.fills, [])
        self.assertEqual(self.tags_left(), [])

    def test_a_shell_into_air_only_that_melts(self):
        self.mob("zombie", 10.5, z=0.5)
        hits = P["around"].around("Steve", vals("around", around_radius=12))
        P["freeze"].freeze(hits, vals("freeze", freeze_seconds=1, freeze_shell=True, freeze_block="packed_ice"))
        made = [(b, blk, mode) for _, b, blk, mode in self.world.fills]
        self.assertEqual(len(made), 6)
        self.assertTrue(all(blk == "packed_ice" and mode == "keep" for _, blk, mode in made))
        cells = set((x, y, zz) for b, _, _ in made for x in range(b[0], b[3] + 1) for y in range(b[1], b[4] + 1)
                    for zz in range(b[2], b[5] + 1))
        self.assertEqual(len(cells), 3 * 4 * 3 - 2)
        self.assertNotIn((10, 64, 0), cells)
        self.assertNotIn((10, 65, 0), cells)
        time.sleep(1.6)
        melted = [c for c in self.world.commands if c.endswith("air replace packed_ice")]
        self.assertEqual(len(melted), 6)
        self.assertEqual(P["freeze"]._FREEZE_SHELLS, [])


class FreezeBlindnessTests(HitsCase):
    def test_blinded_too_when_asked(self):
        z = self.mob("zombie", 10.5)
        hits = aim.near_line(aim.ray("Steve", 20), 1.5, shooter="Steve")
        P["freeze"].freeze(hits, vals("freeze", freeze_seconds=2, freeze_blindness=True))
        self.assertEqual(z["effects"]["blindness"], (2, 3))


class PushTests(HitsCase):
    def test_a_mob_thrown_away_pulled_in_or_lifted_once(self):
        z = self.mob("zombie", 10.5)
        frm = world.Pos(0.5, 64.0, 0.5)
        hits = P["around"].around(frm, vals("around", around_radius=12))
        for way, want in (("away", (1.2, 0.4, 0.0)), ("toward", (-1.2, 0.4, 0.0)), ("up", (0.0, 1.054, 0.0))):
            self.assertEqual(P["push"].push(hits + hits, frm, vals("push", push_way=way, push_strength=6)), 1)
            self.assertEqual(tuple(round(c, 3) for c in z["motion"]), want, way)
        P["push"].push(hits, frm, vals("push", push_way="toward", push_strength=20))
        self.assertAlmostEqual(z["motion"][0], -(10.0 - 1.5) / 5.0, places=3)     # never past frm

    def test_a_player_moved_into_room_to_stand_once(self):
        self.world.add_player("Alex", (6.5, 64.0, 0.5))
        hits = P["around"].around("Steve", vals("around", around_what="players"))
        self.assertEqual(P["push"].push(hits + hits, "Steve", vals("push", push_strength=6)), 1)
        self.assertEqual([round(c, 1) for c in self.world.players["Alex"]["pos"]], [12.5, 64.1, 0.5])
        self.world.solid.update({(OVERWORLD, 18, 64, 0), (OVERWORLD, 18, 65, 0)})
        hits = P["around"].around("Steve", vals("around", around_what="players", around_radius=20))
        P["push"].push(hits, "Steve", vals("push", push_strength=6))            # no room 6 out: 3 out
        self.assertEqual(round(self.world.players["Alex"]["pos"][0], 1), 15.5)
        self.world.solid.update({(OVERWORLD, 21, 64, 0), (OVERWORLD, 21, 65, 0)})
        hits = P["around"].around("Steve", vals("around", around_what="players", around_radius=20))
        self.assertEqual(P["push"].push(hits, "Steve", vals("push", push_strength=6)), 0)   # no room at all
        P["push"].push(hits, "Steve", vals("push", push_way="up", push_strength=6))
        self.assertEqual(self.world.players["Alex"]["effects"]["levitation"], (1, 7))


class PushMoreTests(HitsCase):
    def test_the_way_given_by_the_call_and_a_projectile_turned_by_its_uuid(self):
        z = self.mob("zombie", 10.5)
        frm = world.Pos(0.5, 64.0, 0.5)
        hits = P["around"].around(frm, vals("around", around_radius=12))
        P["push"].push(hits, frm, vals("push", push_way="away", push_strength=6), way="up")
        self.assertEqual(tuple(round(c, 3) for c in z["motion"]), (0.0, 1.054, 0.0))
        u = "00000001-0000-0002-0000-0003ffffffff"
        arrow = aim.Hit(3.0, world.Pos(3.5, 65.0, 0.5), "Arrow", False, u)
        P["push"].push([arrow, arrow], frm, vals("push", push_strength=5))
        self.assertEqual(len([c for c in self.world.commands if c.startswith("execute as %s at @s run data modify "
                                                                          "entity @s Motion set value [1.000d,0.100d,0.000d]" % u)]), 1)
        self.assertIn("tag %s add %s" % (u, mc.TAG), self.world.commands)       # turned once: it is the script's now

    def test_around_hostile_mobs_only(self):
        self.mob("zombie", 3.5)
        self.mob("cow", 4.5)
        self.mob("wolf", 5.5)
        hits = P["around"].around("Steve", vals("around", around_what="hostile"))
        self.assertEqual([h.name for h in hits], ["Zombie"])


# ---- lightning, explosion, crater ----------------------------------------------------------------

class LightningTests(HitsCase):
    def test_a_ring_of_five_round_a_place_sparing_who_called_it(self):
        n = P["lightning"].lightning(world.Pos(10.5, 64.0, 0.5), "Steve",
                                     vals("lightning", lightning_count=5, lightning_layout="ring",
                                          lightning_spread=2, lightning_spare="Alex"))
        self.assertEqual(n, 5)
        bolts = [e for e in self.world.entities if e["type"] == "lightning_bolt"]
        self.assertEqual(sorted((round(e["pos"][0], 1), round(e["pos"][2], 1)) for e in bolts),
                         [(8.5, 0.5), (10.5, -1.5), (10.5, 0.5), (10.5, 2.5), (12.5, 0.5)])
        self.assertTrue(all(mc.TAG in e["tags"] for e in bolts))
        self.assertTrue(all("unless entity @a[name=Steve,distance=..4] unless entity @a[name=Alex,distance=..4]" in c
                            for c in self.sent("summon lightning_bolt")))

    def test_one_by_one_and_scattered_within_the_spread(self):
        t = time.time()
        P["lightning"].lightning("Steve", "", vals("lightning", lightning_count=3, lightning_spread=4,
                                                   lightning_every=0.3))
        self.assertGreaterEqual(time.time() - t, 0.55)
        for e in self.world.entities:
            self.assertLessEqual(math.hypot(e["pos"][0] - 0.5, e["pos"][2] - 0.5), 4.01)


class ExplosionTests(HitsCase):
    def test_tnt_in_a_plus_with_the_spared_shielded(self):
        self.mob("wolf", 11.5, owner="Steve")
        n = P["explosion"].explosion(world.Pos(10.5, 64.0, 0.5), "Steve",
                                     vals("explosion", explosion_layout="plus", explosion_spread=3,
                                          explosion_spare="Alex", explosion_power=5))
        self.assertEqual(n, 5)
        tnt = self.sent("summon tnt")
        self.assertEqual(len(tnt), 5)
        self.assertTrue(all("fuse:0s,explosion_power:5.0f" in c for c in tnt))
        shields = self.sent("resistance 2 4")
        self.assertTrue(any("@a[name=Steve," in c for c in shields) and any("@a[name=Alex," in c for c in shields))
        self.assertTrue(any("if data entity @s Owner" in c for c in shields))
        self.assertTrue(any("@e[type=villager," in c for c in shields))
        self.assertLess(self.world.commands.index(shields[-1]), self.world.commands.index(tnt[0]))

    def test_rings_keep_out_of_a_player(self):
        n = P["explosion"].explosion(world.Pos(6.5, 64.0, 0.5), "",
                                     vals("explosion", explosion_layout="rings", explosion_spread=4, explosion_keep=5))
        tnt = [tuple(float(c) for c in re.search(r"summon tnt (\S+) (\S+) (\S+)", s).groups())
               for s in self.sent("summon tnt")]
        self.assertEqual(n, len(tnt))
        self.assertLess(n, 17)
        self.assertTrue(all(math.hypot(x - 0.5, z - 0.5) >= 5 for x, _, z in tnt))

    def test_a_blast_that_breaks_nothing_hurts_from_the_place(self):
        near, far = self.mob("zombie", 11.5), self.mob("zombie", 30.5)
        wolf = self.mob("wolf", 9.5, owner="Alex")
        P["explosion"].explosion(world.Pos(10.5, 64.0, 0.5), "Steve", vals("explosion", explosion_breaks=False))
        self.assertEqual(self.sent("summon tnt"), [])
        self.assertLess(near["health"], 20.0)
        self.assertEqual((far["health"], wolf["health"]), (20.0, 20.0))
        self.assertTrue(all(c.endswith("minecraft:explosion at 10.50 64.00 0.50") for c in self.sent("run damage")))
        self.assertFalse(self.world.fills)


class CraterTests(HitsCase):
    def test_carved_from_the_middle_loaded_first_and_let_go(self):
        n = P["crater"].crater(world.Pos(40.5, 70.0, 40.5), vals("crater", crater_radius=6))
        self.assertGreater(n, 800)
        self.assertEqual(set(blk for _, _, blk, _ in self.world.fills), {"air"})
        self.assertTrue(self.sent("forceload add"))
        self.assertEqual(self.world.forced, set())
        first = self.world.fills[0][1]
        self.assertTrue(first[0] <= 40 <= first[3] and first[1] <= 70 <= first[4])

    def test_a_lava_pool_in_its_lowest_third_and_fire_below_its_middle(self):
        P["crater"].crater(world.Pos(0.5, 70.0, 0.5), vals("crater", crater_radius=9, crater_leave="lava",
                                                          crater_load=False))
        lava = [b for _, b, blk, _ in self.world.fills if blk == "lava"]
        self.assertTrue(lava)
        self.assertEqual(max(b[4] for b in lava), 70 - 9 + 3)
        self.assertTrue(all(m == "replace air" for _, _, blk, m in self.world.fills if blk == "lava"))
        self.assertFalse(self.sent("forceload"))
        del self.world.fills[:]
        P["crater"].crater(world.Pos(0.5, 70.0, 0.5), vals("crater", crater_radius=9, crater_leave="fire",
                                                          crater_load=False))
        self.assertEqual(max(b[4] for _, b, blk, _ in self.world.fills if blk == "fire"), 70)

    def test_never_the_bedrock_floor_and_two_at_most_waiting(self):
        P["crater"].crater(world.Pos(0.5, -58.0, 0.5), vals("crater", crater_radius=5, crater_load=False))
        self.assertGreaterEqual(min(b[1] for _, b, _, _ in self.world.fills), -59)
        P["crater"]._CRATER_QUEUE[0] = 2
        try:
            self.assertEqual(P["crater"].crater(world.Pos(0.5, 70.0, 0.5), vals("crater")), 0)
        finally:
            P["crater"]._CRATER_QUEUE[0] = 0


class OffAndTunnelTests(HitsCase):
    def test_no_crater_and_no_blast_at_zero(self):
        self.assertEqual(P["crater"].crater(world.Pos(0.5, 70.0, 0.5), vals("crater", crater_radius=0, crater_tunnel=2),
                                            path=[world.Pos(5.5, 70.0, 0.5)]), 0)
        self.assertEqual(P["explosion"].explosion(world.Pos(0.5, 70.0, 0.5), "Steve", vals("explosion", explosion_power=0)), 0)
        self.assertEqual(self.world.fills, [])
        self.assertEqual(self.sent("summon"), [])

    def test_a_tunnel_along_a_beam_first(self):
        path = [world.Pos(x + 0.5, 70.0, 0.5) for x in range(4, 20, 2)]
        P["crater"].crater(world.Pos(20.5, 70.0, 0.5), vals("crater", crater_radius=3, crater_tunnel=1, crater_load=False),
                           path=path)
        first = self.world.fills[0][1]
        self.assertEqual(first, (3, 69, -1, 5, 71, 1))                     # the cube round the first place
        self.assertTrue(any(b[0] <= 20 <= b[3] for _, b, _, _ in self.world.fills))

    def test_too_big_to_load_first(self):
        P["crater"]._crater_carve(world.Pos(0.5, 300.0, 0.5), vals("crater", crater_radius=200), ())
        self.assertFalse(self.sent("forceload add"))


# ---- all eight copied into one script, run as on EC2, stopped -------------------------------

WIRING = '''
eye = "Steve"
r = aim.ray(eye, 30)
hits = aim.near_line(r, 1.5, shooter=eye) + around(r.end, v) + lock_on(eye, v)
mc.log("hurt", damage_hits(hits, eye, v))
mc.log("frozen", freeze(hits, v))
mc.log("pushed", push(hits, eye, v))
mc.log("struck", lightning(r.point(20), eye, v))
mc.log("blasts", explosion(r.point(20), eye, v))
mc.log("carved", crater(r.point(20), v))
mc.log("ready")
for n in loop(every=0.2):
    pass
'''


class ScriptTests(unittest.TestCase):
    def test_eight_presets_in_one_script_leave_nothing_after_a_stop(self):
        text = library.SKELETON.replace("from kit import result, values", "from kit import values, aim, loop\nimport mc")
        for name in NAMES:
            text = library.add_preset(text, library.parse(os.path.join(PRESETS, name + ".py"), "preset"), "main")[0]
        w = HitsWorld()
        w.add_player("Steve", (0.5, 64.0, 0.5), rot=(-90.0, 0.0))
        w.add_entity("zombie", (8.5, 64.0, 0.5))
        w.add_entity("zombie", (14.5, 64.0, 0.5))
        password = secrets.token_hex(8)
        fake = FakeRcon(password, responder=w.respond).start()
        try:
            run_dir = tempfile.mkdtemp(dir=TMP)
            with open(os.path.join(run_dir, "hitsprobe.values.json"), "w") as f:
                f.write('{"freeze_shell": true, "freeze_seconds": 60}')       # live values, as an edit writes
            proc = start_script("hitsprobe", text + WIRING, fake, password, run_dir=run_dir,
                                vals=test_kit.values_meta(*[P[n] for n in NAMES]))
            deadline = time.time() + 15
            while not any(c.startswith("fill") and "air replace" not in c and "ice keep" in c for c in w.commands) \
                    and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.5)
            self.assertTrue(w.forced or any("forceload remove" in c for c in w.commands))
            t = time.time()
            proc.send_signal(signal.SIGTERM)
            out = proc.communicate(timeout=5)[0].decode()
            self.assertLess(time.time() - t, 2.0)
            self.assertEqual(proc.returncode, 0, out)
            self.assertNotIn("Traceback", out)
            for word in ("hurt 2", "frozen 2", "pushed 2", "struck 1", "blasts 1", "carved"):
                self.assertIn(word, out)
            with w.lock:
                self.assertEqual(len([c for c in w.commands if c.endswith("air replace ice")]), 12, "shells left")
                self.assertEqual([e for e in w.entities if any(t.startswith("vfg_hitsprobe") for t in e["tags"])], [])
                self.assertEqual(w.forced, set())
                self.assertEqual(w.commands[-1], "kill @e[tag=vfg_hitsprobe]")
        finally:
            fake.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
