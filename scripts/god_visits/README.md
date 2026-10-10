Every few minutes sends a giant god to a random player to give them a gift or take something, until stopped.
Usage: god_visits [player=@a] [director_min_s=180] [director_max_s=360] [director_weights=6,4] [gifts=...] [takes=...] [take_most=8] [stay_s=8] [director_each=false]
Ends: when stopped

Built from the director preset; every value but player is live.

- player: who may be visited. director_min_s, director_max_s: the wait between visits, in seconds of someone being online; director_each=true gives each player their own clock.
- director_events and director_weights: gift and take, 6 to 4.
- gifts: what a god gives ("diamond 4"); takes: what it clears, at most take_most of one; stay_s: how long it stays.

A giant glowing god (The Radiant One, The Storm God or Thanatos; a mannequin tagged vfg_<instance>) appears in front of the player and whispers to them alone.
On stop: removes a god if one is visiting.
