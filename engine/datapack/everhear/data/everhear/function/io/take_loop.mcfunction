execute unless data storage everhear:events q[0] run return 0
execute store result score #h everhear.q run data get storage everhear:events q[0].n
execute if score #h everhear.q > #upto everhear.q run return 0
data remove storage everhear:events q[0]
function everhear:io/take_loop
