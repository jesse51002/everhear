from actions.gravity_crush import GravityCrush


class GravityLift(GravityCrush):
    """Give a player a gravity lift staff: right-click to throw every mob round where he looks up into the air."""

    takes = ["player", "around_radius", "around_what", "push_strength", "range", "cooldown"]
    fixed = {"mode": "lift", "item": "Gravity Lift Staff"}
