"""Weapon Chest: places a chest of library items for players, in two modes (gun_chests merged in
here: engine/library/README.md's merge rule, DESIGN2's "gun_chests into weapon_chest").

- `contents="powers"` (the old weapon_chest script): one chest beside the first player, or one
  beside each player with `per_player`, or one at a place, holding six named power weapons and 64
  arrows -- hellfire_longbow, glacier_bow, stormcaller, thunderbrand, doomcleaver and gravebreaker,
  each its own Item in this group, reusing frozen, lightning_strike, blast_at_look and self_lift.
  It runs until stopped, placing (once, or once each with `per_player`) for players new to it
  every few seconds, the same joiner check the old script ran.
- `contents="guns"` (the old gun_chests script): several chests scattered `nearest` to `farthest`
  blocks round each player, each with two of this group's own gun-like items and their ammo,
  under a glowing "GUN CHEST" label that, like the old script's, stays after this one-shot ends.
  The old script's own named guns (AK-47, Shotgun, Sniper Rifle, Rocket Launcher, Railgun, Harpoon
  Gun) were vanilla crossbows, bows and a trident with custom names and enchantments; here they
  are library items that already exist, by their ids (library/README.md, and this brief): railgun,
  gatling_gun, super_sniper, rocket_launcher, spear_bomb -- no new item covers a "gun" better than
  these already do.

# NEEDS (see the sites below):
- forceloading a chunk does not guarantee it is loaded by the very next call (`ground_y` can still
  read None the moment after): a gun chest's site polls a few times, `gap_s` apart, and is skipped
  if it never loads within `load_tries`.
- six named power weapons plus 64 arrows are more NBT than one command holds (1446 bytes): the
  kit's `chest()` queues the chest with what fits and the rest after it, so a powers chest is one
  chest, filled in two or three commands."""

import math
import random
from typing import Literal

from vfg_engine import Args, Field, Place, Players, Scenario, ScenarioConfig, State, UNTIL_STOPPED, players
from vfg_engine.kit import ahead, chest, mob_nbt

# The old gun_chests table was real vanilla crossbows/bows/a trident, custom-named and enchanted;
# these are the library's own gun-like items instead (two picked at random for each chest).
GUNS = ["railgun", "gatling_gun", "super_sniper", "rocket_launcher", "spear_bomb"]
POWER_ITEMS = ["everhear:hellfire_longbow", "everhear:glacier_bow", "everhear:stormcaller",
              "everhear:thunderbrand", "everhear:doomcleaver", "everhear:gravebreaker", "arrow 64"]


