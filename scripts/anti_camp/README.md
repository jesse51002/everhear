Rains TNT on any player who stays within four blocks of one spot for too long, until stopped.
Usage: anti_camp [player=@a] [stands_seconds=15] [stands_radius=4] [tnt=7] [height=12] [fuse=2] [mock=true] [stands_warning=...]
Ends: when stopped

Built from the stands_still preset; every value but player is live (`vfg script set anti_camp stands_seconds=30`).

- player: who is watched: @a, @r, or names separated by commas.
- stands_seconds: how long a player may stay put (at least 6); stands_radius: how far they must move. The warning (stands_warning) shows on their actionbar for the last 5 s.
- tnt, height, fuse: primed TNT (fuse in seconds) that falls from that high round the camper.
- mock: the Demon Lord mocks them in chat.

TNT is tagged vfg_<instance>. Explosions break blocks.
On stop: kills TNT still falling.
