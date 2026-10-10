from actions._types import Action, Field, Literal, Live, ParticleId, Players, SoundId, preset


class Railgun(Action, preset("damage_hits")):
    """Give a player a railgun: click to fire a beam that hurts everything it passes, through walls."""

    script = "railgun"
    runs = "until_stopped"
    takes = ["player", "damage_amount", "cooldown", "range", "through_walls"]
    defaults = {"damage_amount": 30, "damage_type": "magic"}
    fixed = {"fire": "click"}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    item: str = "Railgun"
    """the name of the item in the hand"""
    fire: Literal["click", "hold"] = "click"
    """click fires once a click; hold fires every cooldown while the button is held"""
    cooldown: Live[float] = Field(2, ge=0, le=60)
    """seconds between shots"""
    range: Live[int] = Field(100, ge=4, le=300)
    """how far the beam reaches, in blocks"""
    through_walls: Live[bool] = True
    """whether the beam passes through blocks"""
    particle: Live[ParticleId] = "end_rod"
    """the particle the beam is drawn with"""
    sound: Live[SoundId] = "entity.firework_rocket.blast"
    """the sound of a shot, heard round the shooter"""
