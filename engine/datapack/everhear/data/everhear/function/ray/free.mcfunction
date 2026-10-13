# One point of a free ray, #rs/32 blocks from its origin.
scoreboard players add #rs everhear.t 16
execute unless block ~ ~ ~ #everhear:ray_through run return run function everhear:ray/b0
execute unless loaded ~ ~ ~ run return run data modify storage everhear:io cur.l.u set value 1b
execute if score #rs everhear.t < #rmx everhear.t positioned ^ ^ ^0.5 run function everhear:ray/free
