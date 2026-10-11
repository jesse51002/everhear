"""Save Place: save where a player stands, or given coordinates, under a name everyone can use."""
ASKS = ["save this spot as the castle", "remember where I am as home"]
# No player asked to save a place in the 2026-10-08 playtest: ASKS are the words the
# conversation hands on for one.
from kit import values, result, world
import math
import sys
import mc
import places


def fail(summary, **facts):
    result(summary, **facts)
    sys.exit(1)


v = values()
name, player, at = v["name"], v["player"], v["at"]
if not name:
    fail("which name? a place is saved under a short name: castle, home, zoo_entrance")
if bool(player) == bool(at):
    fail("give the player or the coordinates, not both" if player else "give the player or the coordinates")
if player:
    c = mc.pos(player)
    if c is None:
        fail("%s is not online" % player, offline=[player])
    spot = world.Pos(c[0], c[1], c[2], world.dim_of(player))
else:
    spot = world.place(at)
    if spot is None:
        fail("%s is nowhere: not x y z, a saved place or a player online" % at)
# by: the task that saved it, by its name, as the places list shows it (castle 197 68 251
# overworld (castle_steve)); "" when no task did (the brain's own copy, or a cast's).
by = mc.task()
try:
    p = places.save(mc.rcon, name, tuple(spot), spot.dim or world.OVERWORLD, by)
except (places.PlaceError, ValueError) as e:
    fail(str(e))
dim = p["dim"][len("minecraft:"):] if p["dim"].startswith("minecraft:") else p["dim"]
result("saved %s at %d %d %d in %s" % (name, p["pos"][0], p["pos"][1], p["pos"][2], dim),
       name=name, pos=p["pos"], dim=p["dim"], by=p["by"])
