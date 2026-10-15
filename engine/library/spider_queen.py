"""Spider Queen: a giant glowing spider, one of the Demon Lord's four boss-round bosses (with the
Gravekeeper, Ravager Warlord and the Hollow King, cycled every 4th round). `demon_lord` spawns it
itself (its own boss bar, its own health override for the round's difficulty), granting `enrage`,
the boss_fight Actors' shared "harder as it weakens" buff."""

from vfg_engine import Actor, ActorConfig


class SpiderQueen(Actor):
    class Config(ActorConfig):
        summary = "A giant glowing spider, seen through walls: enrages as it weakens."
        entity_type = "minecraft:spider"
        health = 500            # demon_lord always overrides this for the round (40 + 12 x level x players)
        size = 3                # the old BOSSES scale 3.0
        damage = 2               # the spider's own vanilla melee hit, in half-hearts
        name = "Spider Queen"
        name_visible = True
        glowing = True
        abilities = ["enrage"]
