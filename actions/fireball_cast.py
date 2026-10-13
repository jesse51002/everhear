from actions._types import Action, Field, Live, Players, preset


class FireballCast(Action, preset("projectile"), preset("explosion")):
    """Fire a fireball from a player's eyes where he looks, at once, exploding on the first mob or block it hits: the one to bind to a spoken incantation ("fireball")."""

    script = "fireball_cast"
    runs = "once"
    takes = ["player", "explosion_power", "explosion_breaks", "reach", "projectile_speed"]
    defaults = {"projectile_kind": "block", "projectile_block": "magma_block", "projectile_size": 1,
                "projectile_speed": 40, "projectile_particle": "flame", "explosion_power": 3,
                "explosion_layout": "one"}

    player: Players = "@a"
    """who fires it, from his eyes where he looks: the caster's name, several separated by commas, or @a"""
    reach: Live[int] = Field(80, ge=4, le=300)
    """how far it flies at most, in blocks; it stops at the first mob or block on the way"""
    fireball_width: Live[float] = Field(1.2, ge=0.2, le=8)
    """how close to its line a mob must be to be hit, in blocks"""
