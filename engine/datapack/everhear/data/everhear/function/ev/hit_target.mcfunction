data modify storage everhear:io new.d.target set from entity @s UUID
tag @s add everhear.hit
scoreboard players set #hits everhear.t 1
execute if data storage everhear:io new.d{ranged:1b} at @s run function everhear:land/by_target
