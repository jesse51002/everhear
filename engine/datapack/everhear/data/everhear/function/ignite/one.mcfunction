$execute unless entity @s[type=minecraft:player] run return run data merge entity @s {Fire:$(k)s}
execute unless block ~ ~ ~ minecraft:air unless block ~ ~1 ~ minecraft:air run return 0
attribute @s minecraft:burning_time modifier remove everhear:ignite
$attribute @s minecraft:burning_time modifier add everhear:ignite $(f) add_multiplied_base
effect give @s minecraft:fire_resistance 1 0 true
tag @s add everhear.ign
execute if block ~ ~ ~ minecraft:air run return run function everhear:ignite/lava
execute positioned ~ ~1 ~ run return run function everhear:ignite/lava
