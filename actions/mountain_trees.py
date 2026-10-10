from actions._types import Action, Field, Place, Players


class MountainTrees(Action):
    """Plant spruce trees on the grass slopes of the mountains built beside a player, below y 130."""

    script = "mountain_trees"
    runs = "once"
    takes = ["player", "n", "away", "corner"]
    timeout_s = 300

    player: Players = "@a"
    """beside whom the mountains are, the first one named who is online: a name, several separated by commas, or @a"""
    away: int = Field(60, ge=0, le=1000)
    """how far east of the player the area's west edge is, in blocks, as the mountains had it (not used with corner)"""
    corner: Place = ""
    """the area's north-west corner instead, as the mountains had it: a saved place, x y z (its y is not used), or a player"""
    n: int = Field(250, ge=1, le=2000)
    """how many trees, at most; it tries eight spots for each and plants only on grass"""
    seed: int = Field(5, ge=0, le=1000000)
    """picks the spots: the same seed tries the same spots again"""
