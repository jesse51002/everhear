from actions._types import Action, Field, ItemId, Live, Players, preset


class GodVisits(Action, preset("director")):
    """A god visits us now and then: a giant appears to one player, speaks to them alone, and gives a gift or takes something."""

    script = "god_visits"
    runs = "until_stopped"
    takes = ["player", "director_events", "director_min_s", "director_max_s", "gifts"]
    defaults = {"director_weights": [6, 4], "director_each": False}

    player: Players = "@a"
    """who it may visit: a name, several separated by commas, or @a"""
    director_events: Live[list[str]] = ["gift", "take"]
    """what a visit does, one at random: gift (one of gifts), take (one of take_items); also the director's sound, shadow, mob, message, dark"""
    director_min_s: Live[float] = Field(180, ge=10, le=3600)
    """the shortest wait between visits, in seconds someone is online"""
    director_max_s: Live[float] = Field(360, ge=10, le=3600)
    """the longest wait between visits, in seconds (under director_min_s: director_min_s)"""
    gifts: Live[list[str]] = ["diamond 4", "golden_apple 3", "enchanted_golden_apple 1", "netherite_ingot 1",
                              "totem_of_undying 1", "ender_pearl 8", "experience_bottle 32", "cooked_beef 32"]
    """what a god may give, one at random: an item's id and how many ("diamond 4")"""
    take_items: Live[list[ItemId]] = ["diamond", "golden_apple", "ender_pearl", "cooked_beef", "iron_ingot", "arrow",
                                      "torch", "bread"]
    """what a god may take, one at random, up to take_most of it"""
    take_most: Live[int] = Field(8, ge=1, le=64)
    """the most of an item a god takes at once"""
    stay_s: Live[float] = Field(8, ge=2, le=60)
    """how long a god stays in front of the player, in seconds"""
