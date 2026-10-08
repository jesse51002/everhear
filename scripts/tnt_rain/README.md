Drops primed TNT from the sky around every online player, or the named ones, every few seconds until stopped.
Usage: tnt_rain [every_s=20] [count=10] [player...]
Ends: when stopped

- every_s: seconds between volleys (at least 1). The first volley falls at once.
- count: TNT per player per volley, 1 to 40.
- player...: only these players; none means everyone online. Names come after the numbers.

TNT falls from 25 blocks up within 7 blocks of each target, fuse 4 s, tagged vfg_tnt_rain. It blows holes in the ground.
On stop: kills TNT still falling.
