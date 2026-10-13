# The engine's snapshot (vfg_engine.mc.snapshot), into everhear:out snap: the game time, a compound per
# online player (everhear:io/player) and one per tracked entity that exists (everhear:io track, a list
# of {u:"<uuid>"} the engine sets).
data modify storage everhear:out snap set value {p:[],e:[]}
execute store result storage everhear:out snap.time int 1 run time query gametime
execute as @a at @s run function everhear:io/player
data modify storage everhear:io tq set from storage everhear:io track
execute if data storage everhear:io tq[0] run function everhear:io/track_next
