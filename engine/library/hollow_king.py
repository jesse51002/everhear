"""The Hollow King: a giant, fast zombie in netherite, one of the Demon Lord's four boss-round
bosses (with the Gravekeeper, Spider Queen and Ravager Warlord, cycled every 4th round).
`demon_lord` spawns it itself (its own boss bar, its own health override for the round's
difficulty), granting `enrage`, the boss_fight Actors' shared "harder as it weakens" buff.

The old `BOSSES` entry set its `movement_speed` attribute's base straight to 0.32 (a zombie's own
base is 0.23); `self.modifier` only adds to a base, so `start` adds the difference instead, which
lands it at the same 0.32 it walked at before."""

from vfg_engine import Actor, ActorConfig

_ZOMBIE_BASE_SPEED = 0.23
_OLD_SPEED = 0.32


class HollowKing(Actor):
    class Config(ActorConfig):
        summary = "A giant, fast zombie in netherite, head to chest: enrages as it weakens."
        entity_type = "minecraft:zombie"
        health = 550            # demon_lord always overrides this for the round (40 + 12 x level x players)
        size = 1.5               # the old BOSSES scale 1.5
        damage = 3                # the zombie's own vanilla melee hit, in half-hearts
        name = "The Hollow King"
        name_visible = True
        equipment = {"head": "minecraft:netherite_helmet", "chest": "minecraft:netherite_chestplate",
                    "mainhand": "minecraft:netherite_axe"}
        abilities = ["enrage"]

    def start(self, ctx):
        self.modifier("movement_speed", _OLD_SPEED - _ZOMBIE_BASE_SPEED, "add_value")
