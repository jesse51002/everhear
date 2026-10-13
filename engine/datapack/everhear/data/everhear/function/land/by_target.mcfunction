# As and at the target of @s's ranged hit: the projectile is the nearest of @s's still flying by it
# (an arrow that hits is gone right after this tick's hit).
execute as @e[type=#everhear:projectiles,distance=..6,tag=!everhear.done,sort=nearest] if function everhear:land/is_mine run function everhear:land/keep
