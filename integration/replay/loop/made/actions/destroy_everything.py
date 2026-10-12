from actions._types import Action, Live, Place, Players, preset


class DestroyEverything(Action, preset("countdown"), preset("crater")):
    """Destroy everything: five seconds of fire round a player, then a huge block-destroying blast that empties the ground within the radius, with no harm to them."""

    script = "destroy_everything"
    runs = "once"
    takes = ["player", "countdown_seconds", "crater_radius", "nuke_at", "fire_seconds"]
    defaults = {"countdown_seconds": 5, "countdown_text": "Destroy everything", "crater_load": True,
                "crater_radius": 30, "crater_leave": "fire", "nuke_kill": True, "nuke_at": "KrownJM"}

    fire_seconds: float = 5
    """how many seconds the fire burns round the player before the blast"""

    player: Players = "@a"
    """whose spot it falls on, where the first one named stands as it starts: a name, several separated by commas, or @a"""
    nuke_at: Place = ""
    """where it goes off instead: a saved place, x y z, or a player; empty: where the player stands"""
    nuke_kill: Live[bool] = True
    """kill every mob, and every player not in creative or spectator, within crater_radius as it goes off"""
