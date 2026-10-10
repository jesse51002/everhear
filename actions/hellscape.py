from actions._types import Action, Field, Literal, Players, preset


class Hellscape(Action, preset("replace_blocks")):
    """Turn the land round the players into the Nether, once: netherrack and magma, lava for water, fire for grass, burnt trunks, soul sand, nothing green."""

    script = "hellscape"
    runs = "once"
    takes = ["player", "radius", "height"]
    defaults = {"replace_rules": [
        "water > lava",
        "#minecraft:leaves > air",
        "#minecraft:logs > basalt",
        "#minecraft:replaceable_by_trees #minecraft:flowers #minecraft:crops #minecraft:saplings > fire",
        "grass_block podzol mycelium mud moss_block #minecraft:dirt farmland dirt_path > netherrack",
        "#minecraft:base_stone_overworld > netherrack",
        "#minecraft:sand > soul_sand",
        "gravel clay > soul_soil",
        "#minecraft:snow > air",
        "#minecraft:ice > lava",
        "netherrack > 92% keep, 5% magma_block, 3% blackstone",
    ]}
    timeout_s = 120

    player: Players = "@a"
    """round whom the land turns: a name, several separated by commas, or @a"""
    radius: int = Field(48, ge=1, le=512)
    """how far round each player it turns, in blocks each way; only loaded chunks change"""
    height: int = Field(32, ge=0, le=384)
    """how far above and below their feet it turns, in blocks (0: the world's whole height)"""
    dims: list[Literal["overworld", "the_nether", "the_end"]] = ["overworld"]
    """the dimensions it changes, round the players who are in them"""
