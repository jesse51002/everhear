from typing import ClassVar

from actions._types import Action, Field


class EngineStart(Action):
    """Start one of the engine's scenarios, by its name under ENGINE: a horde, a storm, a boss fight or a zone with rules, for the whole world."""

    engine: ClassVar[str] = "start"   # the engine's control op its values go to: no script
    runs = "once"
    takes = ["scenario"]

    scenario: str = Field("", pattern=r"^(|[a-z][a-z0-9_]{0,63})$")
    """the scenario, by its name under ENGINE, such as horde"""
