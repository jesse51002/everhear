from actions._types import Action, Live, Player, Players, preset


class HorrorMode(Action, preset("director")):
    """Spooky horror mode: ash in the air, sounds behind us, a figure watching, a creeper that hisses and vanishes, whispers, the dark."""

    script = "horror_mode"
    runs = "until_stopped"
    takes = ["player", "no_dark", "director_events", "director_min_s", "director_max_s", "director_messages"]

    player: Players = "@a"
    """who it scares: a name, several separated by commas, or @a"""
    no_dark: Live[list[Player]] = []
    """players the dark never falls on, lifted at once from anyone added; the rest of the horror stays"""
