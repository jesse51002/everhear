setblock ~ ~ ~ minecraft:lava strict
summon minecraft:marker ~ ~ ~ {Tags:["everhear.lava"]}
schedule function everhear:ignite/clear 2t append
return 1
