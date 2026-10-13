# McIo.ground_y: returns the height Minecraft keeps for the column x z (motion_blocking_no_leaves: the
# first block above the highest that blocks movement, leaves aside: where feet stand), or -99999 when
# its chunk is not loaded. Macro {d, x, z}.
$execute in $(d) unless loaded $(x) 0 $(z) run return -99999
$execute in $(d) positioned $(x) 0 $(z) positioned over motion_blocking_no_leaves summon minecraft:marker run function everhear:io/marker_y
return run data get storage everhear:io y
