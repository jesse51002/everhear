from actions._types import Action, Any, Field


class IncantationEdit(Action):
    """Change a player's incantation, by player and phrase: its action's values, its cooldown or its aliases.

    Only what is given changes. The values are checked against the action the
    phrase fires, over the ones it has; a player field set empty becomes whoever
    casts it. New aliases replace the whole list, and are checked for phrases of
    that player's they sound like.
    """

    script = "incantations"
    runs = "once"
    takes = ["player", "phrase", "values", "cooldown_s", "aliases"]

    player: str = Field("", pattern=r"^(|@a|[A-Za-z0-9_]{1,16}( *, *[A-Za-z0-9_]{1,16})*)$")
    """whose incantation: the player's exact name, as the list of incantations has it; several separated by commas; @a for the one everyone has"""
    phrase: str = Field("", max_length=80)
    """the incantation's phrase, as the list of incantations has it"""
    values: dict[str, Any] = {}
    """the values to change, each by its name in the action the phrase fires, with its new setting; leave it out to keep them"""
    cooldown_s: float = Field(3, ge=0, le=600)
    """the new seconds before it can be cast again; leave it out to keep the cooldown"""
    aliases: list[str] = []
    """the whole new list of other ways it is said or misheard, such as fireball or fire bowl; leave it out to keep them"""
