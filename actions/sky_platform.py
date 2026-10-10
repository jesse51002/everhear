from actions._types import Action, Field, Literal, Live, Place, Players


class SkyPlatform(Action):
    """Put a player on an obsidian platform high in the sky, with a rim and lights, and set their spawn there."""

    script = "sky_platform"
    runs = "once"
    takes = ["player", "platform_y", "platform_size", "shape_blocks", "platform_spawn"]

    player: Players = "@a"
    """who gets one, high over where they stand: a name, several separated by commas, or @a"""
    platform_y: int = Field(200, ge=-60, le=317)
    """how high it is: the y of its floor, kept under the top of the world"""
    platform_size: int = Field(9, ge=3, le=64)
    """how wide it is, a square, in blocks (an even size is made odd)"""
    platform_at: Place = ""
    """over where instead: a saved place, x y z (its y is not used), or a player; one platform there for all; empty: over each player"""
    platform_rim: bool = True
    """a rim a block high round its edge, with glowstone on its corners"""
    platform_tp: bool = True
    """put each player on it, facing as they were"""
    platform_spawn: bool = True
    """set each player's spawn on it"""
    shape_facing: Live[Literal["look", "north", "east", "south", "west"]] = "look"
    """which way it is laid out: look, the way the player looks, or a compass direction; a square looks the same each way"""
    shape_blocks: Live[str] = "obsidian"
    """what its floor and rim are made of: a block id or a mix, "70% obsidian 30% crying_obsidian" (no %: counts 1)"""
