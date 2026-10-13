# @s died (Minecraft's deathCount went up): where (LastDeathLocation: its dimension and block). @s
# stays marked dead until everhear:ev/respawn.
scoreboard players set @s everhear.deaths 0
scoreboard players set @s everhear.dead 1
data modify storage everhear:io new set value {k:"death",d:{}}
data modify storage everhear:io new.d.pos set from entity @s LastDeathLocation.pos
data modify storage everhear:io new.d.dim set from entity @s LastDeathLocation.dimension
function everhear:ev/push
