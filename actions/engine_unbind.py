from actions._types import Action, Field, Player


class EngineUnbind(Action):
    """Remove a player's incantation, by player and phrase, so saying it does nothing; what it cast or started stays."""

    engine = "unbind"
    takes = ["player", "phrase"]

    player: Player = ""
    """whose incantation: the player's exact name, as INCANTATIONS has it"""
    phrase: str = Field("", max_length=80)
    """the incantation's phrase, as INCANTATIONS has it"""
