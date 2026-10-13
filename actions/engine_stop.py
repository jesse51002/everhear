from typing import ClassVar

from actions._types import Action, Field


class EngineStop(Action):
    """Stop a scenario the engine is running, by its name, or one run of it by its instance under ENGINE: everything it spawned goes."""

    engine: ClassVar[str] = "stop"   # the engine's control op its values go to: no script
    runs = "once"
    takes = ["scenario", "instance"]

    scenario: str = Field("", pattern=r"^(|[a-z][a-z0-9_]{0,63})$")
    """the scenario, by its name under ENGINE, such as horde: every run of it stops; empty when instance names one"""
    instance: str = Field("", pattern=r"^(|[A-Za-z0-9_.:@#-]{1,96})$")
    """one running instance of it, as ENGINE names it; empty to stop every run of the scenario"""
