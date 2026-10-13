# Appends everhear:io new ({k, d}) to the queue as the next event about @s, with its number (n), the
# game time (t) and @s's UUID (u). A full queue turns it away and counts it in #over, which the
# engine reads with every page and logs: nothing is dropped in silence.
execute if score #len everhear.q >= #max everhear.q run return run scoreboard players add #over everhear.q 1
scoreboard players add #n everhear.q 1
execute store result storage everhear:io new.n int 1 run scoreboard players get #n everhear.q
execute store result storage everhear:io new.t int 1 run time query gametime
data modify storage everhear:io new.u set from entity @s UUID
data modify storage everhear:events q append from storage everhear:io new
scoreboard players add #len everhear.q 1
