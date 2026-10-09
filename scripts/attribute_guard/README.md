Puts any player attribute that was changed back to its default every five seconds, until stopped.
Usage: attribute_guard [player=@a] [every=5]
Ends: when stopped

- player: whose attributes are guarded; every (live): seconds between checks.

Checks each player's attributes (max health, speed, scale, gravity, reach and more) and resets any that differ from vanilla, saying "[Demon Lord] Someone tampered with <player>. I undid it." in chat. Do not run it beside a script that sets attributes on purpose.
Leaves nothing in the world.
