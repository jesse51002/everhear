from actions._types import Action, Place, Players, preset


class HordeBoss(Action, preset("boss")):
    """Spawn a giant boss near us, in netherite, with lots of health, a health bar and minions; loot drops where it dies."""

    script = "horde_boss"
    runs = "once"
    takes = ["player", "boss_name", "boss_mob", "boss_health", "boss_scale", "boss_minion"]

    player: Players = "@a"
    """who it rises near: the first of them online (a name, several separated by commas, or @a)"""
    at: Place = ""
    """where it rises instead: a saved place, x y z or a player (empty: near the player)"""
