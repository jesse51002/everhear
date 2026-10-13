from typing import ClassVar

from actions._types import Action, Field


class EngineUnbind(Action):
    """Remove a player's engine incantation, by player and phrase, so saying it casts nothing; the ability stays theirs."""

    engine: ClassVar[str] = "unbind"   # the engine's control op its values go to: no script
    runs = "once"
    takes = ["player", "phrase"]

    player: str = Field("", pattern=r"^(|[A-Za-z0-9_]{1,16})$")
    """whose incantation: the player's exact name, as INCANTATIONS has it"""
    phrase: str = Field("", max_length=80)
    """the incantation's phrase, as INCANTATIONS has it"""
