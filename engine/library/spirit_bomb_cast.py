"""Spirit Bomb's cast: a sphere of light grows overhead for charge_s to a swelling choir, then an
orb flies to where the caster looks (up to reach) and bursts there in rings of blasts, carving a
crater. The Spirit Bomb item arms it on its own hold-to-arm; once armed this does its own charging
and firing, and, granted and bound, it is also cast by voice. Only one is ever in the air per
caster. (The old spirit_bomb script and action's numbers.)"""

from vfg_engine import Ability, AbilityConfig, Field, State
from vfg_engine.kit import blast, blast_damage, charge_frames, crater, fly, hitscan, melody


class SpiritBombCast(Ability):
    class Config(AbilityConfig):
        summary = "Charges a sphere of light overhead, then an orb flies to where you look and bursts in rings of blasts."
        charge_size: float = Field(10, ge=1, le=40)
        reach: float = Field(100, ge=4, le=300)
        orb_size: float = Field(8, ge=1, le=32)
        speed: float = Field(40, ge=1, le=200)
        power: float = Field(16, ge=0, le=60)
        spread: float = Field(32, ge=0, le=100)
        crater_radius: int = Field(100, ge=0, le=300)
        charge_s = 5
        cooldown_s = 0
        scale_per_point = 0

        def damage(self, ctx):
            return blast_damage(self.power, 0.0)     # the worst case: standing at the blast's very middle

        def duration_s(self, ctx):
            return self.reach / self.speed + 3        # time to arrive, then its landing tune

    class State(State):
        tuned: bool = False

    def on_charge(self, ctx, ev):
        charge_frames(self, ctx, ev.progress, "sphere", self.config.charge_size)
        if not self.state.tuned:
            melody(self, ctx, "spirit_bomb")
            self.state.tuned = True

    def on_cast(self, ctx, ev):
        if any(not run.charging for run in ctx.running):
            self.end()                                 # one bomb in the air at a time; others may still charge
            return
        c = self.config
        shot = hitscan(self, ctx, c.reach, 1.5, "everything", through_walls=False)
        fly(self, ctx, shot.start, shot.end, c.speed, "orb", c.orb_size,
            "minecraft:dust{color:[1.0,1.0,1.0],scale:3}", then="landed")

    def landed(self, ctx, at):
        c = self.config
        melody(self, ctx, "spirit_bomb_throw")
        blast(self, ctx, at, c.power, "rings", c.spread, breaks=False)
        crater(self, ctx, at, c.crater_radius, leave="air")
