Drops primed TNT from the sky around every online player, or the named ones, every few seconds until stopped.
Usage: tnt_rain [player=@a] [every=20] [sky_count=10] [sky_radius=7] [sky_height=25] [fuse=4] [sky_open=true]
Ends: when stopped

Built from the sky_above preset; every value but player is live.

- player: on whom. every: seconds between volleys (at least 1); the first falls at once.
- sky_count: TNT per player per volley, 1 to 40, within sky_radius blocks and sky_height up (sky_band: give or take); sky_open keeps only points in open air.
- fuse: seconds.

TNT is tagged vfg_<instance>. It blows holes in the ground.
On stop: kills TNT still falling.
