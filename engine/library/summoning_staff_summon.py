"""Summoning staff summon: zombies appear at safe spots where the caster looks, up to range blocks
away, at most cap alive at once; mob_griefing is held off while any of this cast's live, and goes
back once none do. The Summoning Staff item fires it on a right-click; granted and bound, it is
cast by voice. (The old wither_wand script's summon and griefing hold, with summon_mob zombie,
shared with wither_wand_summon's Config, library/README.md.)"""

from vfg_engine import Ability, AbilityConfig, Field, UNTIL_STOPPED
from vfg_engine.kit import mob_nbt, speak, summon_near


class SummoningStaffSummon(Ability):
    every = 1                                   # tick(): end once every one of this cast's is gone

    class Config(AbilityConfig):
        summary = "Summons zombies at safe spots where you look, holding mob_griefing off while any live."
        mob: str = "minecraft:zombie"
        count: int = Field(5, ge=1, le=20)      # how many at once
        spread: float = Field(3, ge=1, le=20)   # how far round the spot they appear, in blocks
        cap: int = Field(15, ge=1, le=50)       # never more than this many of this cast's alive at once
        range: float = Field(100, ge=4, le=400)  # how far away it may summon, in blocks
        damage = 0                 # declared: its zombies deal their own vanilla melee, uncounted here
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 5
        duration_s = UNTIL_STOPPED

    def on_cast(self, ctx, ev):
        c = self.config
        user = ctx.user
        look = ctx.world.ray(user.eye, user.facing, c.range)
        # NEEDS: knowing whether `look.pos` is in a protected area, to refuse summoning there (the
        # old wand did); the engine exposes no such read to content today.
        made = summon_near(self, ctx, look.pos, c.mob, c.count, c.spread, nbt=mob_nbt(), cap=c.cap)
        if made:
            self.hold("mob_griefing", False)
            self.sound("minecraft:entity.wither.spawn", look.pos, volume=2)
        else:
            speak(self, ctx, "No room to summon there", voice="", color="gray", to=[user.name], where="actionbar")
            self.end()

    def tick(self, ctx):
        if not self.spawned:
            self.end()
