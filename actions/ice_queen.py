from actions._types import Action, Field, Literal, Live, Place, Players, preset


class IceQueen(Action, preset("boss"), preset("around"), preset("freeze"), preset("summon")):
    """Spawn the ice queen boss: she freezes everyone near her in ice and calls frost servants, until she dies."""

    script = "ice_queen"
    runs = "once"
    takes = ["player", "at", "boss_health", "boss_attack", "freeze_seconds", "summon_count"]
    defaults = {"boss_name": "Ice Queen", "boss_mob": "stray", "boss_health": 1000, "boss_scale": 2, "boss_attack": 8,
                "boss_bar": "blue", "boss_every": 8, "boss_reward": "blue_ice", "boss_reward_count": 16,
                "around_radius": 16, "around_what": "players", "freeze_seconds": 4, "freeze_shell": True,
                "freeze_block": "packed_ice", "summon_mob": "stray", "summon_count": 3, "summon_spread": 4,
                "summon_cap": 6, "summon_name": "Frost Servant"}

    player: Players = "@a"
    """who she rises near: the first of them online (a name, several separated by commas, or @a)"""
    at: Place = ""
    """where she rises instead: a saved place, x y z or a player (empty: near the player)"""
    cold: Live[float] = Field(6, ge=0, le=100)
    """the freeze damage her ice blast does to each it freezes, in half-hearts (0: none)"""
    boss_gear: Literal["none", "leather", "golden", "iron", "diamond", "netherite", "ice"] = "ice"
    """what she wears, never dropped: ice is a diamond crown and boots, pale blue robes and an iron sword; any other a full set of that armour and its sword (leather comes with a wooden sword); none: nothing"""
