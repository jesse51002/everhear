execute if entity @s[nbt={Health:0.0f}] if data entity @s attributes run return 0
data remove entity @s CustomName
data remove entity @s Owner
tp @s ~ -1024 ~
kill @s
return 1
