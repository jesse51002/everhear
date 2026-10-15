"""Arena: keeps players inside an area - a flat radius round a centre, a box, or near other
players (tether) - teleporting back anyone who leaves it, with a sound and a message. shape="near",
radius=30, message="Stay with the group" is the old tether script: whoever strays more than 30
blocks (flat) from everyone else is sent to the nearest of them. (The old arena and tether scripts
and actions, the same loop in both; only their defaults differed.)

"radius" and "box" watch their area (self.watch: a flat cylinder of all heights, or the box) and
answer its Exit events, so nothing is polled; anyone already outside when it starts, and anyone who
joins or respawns outside, is sent in too, as the old polling scripts did. A box sends a leaver to
the nearest spot inside it. "near" follows players who move, so it alone ticks, every half second,
on the snapshot."""

import math
from typing import Literal

from vfg_engine import (UNTIL_STOPPED, Args, Exit, Field, Join, Place, Players, Respawn, Scenario, ScenarioConfig, Spot,
                        State, players)
from vfg_engine.kit import speak


def _flat(a, b):
    return math.hypot(a[0] - b[0], a[2] - b[2])


class Arena(Scenario):
    every = 0.5                      # tick(), shape "near" only: the snapshot alone, so this is cheap

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
        watching: bool = False   # "radius"/"box": its area is watched
        outside: list = []     # "near": names already sent back, until they are near again (one stray, one send)

    def start(self, ctx):
        a = self.args
        if a.shape != "near":
            self.listen(Exit, "on_exit")
            self.listen(Join, "on_arrive")
            self.listen(Respawn, "on_arrive")
            self.watch_area(ctx)
        words, facts = self.describe()
        self.result(words, **facts)

    def watch_area(self, ctx):
        """Watch the area, once its home is known, and send in whoever is outside it already."""
        a = self.args
        if self.state.watching or (a.shape == "box" and len(a.box) != 6):
            return
        if a.shape == "box" or a.center is None:
            if not self.state.home_dim:
                self.establish_home(ctx, players(ctx, a.players))
            if not self.state.home_dim:
                return                       # nobody of them on yet: a Join starts it
        if a.shape == "box":
            lo = [min(a.box[i], a.box[i + 3]) for i in range(3)]
            hi = [max(a.box[i], a.box[i + 3]) + 1 for i in range(3)]
            self.watch("arena", box=(lo, hi), dim=self.state.home_dim)
        else:
            centre, dim = ((a.center.pos, a.center.dim) if a.center is not None
                           else (self.state.home_pos, self.state.home_dim))
            self.watch("arena", center=centre, radius=a.radius, height=math.inf, dim=dim)
        self.state.watching = True
        for name in players(ctx, a.players):        # those outside already: in at once
            self.check(ctx, name)

    def on_exit(self, ctx, ev):
        if ev.area == "arena" and ev.instance == self.instance:
            self.check(ctx, ev.who)

    def on_arrive(self, ctx, ev):
        if not self.state.watching:
            self.watch_area(ctx)
        else:
            self.check(ctx, ev.who)

    def check(self, ctx, name):
        """Send `name` back if they are one of those kept in, alive, and outside."""
        if name not in players(ctx, self.args.players):
            return
        p = ctx.world.player(name)
        if p is None or not p.alive:
            return
        back = self.back_of(self.args, p, name, {}, [])
        if back is not None:
            self.send_back(ctx, name, back)

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
        if a.shape != "near":
            return                   # radius and box answer their area's Exit events
        watched = players(ctx, a.players)
        if not watched:
            return
        anchors = players(ctx, a.near)
        seen = {n: ctx.world.player(n) for n in set(watched) | set(anchors)}
        outside = set(self.state.outside)
        changed = False
        for name in watched:
            p = seen.get(name)
            if p is None or not p.alive:
                continue
            back = self.back_of(a, p, name, seen, anchors)
            if back is None:
                if name in outside:
                    outside.discard(name)
                    changed = True
            elif name not in outside:
                outside.add(name)
                changed = True
                self.send_back(ctx, name, back)
        if changed:
            self.state.outside = sorted(outside)

    def establish_home(self, ctx, watched):
        for name in watched:
            p = ctx.world.player(name)
            if p is not None:
                self.state.home_dim, self.state.home_pos = p.dim, list(p.pos)
                return

    def back_of(self, a, p, name, seen, anchors):
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
            if p.dim == self.state.home_dim and all(lo[i] <= p.pos[i] <= hi[i] for i in range(3)):
                return None
            if p.dim != self.state.home_dim:
                return Spot(dim=self.state.home_dim, pos=((lo[0] + hi[0]) / 2.0, lo[1], (lo[2] + hi[2]) / 2.0))
            # the nearest spot inside, half a block in from the side they left by
            return Spot(dim=p.dim, pos=tuple(min(max(p.pos[i], lo[i] + 0.5), hi[i] - 0.5) if i != 1
                                             else min(max(p.pos[1], lo[1]), hi[1] - 2) for i in range(3)))
        centre, dim = (a.center.pos, a.center.dim) if a.center is not None else (self.state.home_pos,
                                                                                 self.state.home_dim)
        return None if p.dim == dim and _flat(p.pos, centre) <= a.radius else Spot(dim=dim, pos=tuple(centre))

    def send_back(self, ctx, name, back):
        self.teleport(name, back.pos, dim=back.dim)
        self.sound("minecraft:entity.enderman.teleport", at=back, volume=1.5)
        if self.args.message:
            speak(self, ctx, self.args.message, voice="", color="red", to=[name], where="actionbar")
