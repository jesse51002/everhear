"""The Gravekeeper: a giant wither skeleton in netherite, one of the Demon Lord's four boss-round
bosses (with Spider Queen, Ravager Warlord and the Hollow King, cycled every 4th round).
`demon_lord` spawns it itself (its own boss bar, its own health override for the round's
difficulty), granting `enrage`, the boss_fight Actors' shared "harder as it weakens" buff."""

from vfg_engine import Actor, ActorConfig


class Gravekeeper(Actor):
    class Config(ActorConfig):
        summary = "A giant wither skeleton in netherite, with a never-dropped netherite sword: enrages as it weakens."
        entity_type = "minecraft:wither_skeleton"
        health = 500            # demon_lord always overrides this for the round (40 + 12 x level x players)
        size = 2                # the old BOSSES scale 2.0
        damage = 8               # the wither skeleton's own vanilla melee hit, in half-hearts
        name = "The Gravekeeper"
        name_visible = True
        equipment = {"mainhand": "minecraft:netherite_sword"}
        abilities = ["enrage"]
