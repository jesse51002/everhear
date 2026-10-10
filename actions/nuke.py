from actions._types import Action, Field, Live, Place, Players, preset


class Nuke(Action, preset("countdown"), preset("crater")):
    """Drop a nuke on a player or a place: a countdown in chat (ten minutes unless told), then a flash, everything alive in the radius dies and the ground is carved away."""

    script = "nuke"
    runs = "once"
    takes = ["player", "countdown_seconds", "crater_radius", "nuke_at"]
    defaults = {"countdown_seconds": 600, "countdown_text": "☢ Nuke", "crater_load": False}
    timeout_s = 900

    player: Players = "@a"
    """whose spot it falls on, where the first one named stands as it starts: a name, several separated by commas, or @a"""
    nuke_at: Place = ""
    """where it goes off instead: a saved place, x y z, or a player; empty: where the player stands"""
    nuke_kill: Live[bool] = True
    """kill every mob, and every player not in creative or spectator, within crater_radius as it goes off"""
    crater_radius: Live[int] = Field(1000, ge=0, le=1000)
    """how big the blast is, a ball this many blocks round the middle: what dies, and the hole, carved only where chunks are loaded (0: no hole)"""
