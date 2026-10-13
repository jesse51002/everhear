"""Frost Walk: a slab of blocks round the bearer turns to ice (or whatever the rules say) as they
walk, fly or swim - not only on a step: every check, the slab follows them, and only the part not
already changed last time is queued again, so a one-block drift changes one edge, not the whole
slab. (made/main's krownjm_ice_fast, an instance leftover from walk_replace's own line of scripts,
ran as "Ice Bubble": a 31x11x31 slab, check_every 0.1, "stone ... netherrack water lava > ice" live.
Its one rule string is kept as one ReplaceRules arg here, as walk_replace's is, rather than its
original separate `to`/`from` fields - one way to say a rule, as the library already does.
# NEEDS: a box-difference helper in the kit (the old kit's area.minus), so a mark that follows its
bearer continuously does not have to cut its own slab into edges; written locally below, since
nothing in the kit does this yet.)"""

import math

from vfg_engine import Args, Field, Mark, MarkConfig, ReplaceRules, State, UNTIL_STOPPED

ICE_RULE = ("solid water lava bubble_column kelp kelp_plant #minecraft:replaceable_by_trees "
           "#minecraft:flowers #minecraft:crops #minecraft:saplings > ice")


def _edges(lo, hi, old_lo, old_hi):
    """The box lo..hi with the box old_lo..old_hi cut out of it, as up to six boxes (axis by
    axis): what is new in a slab that has shifted from old_lo..old_hi to lo..hi."""
    if any(old_hi[i] < lo[i] or old_lo[i] > hi[i] for i in range(3)):
        return [(lo, hi)]
    out, lo, hi = [], list(lo), list(hi)
    for i in range(3):
        if lo[i] < old_lo[i]:
            a, b = list(lo), list(hi)
            b[i] = old_lo[i] - 1
            out.append((tuple(a), tuple(b)))
            lo[i] = old_lo[i]
        if hi[i] > old_hi[i]:
            a, b = list(lo), list(hi)
            a[i] = old_hi[i] + 1
            out.append((tuple(a), tuple(b)))
            hi[i] = old_hi[i]
    return out


class FrostWalk(Mark):
    every = 0.1                      # tick(): the snapshot alone (ctx.user.pos), so this is cheap

    class Config(MarkConfig):
        summary = ("A slab of ice (or whatever the rules say) follows the bearer as they walk, fly or swim; "
                   "only the new edge changes each check, not the whole slab again.")
        duration_s = UNTIL_STOPPED
        damage = 0
        on_death = "end"
        on_milk = "keep"

    class Args(Args):
        radius: int = Field(15, ge=0, le=32, description="how far out, each way, the slab reaches from the "
                           "block the bearer stands on")
        below: int = Field(5, ge=1, le=16, description="how far down from the block they stand on")
        above: int = Field(5, ge=0, le=16, description="how far up from it")
        rules: ReplaceRules = Field(ICE_RULE, description="the rule(s) it applies to the slab, in order "
                                   '("from ... > to"; the live run used "stone ... netherrack water lava > '
                                   'ice")')
        cell: int = Field(4, ge=1, le=16, description="a rule whose to-side is a mix picks one per cube this "
                         "many blocks wide, the same pick every time")

    class State(State):
        last_lo: list = []     # the last slab queued: its corners, once known
        last_hi: list = []
        last_dim: str = ""

    def tick(self, ctx):
        a = self.args
        p = ctx.user
        if p is None:
            return
        x, y, z = p.pos
        fx, fy, fz = int(math.floor(x)), int(math.floor(y - 0.01)), int(math.floor(z))
        lo = [fx - a.radius, fy - a.below + 1, fz - a.radius]
        hi = [fx + a.radius, fy + a.above, fz + a.radius]
        dim = p.dim
        if dim == self.state.last_dim and self.state.last_lo and self.state.last_hi:
            pieces = _edges(lo, hi, self.state.last_lo, self.state.last_hi)
        else:
            pieces = [(lo, hi)]
        for piece_lo, piece_hi in pieces:
            self.blocks.replace((piece_lo, piece_hi), a.rules, cell=a.cell, dim=dim)
        self.state.last_lo, self.state.last_hi, self.state.last_dim = lo, hi, dim
