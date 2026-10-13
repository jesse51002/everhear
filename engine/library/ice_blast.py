"""Ice blast: every `every_s`, freezes every player near the Ice Queen's body for the cold's
damage. Passive: granted to her at spawn, it fires on its own while she lives, in turn with
frost_servants (each on its own every_s, so the two are not a strict alternation as the old
script's turn-taking was, but both happen about every 8 s).

The old ice_queen also wrapped each frozen body in a shell of packed ice that melted when the
freeze ended (freeze_shell). The shared `frozen` mark (ice_staff's and ice_beam's group) dropped
that: NEEDS: no shell here, since frozen's own freeze_shell was never built."""

from vfg_engine import Ability, AbilityConfig, Field, UNTIL_STOPPED, State
from vfg_engine.kit import targets


class IceBlast(Ability):
    class Config(AbilityConfig):
        summary = "Every every_s, freezes every player near her body in a shell of ice, taking cold damage."
        every_s: float = Field(8, ge=1, le=120)     # the old boss_every
        radius: float = Field(16, ge=1, le=64)      # the old around_radius
        freeze_s: float = Field(4, ge=1, le=60)     # the old freeze_seconds
        damage: float = Field(6, ge=0, le=100)      # the old "cold"; half-hearts per player frozen
        damage_type = "minecraft:freeze"
        duration_s = UNTIL_STOPPED                  # passive: runs for as long as it is granted
        charge_s = 0
        cooldown_s = 0
        scale_per_point = 0

    every = 1

    class State(State):
        due: float = 0.0

    def start(self, ctx):
        self.state.due = ctx.now + self.config.every_s

    def tick(self, ctx):
        if ctx.now < self.state.due:
            return
        c = self.config
        self.state.due = ctx.now + c.every_s
        self.particle("minecraft:snowflake", ctx.user.pos, count=150, delta=(4.0, 1.0, 4.0), speed=0.05)
        self.sound("minecraft:block.glass.break", ctx.user.pos, volume=3, pitch=0.6)
        for t in targets(self, ctx, ctx.user, c.radius, "players"):
            # frozen is ice_staff's and ice_beam's group's Mark, named by id alone (library README:
            # "name another class by its id"), not written in this group.
            self.apply(t.key, "frozen", seconds=c.freeze_s)
            if c.damage:
                self.damage(t.key, c.damage, c.damage_type)
