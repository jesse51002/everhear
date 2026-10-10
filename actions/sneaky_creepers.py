from actions._types import Action, Field, Live, Players


class SneakyCreepers(Action):
    """Sneaky creepers: invisible near us so you can't see them coming, but they hiss loudly before they blow."""

    script = "sneaky_creepers"
    runs = "until_stopped"
    takes = ["player", "invisible_within", "hiss_within", "fuse"]

    player: Players = "@a"
    """who the creepers sneak up on: a name, several separated by commas, or @a"""
    invisible_within: Live[float] = Field(10, ge=1, le=64)
    """how near a player creepers turn invisible, in blocks"""
    hiss_within: Live[float] = Field(4, ge=0, le=32)
    """how near a player a creeper hisses loudly, every second, in blocks (0: no hiss)"""
    fuse: Live[float] = Field(3, ge=1.5, le=10)
    """a creeper's fuse, in seconds (the game's own is 1.5), so there is time to run; a change reaches creepers that come after it"""
