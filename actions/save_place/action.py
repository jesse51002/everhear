from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from actions._types import Action, PlaceName, Player

_NUM = r"-?\d+(?:\.\d+)?"
_AT = re.compile(r"^(%s)[ ,]+(%s)[ ,]+(%s)$" % (_NUM, _NUM, _NUM))


class SavePlace(Action):
    """Save where a player stands, or given coordinates, under a name everyone can use.

    Give either the player or the coordinates, never both. Saving a name again
    moves it. Every later task, and every model, sees the saved places by name,
    so teleport can go there.
    """

    action: Literal["save_place"]
    name: PlaceName = Field(description="the place's name, short, lowercase, with _ for spaces: castle, home, zoo_entrance")
    player: Player | None = Field(None, description="save where this player stands, by their exact name")
    at: str = Field("", description="or save these coordinates instead, 'x y z' in whole numbers")

    @field_validator("at")
    @classmethod
    def _at(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return ""
        m = _AT.match(v)
        if not m:
            raise ValueError("'x y z', three numbers")
        return " ".join(str(math.floor(float(g))) for g in m.groups())

    @model_validator(mode="after")
    def _one(self) -> "SavePlace":
        if (self.player is None) == (not self.at):
            raise ValueError("give the player or the coordinates, not both and not neither")
        return self

    def vfg_argv(self, task: str) -> list[str]:
        if self.player is not None:
            return ["place", "save", self.name, "--player", self.player, "--by", task]
        x, y, z = self.at.split()
        return ["place", "save", self.name, "--at", x, y, z, "--by", task]
