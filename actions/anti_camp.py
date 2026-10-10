from actions._types import Action, Field, Live, Players, preset


class AntiCamp(Action, preset("stands_still")):
    """Punish campers: TNT falls on anyone who stands still in one spot too long, after a warning."""

    script = "anti_camp"
    runs = "until_stopped"
    takes = ["player", "stands_seconds", "stands_radius", "tnt"]
    defaults = {"stands_warning": "MOVE. The Demon Lord sees you hiding..."}

    player: Players = "@a"
    """who it watches: a name, several separated by commas, or @a"""
    stands_seconds: Live[float] = Field(15, ge=6, le=3600)
    """how long a player may stay put, in seconds, before the TNT falls (the warning shows in the last 5); then it counts again"""
    tnt: Live[int] = Field(7, ge=1, le=30)
    """how many TNT fall round a camper each time"""
    height: Live[int] = Field(12, ge=2, le=60)
    """how high over the camper the TNT appears, in blocks"""
    fuse: Live[float] = Field(2, ge=0.5, le=10)
    """seconds before the TNT blows up"""
    mock: Live[bool] = True
    """the Demon Lord mocks the camper in chat as the TNT falls"""
