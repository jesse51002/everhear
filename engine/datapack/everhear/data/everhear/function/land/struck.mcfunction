data modify storage everhear:io land set value {k:"land",d:{hit:"entity"}}
function everhear:land/fill
data modify storage everhear:io new set from storage everhear:io land
execute on origin run function everhear:ev/push
