# @s was dead and is alive again.
scoreboard players set @s everhear.dead 0
data modify storage everhear:io new set value {k:"respawn",d:{}}
function everhear:ev/push
