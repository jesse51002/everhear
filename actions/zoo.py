from actions._types import Action, Players, preset


class Zoo(Action, preset("horde_wave")):
    """Spawn random animals around me, a zoo, and keep them coming up to a most alive at once."""

    script = "zoo"
    runs = "until_stopped"
    takes = ["player", "horde_mobs", "horde_per_player", "horde_cap"]
    defaults = {"horde_mobs": ["pig", "cow", "sheep", "chicken", "rabbit", "fox", "goat", "llama", "cat", "wolf",
                               "parrot", "frog", "turtle", "axolotl", "panda", "horse", "donkey", "bee", "armadillo",
                               "camel"],
                "horde_per_player": 20, "horde_every": 1, "horde_cap": 200, "horde_spread": 8, "horde_announce": False}

    player: Players = "@a"
    """who the animals come round: a name, several separated by commas, or @a"""
