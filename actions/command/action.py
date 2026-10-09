from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from actions._types import Action, clean_command


class Command(Action):
    """Run vanilla commands, in order: anything that takes effect at once — items, effects, time, weather, killing or summoning a few mobs, attributes, game rules, sounds, titles, looking up where someone is. Never places or removes blocks.

    Up to five commands, run one after another in the order written, each on one
    line and without a leading slash. Minecraft's own replies are the result: a
    reply it marks as an error ("Unknown item", "No player was found",
    "Incorrect argument for command") stops the list there and comes back to
    you. setblock, fill, clone, place and fillbiome are refused, even after
    `execute ... run`: building is the orchestrator's. So are the owner's
    commands: stop, op, deop, ban, pardon, whitelist, kick and save-off.
    """

    action: Literal["command"]
    commands: list[str] = Field(
        min_length=1, max_length=5,
        description="the server commands to run, in the order they run, without a leading slash, "
                    "for example 'give Steve minecraft:golden_apple 64'; at most five, each under 1400 characters")

    @field_validator("commands")
    @classmethod
    def _each(cls, v: list[str]) -> list[str]:
        return [clean_command(c) for c in v]
