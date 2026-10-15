# As the attacker, tagged everhear.me: the entity it just hurt, hurt this tick (HurtTime 10) with @s
# its attacker, not yet reported this tick. Near first (a melee hit reaches a few blocks), then anywhere
# loaded (a shot, or a long reach), then a player it killed (@a selects the dead, @e does not).
execute as @e[distance=..16,tag=!everhear.me,tag=!everhear.hit] if function everhear:ev/by_me if entity @s[nbt={HurtTime:10s}] run function everhear:ev/hit_target
execute unless data storage everhear:io new.d.target as @e[tag=!everhear.me,tag=!everhear.hit] if function everhear:ev/by_me if entity @s[nbt={HurtTime:10s}] run function everhear:ev/hit_target
execute unless data storage everhear:io new.d.target as @a[tag=!everhear.me,tag=!everhear.hit] if entity @s[nbt={Health:0.0f}] if function everhear:ev/by_me run function everhear:ev/hit_target
