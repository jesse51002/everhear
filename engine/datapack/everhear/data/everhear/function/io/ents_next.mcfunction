data modify storage everhear:io et set from storage everhear:io en
data modify storage everhear:io et.t set from storage everhear:io eq[0].t
function everhear:io/ents_type with storage everhear:io et
data remove storage everhear:io eq[0]
execute if data storage everhear:io eq[0] run function everhear:io/ents_next
