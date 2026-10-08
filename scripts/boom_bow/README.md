Makes the player's arrows explode where they hit, from one TNT at radius 0 to a nuke at 10 or more, until stopped.
Usage: boom_bow <player> [radius=4]
Ends: when stopped

- player: whose arrows explode.
- radius: 0 drops one primed TNT where the arrow lands; 1 to 9 is a powered-creeper blast of that radius; 10 or more adds a ring of eight secondary blasts and a mushroom cloud. Capped at 30.

Arrows that land are removed as they explode. Tags the player's arrows vfg_boom_bow_a and vfg_boom_bow_seen. Explosions break blocks.
On stop: removes those tags.
