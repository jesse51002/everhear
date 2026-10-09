Marks each player's death with a skull and a glowing name tag for a while and says where in chat, until stopped.
Usage: grave_marks [player=@a] [seconds=30] [found_within=3] [on_death_event=death]
Ends: when stopped

Built from the on_death preset; every value but player is live.

- player: whose deaths are marked. seconds: how long a mark stays (at least 5); walking back to it (within found_within blocks, once they have left it) clears it early.
- on_death_event: death, kill (a mark where they made a kill) or both.

Places a skeleton skull where the player died and a floating glowing tag "<name> died here (#n)" above it (tagged vfg_<instance>), and says "[Grave] <name> died at x y z" in chat. Counts deaths on the objectives vfg_<instance>_deaths and _kills.
On stop: removes every mark still up and the objectives.
