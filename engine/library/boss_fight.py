"""Boss fight: spawns a boss Actor (one body, or several on one health bar), rises near a player
or at a place, music looped while it lives if asked, mob griefing held off if asked; it ends when
every body is dead, giving a reward where the last one fell and showing VICTORY. Its abilities
(minions, enraging as it weakens, an ice blast, frost servants...) are the Actor's own, granted
when it is spawned: this scenario only rises it, bars it and ends the fight.

Three old scripts merged in, one scenario with three Actors, by `boss` (DESIGN2 §7):
horde_boss (boss="horde_king", the defaults below), dragon_boss / three_headed_dragon
(boss="dragon_head", bodies=3, height=30, music="boss", griefing=False, name="Three-Headed
Dragon"), ice_queen (boss="ice_queen", reward="blue_ice", reward_count=16). Each Actor's own
Config carries its health, size, damage, gear and abilities; this scenario never reads another
class's Config (WRITING.md: "a class never imports another content class"), so it cannot report
a body's true health or name unless `health` or `name` override it here, or show how an unknown
`boss` id looks until it tries to spawn one."""

import json
from typing import Literal

from vfg_engine import Args, EntityType, Field, ItemId, Place, Players, Scenario, ScenarioConfig, Spot, State, \
    UNTIL_STOPPED, players
from vfg_engine.kit import boss_bar, melody, speak


