# @s died (Minecraft's deathCount went up). @s stays marked dead until everhear:ev/respawn.
scoreboard players set @s everhear.deaths 0
scoreboard players set @s everhear.dead 1
data modify storage everhear:io new set value {k:"death",d:{}}
function everhear:ev/push
