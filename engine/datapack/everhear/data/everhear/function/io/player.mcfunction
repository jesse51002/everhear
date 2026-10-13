# One player's compound for the snapshot, as and at the player. The player's data is read once into
# everhear:io tmp (each read of an entity's data saves all of it) and the fields taken from there:
# u UUID, d dimension, p feet, e eyes, r rotation [yaw, pitch], h health, mh max health, g game mode
# (0 survival, 1 creative, 2 adventure, 3 spectator), s sneaking, fx active_effects as Minecraft keeps
# them, hd the EverHear id in each held or worn slot, inv the EverHear ids anywhere in the inventory
# (the 36 slots; the worn and off-hand ones are in hd), l the raycast (everhear:ray/start).
data modify storage everhear:io tmp set from entity @s
data modify storage everhear:io cur set value {inv:[]}
data modify storage everhear:io cur.u set from storage everhear:io tmp.UUID
data modify storage everhear:io cur.d set from storage everhear:io tmp.Dimension
data modify storage everhear:io cur.p set from storage everhear:io tmp.Pos
data modify storage everhear:io cur.r set from storage everhear:io tmp.Rotation
data modify storage everhear:io cur.h set from storage everhear:io tmp.Health
data modify storage everhear:io cur.g set from storage everhear:io tmp.playerGameType
data modify storage everhear:io cur.fx set from storage everhear:io tmp.active_effects
data modify storage everhear:io cur.hd.mainhand set from storage everhear:io tmp.SelectedItem.components."minecraft:custom_data".everhear.item
data modify storage everhear:io cur.hd.offhand set from storage everhear:io tmp.equipment.offhand.components."minecraft:custom_data".everhear.item
data modify storage everhear:io cur.hd.head set from storage everhear:io tmp.equipment.head.components."minecraft:custom_data".everhear.item
data modify storage everhear:io cur.hd.chest set from storage everhear:io tmp.equipment.chest.components."minecraft:custom_data".everhear.item
data modify storage everhear:io cur.hd.legs set from storage everhear:io tmp.equipment.legs.components."minecraft:custom_data".everhear.item
data modify storage everhear:io cur.hd.feet set from storage everhear:io tmp.equipment.feet.components."minecraft:custom_data".everhear.item
data modify storage everhear:io cur.inv append from storage everhear:io tmp.Inventory[].components."minecraft:custom_data".everhear.item
execute store result storage everhear:io cur.mh float 0.001 run attribute @s minecraft:max_health get 1000
execute store success storage everhear:io cur.s byte 1 if predicate everhear:sneaking
execute anchored eyes positioned ^ ^ ^ summon minecraft:marker run function everhear:io/eye
function everhear:ray/start
data modify storage everhear:out snap.p append from storage everhear:io cur
