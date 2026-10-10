from actions._types import Action, Field, Players


class LuckyBlocks(Action):
    """Give us lucky blocks: breaking one gives a random reward, or springs a trap."""

    script = "lucky_blocks"
    runs = "until_stopped"
    takes = ["player", "give"]

    player: Players = "@a"
    """who gets them, and whose breaking counts: a name, several separated by commas, or @a"""
    give: int = Field(3, ge=0, le=64)
    """how many lucky blocks (sponges named Lucky Block) each of them gets at the start (0: none)"""
