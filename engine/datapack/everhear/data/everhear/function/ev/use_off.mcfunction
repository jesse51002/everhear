data modify storage everhear:io new.d.slot set value "offhand"
data modify storage everhear:io new.d.item set from entity @s equipment.offhand.components."minecraft:custom_data".everhear.item
execute if data storage everhear:io new.d.item run function everhear:ev/push