class WeaponChest(Scenario):
    every = 2                              # powers: how often it checks for players new to it

    class Config(ScenarioConfig):
        summary = ("Places a chest of library items: powers (six named power weapons and arrows, for the "
                   "group or one each) or guns (several chests scattered round each player, two guns each).")
        duration_s = UNTIL_STOPPED          # powers: runs until stopped, watching for new joiners;
                                            # guns: ends itself once every chest is placed or given up on
        gap_s: float = Field(0.1, ge=0.05, le=2)       # between one gun chest site's steps
        load_tries: int = Field(10, ge=1, le=60)       # a gun chest site: how many times it polls for its chunk

    class Args(Args):
        players: Players = Field("@a", description="who gets chests, and (powers) whose weapons have their "
                                 "powers: @a, @r or names")
        contents: Literal["powers", "guns"] = Field("powers", description="powers: one chest (or one each, "
                                 "with per_player) of six named power weapons and arrows; guns: several "
                                 "chests scattered round each player, each with two random guns and ammo")
        per_player: bool = Field(False, description="powers only: a chest beside each player (true), or one "
                                 "chest for the whole group, beside the first of them or at `at` (false)")
        at: Place = Field("", description="powers only, and not per_player: where the one chest goes instead "
                                 "-- a saved place, x y z, or a player's name, beside him; empty: beside the "
                                 "first player")
        chests_per_player: int = Field(4, ge=1, le=10, description="guns only: how many chests scattered "
                                 "round each player")
        nearest: int = Field(40, ge=0, le=1000, description="guns only: the closest a chest is to its "
                                 "player, in blocks")
        farthest: int = Field(160, ge=0, le=1000, description="guns only: the farthest a chest is from its "
                                 "player, in blocks (never nearer than nearest; 0 with nearest 0: beside them)")

    class State(State):
        given: list = []                # powers, per_player: names already given their own chest
        placed_once: bool = False       # powers, shared: the one chest is down
        todo: list = []                 # guns: player names still owed a chest, one entry per chest
        cur_x: int = 0
        cur_z: int = 0
        cur_dim: str = "minecraft:overworld"
        cur_name: str = ""
        cur_tries: int = 0
        placed_at: list = []            # guns: [[player, x, z]] placed, for the result

    def start(self, ctx):
        if self.args.contents == "guns":
            self._start_guns(ctx)
            return
        a = self.args
        if a.at is not None:
            online = players(ctx, a.players)
            name = online[0] if online else ""
            self._place_powers(ctx, a.at.pos, a.at.dim, name)
            self.state.placed_once = True
            self.result("a chest of powers is ready at %s" % self._said(a.at), players=[name] if name else [])
            return
        self._place_for_new(ctx)
        self.result("a chest of powers is ready for %s" % a.players,
                    players=list(self.state.given) if a.per_player else (["everyone"] if self.state.placed_once
                                                                        else []))

    def tick(self, ctx):
        if self.args.contents != "powers" or self.args.at is not None:
            return            # guns: all done in start(); a fixed place: nothing more as players join
        self._place_for_new(ctx)

    # ---- powers: one chest, or one each, beside a player ----------------------------------------

    def _place_for_new(self, ctx):
        a = self.args
        names = players(ctx, a.players)
        if a.per_player:
            for name in names:
                if name in self.state.given:
                    continue
                p = ctx.world.player(name)
                if p is None:
                    continue
                self._place_powers(ctx, ahead(ctx, p, 2), p.dim, name)
                self.state.given = self.state.given + [name]
        elif not self.state.placed_once and names:
            p = ctx.world.player(names[0])
            if p is not None:
                self._place_powers(ctx, ahead(ctx, p, 2), p.dim, names[0])
                self.state.placed_once = True

    def _place_powers(self, ctx, at, dim, name):
        chest(self, ctx, at, POWER_ITEMS, dim=dim)
        if name:
            self.message("A chest of powers awaits.", to=[name])

    def _said(self, spot) -> str:
        where = "%d %d %d" % tuple(round(c) for c in spot.pos)
        return where if spot.dim == "minecraft:overworld" else where + " in " + spot.dim.split(":")[-1]

    # ---- guns: several chests scattered round each player ---------------------------------------

    def _start_guns(self, ctx):
        a = self.args
        names = players(ctx, a.players)
        if not names:
            self.result("no gun chest: nobody of %s is online" % a.players, players=[], chests=0, at=[])
            self.end()
            return
        self.state.todo = [n for n in names for _ in range(a.chests_per_player)]
        self.after(0.0, "next_gun_site")

    def next_gun_site(self, ctx):
        if not self.state.todo:
            self._finish_guns(ctx)
            return
        name = self.state.todo[0]
        self.state.todo = self.state.todo[1:]
        p = ctx.world.player(name)
        if p is None:                                      # left meanwhile: drop this one, try the next
            self.after(self.config.gap_s, "next_gun_site")
            return
        a = self.args
        near, far = a.nearest, max(a.nearest, a.farthest)
        angle, r = random.uniform(0, 2 * math.pi), random.uniform(near, far)
        x, z = int(p.pos[0] + r * math.cos(angle)), int(p.pos[2] + r * math.sin(angle))
        self.blocks.forceload((x, 0, z), (x, 0, z), dim=p.dim)
        self.state.cur_x, self.state.cur_z, self.state.cur_dim = x, z, p.dim
        self.state.cur_name, self.state.cur_tries = name, 0
        self.after(self.config.gap_s, "gun_site_ready")

    def gun_site_ready(self, ctx):
        x, z, dim = self.state.cur_x, self.state.cur_z, self.state.cur_dim
        y = ctx.world.ground_y(x, z, dim=dim)
        if y is None and self.state.cur_tries < self.config.load_tries:
            self.state.cur_tries += 1
            self.after(self.config.gap_s, "gun_site_ready")
            return
        if y is not None:
            self._place_gun_chest(ctx, x, y, z, dim, self.state.cur_name)
        self.after(self.config.gap_s, "next_gun_site")

    def _place_gun_chest(self, ctx, x, y, z, dim, name):
        picks = random.sample(GUNS, min(2, len(GUNS)))
        chest(self, ctx, (x, y, z), ["everhear:%s" % g for g in picks], dim=dim)
        label = "GUN CHEST: %s" % ", ".join(g.replace("_", " ").title() for g in picks)
        self.spawn("minecraft:armor_stand", (x + 0.5, y + 1, z + 0.5), dim=dim, keep=True,
                   nbt=mob_nbt(name=label, glowing=True, no_ai=True, invulnerable=True,
                              extra="Invisible:1b,Marker:1b,NoGravity:1b"))
        self.state.placed_at = self.state.placed_at + [[name, x, z]]

    def _finish_guns(self, ctx):
        a = self.args
        n = len(self.state.placed_at)
        who = sorted({row[0] for row in self.state.placed_at})
        if n:
            self.message("%d GUN CHESTS dropped %d-%d blocks around you, marked with glowing gold tags."
                        % (n, a.nearest, a.farthest), to=who)
            self.result("%d gun chests hidden %d-%d blocks round %s" % (n, a.nearest, a.farthest,
                       ", ".join(who)), players=who, chests=n,
                       at=["%d %d" % (row[1], row[2]) for row in self.state.placed_at])
        else:
            self.result("no gun chest: nobody stayed online long enough", players=[], chests=0, at=[])
        self.end()
