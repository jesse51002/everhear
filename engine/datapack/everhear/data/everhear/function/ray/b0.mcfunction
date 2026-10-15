# The ray is inside a block here, and was not half a block back: halve the gap four times.
execute positioned ^ ^ ^-0.25 unless block ~ ~ ~ #everhear:ray_through run return run function everhear:ray/b0_back
function everhear:ray/b1
