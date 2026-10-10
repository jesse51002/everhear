from actions._types import Action, Field, Live, Players, preset


class ChunkChanger(Action, preset("replace_blocks"), preset("enters_chunk")):
    """Change blocks by rules in every chunk a player walks into, once each, as they go: all the snow to random blocks unless told what."""

    script = "chunk_changer"
    runs = "until_stopped"
    takes = ["player", "replace_rules", "around", "replace_rate"]
    defaults = {"replace_rules": [
        "#minecraft:snow > white_wool light_gray_wool red_wool blue_wool lime_wool yellow_wool cyan_wool "
        "purple_wool orange_wool pink_wool white_concrete light_blue_concrete green_concrete "
        "magenta_concrete terracotta orange_terracotta brown_terracotta oak_planks spruce_planks "
        "dark_oak_planks stone andesite granite diorite stone_bricks deepslate quartz_block glass "
        "coal_ore lapis_ore"]}

    player: Live[Players] = "@a"
    """whose chunks change as they walk: a name, several separated by commas, or @a"""
    around: Live[int] = Field(1, ge=0, le=3)
    """how many chunks round the one entered change with it, each way (1: the eight round it too, so no seam shows)"""
