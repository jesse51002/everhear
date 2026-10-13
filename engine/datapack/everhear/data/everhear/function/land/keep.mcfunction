# As the projectile a ranged hit came from: its land event, kept for after the hit's (the first only).
execute if score #lk everhear.t matches 1 run return 0
scoreboard players set #lk everhear.t 1
data modify storage everhear:io pj set from entity @s
data modify storage everhear:io land set value {k:"land",d:{hit:"entity"}}
execute at @s run function everhear:land/fill
