from actions._types import Action, Field, Literal, Players, preset


class WorldReplace(Action, preset("replace_blocks")):
    """Replace or remove a block everywhere round the players at once, like all the obsidian or all the water: the loaded world, up to 512 blocks out."""

    script = "world_replace"
    runs = "once"
    takes = ["player", "replace_rules", "radius", "dims"]
    defaults = {"replace_rules": ["obsidian > air"]}
    timeout_s = 120

    player: Players = "@a"
    """round whom it changes: a name, several separated by commas, or @a"""
    radius: int = Field(80, ge=1, le=512)
    """how far round each player it changes, in blocks each way; only loaded chunks change"""
    height: int = Field(0, ge=0, le=384)
    """how far above and below their feet it changes, in blocks (0: the world's whole height)"""
    dims: list[Literal["overworld", "the_nether", "the_end"]] = ["overworld", "the_nether", "the_end"]
    """the dimensions it changes, round the players who are in them"""
