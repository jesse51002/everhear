# A new world id: two random numbers and the game time it was made at.
execute store result storage everhear:meta world.a int 1 run random value 0..2147483646
execute store result storage everhear:meta world.b int 1 run random value 0..2147483646
execute store result storage everhear:meta world.t int 1 run time query gametime
