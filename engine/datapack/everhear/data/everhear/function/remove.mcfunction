# Removes the entity $(u) and leaves nothing (vfg_engine.mc's Remove): no loot, no experience, no death
# message. Its name and owner go first (a named or tamed mob's death is announced), then it is moved
# 1024 blocks under the world, where whatever it drops is gone on its first tick, and killed there.
# Returns 1 when it was removed; 0 when no such entity exists, it is already dead (a dying mob
# stays selectable by its UUID for a second), or it is a player.
$return run execute as $(u) at @s unless entity @s[type=minecraft:player] run function everhear:remove_one
