# Every game tick: the events Minecraft reports only through scoreboards. Hits, hurts, kills and milk
# come from advancements, the moment they happen (everhear:ev/*).
execute as @a[scores={everhear.use_c=1..}] run function everhear:ev/use {base:"minecraft:carrot_on_a_stick",score:"everhear.use_c"}
execute as @a[scores={everhear.use_w=1..}] run function everhear:ev/use {base:"minecraft:warped_fungus_on_a_stick",score:"everhear.use_w"}
execute as @a[scores={everhear.deaths=1..}] run function everhear:ev/death
# @e, unlike @a, never selects a dead player: these are back.
execute as @e[type=minecraft:player,scores={everhear.dead=1..}] run function everhear:ev/respawn
# The entities a hit reported last tick may be hit again.
execute if score #hits everhear.t matches 1 run function everhear:ev/hits_clear
