from actions._types import Action, BlockMix, Field, Literal, Live, Place, Players


class SniperNest(Action):
    """Build a sniper tower in front of a player or at a place: a ladder up to a walled lookout with slits, their name on a sign, their spawn on top."""

    script = "sniper_nest"
    runs = "once"
    takes = ["player", "nest_height", "nest_ahead", "nest_at", "nest_sign"]

    player: Players = "@a"
    """who gets one, in front of them, its door facing them: a name, several separated by commas, or @a"""
    nest_height: int = Field(14, ge=4, le=120)
    """how high the lookout's floor is over the ground, in blocks"""
    nest_ahead: int = Field(5, ge=0, le=64)
    """how far in front of the player its door is, in blocks; not used at nest_at"""
    nest_at: Place = ""
    """where it goes instead: a saved place, x y z, or a player, its door there facing north; one nest there for all; empty: in front of each player"""
    nest_sign: list[str] = ["{player}", "Sniper Nest"]
    """the sign over the door: up to four lines of up to 40 letters, {player} for the player's name; empty: no sign"""
    nest_spawn: bool = True
    """set each player's spawn on the lookout"""
    shape_facing: Live[Literal["look", "north", "east", "south", "west"]] = "look"
    """which way it faces, from the door in: look, the way the player looks (south at nest_at), or a compass direction"""
    shape_blocks: Live[BlockMix] = "stone_bricks"
    """what the tower is made of: a block id or a mix, "70% stone_bricks 30% cobblestone" (no %: counts 1)"""
