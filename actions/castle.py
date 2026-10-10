from actions._types import Action, BlockMix, Field, Literal, Live, Place, Players


class Castle(Action):
    """Build a castle in front of a player or at a place: walls, four round towers, a gate and a keep, of any block and size, with a sign over the gate."""

    script = "castle"
    runs = "once"
    takes = ["player", "castle_size", "shape_blocks", "castle_tower_blocks", "castle_sign", "castle_stairs",
             "castle_ahead", "castle_at"]
    timeout_s = 120

    player: Players = "@a"
    """who gets one, in front of them, its gate facing them: a name, several separated by commas, or @a"""
    castle_size: int = Field(23, ge=11, le=101)
    """how wide the castle is, side to side and front to back, in blocks (an even size is made odd)"""
    castle_height: int = Field(7, ge=4, le=40)
    """how high its walls are, in blocks; the towers rise half as high again"""
    castle_tower_blocks: BlockMix = "cobblestone"
    """what the towers and their roofs are made of: a block id or a mix, "70% cobblestone 30% mossy_cobblestone" (no %: counts 1)"""
    castle_ahead: int = Field(6, ge=0, le=64)
    """how far in front of the player its gate is, in blocks; not used at castle_at"""
    castle_at: Place = ""
    """where it goes instead: a saved place, x y z, or a player, its gate there facing north; empty: in front of each player"""
    castle_sign: list[str] = []
    """a sign over the gate: up to four lines of up to 40 letters, {player} for the player's name; empty: no sign"""
    castle_stairs: int = Field(0, ge=0, le=380)
    """a staircase from the yard this many blocks down, carved through the ground (0: none)"""
    shape_facing: Live[Literal["look", "north", "east", "south", "west"]] = "look"
    """which way it faces, from the gate in: look, the way the player looks (south at castle_at), or a compass direction"""
    shape_blocks: Live[BlockMix] = "stone_bricks"
    """what the walls, the keep and the floor are made of: a block id or a mix, "70% stone_bricks 30% cobblestone" (no %: counts 1)"""
