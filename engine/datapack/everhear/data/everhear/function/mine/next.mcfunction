function everhear:mine/one with storage everhear:io mq[0]
data remove storage everhear:io mq[0]
execute if data storage everhear:io mq[0] run function everhear:mine/next
