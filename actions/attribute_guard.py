from actions._types import Action, Field, Live, Players


class AttributeGuard(Action):
    """Reset my attributes to normal (size, speed, health, gravity) and keep them normal: anything changed is put back every few seconds."""

    script = "attribute_guard"
    runs = "until_stopped"
    takes = ["player", "every"]

    player: Players = "@a"
    """whose attributes it guards: a name, several separated by commas, or @a"""
    every: Live[float] = Field(5, ge=1, le=600)
    """seconds between checks; an attribute changed is put back to its default at the next one, and chat says so"""
