from actions._types import Action, Field, Place, Players


class Mountains(Action):
    """Build a mountain range 200 by 100 blocks beside a player: snowy peaks, pines, two caves, two lakes, mountain biomes."""

    script = "mountains"
    runs = "once"
    takes = ["player", "away", "corner", "seed"]
    timeout_s = 600

    player: Players = "@a"
    """whom it is built beside, the first one named who is online: a name, several separated by commas, or @a"""
    away: int = Field(60, ge=0, le=1000)
    """how far east of the player its west edge is, in blocks; the range spans their z (not used with corner)"""
    corner: Place = ""
    """where its north-west corner goes instead: a saved place, x y z (its y is not used), or a player; in the overworld"""
    seed: int = Field(167, ge=0, le=1000000)
    """picks the range's shape, its peaks, caves and lakes: the same seed builds the same range"""
