# everhear:hurt's reward: @s took damage, this moment. amount is the health it lost and absorbed what
# absorption hearts took, both as Minecraft's damage_taken and damage_absorbed statistics counted them
# (after armour), in points. The attacker is named only when this damage set it: Minecraft keeps the
# last attacker for 5 s, and ticks_since_last_hurt_by_mob is 0 only in the tick it was set, so a fall
# just after a zombie's hit does not name the zombie.
advancement revoke @s only everhear:hurt
data modify storage everhear:io new set value {k:"hurt",d:{}}
execute store result storage everhear:io new.d.amount float 0.1 run scoreboard players get @s everhear.dmg
execute store result storage everhear:io new.d.absorbed float 0.1 run scoreboard players get @s everhear.abs
scoreboard players set @s everhear.dmg 0
scoreboard players set @s everhear.abs 0
scoreboard players set #ago everhear.t -1
execute store result score #ago everhear.t run data get entity @s ticks_since_last_hurt_by_mob
execute if score #ago everhear.t matches 0 on attacker run function everhear:ev/hurt_attacker
function everhear:ev/push
