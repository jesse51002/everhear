from actions._types import Action, Field, Live, Players, preset


class GraveMarks(Action, preset("on_death")):
    """Mark where I died with a skull and my name, and say where in chat."""

    script = "grave_marks"
    runs = "until_stopped"
    takes = ["player", "seconds"]

    player: Players = "@a"
    """whose deaths it marks: a name, several separated by commas, or @a"""
    seconds: Live[float] = Field(30, ge=5, le=3600)
    """how long a mark stays, in seconds; it fades sooner once its player walks back to it"""
    found_within: Live[float] = Field(3, ge=0, le=16)
    """how near a player must come back to their mark, in blocks, for it to fade before its time"""
