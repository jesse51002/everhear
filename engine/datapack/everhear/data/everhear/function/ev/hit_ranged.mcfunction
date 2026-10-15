# everhear:hit_ranged's reward: something @s shot or threw hit an entity, this moment. A hit event with
# ranged:1b (its item the EverHear id of the weapon the projectile came from, else the main hand's),
# then the projectile's land event (hit "entity", with the target), so an arrow that vanishes in a mob
# lands too. The projectile is the nearest of @s's flying by the target, or, when a killing shot left
# no target to look by, @s's only one in flight.
function everhear:ev/hit_ranged_type
advancement revoke @s only everhear:hit_ranged
data modify storage everhear:io new set value {k:"hit",d:{ranged:1b}}
execute store result storage everhear:io new.d.target_type int 1 run scoreboard players get #y everhear.t
data remove storage everhear:io land
scoreboard players set #lk everhear.t 0
tag @s add everhear.me
function everhear:ev/find_target
execute unless data storage everhear:io land run function everhear:land/only_one
tag @s remove everhear.me
data modify storage everhear:io new.d.item set from entity @s SelectedItem.components."minecraft:custom_data".everhear.item
execute if data storage everhear:io land.d.item run data modify storage everhear:io new.d.item set from storage everhear:io land.d.item
function everhear:ev/push
execute if data storage everhear:io land run function everhear:land/after_hit
