# As the player who mined: the event, with the block and how many since the last tick. Minecraft's
# statistic says nothing of where.
data modify storage everhear:io new set value {k:"mine",d:{}}
$data modify storage everhear:io new.d.block set value "$(b)"
$execute store result storage everhear:io new.d.count int 1 run scoreboard players get @s $(o)
$scoreboard players set @s $(o) 0
function everhear:ev/push
