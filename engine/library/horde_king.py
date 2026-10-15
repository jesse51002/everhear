"""Horde King: a giant armoured zombie boss. The horde_boss action's boss: boss_fight spawns it
(boss="horde_king", its default), with a red bar; it calls minions and enrages as it weakens
through its own abilities (call_minions, enrage), granted when it is spawned."""

from vfg_engine import Actor, ActorConfig


class HordeKing(Actor):
    class Config(ActorConfig):
        summary = "A giant zombie boss in netherite: calls minions and enrages as it weakens."
        entity_type = "minecraft:zombie"
        health = 800
        size = 3
        damage = 12               # its melee hit, in half-hearts (the old boss_attack)
        name = "Horde King"
        name_visible = True
        equipment = {"head": "minecraft:netherite_helmet", "chest": "minecraft:netherite_chestplate",
                    "legs": "minecraft:netherite_leggings", "feet": "minecraft:netherite_boots",
                    "mainhand": "minecraft:netherite_sword"}
        abilities = ["call_minions", "enrage"]
