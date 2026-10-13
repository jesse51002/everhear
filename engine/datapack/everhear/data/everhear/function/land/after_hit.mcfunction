# As the thrower, after its hit event: the projectile's land event, with what it hit.
data modify storage everhear:io land.d.target set from storage everhear:io new.d.target
data modify storage everhear:io land.d.target_type set from storage everhear:io new.d.target_type
data modify storage everhear:io new set from storage everhear:io land
function everhear:ev/push
