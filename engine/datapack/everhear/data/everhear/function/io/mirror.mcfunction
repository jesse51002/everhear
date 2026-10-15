# The mirror's read (vfg_engine.mc.mirror), into everhear:mirror: the game time, and a compound
# (everhear:io/entity) for every entity within $(r) blocks of any player, each once. Players are left
# out: they are the snapshot's, and reading one saves all its data (its recipe book too) every pass.
data modify storage everhear:mirror e set value []
execute store result storage everhear:mirror time int 1 run time query gametime
$execute as @a at @s as @e[distance=..$(r),tag=!everhear.m,type=!minecraft:marker,type=!minecraft:player] run function everhear:io/mirror_one
execute if data storage everhear:mirror e[0] run tag @e[tag=everhear.m] remove everhear.m
