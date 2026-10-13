from actions._types import Action, ClassId, Instance


class EngineStop(Action):
    """Stop a scenario the engine is running: every run of it by its name, or one run by its instance under RUNNING NOW. Everything it spawned goes.

    An ability is taken away with engine_revoke, a mark with engine_clear.
    """

    engine = "stop"
    takes = ["scenario", "instance"]

    scenario: ClassId = ""
    """the scenario, by its name under ENGINE, such as horde: every run of it stops; empty when instance names one"""
    instance: Instance = ""
    """one run of it, as RUNNING NOW names it (scenario:horde#3); empty to stop every run of the scenario"""
