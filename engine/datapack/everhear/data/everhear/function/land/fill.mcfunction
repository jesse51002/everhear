# As the projectile, its data in everhear:io pj: into everhear:io land.d its UUID, type, place and
# dimension, and the EverHear id of the weapon it came from (an arrow keeps its bow or crossbow as
# `weapon`; a trident is its own `item`). Done with from now.
tag @s add everhear.done
data modify storage everhear:io land.d.projectile set from storage everhear:io pj.UUID
data modify storage everhear:io land.d.pos set from storage everhear:io pj.Pos
function everhear:type/entity
execute store result storage everhear:io land.d.projectile_type int 1 run scoreboard players get #y everhear.t
data modify storage everhear:io land.d.item set from storage everhear:io pj.weapon.components."minecraft:custom_data".everhear.item
execute if entity @s[type=minecraft:trident] run data modify storage everhear:io land.d.item set from storage everhear:io pj.item.components."minecraft:custom_data".everhear.item
execute if dimension minecraft:overworld run data modify storage everhear:io land.d.dim set value "minecraft:overworld"
execute if dimension minecraft:the_nether run data modify storage everhear:io land.d.dim set value "minecraft:the_nether"
execute if dimension minecraft:the_end run data modify storage everhear:io land.d.dim set value "minecraft:the_end"
