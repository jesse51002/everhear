from actions._types import Action, Field, Live, Players, preset


class IceBeamCast(Action, preset("freeze"), preset("replace_blocks"), preset("theme_music"), preset("damage_hits")):
    """Ice Beam: say "ice beam" and fire an ice beam where you look, freezing and blinding everything it hits and turning the blocks along it to ice."""

    script = "ice_beam_cast"
    runs = "once"
    takes = ["player", "width", "length", "freeze_seconds", "damage_amount", "damage_what"]
    defaults = {"freeze_seconds": 10, "freeze_blindness": True, "replace_rules": ["solid water lava > packed_ice"], "replace_radius": 2, "theme_tune": "ice_beam", "damage_amount": 0, "damage_type": "freeze"}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    item: str = "Ice Beam Staff"
    """the name of the item in the hand"""
    cooldown: Live[float] = Field(0.5, ge=0, le=60)
    """seconds between shots"""
    length: Live[int] = Field(80, ge=4, le=300)
    """how far the beam reaches, in blocks"""
    width: Live[float] = Field(5, ge=1, le=16)
    """how wide the beam is, in blocks: everything within half of it from its middle is frozen"""
    through_walls: Live[bool] = True
    """whether the beam passes through blocks; off, it stops at the first"""
    tunnel: Live[bool] = True
    """break a tunnel three blocks across along the beam, inside the blocks it turns to ice (replace_radius)"""
    path: Live[bool] = True
    """lay a path of packed ice under the beam, three wide and two blocks below it, where there is air, water or lava"""
