"""The EverHear game engine (the game engine spec): what content imports.

A content file is one Python file under made/<server>/content/, holding a class of one of the five
kinds, and imports only from here:

    from vfg_engine import Ability, AbilityConfig, Field

The names load on first use (PEP 562), so `vfg_engine.io` and `vfg_engine.bases` import with the
standard library alone; the engine itself is `vfg_engine.engine.Engine`. How to write content is
engine/WRITING.md.
"""

from importlib import import_module

# name -> the module that defines it
_WHERE = {
    # the five kinds content writes
    "Item": "content", "Ability": "content", "Mark": "content", "Actor": "content",
    "Scenario": "content", "State": "content",
    # a Scenario's or a Mark's Args, their types, and reading a Players arg (DESIGN2 §1)
    "Args": "args", "Spot": "args", "Player": "args", "Players": "args", "Place": "args",
    "EntityType": "args", "BlockId": "args", "BlockMix": "args", "ItemId": "args",
    "ReplaceRules": "args", "Command": "args", "Seconds": "args", "players": "args",
    # their config bases, pydantic's Field for bounds, and the until-stopped duration
    "ItemConfig": "config", "AbilityConfig": "config", "MarkConfig": "config",
    "ActorConfig": "config", "ScenarioConfig": "config", "Field": "config",
    "UNTIL_STOPPED": "config",
    # the events a listen names
    "Event": "events", "Use": "events", "Hit": "events", "Hurt": "events", "Kill": "events",
    "Death": "events", "Respawn": "events", "Milk": "events", "Join": "events",
    "Leave": "events", "Held": "events", "Unheld": "events", "Sneak": "events",
    "Unsneak": "events", "Enter": "events", "Exit": "events", "Gone": "events", "Cast": "events",
    "Land": "events", "Mine": "events",
}

__all__ = sorted(_WHERE)


def __getattr__(name):
    module = _WHERE.get(name)
    if module is None:
        raise AttributeError("vfg_engine has no %r (content imports: %s)" % (name, ", ".join(__all__)))
    value = getattr(import_module("vfg_engine." + module), name)
    globals()[name] = value
    return value


def __dir__():
    return __all__
