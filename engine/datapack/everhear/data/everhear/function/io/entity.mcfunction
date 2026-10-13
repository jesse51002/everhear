# One entity's compound, as the entity, into everhear:io ent: u UUID, y its type's index (everhear:meta
# types), d dimension, p position, h health (living things only: an item's Health is its own), g tags,
# c custom name.
data modify storage everhear:io tmp set from entity @s
data modify storage everhear:io ent set value {}
data modify storage everhear:io ent.u set from storage everhear:io tmp.UUID
data modify storage everhear:io ent.p set from storage everhear:io tmp.Pos
execute if data storage everhear:io tmp.attributes run data modify storage everhear:io ent.h set from storage everhear:io tmp.Health
data modify storage everhear:io ent.g set from storage everhear:io tmp.Tags
data modify storage everhear:io ent.c set from storage everhear:io tmp.CustomName
function everhear:type/entity
execute store result storage everhear:io ent.y int 1 run scoreboard players get #y everhear.t
execute at @s run function everhear:io/dim
