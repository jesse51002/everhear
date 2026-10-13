# The ray is inside a block here, and was not 1/4 block back: halve the gap three times.
execute positioned ^ ^ ^-0.125 unless block ~ ~ ~ #everhear:ray_through run return run function everhear:ray/b1_back
function everhear:ray/b2
