Runs the Demon Lord game master, with escalating mob waves, minigames and a boss every fourth round, until stopped.
Usage: demon_lord [round=1] [level=round]
Ends: when stopped

- round: the round to start at; it picks the event (the rotation repeats every 16 rounds) and every 4th round is a boss.
- level: the starting difficulty, which adapts after each round: many deaths lower it (and drop supply chests), none raise it.

Each round: a supply chest beside every player, then one event (spider webs, zombie horde, TNT sky, lava and blazes, skeletons, creepers, phantoms, the golden sanctuary, defend Old Gregor's hut, parkour of the damned, the closing ring, blood moon, lightning storm) or a boss with a bossbar (The Gravekeeper, Spider Queen, Ravager Warlord, The Hollow King). It waits until its mobs are dead, or 30 s without a kill, then a one-minute break. It forces everyone to survival mode, pulls stragglers to the group, and resets spawnpoints to where players stand. Speaks as "[Demon Lord]" in dark red, with titles and sounds. Logs the round and level each round.

Leaves in the world: supply chests, the sanctuary platform and Gregor's hut. Mobs are tagged vfg_demon_lord; markers also vfg_demon_lord_prop. Deaths are counted on the scoreboard objective vfg_dl_deaths.
On stop: kills its mobs and markers, removes the bossbar vfg:demon_lord and the objective, resets the world border, and removes the parkour course and restores fall_damage and pvp if a parkour was running.
