from actions.castle import Castle


class IceCastle(Castle):
    """Build an ice castle in front of a player or at a place: packed ice walls and keep, blue ice towers."""

    takes = ["player", "castle_size", "castle_sign", "castle_stairs", "castle_ahead", "castle_at"]
    fixed = {"shape_blocks": "packed_ice", "castle_tower_blocks": "blue_ice"}
