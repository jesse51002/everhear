# everhear:kill's reward: @s killed an entity, this moment. Its type comes from the criteria granted.
# A killed player stays (dead) where @a still finds it, so a player's kill names its target; a killed
# mob is dead, @e never selects it, and a mob's kill carries only its type.
function everhear:ev/kill_type
advancement revoke @s only everhear:kill
data modify storage everhear:io new set value {k:"kill",d:{}}
execute store result storage everhear:io new.d.target_type int 1 run scoreboard players get #y everhear.t
data modify storage everhear:io new.d.item set from entity @s SelectedItem.components."minecraft:custom_data".everhear.item
tag @s add everhear.me
execute as @a[distance=..64,tag=!everhear.me] if entity @s[nbt={Health:0.0f}] if function everhear:ev/by_me run function everhear:ev/kill_target
tag @s remove everhear.me
function everhear:ev/push
