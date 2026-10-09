from __future__ import annotations

import math
import re
from typing import ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from actions._types import ID_PATTERN, PLACE_PATTERN, Action, PlayerOrAll

_NUM = r"-?\d+(?:\.\d+)?"
_COORDS = re.compile(r"^(%s)[ ,]+(%s)(?:[ ,]+(%s))?$" % (_NUM, _NUM, _NUM))
_DIMS = {"overworld": "overworld", "nether": "the_nether", "the_nether": "the_nether",
         "end": "the_end", "the_end": "the_end"}
BORDER = 29_999_000


class Teleport(Action):
    """Move a player, or everyone, to a biome, a structure, a saved place, coordinates, far away, their spawn point or where they last died, landing on safe ground.

    To another player it is one `tp`, which the command action does: do not use
    this for that. The handler finds the place, gives Resistance and Slow
    Falling for 5 seconds, lands the player with spreadplayers within 8 blocks
    of it, and lands again further out (16, 32, 64) while the ground is magma,
    cactus, powder snow, a campfire, berries, dripstone, water or lava. A move
    over 1,000 blocks sets the player's spawn point where they land. With @a,
    the first player online lands and everyone else is teleported onto them.
    The report says who went where, with the coordinates.
    """

    timeout_s: ClassVar[int] = 60

    action: Literal["teleport"]
    player: PlayerOrAll = Field(
        description="who moves: one player's exact name, or @a for everyone online")
    to: Literal["biome", "structure", "place", "coords", "far", "spawn_point", "last_death"] = Field(
        description="what kind of destination: biome (the nearest one), structure (the nearest one, "
                    "such as a village), place (a saved place by name), coords (given x and z), far (a "
                    "random spot that many blocks away), spawn_point (where the player respawns: their "
                    "bed, else the world spawn), last_death (where the player last died)")
    target: str = Field(
        "",
        description="a biome or structure id, a #tag allowed; a saved place's name; 'x z' for coords; "
                    "a distance in blocks for far; empty for spawn_point and last_death")
    dimension: Literal["overworld", "the_nether", "the_end"] = Field(
        "overworld",
        description="the dimension to look in, for biome, structure, coords and far; a saved place, "
                    "a spawn point and a death already know theirs")

    @field_validator("dimension", mode="before")
    @classmethod
    def _dim(cls, v: object) -> object:
        if isinstance(v, str):
            d = v.strip().lower()
            if d.startswith("minecraft:"):
                d = d[len("minecraft:"):]
            return _DIMS.get(d, d)
        return v

    @model_validator(mode="after")
    def _target(self) -> "Teleport":
        t = (self.target or "").strip()
        if self.to in ("biome", "structure"):
            t = t.lower().replace(" ", "_")
            if not re.match(ID_PATTERN, t):
                raise ValueError("target: a %s id such as snowy_taiga or #village" % self.to)
            tag = t.startswith("#")
            t = t.lstrip("#")
            if t.startswith("minecraft:"):
                t = t[len("minecraft:"):]
            t = ("#" if tag else "") + t
        elif self.to == "place":
            t = re.sub(r"[\s-]+", "_", t.lower())
            if not re.match(PLACE_PATTERN, t):
                raise ValueError("target: a saved place's name, 1 to 32 of a-z, 0-9 and _")
        elif self.to == "coords":
            m = _COORDS.match(t)
            if not m:
                raise ValueError("target: 'x z' (or 'x y z'), whole numbers")
            nums = [math.floor(float(g)) for g in m.groups() if g is not None]
            x, z = nums[0], nums[-1]
            if abs(x) > BORDER or abs(z) > BORDER or (len(nums) == 3 and not -64 <= nums[1] <= 320):
                raise ValueError("target: coordinates outside the world")
            t = " ".join(str(n) for n in nums)
        elif self.to == "far":
            if not t:
                t = "1000"
            if not re.match(r"^\d+$", t) or not 1 <= int(t) <= 1_000_000:
                raise ValueError("target: a distance in blocks, 1 to 1000000")
            t = str(int(t))
        else:
            t = ""
        self.target = t
        return self
