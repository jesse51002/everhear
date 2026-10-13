"""Sneaky Creepers: every world creeper gets a longer fuse, set once when it is first seen, and
turns invisible near a player, hissing loudly when close enough to be a warning. Like the old
script, a stop puts nothing back: a creeper already given the longer fuse keeps it (there are no
more world creepers left for the old, shorter fuse to reach). (The old sneaky_creepers script and
action.)"""

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, UNTIL_STOPPED, players
from vfg_engine.kit import undo_world_mob_rule, world_mob_rule

CREEPER = "minecraft:creeper"
VANILLA_FUSE_TICKS = 30               # a creeper's own fuse, 1.5 s: what a stop puts back


class SneakyCreepers(Scenario):
    every = 1

    class Config(ScenarioConfig):
        summary = "Creepers turn invisible and get a longer fuse near a player, hissing loudly as a warning."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        players: Players = Field("@a", description="who the creepers sneak up on: @a, @r, or names")
        invisible_within: float = Field(10, ge=1, le=64, description="how near a player a creeper turns "
                                                                     "invisible, in blocks")
        hiss_within: float = Field(4, ge=0, le=32, description="how near a player a creeper hisses loudly, "
                                                               "every second, in blocks (0: no hiss)")
        fuse: float = Field(3, ge=1.5, le=10, description="a creeper's fuse once it is seen, in seconds (the "
                                                          "game's own is 1.5); a change reaches only creepers "
                                                          "seen after it")

    def stop(self, ctx, reason):
        undo_world_mob_rule(self, ctx, CREEPER)

    def tick(self, ctx):
        a = self.args
        reach = max(a.invisible_within, a.hiss_within, ctx.world.mirror_radius)
        world_mob_rule(self, ctx, CREEPER, nbt="{Fuse:%ds}" % max(1, round(a.fuse * 20)),
                       undo_nbt="{Fuse:%ds}" % VANILLA_FUSE_TICKS, radius=reach)
        hissed = set()
        for name in players(ctx, a.players):
            p = ctx.world.player(name)
            if p is None or not p.alive:
                continue
            for e in self.creepers_near(ctx, p.pos, p.dim, a.invisible_within):
                self.potion("invisibility", 3, who=e.uuid, particles=False)
            if a.hiss_within:
                for e in self.creepers_near(ctx, p.pos, p.dim, a.hiss_within):
                    if e.uuid not in hissed:
                        hissed.add(e.uuid)
                        self.sound("minecraft:entity.creeper.primed", at=e.pos, volume=4, pitch=0.8, dim=p.dim)

    def creepers_near(self, ctx, pos, dim, radius):
        """Every world creeper within `radius` of `pos`: the mirror when it reaches, else a live read."""
        if radius <= ctx.world.mirror_radius:
            found = ctx.world.entities_near(pos, radius, dim=dim)
        else:
            found = ctx.world.entities(pos, radius, types=[CREEPER], dim=dim)
        return [e for e in found if e.type == CREEPER]
