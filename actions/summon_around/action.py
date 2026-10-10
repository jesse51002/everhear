from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, field_validator

from actions._types import Action, MobId, PlayerOrAll


class SummonAround(Action):
    """Summon a number of one mob round each player, spread to safe spots, optionally scaled, named or glowing.

    The mobs appear at the player and are scattered within the radius onto
    ground that is not water, lava or fire, ten at a time. For a few mobs at an
    exact spot, the command action's summon is enough. The report says how
    many stand and round whom, with the coordinates.
    """

    timeout_s: ClassVar[int] = 60

    action: Literal["summon_around"]
    players: PlayerOrAll = Field(
        description="round whom: one player's exact name, or @a for every player online")
    mob: MobId = Field(description="the mob's id, such as zombie, creeper or cow")
    count: int = Field(10, ge=1, le=100, description="how many round each player, 1 to 100")
    radius: int = Field(16, ge=4, le=64, description="how far from the player they may land, in blocks, 4 to 64")
    scale: float = Field(1.0, ge=0.25, le=8.0,
                         description="their size: 1 is normal, 2 twice as big, 0.5 half")
    name: str = Field("", max_length=32, description="a name shown above each one, or empty for none")
    glowing: bool = Field(False, description="true to make them glow, visible through walls")

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ValueError("a name is plain text on one line")
        return v
