from actions._types import Action, Field, Live, Players, preset


class InfinityGauntlet(Action, preset("damage_hits")):
    """Give a player an infinity gauntlet: right-click and the first thing they point at is gone, in a beam of flame, with no cooldown."""

    script = "infinity_gauntlet"
    runs = "until_stopped"
    takes = ["player", "damage_kill", "damage_amount", "damage_what", "cooldown", "range"]
    defaults = {"damage_type": "lightning_bolt", "damage_kill": True, "damage_most": 1}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    item: str = "Infinity Gauntlet"
    """the name of the item in the hand"""
    cooldown: Live[float] = Field(0, ge=0, le=60)
    """seconds between uses"""
    range: Live[int] = Field(80, ge=4, le=300)
    """how far it reaches, in blocks"""
    damage_amount: Live[float] = Field(100, ge=0, le=100000)
    """damage to what it hits, in half-hearts (a player has 20); does nothing while damage_kill is on, and it is on by default: set damage_kill off for this to count"""
