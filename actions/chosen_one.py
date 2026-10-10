from actions._types import Action, Live, Players


class ChosenOne(Action):
    """Make a player the chosen one: a giant radiant god appears to them alone, names them and gives them a holy sword."""

    script = "chosen_one"
    runs = "once"
    takes = ["player", "blade"]
    timeout_s = 40

    player: Players = "@r"
    """who is chosen: the first online of the names given, or @r for one at random; nobody else sees or hears it"""
    blade: Live[str] = "Holy Zombie Slayer"
    """the sword's name, a netherite sword with smite 10 that never breaks"""
