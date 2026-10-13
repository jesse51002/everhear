from typing import ClassVar

from actions._types import Action, Field


class EngineGive(Action):
    """Give a player one of the engine's items, by its name under ENGINE: a stack of one, on the item's own base, that works for whoever holds it."""

    engine: ClassVar[str] = "give"   # the engine's control op its values go to: no script
    runs = "once"
    takes = ["player", "item"]

    player: str = Field("", pattern=r"^(|[A-Za-z0-9_]{1,16})$")
    """who gets it: one player's exact name"""
    item: str = Field("", pattern=r"^(|[a-z][a-z0-9_]{0,63})$")
    """the item, by its name under ENGINE, such as fire_staff"""
