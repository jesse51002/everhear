from actions._types import Action, Field, Live


class DemonLord(Action):
    """A game master, the Demon Lord, that sends waves of mobs, minigames and boss fights at everyone, round after round, harder as you win."""

    script = "demon_lord"
    runs = "until_stopped"
    takes = ["round", "level", "break_s"]

    round: int = Field(1, ge=1, le=1000)
    """the round it starts at; the rounds repeat every 16: 12 and 16 are a boss, 4 and 8 a minigame"""
    level: float = Field(0, ge=0, le=1000)
    """the difficulty it starts at (0: the round's own); it rises after a round nobody died in, and falls after many deaths"""
    break_s: Live[float] = Field(60, ge=0, le=600)
    """the break between rounds, in seconds"""
