Marks each player's death with a skull and a glowing name tag for a while and says where in chat, until stopped.
Usage: grave_marks [seconds=30]
Ends: when stopped

- seconds: how long a mark stays (at least 5). Walking back to it clears it early.

Places a skeleton skull where the player died and a floating glowing tag "<name> died here (#n)" above it (tagged vfg_grave_marks), and says "[Grave] <name> died at x y z" in chat. Counts deaths on the scoreboard objective vfg_gm_deaths.
On stop: removes every mark still up and the objective.
