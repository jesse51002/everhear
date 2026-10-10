Plays kill-streak, hit-marker and hurt sounds to each player, until stopped.
Usage: kill_fx [player=@a] [streak_s=8] [hit_markers=true] [hurt_sound=true] [volume=1] [on_death_event=both]
Ends: when stopped

Built from the on_death preset; every value but player is live.

Each player hears: a death sting when they die, a rising chime per kill (kills within streak_s s make a streak; three or more add a twinkle), a hit marker when they deal damage, a thud when they take it. Uses the objectives vfg_<instance>_deaths, _kills, _dealt and _taken.
On stop: removes the objectives.
