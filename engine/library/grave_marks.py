"""Grave Marks: where a player dies (or makes a kill), a skull and a glowing floating name mark
the spot for a while, and chat says where. A mark fades once its time is up, or once its player
has walked away from it and come back within reach. (The old grave_marks script and action.)"""

import math
from typing import Literal

from vfg_engine import Args, Death, Field, Kill, Players, Scenario, ScenarioConfig, State, UNTIL_STOPPED
from vfg_engine.kit import mob_nbt, speak


def matches(players_arg, name):
    """Whether `name` matches a Players arg, by name alone: unlike `players(ctx, arg)`, which lists
    who is alive now (wrong here: the one who just died is never alive in that same event)."""
    s = (players_arg or "@a").strip()
    if s in ("@a", "@r"):
        return True
    return name.lower() in {n.strip().lower() for n in s.split(",") if n.strip()}


class GraveMarks(Scenario):
    every = 1                      # tick(): a mark whose time is up fades, and so does one its player came back to

    class Config(ScenarioConfig):
        summary = "A skull and a glowing name mark where a player dies (or kills), for a while; chat says where."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        players: Players = Field("@a", description="whose deaths (or kills) it marks: @a, @r, or names")
        on: Literal["death", "kill", "both"] = Field("death", description="what makes a mark: a death, a "
                                                     "kill, or both")
        seconds: float = Field(30, ge=5, le=3600, description="how long a mark stays, in seconds")
        found_within: float = Field(3, ge=0, le=16, description="how near its player must come back, in "
                                                                "blocks, for a mark to fade before its time "
                                                                "(0: never this way)")

    class State(State):
        graves: list = []          # {tag, p, pos [x,y,z], dim, made (ctx.now), label (its armor stand's uuid), left}
        counts: dict = {}          # player -> marks made, for the label's "#n"
        next_tag: int = 1

    def start(self, ctx):
        if self.args.on != "kill":
            self.listen(Death, "on_death")
        if self.args.on != "death":
            self.listen(Kill, "on_kill")

    def stop(self, ctx, reason):
        for g in self.state.graves:
            self.clear(g)
        self.state.graves = []

    def tick(self, ctx):
        for g in list(self.state.graves):
            if ctx.now - g["made"] >= self.args.seconds:
                self.fade(g)
                continue
            p = ctx.world.player(g["p"])
            if p is None:
                continue
            middle = (g["pos"][0] + 0.5, g["pos"][1], g["pos"][2] + 0.5)
            near = p.dim == g["dim"] and math.dist(p.pos, middle) <= self.args.found_within
            if not near and not g["left"]:
                self.state.graves = [{**o, "left": True} if o["tag"] == g["tag"] else o
                                     for o in self.state.graves]
            elif near and g["left"]:
                self.fade(g, found=True)

    def on_death(self, ctx, ev):
        if not matches(self.args.players, ev.who) or ev.pos is None:
            return
        self.mark(ctx, ev.pos, ev.dim or "minecraft:overworld", ev.who, "died")

    def on_kill(self, ctx, ev):
        if not matches(self.args.players, ev.who):
            return
        p = ctx.world.player(ev.who)
        if p is None:
            return
        self.mark(ctx, tuple(math.floor(c) for c in p.pos), p.dim, ev.who, "made a kill")

    def mark(self, ctx, pos, dim, p, verb):
        x, y, z = (int(c) for c in pos)
        n = self.state.counts.get(p, 0) + 1
        self.state.counts = {**self.state.counts, p: n}
        tag, self.state.next_tag = self.state.next_tag, self.state.next_tag + 1
        self.blocks.setblock((x, y, z), "minecraft:skeleton_skull", dim=dim)
        label = self.spawn("minecraft:armor_stand", (x + 0.5, y + 1, z + 0.5), dim=dim,
                           nbt=mob_nbt(name="%s %s here (#%d)" % (p, verb, n), glowing=True, no_ai=True,
                                      invulnerable=True, extra="Invisible:1b,Marker:1b,NoGravity:1b"))
        self.state.graves = self.state.graves + [{"tag": tag, "p": p, "pos": [x, y, z], "dim": dim,
                                                  "made": ctx.now, "label": label, "left": False}]
        where = dim.split(":")[-1]
        speak(self, ctx, "%s %s at %d %d %d (%s). A skull marks the spot." % (p, verb, x, y, z, where),
              voice="Grave", color="dark_red")

    def fade(self, g, found=False):
        self.clear(g)
        self.state.graves = [o for o in self.state.graves if o["tag"] != g["tag"]]
        if found:
            self.message("You found your grave. The mark fades.", to=[g["p"]])

    def clear(self, g):
        self.blocks.setblock(tuple(g["pos"]), "minecraft:air", dim=g["dim"])
        self.remove(g["label"])
