# Each watched kind (everhear:config mine, a list of {o: its objective, b: the block id} the engine
# sets): a player whose minecraft.mined:<block> count went up mined one.
data modify storage everhear:io mq set from storage everhear:config mine
function everhear:mine/next
