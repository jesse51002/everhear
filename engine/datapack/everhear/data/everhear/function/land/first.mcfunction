scoreboard players set #pl everhear.t 0
execute store success score #pl everhear.t on origin if entity @s[type=minecraft:player]
execute if score #pl everhear.t matches 1 run return run tag @s add everhear.ours
tag @s add everhear.done
