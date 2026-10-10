from actions._types import Action, Live, Players, preset


class Arena(Action, preset("leaves_area")):
    """Keep everyone inside a radius, or a box: anyone who walks out is teleported back to the middle."""

    script = "arena"
    runs = "until_stopped"
    takes = ["player", "leaves_radius", "leaves_area", "leaves_center", "leaves_box"]

    player: Players = "@a"
    """who is kept in: a name, several separated by commas, or @a"""
    message: Live[str] = "Back inside the arena"
    """what a player who is sent back sees on their screen (empty: nothing)"""