class BossFight(Scenario):
    class Config(ScenarioConfig):
        summary = ("Rises a boss (one Actor, one or more bodies) near a player or at a place, with a health bar; "
                   "its own abilities run on their own. Ends when every body is dead: a reward where it fell, "
                   "VICTORY shown.")
        duration_s = UNTIL_STOPPED                    # it ends itself: every body dead
        spacing: float = Field(6, ge=1, le=32)         # blocks between extra bodies, side by side
        # NEEDS: a boss's own size (ActorConfig.size) is not readable from here to space bodies by it (a class
        # never reads another's Config); `spacing` is a fixed guess instead of "3x its size" as the old preset did.
        bar_colors: dict[str, str] = {"ice_queen": "blue"}     # by boss id; "red" for any other (not in takes)

    class Args(Args):
        boss: str = Field("horde_king", max_length=63, pattern=r"^[a-z][a-z0-9_]*$",
                          description="the boss: an Actor id this server has (horde_king, dragon_head, ice_queen); "
                                      "refused in words if it is not one, or not a boss at all")
        name: str = Field("", max_length=64, description="its name instead, shown over it and on the bar (empty: "
                                                          "the Actor's own)")
        mob: EntityType = Field("", description="the mob it is instead (empty: the Actor's own). NEEDS: not yet "
                                                "applied: self.spawn always uses the Actor's own entity_type, so "
                                                "this is accepted but does nothing today")
        health: int = Field(0, ge=0, le=1024, description="each body's health instead, in half-hearts (0: the "
                                                           "Actor's own)")
        scale: float = Field(0, ge=0, le=16, description="its size instead (0: the Actor's own)")
        bodies: int = Field(1, ge=1, le=8, description="how many bodies it has, on one bar; it dies when every one "
                                                       "is dead (3: a three-headed dragon)")
        near: Players = Field("@a", description="who it rises near: the first of them online (@a, @r, or names)")
        at: Place = Field("", description="where it rises instead: a saved place, x y z or a player (empty: near "
                                          "`near`)")
        height: int = Field(0, ge=0, le=200, description="how many blocks up it rises (0: at the ground; a dragon "
                                                         "in the sky: 30)")
        music: Literal["none", "boss"] = Field("none", description='boss music, looped until it dies ("boss": '
                                                                    "Pigstep with a growl, a wither and thunder "
                                                                    'over it; "none": none)')
        reward: ItemId = Field("diamond", description="the item it drops where it falls")
        reward_count: int = Field(8, ge=0, le=64, description="how many of it (0: none)")
        griefing: bool = Field(True, description="mobs may break blocks while it lives; off holds mob_griefing off "
                                                 "until it dies")

    class State(State):
        bodies: list[str] = []          # uuids spawned
        last: list[float] = []          # the last known position of a living body
        last_dim: str = ""
        where: str = ""                 # said in words, for the result

    every = 1                           # tick(): the bar, and whether every body is gone

    def start(self, ctx):
        a = self.args
        try:
            spot = self._where(ctx)
        except ValueError as exc:
            self._refuse(str(exc))
            return
        x, y, z = spot.pos
        at = (x, y + a.height, z)
        nbt = self._override_nbt(a)
        bodies = []
        try:
            for i in range(a.bodies):
                off = (i - (a.bodies - 1) / 2.0) * self.config.spacing
                bodies.append(self.spawn(a.boss, (at[0] + off, at[1], at[2]), dim=spot.dim, nbt=nbt))
        except ValueError as exc:
            for u in bodies:
                self.remove(u)
            self._refuse(str(exc))
            return
        self.state.bodies = bodies
        self.state.last = list(at)
        self.state.last_dim = spot.dim
        self.state.where = self._said(at, spot.dim)
        if not a.griefing:
            self.hold("mob_griefing", False)            # auto-released when this ends (WRITING.md)
        if a.music != "none":
            melody(self, ctx, a.music, at=at, to="everyone", key=self.instance, loop=True)
        name = self._name(a)
        speak(self, ctx, name, voice="", color="dark_red", where="title")
        speak(self, ctx, "has risen", voice="", color="dark_red", where="subtitle")
        self.sound("minecraft:entity.wither.spawn", at, dim=spot.dim, volume=4, pitch=0.8)
        facts = {"boss": a.boss, "where": self.state.where, "bodies": a.bodies}
        if a.health:
            facts["health"] = a.health * a.bodies
            self.result("%s rises %s, with %d health" % (name, self.state.where, facts["health"]), **facts)
        else:
            self.result("%s rises %s" % (name, self.state.where), **facts)

    def tick(self, ctx):
        a = self.args
        name = self._name(a)
        _total, alive = boss_bar(self, ctx, self.state.bodies, name, self.config.bar_colors.get(a.boss, "red"))
        for u in self.state.bodies:
            e = ctx.world.get(u)
            if e is not None and e.health is not None and e.health > 0:
                self.state.last, self.state.last_dim = list(e.pos), e.dim
        if alive == 0:
            self._victory(ctx, name)

    def stop(self, ctx, reason):
        if self.args.music != "none":
            melody(self, ctx, None, key=self.instance)    # cuts the loop off; a record too

    # ---- helpers ---------------------------------------------------------------------------

    def _where(self, ctx) -> Spot:
        a = self.args
        if a.at is not None:
            return a.at
        for name in players(ctx, a.near):
            p = ctx.world.player(name)
            if p is not None:
                return Spot(dim=p.dim, pos=tuple(p.pos))
        raise ValueError("nobody is online" if a.near in ("@a", "@r") else a.near + " is not online")

    def _said(self, at, dim) -> str:
        where = "at %d %d %d" % (round(at[0]), round(at[1]), round(at[2]))
        return where if dim == "minecraft:overworld" else where + " in " + dim.split(":")[-1]

    def _name(self, a) -> str:
        return a.name or a.boss.replace("_", " ").title()

    def _override_nbt(self, a) -> str | None:
        """Extra SNBT for a per-spawn name, health or size, merged after the Actor's own
        (ActorConfig.nbt: "merged last but the spawn's own"). NEEDS: this relies on Minecraft
        keeping the later of two same-named keys in one summon (not proven live in this stage);
        there is no per-spawn override built into self.spawn itself."""
        parts, attrs = [], []
        if a.name:
            parts += ["CustomName:%s" % json.dumps(a.name), "CustomNameVisible:1b"]
        if a.health:
            attrs.append('{id:"minecraft:max_health",base:%g}' % a.health)
            parts.append("Health:%gf" % a.health)
        if a.scale:
            attrs.append('{id:"minecraft:scale",base:%g}' % a.scale)
        if attrs:
            parts.append("attributes:[%s]" % ",".join(attrs))
        return ("{%s}" % ",".join(parts)) if parts else None

    def _refuse(self, why: str):
        a = self.args
        self.result("no %s: %s" % (a.boss, why), boss=a.boss, bodies=0)
        self.end()

    def _victory(self, ctx, name: str):
        a = self.args
        at = tuple(self.state.last) if self.state.last else (0.0, 0.0, 0.0)
        dim = self.state.last_dim or "minecraft:overworld"
        if a.reward_count:
            self.spawn("minecraft:item", at, dim=dim, nbt='{Item:{id:"%s",count:%d}}' % (a.reward, a.reward_count),
                      keep=True)
        speak(self, ctx, "VICTORY", voice="", color="gold", where="title")
        speak(self, ctx, "%s has fallen" % name, voice="", color="gold", where="subtitle")
        self.sound("minecraft:ui.toast.challenge_complete", at, dim=dim, volume=3)
        self.result("%s, who rose %s, is dead" % (name, self.state.where), dead=True, boss=a.boss,
                    bodies=len(self.state.bodies))
        self.end()
