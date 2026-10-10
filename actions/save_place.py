from actions._types import Action, Field, Place


class SavePlace(Action):
    """Save where a player stands, or given coordinates, under a name everyone can use.

    Give either the player or the coordinates, never both. Saving a name again
    moves it. Every later task, and every model, sees the saved places by name,
    so teleport can go there.
    """

    script = "save_place"
    runs = "once"
    takes = ["name", "player", "at"]

    name: str = Field("", pattern=r"^[a-z0-9_]{0,32}$")
    """the place's name, short, lowercase, with _ for spaces: castle, home, zoo_entrance"""
    player: str = Field("", pattern=r"^[A-Za-z0-9_]{0,16}$")
    """save where this player stands, by their exact name; empty when the coordinates are given"""
    at: Place = ""
    """or save these coordinates instead, x y z (the_nether or the_end after them); empty when the player is given"""
