Turns zombies into slow walkers that hear sprinting from far away and barely notice sneaking, until stopped.
Usage: walking_dead
Ends: when stopped

No arguments. Every zombie becomes a slow walker that can break doors (tag vfg_walking_dead_w) and sometimes brings a friend (tagged vfg_walking_dead). Near a player their follow range is 5 blocks when the player sneaks, 40 when they sprint, 16 otherwise; they speed up within 12 and 6 blocks. Uses the scoreboard objectives vfg_wd_sneak and vfg_wd_sprint.
On stop: resets the zombies' speed and follow range, removes the tag and the objectives, and kills the extra zombies it summoned.
