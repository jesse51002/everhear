from actions._types import Action, Live, Place, Players, preset


class BarrierShield(Action, preset("around"), preset("push")):
    """A barrier round a player, or round a place such as their base: a bubble of light that throws back every mob that comes inside it."""

    script = "barrier_shield"
    runs = "until_stopped"
    takes = ["player", "at", "around_radius", "around_what", "push_strength"]
    defaults = {"around_radius": 5, "push_strength": 4}

    player: Live[Players] = "@a"
    """who has a barrier round them, which never throws them: a name, several separated by commas, or @a"""
    at: Live[Place] = ""
    """one barrier round a place instead: a saved place, x y z, or where a player stands as it starts; empty: round each player"""
