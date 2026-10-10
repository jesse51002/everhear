from actions._types import Action, Literal, Players


class Teleport(Action):
    """Move a player, or everyone, to a biome, a structure, a saved place, coordinates, far away, their spawn point or where they last died, landing on safe ground.

    To another player it is one `tp`, which the command action does: do not use
    this for that. It finds the place, gives Resistance and Slow Falling for 5
    seconds, lands the player with spreadplayers within 8 blocks of it, and lands
    again further out (16, 32, 64) while the ground is magma, cactus, powder
    snow, a campfire, berries, dripstone, water or lava. A move over 1,000 blocks
    sets the player's spawn point where they land. With @a, the first player
    online lands and everyone else is teleported onto them. The report says who
    went where, with the coordinates.
    """

    script = "teleport"
    runs = "once"
    takes = ["player", "to", "target", "dimension"]
    timeout_s = 60

    player: Players = "@a"
    """who moves: one player's exact name, several separated by commas, or @a for everyone online"""
    to: Literal["biome", "structure", "place", "coords", "far", "spawn_point", "last_death"] = "place"
    """what kind of destination: biome (the nearest one), structure (the nearest one, such as a village), place (a saved place by name), coords (given x and z), far (a random spot that many blocks away), spawn_point (where the player respawns: their bed, else the world spawn), last_death (where the player last died)"""
    target: str = ""
    """a biome or structure id, a #tag allowed, such as snowy_taiga or #village; a saved place's name; 'x z' (or 'x y z') for coords; a distance in blocks for far, 1 to 1000000 (empty: 1000); empty for spawn_point and last_death"""
    dimension: Literal["overworld", "the_nether", "the_end"] = "overworld"
    """the dimension to look in, for biome, structure, coords and far; a saved place, a spawn point and a death already know theirs"""
