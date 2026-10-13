data modify storage everhear:io new.d.attacker set from entity @s UUID
function everhear:type/entity
execute store result storage everhear:io new.d.attacker_type int 1 run scoreboard players get #y everhear.t
