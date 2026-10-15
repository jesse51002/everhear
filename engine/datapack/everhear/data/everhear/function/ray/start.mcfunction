# A player's raycast for the snapshot, as and at the player: from the eyes along the look, half a
# block a step, to the first block that is not see-through (#everhear:ray_through, taken as a full
# cube) or the first entity the ray comes within 0.25 of (but not #everhear:ray_ignore), at most
# #rmax/32 blocks. Into everhear:io cur.l: k "block", "entity" or "none"; d the distance in 1/32 of
# a block; and for a hit p the point (just inside the block, found to 1/32 by halving the last
# step four times; for an entity, the ray's point), b the block's index (everhear:meta blocks), or
# e and y the entity's UUID and type's index. A step costs about 1 µs of the server's time.
scoreboard players set #rs everhear.t 0
scoreboard players set #rhit everhear.t 0
data modify storage everhear:io cur.l set value {k:"none"}
tag @s add everhear.ray_self
execute anchored eyes positioned ^ ^ ^0.5 run function everhear:ray/step
tag @s remove everhear.ray_self
execute store result storage everhear:io cur.l.d int 1 run scoreboard players get #rs everhear.t
