scoreboard players set #pv everhear.t 0
$execute in $(d) positioned $(x) $(y) $(z) store result score #pv everhear.t run function everhear:probe/$(f) {b:"$(b)"}
data modify storage everhear:out probe append value 0b
execute if score #pv everhear.t matches 1 run data modify storage everhear:out probe[-1] set value 1b
