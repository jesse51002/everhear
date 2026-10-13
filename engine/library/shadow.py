"""Shadow: the dark, silent figure horror (and the old herobrine) stands behind a player for a few
seconds at a time, a Herobrine-skinned head over dyed leather armour, watching. Which way it faces
changes every time it is sent, so horror writes the Rotation it is spawned with (merged onto this)."""

from vfg_engine import Actor, ActorConfig


class Shadow(Actor):
    class Config(ActorConfig):
        summary = "A dark, silent figure in a Herobrine head and dyed leather, that stands watching, then is gone."
        entity_type = "minecraft:armor_stand"
        health = 20.0
        size = 1.0
        damage = 0.0
        # NEEDS: equipment with components (a player_head profile named Herobrine, dyed leather colours
        # on the chestplate and leggings); ActorConfig.equipment is only a slot -> plain vanilla item id
        # (config.py), so its look is written here as raw nbt instead.
        nbt = ('{Invulnerable:1b,NoGravity:1b,ShowArms:1b,NoBasePlate:1b,Silent:1b,'
               'equipment:{head:{id:"minecraft:player_head",count:1,components:{"minecraft:profile":'
               '{name:"Herobrine"}}},chest:{id:"minecraft:leather_chestplate",count:1,components:'
               '{"minecraft:dyed_color":3381708}},legs:{id:"minecraft:leather_leggings",count:1,'
               'components:{"minecraft:dyed_color":3355545}}},'
               'drop_chances:{head:0.0f,chest:0.0f,legs:0.0f}}')
