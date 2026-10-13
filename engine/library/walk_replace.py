"""Walk Replace: changes blocks by rules where players walk, over the block queue so nothing
blocks. per="step" (ice_walk): at each step across the ground, or a block up or down, everything
the rules name in a box round the feet changes - ice_path is the same with radius=2, below=1,
above=0, a path under the feet alone. per="chunk" (chunk_changer): each chunk a player walks into,
plus around_chunks round it, changes once, from below the feet to above the head; snow to a random
mix of blocks by default. A landing after a fall of 3 blocks or more flashes like lightning, with
thunder and no harm (per="step" only). (The old ice_walk, ice_path and chunk_changer scripts and
actions: walk_steps and the fall check are now the kit's steps(), and enters_chunk is
chunks_entered(); both keep their own memory, so this class needs no state of its own. The old
replace_blocks' scalar replace_rate (a share of cells that change regardless of the mix) has no
engine equivalent: `self.blocks.replace` only picks among a mix's own weights, so the same effect
is written straight into `rules` with a weighted "keep", e.g. "snow > 60% white_wool 40% keep".
# NEEDS: a plain replace_rate, separate from a mix's weights, for a brain that wants to say "60%
of this chunk" without rewriting the rule.)"""

import math
from typing import Literal

from vfg_engine import Args, Field, Players, ReplaceRules, Scenario, ScenarioConfig, UNTIL_STOPPED, players
from vfg_engine.kit import chunks_entered, steps

ICE_RULE = ("solid water lava bubble_column kelp kelp_plant #minecraft:replaceable_by_trees "
           "#minecraft:flowers #minecraft:crops #minecraft:saplings > packed_ice")


class WalkReplace(Scenario):
    every = 0.1                      # tick(): steps() and chunks_entered() read the snapshot alone, so this is cheap

    class Config(ScenarioConfig):
        summary = ("Changes blocks by rules where players walk: round each step across the ground (ice_walk, "
                   "ice_path), or in each chunk they enter (chunk_changer).")
        duration_s = UNTIL_STOPPED

    class Args(Args):
        players: Players = Field("@a", description="whose walking changes blocks: @a, @r or names")
        rules: ReplaceRules = Field(ICE_RULE, description='the rule(s) it applies, in order, "from ... > to "'
                                   '(ice_path: the same; chunk_changer: "#minecraft:snow > white_wool '
                                   'light_gray_wool red_wool blue_wool lime_wool yellow_wool cyan_wool '
                                   'purple_wool orange_wool pink_wool white_concrete light_blue_concrete '
                                   'green_concrete magenta_concrete terracotta orange_terracotta '
                                   'brown_terracotta oak_planks spruce_planks dark_oak_planks stone andesite '
                                   'granite diorite stone_bricks deepslate quartz_block glass coal_ore '
                                   'lapis_ore")')
        per: Literal["step", "chunk"] = Field("step", description="when it changes blocks: at each step across "
                                             "the ground (ice_walk, ice_path), or in each chunk entered "
                                             "(chunk_changer)")
        every_s: float = Field(2, ge=0.5, le=32, description='per="step" only: blocks walked (flat), or a block '
                              "up or down, before the next change (ice_path: 2, same as ice_walk)")
        radius: int = Field(10, ge=0, le=32, description='per="step": how far out, each way, from the block '
                           "stood on (ice_path: 2, a path under the feet alone)")
        below: int = Field(11, ge=1, le=384, description='per="step": how far down from the block stood on '
                          '(ice_path: 1); per="chunk": how far under the feet, in the chunk (32 for '
                          "chunk_changer)")
        above: int = Field(11, ge=0, le=384, description='per="step": how far up from it (ice_path: 0); '
                          'per="chunk": how far over the head (40 for chunk_changer)')
        around_chunks: int = Field(1, ge=0, le=3, description='per="chunk" only: chunks round the one entered '
                                  "that change too, each way (1: the eight round it, so no seam shows)")
        cell: int = Field(4, ge=1, le=16, description="a rule whose to-side is a mix (more than one block) "
                         "picks one per cube this many blocks wide, the same pick every time")
        flash: bool = Field(True, description='per="step" only: a landing after a fall of 3 blocks or more '
                           "flashes like lightning, with thunder; it does no harm")
        dims: list[Literal["overworld", "the_nether", "the_end"]] = Field(
            ["overworld", "the_nether", "the_end"], description='per="chunk" only: the dimensions it works in')

    def start(self, ctx):
        a = self.args
        if a.per == "chunk":
            n = len([p for p in a.rules.split(";") if p.strip()])
            seam = " and the %d round it" % ((2 * a.around_chunks + 1) ** 2 - 1) if a.around_chunks else ""
            self.result("chunk changer on for %s: %d rule%s in each chunk they walk into%s"
                       % (a.players, n, "" if n == 1 else "s", seam), players=a.players, rules=a.rules)
        else:
            self.result("ice walk on for %s: everything %d blocks round each step turns to ice, %d down and "
                       "%d up" % (a.players, a.radius, a.below, a.above), players=a.players, radius=a.radius,
                       below=a.below, above=a.above)

    def tick(self, ctx):
        a = self.args
        for name in players(ctx, a.players):
            if a.per == "chunk":
                self.do_chunk(ctx, name)
            else:
                self.do_step(ctx, name)

    def do_step(self, ctx, name):
        """Each step on the ground (or a landing from a fall): the box round the feet changes."""
        a = self.args
        step = steps(self, ctx, name, a.every_s)
        if step is None:
            return
        fx, fy, fz = step.pos
        x, y, z = int(math.floor(fx)), int(math.floor(fy - 0.01)), int(math.floor(fz))
        r = a.radius
        lo, hi = (x - r, y - a.below + 1, z - r), (x + r, y + a.above, z + r)
        self.blocks.replace((lo, hi), a.rules, cell=a.cell, dim=step.dim)
        if a.flash and step.fell:
            self.land_flash(step)

    def do_chunk(self, ctx, name):
        """Each chunk entered (and around_chunks round it), once: the chunk's box changes."""
        a = self.args
        p = ctx.world.player(name)
        if p is None:
            return
        for box in chunks_entered(self, ctx, name, around=a.around_chunks, below=a.below, above=a.above,
                                  dims=a.dims):
            self.blocks.replace(box, a.rules, cell=a.cell, dim=p.dim)

    def land_flash(self, step):
        """A flash, sparks and thunder at a landing; no bolt, so no fire and no harm."""
        x, y, z = step.pos
        at, up = (x, y + 1, z), (x, y + 0.2, z)
        self.particle("minecraft:flash{color:[1.0,1.0,1.0,1.0]}", at, force=True, dim=step.dim)
        self.particle("minecraft:electric_spark", at, count=80, delta=(1.0, 1.0, 1.0), speed=0.5, force=True,
                      dim=step.dim)
        self.particle("minecraft:end_rod", up, count=60, delta=(0.6, 0.1, 0.6), speed=0.3, force=True,
                      dim=step.dim)
        self.sound("minecraft:entity.lightning_bolt.thunder", at=at, volume=2, dim=step.dim)
        self.sound("minecraft:entity.lightning_bolt.impact", at=at, volume=2, dim=step.dim)
