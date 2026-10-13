from actions._types import Action, BlockId, Field, Live, Place, Players, preset


class LaunchPad(Action, preset("under_feet"), preset("launch")):
    """Launch pads, a jump pad or a dash: standing on a slime block (or another block) throws a player through the air, forward where they face or straight up, landing safely."""

    script = "launch_pad"
    runs = "until_stopped"
    takes = ["player", "pad_block", "launch_way", "launch_strength"]
    defaults = {"launch_way": "forward", "launch_strength": 12}
    fixed = {"under_radius": 0, "under_depth": 1, "under_above": 0}

    player: Live[Players] = "@a"
    """whom the pads launch: a name, several separated by commas, or @a"""
    pad_block: Live[BlockId] = "slime_block"
    """the block that launches whoever stands on it"""
    cooldown: Live[float] = Field(2, ge=0, le=60)
    """seconds before the same player is launched again"""
    at: Live[Place] = ""
    """for away or toward: the place they are thrown from or toward (a saved place, x y z, or a player); empty: the pad itself"""
