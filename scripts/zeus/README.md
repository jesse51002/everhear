Makes every trident a player throws call down lightning where it lands or hits, until stopped.
Usage: zeus [player=@a] [lightning_count=1] [lightning_spread=3] [lightning_layout=scatter] [lightning_every=0] [projectile_kind=trident] ...
Ends: when stopped

Built from the projectile_lands and lightning presets; every value but player is live.

- player: whose tridents call lightning; any weather.
- lightning_count bolts, scattered or in a ring within lightning_spread blocks, lightning_every s apart; none within 4 blocks of the thrower or of a player in lightning_spare.
- projectile_kind: trident, arrow or both; projectile_named, projectile_cooldown, projectile_remove as in the preset.

Tags the tridents it has seen vfg_<instance>_seen.
On stop: removes that tag. Leaves nothing else.
