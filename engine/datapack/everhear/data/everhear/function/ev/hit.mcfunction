# everhear:hit's reward: @s hit an entity in melee, this moment. Its type comes from the criteria
# granted; the entity is found by everhear:ev/find_target. A mob the hit killed is dead, no selector
# finds it, and the event has no target (Minecraft runs the death first, so the kill event comes just
# before); a player the hit killed is still found.
function everhear:ev/hit_type
advancement revoke @s only everhear:hit
data modify storage everhear:io new set value {k:"hit",d:{ranged:0b}}
execute store result storage everhear:io new.d.target_type int 1 run scoreboard players get #y everhear.t
data modify storage everhear:io new.d.item set from entity @s SelectedItem.components."minecraft:custom_data".everhear.item
tag @s add everhear.me
function everhear:ev/find_target
tag @s remove everhear.me
function everhear:ev/push
