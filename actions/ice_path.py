from actions.ice_walk import IceWalk


class IcePath(IceWalk):
    """Ice under a player's feet wherever they walk: a path of packed ice two blocks each way, nothing above or below it."""

    takes = ["player", "flash"]
    fixed = {"under_radius": 2, "under_depth": 1, "under_above": 0}
