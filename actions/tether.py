from actions._types import Action, Live, Players, preset


class Tether(Action, preset("leaves_area")):
    """Keep a player next to the others: whenever he wanders too far from all of them, he is teleported back to the nearest."""

    script = "tether"
    runs = "until_stopped"
    takes = ["player", "leaves_of_player", "leaves_radius", "message"]
    defaults = {"leaves_area": "player", "leaves_radius": 30}

    player: Players = "@a"
    """who is kept near the others: a name, several separated by commas, or @a"""
    message: Live[str] = "Stay with the group"
    """the words over the hotbar of whoever is brought back (empty: none)"""
