from actions.railgun import Railgun


class GatlingGun(Railgun):
    """Give a player a gatling gun: hold right-click to rapid-fire."""

    takes = ["player", "damage_amount", "damage_what"]
    fixed = {"fire": "hold", "item": "Gatling Gun", "cooldown": 0.1, "range": 24,
             "particle": "crit", "sound": "entity.arrow.shoot"}
