Turns zombies into slow walkers that hear sprinting from far away and barely notice sneaking, until stopped.
Usage: walking_dead [player=@a] [walk_speed=0.17] [sneak_range=5] [walk_range=16] [sprint_range=40] [friends=0.3]
Ends: when stopped

- player: whose noise counts. Every value but player is live.
- walk_speed: a walker's speed (they speed up within 12 and 6 blocks). sneak_range, walk_range, sprint_range: their follow range near a player who sneaks, walks or sprints. friends: the chance each second that a walker brings a friend.

Every zombie becomes a walker that can break doors (tag vfg_<instance>_w); friends are tagged vfg_<instance>. Uses the scoreboard objectives vfg_wd_sneak and vfg_wd_sprint.
On stop: resets the zombies' speed and follow range, removes the tag and the objectives, and kills the friends it summoned.
