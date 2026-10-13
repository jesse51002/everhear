"""Walking Dead: the whole zombie family (zombie, husk, drowned, zombie villager) becomes slow
walkers that can break doors, in bigger groups: a zombie's follow range is set by the nearest
noise a player made this second (sneak 5, walk 16, sprint 40 blocks, by default), and it lunges
faster within 12 blocks, faster again within 6. Random "friends" are summoned beside newly turned
walkers. (The old walking_dead script and action.)"""

import random

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, UNTIL_STOPPED, players
from vfg_engine.kit import world_mob_rule

ZOMBIE_TYPES = ("minecraft:zombie", "minecraft:husk", "minecraft:drowned", "minecraft:zombie_villager")
# The zombie family's own vanilla attribute bases (26.3's mob attribute defaults), assumed: the engine has
# no verb to read a mob's own base, only self.modifier, an offset from it.
# NEEDS: reading a mob's own attribute base, so this offset does not have to assume it.
VANILLA_SPEED = 0.23
VANILLA_FOLLOW = 35.0


class WalkingDead(Scenario):
    every = 1

    class Config(ScenarioConfig):
        summary = ("Zombies become slow walkers that can break doors, in bigger groups: their follow range "
                   "is a player's noise, and they lunge faster up close.")
        duration_s = UNTIL_STOPPED

    class Args(Args):
        players: Players = Field("@a", description="whose steps the zombies hear: @a, @r, or names")
        walk_speed: float = Field(0.17, ge=0.05, le=0.5, description="the zombies' speed from afar (a "
                                                                    "zombie's own is 0.23); they lunge faster "
                                                                    "within 12 blocks, faster again within 6")
        sneak_range: int = Field(5, ge=1, le=64, description="how far a zombie notices a player who is "
                                                             "sneaking, in blocks")
        walk_range: int = Field(16, ge=1, le=64, description="how far a zombie notices a player who is "
                                                             "walking, in blocks")
        sprint_range: int = Field(40, ge=1, le=64, description="how far a zombie hears a player who is "
                                                               "sprinting, in blocks")
        friends: float = Field(0.3, ge=0, le=1, description="the chance, each second, that a couple of newly "
                                                            "turned walkers each bring a friend, so they "
                                                            "gather in bigger groups (0: never)")

    def tick(self, ctx):
        a = self.args
        new = []
        for kind in ZOMBIE_TYPES:
            new += world_mob_rule(self, ctx, kind, nbt="{CanBreakDoors:1b}", radius=48)
        if new and random.random() < a.friends:
            for u in random.sample(new, min(2, len(new))):
                e = ctx.world.get(u)
                if e is not None:
                    self.spawn("minecraft:zombie", (e.pos[0] + 1, e.pos[1], e.pos[2] + 1), dim=e.dim,
                              nbt="{CanBreakDoors:1b}")
        for name in players(ctx, a.players):
            p = ctx.world.player(name)
            if p is None or not p.alive:
                continue
            rng = a.sneak_range if p.sneaking else (a.sprint_range if p.sprinting else a.walk_range)
            for e in self.zombies_near(ctx, p.pos, p.dim, 48):
                self.modifier("follow_range", rng - VANILLA_FOLLOW, "add_value", on=e.uuid)
                dist = ((e.pos[0] - p.pos[0]) ** 2 + (e.pos[1] - p.pos[1]) ** 2 + (e.pos[2] - p.pos[2]) ** 2) ** 0.5
                if dist <= 6:
                    speed = a.walk_speed * 0.3 / 0.17                # the closer they get, the faster they lunge
                elif dist <= 12:
                    speed = a.walk_speed * 0.23 / 0.17
                else:
                    speed = a.walk_speed
                self.modifier("movement_speed", speed - VANILLA_SPEED, "add_value", on=e.uuid)

    def zombies_near(self, ctx, pos, dim, radius):
        """Every world zombie-family mob within `radius` of `pos`: the mirror when it reaches, else live."""
        if radius <= ctx.world.mirror_radius:
            found = ctx.world.entities_near(pos, radius, dim=dim)
        else:
            found = ctx.world.entities(pos, radius, types=list(ZOMBIE_TYPES), dim=dim)
        return [e for e in found if e.type in ZOMBIE_TYPES]
