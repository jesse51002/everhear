from actions._types import Action, Field, Live, Players, preset


class DeathGaze(Action, preset("damage_hits"), preset("sneak")):
    """Kill every mob a player stares at while they sneak: a death beam from their eyes, up to the first wall."""

    script = "death_gaze"
    runs = "until_stopped"
    takes = ["player", "range", "damage_what", "damage_kill", "damage_amount", "sneak_mode"]
    defaults = {"damage_type": "magic", "damage_kill": True, "damage_what": "mobs"}

    player: Players = "@a"
    """who has the gaze: a name, several separated by commas, or @a"""
    range: Live[int] = Field(60, ge=4, le=300)
    """how far the gaze reaches, in blocks; a wall stops it sooner"""
