from actions._types import Action, Field, Players


class GunChests(Action):
    """Hide chests of named guns and ammo around the map near each player, each marked by a glowing tag: AK-47, shotgun, sniper, rocket launcher."""

    script = "gun_chests"
    runs = "once"
    takes = ["player", "per_player", "nearest", "farthest"]
    timeout_s = 60

    player: Players = "@a"
    """round whom the chests are hidden: a name, several separated by commas, or @a"""
    per_player: int = Field(4, ge=1, le=10)
    """how many chests round each player, each with two guns and their ammo"""
    nearest: int = Field(40, ge=0, le=1000)
    """the closest a chest is to its player, in blocks"""
    farthest: int = Field(160, ge=1, le=1000)
    """the farthest a chest is from its player, in blocks (never nearer than nearest)"""
