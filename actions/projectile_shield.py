from actions._types import Action, Live, Players, preset


class ProjectileShield(Action, preset("around"), preset("push")):
    """Make arrows, tridents and fireballs bounce off a player: each turns round a few blocks from him and flies back; mobs still come close."""

    script = "projectile_shield"
    runs = "until_stopped"
    takes = ["player", "around_radius", "push_strength"]
    defaults = {"around_what": "projectiles", "push_strength": 8}

    player: Live[Players] = "@a"
    """who is shielded: a name, several separated by commas, or @a"""
