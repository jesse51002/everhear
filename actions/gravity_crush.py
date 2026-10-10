from actions._types import Action, Field, Literal, Live, Players, preset


# The script's push is bent (its staff's mode picks the way, so it has no push_way value):
# the action takes push_strength alone, not preset("push").
class GravityCrush(Action, preset("around"), preset("freeze")):
    """Give a player a gravity crush staff: right-click to slam every mob round where he looks to the ground and pin it there, flyers too."""

    script = "gravity_staff"
    runs = "until_stopped"
    takes = ["player", "around_radius", "around_what", "freeze_seconds", "range", "cooldown"]
    defaults = {"around_radius": 40}
    fixed = {"mode": "crush", "item": "Gravity Crush Staff"}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    item: str = "Gravity Staff"
    """the name of the item in the hand"""
    mode: Live[Literal["crush", "lift", "pull", "push"]] = "crush"
    """what a click does to them: crush, slam to the ground and pin there for freeze_seconds; lift, throw up; pull, toward the spot; push, away from it"""
    range: Live[int] = Field(30, ge=4, le=300)
    """how far away he can aim, in blocks: the spot is where his look meets a block, or this far"""
    cooldown: Live[float] = Field(0, ge=0, le=60)
    """seconds between uses"""
    push_strength: Live[float] = Field(30, ge=0.5, le=40)
    """for lift, pull and push: about how far it moves each one, in blocks: a mob is thrown, a player moved there at once, or floated up"""
