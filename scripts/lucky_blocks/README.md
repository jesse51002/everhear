Gives each player sponges named Lucky Block that bring a random reward or trap when broken, until stopped.
Usage: lucky_blocks [player=@a] [give=3]
Ends: when stopped

- player: who gets them and whose breaks count; give: Lucky Blocks each gets at the start, 0 to 64. More: vfg cmd 'give <player> sponge[item_name="Lucky Block"] 3'.

Breaking any sponge rolls one of 18 outcomes: diamonds, golden apples, a totem, netherite, XP, a cake, a pet wolf, a saddled horse, speed and haste, a lucky sword, zombies, lightning, TNT, levitation, a cobweb trap, a rain of chickens, more lucky blocks. Counts breaks on the scoreboard objective vfg_lucky.
Leaves in the world: the gifts (pets and horses stay the player's). Trap zombies and TNT are tagged vfg_<instance>.
On stop: removes the objective and any trap zombies and TNT.
