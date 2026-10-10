"""Each chunk a player walks into, once, as they go (change blocks chunk by chunk, everywhere they walk): the player and the chunk's box, in every dimension."""
import mc
from kit import area, loop, world
from actions._types import Field, Literal, Live, Values  # the values class: read on the PC only

VALUES = {
    "enters_dims": {"type": "list", "of": ["overworld", "the_nether", "the_end"],
                    "default": ["overworld", "the_nether", "the_end"], "live": True},
    "enters_below": {"type": "int", "min": 0, "max": 384, "default": 32, "live": True},
    "enters_above": {"type": "int", "min": 0, "max": 384, "default": 40, "live": True},
}


class EntersChunk(Values):
    enters_dims: Live[list[Literal["overworld", "the_nether", "the_end"]]] = ["overworld", "the_nether", "the_end"]
    """the dimensions it works in"""
    enters_below: Live[int] = Field(32, ge=0, le=384)
    """how far down each chunk is taken, in blocks under the player's feet"""
    enters_above: Live[int] = Field(40, ge=0, le=384)
    """how far up each chunk is taken, in blocks over the player's feet"""


def enters_chunk(player, v):
    """For each player a player value names ("@a", "Steve,Alex"), yield (name, the world.Box
    of the chunk they are in, from enters_below blocks under their feet to enters_above over
    them) the first time anyone is in that chunk of that dimension, in the dimensions
    enters_dims names. A chunk is done once for as long as the script runs.

        for p, box in loop(enters_chunk(v["player"], v)):
            todo.put(box)           # a queue.Queue that a loop.background worker empties

    Nobody is looked at while the loop's body runs, so a slow change (seconds, with a mix)
    done in the loop itself misses the chunks walked through meanwhile: hand it to a worker,
    as chunk_changer does."""
    done = set()
    while not mc.stopping():
        for p in loop.who(player):
            at, dim = mc.pos(p), world.dim_of(p)
            if not at or (dim or world.OVERWORLD).split(":")[-1] not in v["enters_dims"]:
                continue
            cx, cz = area.chunk_of(at[0], at[2])
            if (dim, cx, cz) in done:
                continue
            done.add((dim, cx, cz))
            y1, y2 = area.band(at[1], v["enters_below"], v["enters_above"], dim)
            yield p, area.chunk_box(cx, cz, y1, y2, dim)
        loop.wait(0.5)
