# The engine's read of the queue (vfg_engine.mc.events). Takes off every event numbered up to $(upto),
# which the engine already has, then copies the next page into everhear:out ev, with how many wait
# (len, this page's included), the last number given (last) and how many a full queue turned away
# (over). A page is at most 12 events, so its `data get` stays under 4 KB. Nothing is taken when the
# counter is behind $(upto): the world was replaced (or the pack reset), and those numbers are new.
$scoreboard players set #upto everhear.q $(upto)
execute if score #n everhear.q >= #upto everhear.q run function everhear:io/take
data modify storage everhear:out ev set value {q:[]}
data modify storage everhear:out ev.q append from storage everhear:events q[0]
data modify storage everhear:out ev.q append from storage everhear:events q[1]
data modify storage everhear:out ev.q append from storage everhear:events q[2]
data modify storage everhear:out ev.q append from storage everhear:events q[3]
data modify storage everhear:out ev.q append from storage everhear:events q[4]
data modify storage everhear:out ev.q append from storage everhear:events q[5]
data modify storage everhear:out ev.q append from storage everhear:events q[6]
data modify storage everhear:out ev.q append from storage everhear:events q[7]
data modify storage everhear:out ev.q append from storage everhear:events q[8]
data modify storage everhear:out ev.q append from storage everhear:events q[9]
data modify storage everhear:out ev.q append from storage everhear:events q[10]
data modify storage everhear:out ev.q append from storage everhear:events q[11]
execute store result storage everhear:out ev.len int 1 run scoreboard players get #len everhear.q
execute store result storage everhear:out ev.last int 1 run scoreboard players get #n everhear.q
execute store result storage everhear:out ev.over int 1 run scoreboard players get #over everhear.q
