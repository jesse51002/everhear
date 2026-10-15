# One point of the ray, #rs/32 blocks from the eyes.
scoreboard players add #rs everhear.t 16
execute unless block ~ ~ ~ #everhear:ray_through run return run function everhear:ray/b0
execute positioned ~-0.75 ~-0.75 ~-0.75 as @e[dx=0,dy=0,dz=0,tag=!everhear.ray_self,type=!#everhear:ray_ignore] positioned ~0.5 ~0.5 ~0.5 if entity @s[dx=0,dy=0,dz=0] positioned ~0.25 ~0.25 ~0.25 run function everhear:ray/entity
execute if score #rhit everhear.t matches 1 run return 0
execute if score #rs everhear.t < #rmax everhear.t positioned ^ ^ ^0.5 run function everhear:ray/step
