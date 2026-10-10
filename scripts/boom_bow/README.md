Makes a player's arrows explode where they land or hit, from one blast to a ring of them with a mushroom cloud, until stopped.
Usage: boom_bow [player=@a] [explosion_power=8] [explosion_layout=one] [explosion_spread=8] [explosion_breaks=true] [cloud=false] [projectile_lands_kind=arrow] ...
Ends: when stopped

Built from the projectile_lands and explosion presets; every value but player is live.

- player: whose arrows explode.
- explosion_power: 1 to 16 (TNT is 4; the old radius 4 was power 8). explosion_layout: one, plus, ring (eight more at explosion_spread: the old "nuke"), rings. explosion_breaks=false hurts without breaking blocks. cloud: a mushroom cloud over each blast.
- projectile_lands_kind: arrow, trident or both; projectile_lands_named: only arrows from the bows of those names; projectile_lands_cooldown; projectile_lands_remove (true: a landed arrow is taken away). Arrows already lying in the ground when it starts are left alone.
- explosion_keep, explosion_spare: no blast within that many blocks of a player; players never hurt. The shooter is never hurt.

Tags the arrows it has seen vfg_<instance>_seen. Explosions break blocks.
On stop: removes that tag.
