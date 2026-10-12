from actions._types import Action, Field, Live, ParticleId, Players, SoundId


class SpeedDash(Action):
    """Speed Dash: say "speed dash" to launch yourself forward, in the direction you face, by a real knockback push; never a teleport or speed effect."""

    script = "speed_dash"
    runs = "once"
    takes = ["player", "dash_power"]

    player: Players = "@a"
    """who gets the dash: the one who said it"""
    dash_power: Live[float] = Field(1, ge=0.5, le=20)
    """how hard the launch hits, in half-hearts: a bigger hit throws you further"""
    dash_particle: Live[ParticleId] = "cloud"
    """the particle puffed round the player at each dash"""
    dash_sound: Live[SoundId] = "entity.firework_rocket.launch"
    """the sound of a dash, heard by the player"""
