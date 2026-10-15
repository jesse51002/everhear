from actions._types import Action, Args, ClassId, Field, Player


class EngineApply(Action):
    """Put one of the engine's marks on a player, with its args: shrunk, giant, frozen, burning; it ends by itself when its time is up, or with engine_clear.

    Its args are the mark's own, as ENGINE lists them, each by its name; one left out
    keeps its default. seconds sets how long it lasts; 0 keeps the mark's own duration.
    The engine checks them, and a wrong one is refused in words, with nothing applied.
    """

    engine = "apply"
    takes = ["player", "mark", "args", "seconds"]

    player: Player = ""
    """who bears it: one player's exact name"""
    mark: ClassId = ""
    """the mark, by its name under ENGINE, such as giant"""
    args: Args = {}
    """its args, each by its name as ENGINE lists them"""
    seconds: float = Field(0, ge=0, le=86400)
    """how long it lasts, in seconds; 0 for the mark's own duration"""
