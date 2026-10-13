"""Ravager Warlord: a ravager carrying an evoker, one of the Demon Lord's four boss-round bosses
(with the Gravekeeper, Spider Queen and the Hollow King, cycled every 4th round). `demon_lord`
spawns it itself (its own boss bar, its own health override for the round's difficulty), granting
`enrage`, the boss_fight Actors' shared "harder as it weakens" buff.

The old `BOSSES` entry rode it with `Passengers:[{id:"evoker",Tags:[...]}]`, so its evoker is a
passenger here too (`Config.nbt`): once mounted it summons vexes on its own, vanilla AI.
# NEEDS: adopting the entities an actor's own passengers summon (its evoker's vexes), so they end
# with this body instead of wandering the world once it is dead, as the old demon_lord's instance
# tag let it do (it tagged and so killed them with the rest of its mobs)."""

from vfg_engine import Actor, ActorConfig


class RavagerWarlord(Actor):
    class Config(ActorConfig):
        summary = "A ravager carrying an evoker, who summons vexes of his own: enrages as it weakens."
        entity_type = "minecraft:ravager"
        health = 700            # demon_lord always overrides this for the round (40 + 12 x level x players)
        size = 1                 # the old BOSSES entry set no scale
        damage = 12              # the ravager's own vanilla melee hit, in half-hearts
        name = "Ravager Warlord"
        name_visible = True
        nbt = '{Passengers:[{id:"minecraft:evoker"}]}'
        abilities = ["enrage"]
