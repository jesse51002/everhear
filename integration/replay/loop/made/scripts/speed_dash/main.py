"""Speed Dash: right-click a named item to dash forward at running speed for a moment, in the direction you move or face; real movement, never a teleport."""
ASKS = ["speed dash", "give me a speed dash", "dash forward", "i want to dash", "a speed boost dash"]
from kit import values, result, items, aim, fx, loop
import itertools
import math
import mc


v = values()
# A dash is a burst of real running speed (the Speed effect) for dash_seconds, in the direction the
# player is moving or facing; it never teleports. Each right-click starts one, and it cannot be
# started again until cooldown has passed.
for p in [v["player"]]:
    # Disabled: the marker-and-damage launch hurt its caster and killed one. No launch runs until a
    # safe momentum mechanism is verified; the cast only plays its effects.
    fx.sound(v["dash_sound"], p, volume=1.5, pitch=1.4)
    fx.particle(v["dash_particle"], p, spread=0.3, count=12, speed=0.05)
result("%s's speed dash is disabled: no safe launch yet, so nothing was thrown" % v["player"], player=v["player"])
