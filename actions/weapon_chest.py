from actions._types import Action, Field, Live, Place, Players, preset


class WeaponChest(Action, preset("damage_hits"), preset("lightning"), preset("around"), preset("projectile_lands"),
                  preset("explosion"), preset("freeze"), preset("push"), preset("launch")):
    """A chest of six weapons with powers beside us: an exploding bow, a freezing bow, a lightning trident, a lightning sword, a blast axe and a lifting mace."""

    script = "weapon_chest"
    runs = "until_stopped"
    takes = ["player", "per_player", "at", "damage_amount", "explosion_power"]
    defaults = {"damage_amount": 10, "damage_type": "lightning_bolt", "around_radius": 4,
                "projectile_lands_named": ["Hellfire Longbow", "Glacier Bow", "Stormcaller"],
                "explosion_power": 3, "explosion_breaks": False, "freeze_seconds": 6, "push_way": "up",
                "push_strength": 8}

    player: Players = "@a"
    """who the chest is for, and whose weapons from it have their powers: a name, several separated by commas, or @a"""
    per_player: bool = False
    """a chest beside each of them; off: one chest, beside the first of them, for everyone"""
    at: Place = ""
    """where the one chest goes: a saved place or x y z, there; a player's name, beside him; empty: beside the first player"""
    cooldown: Live[float] = Field(1, ge=0, le=60)
    """seconds between right-clicks of the sword, the axe and the mace"""
    range: Live[int] = Field(120, ge=4, le=300)
    """how far the sword, the axe and the mace reach where he looks, in blocks"""
