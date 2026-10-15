# The target was not found (the shot killed it: the dead are selected by nothing). The projectile is
# then @s's only one in flight, if it has just one; with several, which one it was cannot be told.
scoreboard players set #lc everhear.t 0
execute as @e[type=#everhear:projectiles,tag=!everhear.done] if function everhear:land/is_mine run scoreboard players add #lc everhear.t 1
execute if score #lc everhear.t matches 1 as @e[type=#everhear:projectiles,tag=!everhear.done] if function everhear:land/is_mine run function everhear:land/keep
