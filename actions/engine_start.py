from actions._types import Action, Args, ClassId


class EngineStart(Action):
    """Start one of the engine's scenarios with its args: a one-off (a command, a teleport, saving a place, a build) answers with what it did; a lasting one (a horde, a storm, a rule) runs until it ends or is stopped.

    Its args are the scenario's own, as ENGINE lists them, each by its name; one left out
    keeps its default. A Player arg takes one player's exact name, a Players arg names or
    @a, a Place arg a saved place's name, x y z, or a player's name. The engine checks
    them, and a wrong one is refused in words, with nothing started.
    """

    engine = "start"
    takes = ["scenario", "args"]
    timeout_s = 45   # the start waits for a one-off's result, up to VFG_ENGINE_START_WAIT_S

    scenario: ClassId = ""
    """the scenario, by its name under ENGINE, such as command, teleport or horde"""
    args: Args = {}
    """its args, each by its name as ENGINE lists them: args.commands, args.at, args.count"""
