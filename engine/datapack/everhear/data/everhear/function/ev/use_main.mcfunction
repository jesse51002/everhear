data modify storage everhear:io new.d.item set from entity @s SelectedItem.components."minecraft:custom_data".everhear.item
execute if data storage everhear:io new.d.item run function everhear:ev/push
