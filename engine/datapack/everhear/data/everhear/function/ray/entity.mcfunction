# As the entity the ray came to, at the ray's point. The first one counts.
execute if score #rhit everhear.t matches 1 run return 0
scoreboard players set #rhit everhear.t 1
data modify storage everhear:io cur.l.k set value "entity"
data modify storage everhear:io cur.l.e set from entity @s UUID
function everhear:type/entity
execute store result storage everhear:io cur.l.y int 1 run scoreboard players get #y everhear.t
execute summon minecraft:marker run function everhear:ray/point
