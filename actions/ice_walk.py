from actions._types import Action, Live, Players, preset


class IceWalk(Action, preset("walk_steps"), preset("under_feet"), preset("replace_blocks")):
    """Turn everything round a player into packed ice as they walk, ten blocks each way: the ground, buried blocks, what is above them, water and lava too."""

    script = "ice_walk"
    runs = "until_stopped"
    takes = ["player", "under_radius", "under_depth", "under_above", "flash"]
    defaults = {"walk_every": 2, "under_radius": 10, "under_depth": 11, "under_above": 11,
                "replace_rules": ["solid water lava bubble_column kelp kelp_plant #minecraft:replaceable_by_trees "
                                  "#minecraft:flowers #minecraft:crops #minecraft:saplings > packed_ice"]}

    player: Live[Players] = "@a"
    """who freezes the world as they walk: a name, several separated by commas, or @a"""
    flash: Live[bool] = True
    """a landing after a fall of three blocks or more flashes like lightning, with thunder; it does no harm"""
