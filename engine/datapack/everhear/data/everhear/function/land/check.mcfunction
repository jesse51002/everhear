# As an arrow or trident not done with: one not looked at yet is followed when a player threw it, and
# done with for good otherwise; one followed is looked at each tick until it comes down.
execute unless entity @s[tag=everhear.ours] run function everhear:land/first
execute if entity @s[tag=everhear.ours] run function everhear:land/look
