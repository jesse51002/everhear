from actions._types import Action, Field, Live, Players


class WalkingDead(Action):
    """Walking dead zombies: slow, in big groups, they hear you sprint from far away and barely notice you sneak."""

    script = "walking_dead"
    runs = "until_stopped"
    takes = ["player", "walk_speed", "sprint_range", "sneak_range", "friends"]

    player: Players = "@a"
    """whose steps the zombies hear: a name, several separated by commas, or @a"""
    walk_speed: Live[float] = Field(0.17, ge=0.05, le=0.5)
    """the zombies' speed from afar (a zombie's own is 0.23); they lunge faster within 12 blocks, faster again within 6"""
    sneak_range: Live[int] = Field(5, ge=1, le=64)
    """how far a zombie notices a player who is sneaking, in blocks"""
    walk_range: Live[int] = Field(16, ge=1, le=64)
    """how far a zombie notices a player who is walking, in blocks"""
    sprint_range: Live[int] = Field(40, ge=1, le=64)
    """how far a zombie hears a player who is sprinting, in blocks"""
    friends: Live[float] = Field(0.3, ge=0, le=1)
    """the chance, each second, that two zombies each bring a friend, so they gather in bigger groups (0: never)"""
