from actions._types import Action, Field, Live, Players, preset


class MeteorStaff(Action, preset("meteor_fall"), preset("explosion")):
    """Give a player a meteor staff: each right-click drops a flaming meteor from the sky onto the block he looks at, and it explodes."""

    script = "meteor_staff"
    runs = "until_stopped"
    takes = ["player", "meteor_size", "explosion_power", "cooldown", "reach"]
    defaults = {"meteor_blocks": "magma_block", "meteor_slant": 15, "meteor_seconds": 2.5,
                "explosion_layout": "plus", "explosion_spread": 3}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    item: str = "Meteor Staff"
    """the name of the item in the hand"""
    cooldown: Live[float] = Field(5, ge=0, le=60)
    """seconds between meteors"""
    reach: Live[int] = Field(120, ge=4, le=300)
    """how far away he can call one down, in blocks: the block he looks at must be within it"""
