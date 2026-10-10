from actions._types import Action, Field, Live, Players, preset


class KillFx(Action, preset("on_death")):
    """Kill streak sounds, hit markers and a thud when hurt, each player hearing their own: a chime per kill, rising with the streak, and a sting when they die."""

    script = "kill_fx"
    runs = "until_stopped"
    takes = ["player", "on_death_event", "hit_markers", "hurt_sound", "volume"]
    defaults = {"on_death_event": "both"}

    player: Players = "@a"
    """who hears them: a name, several separated by commas, or @a"""
    streak_s: Live[float] = Field(8, ge=1, le=60)
    """kills this many seconds apart or closer make a streak, each chime higher; three or more add a twinkle"""
    hit_markers: Live[bool] = True
    """a hit marker sound each time the player deals damage"""
    hurt_sound: Live[bool] = True
    """a thud each time the player takes damage"""
    volume: Live[float] = Field(1, ge=0.1, le=4)
    """how loud every sound is: 1 normal, 2 twice as loud"""
