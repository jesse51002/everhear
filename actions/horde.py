from actions._types import Action, Players, preset


class Horde(Action, preset("horde_wave")):
    """Spawn a horde of zombies, or any mix of mobs, all around each of us, in waves if asked; it ends when they are all dead."""

    script = "horde"
    runs = "once"
    takes = ["player", "horde_mobs", "horde_per_player", "horde_waves", "horde_spread", "horde_explode"]
    defaults = {"horde_per_player": 100, "horde_cap": 300, "horde_spread": 60, "horde_waves": 1}

    player: Players = "@a"
    """who it surrounds: a name, several separated by commas, or @a"""
