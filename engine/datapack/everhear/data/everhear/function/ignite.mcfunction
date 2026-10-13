# McIo's Ignite: sets $(t) on fire for $(k) ticks. A mob's Fire is set. A player's data cannot be
# written, and fire blocks take a second to light one, so a player is lit the one way that is at once:
# lava, for two ticks, where they stand (their feet's block, else their head's, when it is air), with a
# second of Fire Resistance so the lava's own 4 damage is not taken, and the burning_time modifier
# everhear:ignite ($(f), on base) turning lava's 15 s into $(k) ticks. Measured on 26.3 (MINECRAFT.md).
# Returns 1 when lit, 0 when there is no such target or no air round a player to light them from.
$return run execute as $(t) at @s run function everhear:ignite/one {f:$(f),k:$(k)}
