Keeps spawning random animals around the player, never more than the maximum, until stopped.
Usage: zoo <player> [max=200]
Ends: when stopped

- player: who the zoo follows; nothing is spawned while they are offline.
- max: most animals alive at once, up to 400. Up to 20 more each second until it is reached.

Animals (pig, cow, sheep, fox, panda, camel and more) are tagged vfg_zoo.
On stop: kills every animal it spawned.
