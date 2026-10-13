# At the ray's first point inside the block it hit.
scoreboard players set #rhit everhear.t 1
data modify storage everhear:io cur.l.k set value "block"
function everhear:type/block
execute store result storage everhear:io cur.l.b int 1 run scoreboard players get #b everhear.t
execute summon minecraft:marker run function everhear:ray/point
