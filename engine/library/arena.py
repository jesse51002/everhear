"""Arena: keeps players inside an area - a flat radius round a centre, a box, or near other
players (tether) - teleporting back anyone who leaves it, with a sound and a message. shape="near",
radius=30, message="Stay with the group" is the old tether script: whoever strays more than 30
blocks (flat) from everyone else is sent to the nearest of them. (The old arena and tether scripts
and actions, the same loop in both; only their defaults differed.)

self.watch only gives a true sphere or a 3D box, never a flat (infinite-height) cylinder, so
"radius" cannot use it; this ticks every half second and checks flat distance itself, for all
three shapes alike, as the old scripts did.
# NEEDS: a flat cylinder shape for self.watch, so "radius" (and "box", which fits it as-is) could
# use Enter/Exit instead of polling."""

import math
from typing import Literal

from vfg_engine import Args, Field, Place, Players, Scenario, ScenarioConfig, Spot, State, UNTIL_STOPPED, players
from vfg_engine.kit import speak


def _flat(a, b):
    return math.hypot(a[0] - b[0], a[2] - b[2])


class Arena(Scenario):
    every = 0.5                      # tick(): the snapshot alone, so this is cheap

    class Config(ScenarioConfig):
        summary = ("Keeps players inside a radius, a box, or near other players (tether), teleporting back "
                   "anyone who leaves.")
        duration_s = UNTIL_STOPPED

    class Args(Args):
        players: Players = Field("@a", description="who is kept in (shape near: kept near the others): @a, "
                                "@r or names")
        shape: Literal["radius", "box", "near"] = Field("radius", description='radius: within `radius` '
                                "blocks (flat) of `center`; box: inside `box`; near: within `radius` of "
                                "`near` (tether)")
        center: Place = Field("", description='shape="radius": the middle (empty: where the first of them '
                             "stood when it started); a saved place, x y z, or a player")
        radius: float = Field(40, ge=1, le=10000, description='shape="radius"/"near": how far out they may '
                             'go, in blocks, flat (tether: 30)')
        box: list[float] = Field([], description='shape="box": the box kept inside, x1 y1 z1 x2 y2 z2 (six '
                                "numbers); empty: nothing is kept in yet")
        near: Players = Field("@a", description='shape="near": who to stay close to, other than themself '
                             "(tether's leaves_of_player)")
        message: str = Field("Back inside the arena", max_length=100, description="what a player sent back "
                            'sees on their screen (empty: nothing; tether: "Stay with the group")')

    class State(State):
        home_dim: str = ""     # the dimension (and, for "radius" with no center, the position) first seen
        home_pos: list = []
        outside: list = []     # names already sent back, until they are inside again (so one leave sends once)
        last_inside: dict = {}   # shape="box": name -> [x, y, z] of where they last stood inside

    def start(self, ctx):
        a = self.args
        if a.shape == "box" or (a.shape == "radius" and a.center is None):
            # read now, synchronously, so what it reports is the same home tick() will use (not
            # a second, later read of "the first of them", which could by then be a different spot)
            self.establish_home(ctx, players(ctx, a.players))
        words, facts = self.describe()
        self.result(words, **facts)

    def describe(self):
        a = self.args
        if a.shape == "box":
            if len(a.box) == 6:
                return ("%s kept in the box %s" % (a.players, " ".join("%g" % c for c in a.box)),
                       {"players": a.players, "box": list(a.box)})
            return ("%s kept nowhere yet: no box" % a.players, {"players": a.players, "box": []})
        if a.shape == "near":
            return ("%s kept within %g blocks of %s" % (a.players, a.radius, a.near),
                   {"players": a.players, "radius": a.radius, "near": a.near})
        if a.center is not None:
            c = [round(x, 1) for x in a.center.pos]
            return ("%s kept within %g blocks of %s" % (a.players, a.radius, " ".join("%g" % x for x in c)),
                   {"players": a.players, "radius": a.radius, "center": c})
        if not self.state.home_dim:
            return ("%s kept within %g blocks of where the first of them stands" % (a.players, a.radius),
                   {"players": a.players, "radius": a.radius, "center": None})
        c = [round(x, 1) for x in self.state.home_pos]
        return ("%s kept within %g blocks of %s" % (a.players, a.radius, " ".join("%g" % x for x in c)),
               {"players": a.players, "radius": a.radius, "center": c})

    def tick(self, ctx):
        a = self.args
        if a.shape == "box" and len(a.box) != 6:
            return
        watched = players(ctx, a.players)
        if not watched:
            return
        if (a.shape == "box" or (a.shape == "radius" and a.center is None)) and not self.state.home_dim:
            self.establish_home(ctx, watched)
            if not self.state.home_dim:
                return
        anchors = players(ctx, a.near) if a.shape == "near" else []
        seen = {n: ctx.world.player(n) for n in set(watched) | set(anchors)}
        outside, last_inside = set(self.state.outside), dict(self.state.last_inside)
        changed_outside = changed_last = False
        for name in watched:
            p = seen.get(name)
            if p is None or not p.alive:
                continue
            back = self.back_of(a, p, name, seen, anchors, last_inside)
            if back is None:
                if a.shape == "box":
                    last_inside[name] = list(p.pos)
                    changed_last = True
                if name in outside:
                    outside.discard(name)
                    changed_outside = True
            elif name not in outside:
                outside.add(name)
                changed_outside = True
                self.send_back(ctx, name, back)
        if changed_outside:
            self.state.outside = sorted(outside)
        if changed_last:
            self.state.last_inside = last_inside

    def establish_home(self, ctx, watched):
        for name in watched:
            p = ctx.world.player(name)
            if p is not None:
                self.state.home_dim, self.state.home_pos = p.dim, list(p.pos)
                return

    def back_of(self, a, p, name, seen, anchors, last_inside):
        """None while `p` is inside; else the Spot to send them back to."""
        if a.shape == "near":
            near = [seen[n] for n in anchors if n != name and seen.get(n) is not None and seen[n].dim == p.dim]
            if not near:
                return None
            best = min(near, key=lambda q: _flat(q.pos, p.pos))
            return None if _flat(best.pos, p.pos) <= a.radius else Spot(dim=p.dim, pos=best.pos)
        if a.shape == "box":
            lo = [min(a.box[i], a.box[i + 3]) for i in range(3)]
            hi = [max(a.box[i], a.box[i + 3]) + 1 for i in range(3)]
            if p.dim == self.state.home_dim and all(lo[i] <= p.pos[i] < hi[i] for i in range(3)):
                return None
            back = last_inside.get(name)
            if back:
                return Spot(dim=self.state.home_dim, pos=tuple(back))
            return Spot(dim=self.state.home_dim, pos=((lo[0] + hi[0]) / 2.0, lo[1], (lo[2] + hi[2]) / 2.0))
        centre, dim = (a.center.pos, a.center.dim) if a.center is not None else (self.state.home_pos,
                                                                                 self.state.home_dim)
        return None if p.dim == dim and _flat(p.pos, centre) <= a.radius else Spot(dim=dim, pos=tuple(centre))

    def send_back(self, ctx, name, back):
        self.teleport(name, back.pos, dim=back.dim)
        self.sound("minecraft:entity.enderman.teleport", at=back, volume=1.5)
        if self.args.message:
            speak(self, ctx, self.args.message, voice="", color="red", to=[name], where="actionbar")
