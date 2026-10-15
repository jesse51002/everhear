"""Dragon Head: one head of the Three-Headed Dragon. The dragon_boss action's (three_headed_dragon)
boss: boss_fight spawns three of it high in the sky (boss="dragon_head", bodies=3, height=30), one
red bar for the three summed, boss music looped, mob griefing held off; each head enrages on its own
as it weakens, through its own enrage ability."""

from vfg_engine import Actor, ActorConfig


class DragonHead(Actor):
    class Config(ActorConfig):
        summary = "One head of the Three-Headed Dragon: an ender dragon that enrages as it weakens."
        entity_type = "minecraft:ender_dragon"
        health = 600
        size = 1
        damage = 12               # its melee hit, in half-hearts (the old boss_attack, unset by this action)
        name = "Three-Headed Dragon"
        name_visible = True
        abilities = ["enrage"]
