from actions._types import Action, Field


class IncantationRemove(Action):
    """Remove a player's incantation, by player and phrase, so saying it fires nothing."""

    script = "incantations"
    runs = "once"
    takes = ["player", "phrase"]

    player: str = Field("", pattern=r"^(|@a|[A-Za-z0-9_]{1,16}( *, *[A-Za-z0-9_]{1,16})*)$")
    """whose incantation: the player's exact name, as the list of incantations has it; several separated by commas; @a for the one everyone has"""
    phrase: str = Field("", max_length=80)
    """the incantation's phrase, as the list of incantations has it"""
