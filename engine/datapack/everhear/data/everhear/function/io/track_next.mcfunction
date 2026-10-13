function everhear:io/track_one with storage everhear:io tq[0]
data remove storage everhear:io tq[0]
execute if data storage everhear:io tq[0] run function everhear:io/track_next
