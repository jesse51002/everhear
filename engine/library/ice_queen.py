"""Ice Queen: a giant stray boss in icy regalia. The ice_queen action's boss: boss_fight spawns her
(boss="ice_queen"), with a blue bar; her ice_blast and frost_servants abilities each fire every 8 s
on their own, and she enrages as she weakens, all granted when she is spawned.

Her gear (a diamond crown and boots, dyed pale-blue leather robes, an iron sword) is set through
Config.nbt, not ActorConfig.equipment: a dyed leather piece needs a components tag equipment's
plain slot -> item id can't carry, the way the old _boss_gear's "ice" branch built it."""

from vfg_engine import Actor, ActorConfig

_ROBE = '{id:"minecraft:leather_%s",count:1,components:{"minecraft:dyed_color":8900331}}'
_GEAR = ('{equipment:{head:{id:"minecraft:diamond_helmet",count:1},chest:%s,legs:%s,'
         'feet:{id:"minecraft:diamond_boots",count:1},mainhand:{id:"minecraft:iron_sword",count:1}},'
         'drop_chances:{head:0.0f,chest:0.0f,legs:0.0f,feet:0.0f,mainhand:0.0f}}'
         % (_ROBE % "chestplate", _ROBE % "leggings"))


class IceQueen(Actor):
    class Config(ActorConfig):
        summary = "A giant stray queen in icy gear: freezes nearby players and calls frost servants as she weakens."
        entity_type = "minecraft:stray"
        health = 1000
        size = 2
        damage = 8                 # her melee hit, in half-hearts (the old boss_attack)
        name = "Ice Queen"
        name_visible = True
        nbt = _GEAR
        abilities = ["ice_blast", "frost_servants", "enrage"]
