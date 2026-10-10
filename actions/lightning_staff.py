from actions._types import Action, Field, Live, Players, preset


class LightningStaff(Action, preset("damage_hits"), preset("lightning"), preset("around")):
    """Give a player a lightning staff: right-click and lightning strikes the block he looks at at once, hurting everything near it."""

    script = "lightning_staff"
    runs = "until_stopped"
    takes = ["player", "damage_amount", "lightning_count", "lightning_spread", "cooldown", "range"]
    defaults = {"damage_amount": 40, "damage_type": "lightning_bolt", "lightning_count": 4, "around_radius": 4,
                "around_what": "everything"}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    item: str = "Lightning Staff"
    """the name of the item in the hand"""
    cooldown: Live[float] = Field(1, ge=0, le=60)
    """seconds between strikes"""
    range: Live[int] = Field(120, ge=4, le=300)
    """how far away he can strike, in blocks: the block he looks at must be within it"""
