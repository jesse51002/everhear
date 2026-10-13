# everhear:hit's reward: @s hit an entity in melee, this moment. Its type comes from the criteria
# granted. Which entity: one near @s, whose attacker is @s, hurt this tick (HurtTime 10) and not yet
# reported by a hit this tick (a sweep hurts several, one reward each). A mob the hit killed is dead,
# @e never selects it, and the event has no target (Minecraft runs the death first, so the kill
# event comes just before); a player the hit killed is still found (@a selects the dead).
function everhear:ev/hit_type
advancement revoke @s only everhear:hit
data modify storage everhear:io new set value {k:"hit",d:{}}
execute store result storage everhear:io new.d.target_type int 1 run scoreboard players get #y everhear.t
data modify storage everhear:io new.d.item set from entity @s SelectedItem.components."minecraft:custom_data".everhear.item
tag @s add everhear.me
execute as @e[distance=..16,tag=!everhear.me,tag=!everhear.hit] if function everhear:ev/by_me if entity @s[nbt={HurtTime:10s}] run function everhear:ev/hit_target
execute unless data storage everhear:io new.d.target as @a[distance=..16,tag=!everhear.me,tag=!everhear.hit] if entity @s[nbt={Health:0.0f}] if function everhear:ev/by_me run function everhear:ev/hit_target
tag @s remove everhear.me
function everhear:ev/push
