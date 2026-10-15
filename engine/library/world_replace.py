"""World Replace: applies ordered replace rules ("from ... > to mix") round each player at once,
in the dimensions asked, only where loaded: water to lava, snow gone, anything to anything.

rules "hellscape" (the old hellscape action: the same code, its own 11 rules) turns the land round
each player into the Nether: water to lava, leaves gone, trunks to basalt, grass and crops to fire,
the ground to netherrack, sand to soul sand, gravel and clay to soul soil, snow gone, ice to lava,
and patches of magma and blackstone in the netherrack."""

import math
import re
from typing import Literal

from vfg_engine import Args, Field, Players, ReplaceRules, Scenario, ScenarioConfig, State, players

OVERWORLD = "minecraft:overworld"
DIM_NAME = {"overworld": "minecraft:overworld", "the_nether": "minecraft:the_nether", "the_end": "minecraft:the_end"}
HEIGHTS = {"minecraft:overworld": (-64, 319), "minecraft:the_nether": (0, 255), "minecraft:the_end": (0, 255)}
HELLSCAPE_RULES = ("water > lava; #minecraft:leaves > air; #minecraft:logs > basalt; "
                   "#minecraft:replaceable_by_trees #minecraft:flowers #minecraft:crops #minecraft:saplings > "
                   "fire; grass_block podzol mycelium mud moss_block #minecraft:dirt farmland dirt_path > "
                   "netherrack; #minecraft:base_stone_overworld > netherrack; #minecraft:sand > soul_sand; "
                   "gravel clay > soul_soil; #minecraft:snow > air; #minecraft:ice > lava; "
                   "netherrack > 92% keep 5% magma_block 3% blackstone")
_WORD = re.compile(r"(\d+(?:\.\d+)?)%|([^\s,%;>]+(?:\[[^\]]*\])?(?:\{[^}]*\})?)")


def _at_rate(text: str, rate: int) -> str:
    """Each rule's own mix scaled down to `rate` percent, the rest left as it was ("keep")."""
    if rate >= 100:
        return text
    out = []
    for part in [p.strip() for p in text.split(";") if p.strip()]:
        left, _, right = part.partition(">")
        mix, weight = [], None
        for w, block in _WORD.findall(right):
            if w:
                weight = float(w)
            else:
                mix.append((block, 1.0 if weight is None else weight))
                weight = None
        total = sum(w for _, w in mix) or 1.0
        scaled = ["%g%% %s" % (w / total * rate, b) for b, w in mix]
        out.append("%s > %s %g%% keep" % (left.strip(), " ".join(scaled), 100 - rate))
    return "; ".join(out)


class WorldReplace(Scenario):
    class Config(ScenarioConfig):
        summary = ('Applies ordered replace rules round each player at once, in the dimensions asked, only '
                   'where loaded. rules "hellscape": the land turns to the Nether.')
        duration_s = 600
        cell: int = Field(4, ge=1, le=16)               # a mix (or a rate under 100) picks per cube this wide
        rule_sets: dict = {"hellscape": HELLSCAPE_RULES}
        # NEEDS: self.blocks.replace does not report how many blocks a rule actually changed back to content,
        # so the result cannot say a count the way the old scripts did

    class Args(Args):
        players: Players = Field("@a", description="round whom it changes: @a, @r or names")
        rules: ReplaceRules | Literal["hellscape"] = Field(
            ["obsidian > air"], description='the rules, in order, "from ... > to mix" ("solid" for anything a '
                                           'player cannot walk through, #tags, "keep" to leave it as it was); '
                                           '"hellscape": the land turns to the Nether')
        radius: int = Field(80, ge=1, le=512, description="how far round each player it changes, each way, in "
                                                          "blocks; only loaded chunks change")
        height: int = Field(0, ge=0, le=384, description="how far above and below their feet it changes, in "
                                                         "blocks (0: the world's whole height)")
        dims: list[Literal["overworld", "the_nether", "the_end"]] = Field(
            ["overworld", "the_nether", "the_end"], description="the dimensions it changes, round the players "
                                                                 "who are in them")
        rate: int = Field(100, ge=1, le=100, description="the share of cells that change, in percent; under "
                                                         "100, a cell picks at random whether it changes at all")

    class State(State):
        left: int = 0            # replace calls still to come
        done: list = []          # players it changed round, for the result

    def start(self, ctx):
        a = self.args
        names = players(ctx, a.players)
        dims = {DIM_NAME[d] for d in a.dims}
        sites = [p for p in (ctx.world.player(n) for n in names) if p is not None and p.dim in dims]
        if not sites:
            self.result("nothing changed: %s" % (
                "nobody is online" if a.players in ("@a", "@r") else a.players + " is not online"),
                players=[])
            self.end()
            return
        self.state.left = len(sites)
        for i, p in enumerate(sites):
            self.after(i * 0.1, "replace_round", p.name, list(p.pos), p.dim)

    def replace_round(self, ctx, name, pos, dim):
        a, c = self.args, self.config
        lo_y, hi_y = HEIGHTS.get(dim, HEIGHTS[OVERWORLD])
        if a.height:
            lo_y, hi_y = max(lo_y, int(pos[1]) - a.height), min(hi_y, int(pos[1]) + a.height)
        box = ((pos[0] - a.radius, lo_y, pos[2] - a.radius), (pos[0] + a.radius, hi_y, pos[2] + a.radius))
        rules = c.rule_sets["hellscape"] if a.rules == "hellscape" else a.rules
        self.blocks.replace(box, _at_rate(rules, a.rate), cell=c.cell)
        self.state.done = self.state.done + [name]
        self.step_done(ctx)

    def step_done(self, ctx):
        self.state.left -= 1
        if not self.state.left and self.blocks.done:     # nothing left to place: done now
            self.done(ctx)

    def done(self, ctx):                                  # the block queue placed the last of it
        if self.state.left:                               # more is still to be queued
            return
        self._free_stuck(ctx)
        a = self.args
        who = ", ".join(self.state.done)
        if a.rules == "hellscape":
            summary = "the land round %s turned to the Nether, %d each way" % (who, a.radius)
        else:
            summary = "round %s, %d each way: %s" % (who, a.radius, a.rules)
        self.result(summary, players=self.state.done, radius=a.radius, dims=a.dims,
                   rules="hellscape" if a.rules == "hellscape" else list(a.rules.split("; ")))
        self.end()

    def _free_stuck(self, ctx):
        """Lift a player the change left inside a solid block, one block up.
        # NEEDS: a block probe can only say where each player stands right now, not whether a
        # change buried them where they stood when it ran, and this tries once, not the old
        # kit's up to eight; good enough to get someone out, not to land them exactly right."""
        for name in self.state.done:
            p = ctx.world.player(name)
            if p is None:
                continue
            feet = [math.floor(c) for c in p.pos]
            stuck = ctx.world.probe([tuple(feet), (feet[0], feet[1] + 1, feet[2])], "solid", dim=p.dim)
            if any(stuck):
                self.teleport(name, (feet[0] + 0.5, feet[1] + 1, feet[2] + 0.5), dim=p.dim)
