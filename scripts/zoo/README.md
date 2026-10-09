Keeps spawning random animals around each player, never more than the maximum, until stopped.
Usage: zoo [player=@a] [horde_cap=200] [horde_per_player=20] [horde_every=1] [horde_spread=8] [horde_mobs=pig,cow,...]
Ends: when stopped

Built from the horde_wave preset; every value but player and horde_waves is live.

- player: round whom; nothing is spawned while nobody it names is online.
- horde_cap: most animals alive at once, up to 400; horde_per_player more every horde_every s until it is reached, about horde_spread blocks away.
- horde_mobs: which animals (pig, cow, sheep, fox, panda, camel and more).

Animals are tagged vfg_<instance> and vfg_<instance>_horde.
On stop: kills every animal it spawned.
