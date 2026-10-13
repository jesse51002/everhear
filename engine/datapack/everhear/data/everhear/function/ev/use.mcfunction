# A right-click with an "on a stick" base ($(base)), as @s: a use event when the stack used carries an
# EverHear id. Minecraft uses the main hand's stack when it is that base, else the off hand's.
# count is how many uses Minecraft counted since the last tick (one, unless something clicks faster).
data modify storage everhear:io new set value {k:"use",d:{slot:"mainhand"}}
$execute store result storage everhear:io new.d.count int 1 run scoreboard players get @s $(score)
$scoreboard players set @s $(score) 0
$execute if items entity @s weapon.mainhand $(base) run return run function everhear:ev/use_main
$execute if items entity @s weapon.offhand $(base) run function everhear:ev/use_off
