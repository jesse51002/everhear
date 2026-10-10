from actions._types import Action, Field, Live, Players, preset


class IceStaff(Action, preset("damage_hits"), preset("lock_on"), preset("around"), preset("freeze")):
    """Give a player an ice staff: right-click the mob or block he points at, and every mob round it freezes in place and is hurt; no beam."""

    script = "ice_staff"
    runs = "until_stopped"
    takes = ["player", "damage_amount", "lock_range", "around_radius", "freeze_seconds", "cooldown"]
    defaults = {"damage_amount": 200, "damage_type": "magic", "lock_range": 300, "around_radius": 15}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    item: str = "Ice Staff"
    """the name of the item in the hand"""
    cooldown: Live[float] = Field(1, ge=0, le=60)
    """seconds between uses"""
