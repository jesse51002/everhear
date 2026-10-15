"""vfg_engine.mc: the engine's connection to Minecraft 26.3 (the game engine spec, "the datapack",
"Reading the world without waiting" and the writer): McIo, the real vfg_engine.io.Io, over one RCON
connection to a vanilla server that has the EverHear datapack (engine/datapack/everhear).

    from vfg_engine.mc import McIo
    io = McIo(); io.open()

Modules: rcon (the connection and the box's RCON lock), snbt (reading `data get`), commands (each op
as its 26.3 command, each reply as an OpResult), reading (the datapack's compounds as the contract's
dataclasses), mirror (the second thread), datapack (the check at open), mcio (McIo). The 26.3 facts
they rely on are in engine/MINECRAFT.md. Standard library only, besides vfg_engine.io.
"""

from .commands import OpRefused
from .datapack import DatapackError
from .mcio import McIo
from .mirror import McMirror
from .rcon import Rcon, RconError

__all__ = ["McIo", "McMirror", "Rcon", "RconError", "DatapackError", "OpRefused"]
