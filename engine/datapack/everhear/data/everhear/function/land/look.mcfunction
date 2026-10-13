# Its data read once (each read of an entity saves all of it): down in a block (inGround), or a
# trident that struck something (DealtDamage) and was not reported by the ranged hit.
data modify storage everhear:io pj set from entity @s
execute if data storage everhear:io pj{inGround:1b} run return run function everhear:land/block
execute if data storage everhear:io pj{DealtDamage:1b} run return run function everhear:land/struck
