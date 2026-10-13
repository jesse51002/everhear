"""The engine's settings, from the environment (/opt/vfg/.env on the box; DESIGN, "The box").

VFG_ENGINE_TICK_HZ      ticks a second (10; DESIGN, Decided 2)
VFG_ENGINE_SLOW_MS      a content call over this is logged as slow (5; Decided 3)
VFG_ENGINE_CUT_MS       a content call over this is cut (50; Decided 3)
VFG_ENGINE_CONTENT      content folders, colon-separated (/opt/vfg/made/main/content)
VFG_ENGINE_STATE        the saved state (/opt/vfg/engine/state.json)
VFG_ENGINE_EXPORT       the incantation export the bridge reads (/opt/vfg/engine/incantations.json)
VFG_ENGINE_STALE_S      a use, hit, hurt or kill older than this is not acted on (5; vfg_engine.intake)
VFG_PROTECTED           the protected boxes no block job touches (/opt/vfg/protected.json; vfg_engine.blocks)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

BOX_CONTENT = "/opt/vfg/made/main/content"
BOX_STATE = "/opt/vfg/engine/state.json"
BOX_EXPORT = "/opt/vfg/engine/incantations.json"
BOX_PROTECTED = "/opt/vfg/protected.json"


@dataclass(frozen=True)
class Settings:
    tick_hz: float = 10.0
    slow_ms: float = 5.0
    cut_ms: float = 50.0
    content: tuple[str, ...] = ()
    state: str | None = None          # None: nothing is saved (tests)
    export: str | None = None         # None: no export file
    save_every_s: float = 1.0         # a crash loses at most about this much
    check_every_s: float = 5.0        # how often the modifiers on everyone are read back (drift)
    log_keep: int = 1000              # lines the `log` op can return
    stale_s: float = 5.0              # player input older than this is not acted on (intake)
    protected: str | None = None      # the protected boxes' file (None: nothing protected)

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        env = os.environ if env is None else env

        def number(name, default):
            raw = env.get(name)
            if raw in (None, ""):
                return default
            try:
                value = float(raw)
            except ValueError:
                raise ValueError("%s=%r is not a number" % (name, raw)) from None
            if value <= 0 and name == "VFG_ENGINE_TICK_HZ":
                raise ValueError("VFG_ENGINE_TICK_HZ must be above 0, not %r" % raw)
            return value

        content = tuple(p for p in (env.get("VFG_ENGINE_CONTENT") or BOX_CONTENT).split(":") if p)
        return cls(tick_hz=number("VFG_ENGINE_TICK_HZ", 10.0),
                   slow_ms=number("VFG_ENGINE_SLOW_MS", 5.0),
                   cut_ms=number("VFG_ENGINE_CUT_MS", 50.0),
                   content=content,
                   stale_s=number("VFG_ENGINE_STALE_S", 5.0),
                   protected=env.get("VFG_PROTECTED") or BOX_PROTECTED,
                   state=env.get("VFG_ENGINE_STATE") or BOX_STATE,
                   export=env.get("VFG_ENGINE_EXPORT") or BOX_EXPORT)
