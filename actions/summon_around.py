from actions._types import Action, Field, MobId, Players


class SummonAround(Action):
    """Summon a number of one mob round each player, spread to safe spots, optionally scaled, named or glowing.

    The mobs appear at the player and are scattered within the radius onto
    ground that is not water, lava or fire, ten at a time. They carry a tag of
    their own, so they stay when the copy ends. For a few mobs at an exact spot,
    the command action's summon is enough. The report says how many stand and
    round whom, with the coordinates.
    """

    script = "summon_around"
    runs = "once"
    takes = ["players", "mob", "count", "radius", "scale", "name", "glowing"]
    timeout_s = 60

    players: Players = "@a"
    """round whom: one player's exact name, several separated by commas, or @a for every player online"""
    mob: MobId = "zombie"
    """the mob's id, such as zombie, creeper or cow"""
    count: int = Field(10, ge=1, le=100)
    """how many round each player"""
    radius: int = Field(16, ge=4, le=64)
    """how far from the player they may land, in blocks"""
    scale: float = Field(1.0, ge=0.25, le=8.0)
    """their size: 1 is normal, 2 twice as big, 0.5 half"""
    name: str = Field("", max_length=32)
    """a name shown above each one, or empty for none"""
    glowing: bool = False
    """true to make them glow, visible through walls"""
