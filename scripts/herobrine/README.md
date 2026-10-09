Plays random Herobrine horror pranks on the named players, or everyone, every 60 to 150 seconds until stopped.
Usage: herobrine [player=@a] [director_min_s=60] [director_max_s=150] [director_events=...] [director_weights=...] [director_messages=...]
Ends: when stopped

Built from the director preset; every value but player is live.

- player: who gets haunted. The clock only runs while a target is online; each has their own.
- director_events, director_weights: sound (3), door, fake_join, message, shadow, torches, dark, sign, lightning (1 each); also mob, a fake creeper.
- director_messages: what Herobrine whispers.

Pranks: footsteps, cave sounds or a stare behind them, a door creaking, a fake "Herobrine joined the game", a whisper in chat, a Herobrine statue behind the player for a few seconds (tagged vfg_<instance>), torches within 8 blocks removed, darkness and a heartbeat, a "behind you" sign, distant lightning.
Leaves in the world: the signs, and the missing torches.
On stop: removes a statue if one is up, and the darkness.
