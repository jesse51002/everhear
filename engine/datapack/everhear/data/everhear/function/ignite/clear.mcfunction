# Two ticks on: the lava goes (unless something else is there now), and the modifier comes off.
execute as @e[type=minecraft:marker,tag=everhear.lava] at @s run function everhear:ignite/clear_one
execute as @a[tag=everhear.ign] run function everhear:ignite/unmod
