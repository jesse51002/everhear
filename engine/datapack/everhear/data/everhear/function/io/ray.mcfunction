# McIo.ray: a ray from (x, y, z) along (yaw, pitch), half a block a step, to the first block that is
# not see-through (#everhear:ray_through, a full cube) or $(n)/32 blocks; entities do not stop it. Into
# everhear:out ray {k: "block"|"none", d, p?, b?, u?}: as the players' raycast (everhear:ray/start), and
# u:1b when it stopped at an unloaded chunk. Macro {d, x, y, z, yaw, pitch, n}.
$scoreboard players set #rmx everhear.t $(n)
scoreboard players set #rs everhear.t 0
scoreboard players set #rhit everhear.t 0
data modify storage everhear:io cur.l set value {k:"none"}
$execute in $(d) positioned $(x) $(y) $(z) rotated $(yaw) $(pitch) positioned ^ ^ ^0.5 run function everhear:ray/free
execute store result storage everhear:io cur.l.d int 1 run scoreboard players get #rs everhear.t
data modify storage everhear:out ray set from storage everhear:io cur.l
