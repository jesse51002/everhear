data modify storage everhear:io land set value {k:"land",d:{hit:"block"}}
data modify storage everhear:io land.d.block set from storage everhear:io pj.inBlockState
function everhear:land/fill
data modify storage everhear:io new set from storage everhear:io land
execute on origin run function everhear:ev/push
