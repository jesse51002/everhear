from actions.wither_wand import WitherWand


class SummoningStaff(WitherWand):
    """Give a player a summoning staff: right-click to spawn any mob (zombies, skeletons...) where he looks."""

    takes = ["player", "summon_mob", "summon_count", "cooldown", "summon_cap"]
    defaults = {"summon_mob": "zombie"}
    fixed = {"item": "Summoning Staff"}
