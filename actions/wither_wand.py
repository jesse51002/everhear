from actions._types import Action, Field, Live, Players, preset


class WitherWand(Action, preset("summon")):
    """Give a player a wither wand: right-click to spawn withers where he looks, mob griefing off while they live."""

    script = "wither_wand"
    runs = "until_stopped"
    takes = ["player", "summon_count", "cooldown", "summon_cap"]
    defaults = {"summon_spread": 3, "summon_cap": 15}
    fixed = {"summon_mob": "wither"}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    item: str = "Wither Wand"
    """the name of the item in the hand"""
    cooldown: Live[float] = Field(5, ge=0, le=60)
    """seconds between summons"""
    range: Live[int] = Field(100, ge=4, le=300)
    """how far away it summons, in blocks: where the look meets a block, or this far"""
