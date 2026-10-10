from actions._types import Action, Players, preset


class Meteor(Action, preset("sky_above"), preset("meteor_fall"), preset("explosion")):
    """Drop one big flaming meteor out of the sky onto a player: it falls slantwise and blasts a crater where it lands."""

    script = "meteor"
    runs = "once"
    takes = ["player", "meteor_size", "explosion_power", "sky_radius", "explosion_breaks"]
    defaults = {"sky_radius": 7, "sky_count": 1, "meteor_size": 10, "meteor_height": 100, "meteor_slant": 25,
                "meteor_seconds": 4.8, "explosion_layout": "rings", "explosion_keep": 7.5}
    timeout_s = 60

    player: Players = "@a"
    """who it falls on: a name, several separated by commas, or @a; sky_count meteors round each, all at once"""
