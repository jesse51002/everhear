# Runs when the pack loads (server start, /reload). Makes what the pack keeps, leaving what is there:
# the queue and its counters survive a reload and a restart, so no event is lost or numbered twice.

# Counters and the scratch scores the functions use.
scoreboard objectives add everhear.q dummy
scoreboard objectives add everhear.t dummy
# A use of each "on a stick" base, as Minecraft counts it.
scoreboard objectives add everhear.use_c minecraft.used:minecraft.carrot_on_a_stick
scoreboard objectives add everhear.use_w minecraft.used:minecraft.warped_fungus_on_a_stick
# Deaths, and who is dead now (for respawn).
scoreboard objectives add everhear.deaths deathCount
scoreboard objectives add everhear.dead dummy
# Damage taken and absorbed, in tenths of a point, read and reset by each hurt.
scoreboard objectives add everhear.dmg minecraft.custom:minecraft.damage_taken
scoreboard objectives add everhear.abs minecraft.custom:minecraft.damage_absorbed

# The queue: #n the last number given, #len how many wait, #over how many a full queue turned away
# (ever), #max how many it holds.
execute unless score #n everhear.q matches -2147483648.. run scoreboard players set #n everhear.q 0
execute unless score #over everhear.q matches -2147483648.. run scoreboard players set #over everhear.q 0
scoreboard players set #max everhear.q 2048
execute unless data storage everhear:events q run data modify storage everhear:events q set value []
execute store result score #len everhear.q if data storage everhear:events q[]

# How far each player's raycast reaches, in 1/32 of a block (48 blocks); the engine may set it.
execute unless score #rmax everhear.t matches 1.. run scoreboard players set #rmax everhear.t 1536

function everhear:meta
