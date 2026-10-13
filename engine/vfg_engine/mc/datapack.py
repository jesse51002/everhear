"""The check that the EverHear datapack is loaded, and is the version this engine reads (the game
engine spec, "the datapack"; McIo.open runs it).

The datapack keeps its version in storage (everhear:meta version, written by its load function);
engine/datapack/generate.py's VERSION is the same number, and engine/tests/mc checks the two agree.

Standard library only.
"""

from __future__ import annotations

import os

from . import snbt
from .reading import Tables

# everhear:meta's `version` this engine reads. Raised with engine/datapack/generate.py's VERSION.
VERSION = 2
NAME = "file/everhear"
# Where the pack is in the repo; on the box it is copied to /opt/mc/world/datapacks/everhear.
SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "datapack", "everhear")


class DatapackError(RuntimeError):
    """The datapack is missing, disabled, or another version; the message says what to do."""


def check(command) -> Tables:
    """Checks the pack with `command` (one RCON command -> its reply) and reads its tables.
    DatapackError, in words, when the engine cannot use it."""
    listed = command("datapack list enabled")
    if NAME not in listed:
        available = command("datapack list available")
        if NAME in available:
            raise DatapackError("the EverHear datapack is in the world but not enabled: "
                                "`datapack enable \"%s\"` (or restart the server)" % NAME)
        raise DatapackError("the EverHear datapack is not in the world: copy engine/datapack/everhear "
                            "to <world>/datapacks/everhear, then `reload`")
    try:
        version = snbt.data_reply(command("data get storage everhear:meta version"))
    except snbt.NoData:
        raise DatapackError("the EverHear datapack is enabled but its load function has not run "
                            "(everhear:meta has no version): `reload`, and read the server log") from None
    if version != VERSION:
        raise DatapackError("the EverHear datapack in the world is version %r; this engine reads "
                            "version %d: copy engine/datapack/everhear over it, then `reload`" % (version, VERSION))
    try:
        types = snbt.data_reply(command("data get storage everhear:meta types"))
        blocks = snbt.data_reply(command("data get storage everhear:meta blocks"))
    except (snbt.NoData, snbt.SnbtError) as e:
        raise DatapackError("the EverHear datapack's tables (everhear:meta types, blocks) cannot be "
                            "read: %s" % e) from None
    if not isinstance(types, list) or not isinstance(blocks, list):
        raise DatapackError("the EverHear datapack's tables are not lists")
    return Tables(types=[str(t) for t in types], blocks=[str(b) for b in blocks])
