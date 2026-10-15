"""Which base items an EverHear item may be made on, and which triggers each can report (the game
engine spec, "Items": "it should be impossible to have an item on the wrong base").

An EverHear item is a vanilla item (its base) carrying custom_data={everhear:{item:"<id>"}}. What
the engine can notice about it depends on the base:

- **held / unheld / carried**: every item. The snapshot reads each player's slots (the datapack's
  everhear:io/player), so any stack with an EverHear id is seen wherever it is.
- **hit**: every item. The datapack's hit advancement reads the attacker's main hand when a melee
  hit lands, so the hit carries the EverHear id held (or none).
- **use** (a right-click): only carrot_on_a_stick and warped_fungus_on_a_stick. Minecraft counts
  their uses on a scoreboard (minecraft.used:…), every click, with no animation, no cooldown and
  nothing used up (measured on 26.3: one Carpet use, +1). Other items do not report a click in the
  air. Measured and not taken: any base given a minecraft:consumable component reports through the
  using_item advancement, but only while held down (once per game tick), never a click shorter than
  a tick (Carpet's `use once` gave nothing), and the player is slowed while using it.

The reload check asks ``can_report(base, trigger)`` for each trigger an item answers, so an item
that listens for ``use`` on a base that can never report one is refused when loaded, not silently
dead in the game; ``why_not`` says why in words. A base that is not a 26.3 item at all is refused.

Standard library only.
"""

from __future__ import annotations

from .mc.items import ITEMS as _ITEMS

# The bases a right-click is reported for: the two "on a stick" items, through their used-item
# scoreboards (engine/datapack/README.md, "use").
USE_BY_SCOREBOARD = frozenset({
    "minecraft:carrot_on_a_stick",
    "minecraft:warped_fungus_on_a_stick",
})

# Triggers every base reports.
ANY_ITEM_TRIGGERS = frozenset({"held", "unheld", "hit"})

# Every base an EverHear item may use: every item in Minecraft 26.3 (vfg_engine/mc/items.py,
# generated from the server's registries), so a typo ("carrot_stick") never ships.
KNOWN_BASES = frozenset(_ITEMS)

TRIGGERS = frozenset({"use"}) | ANY_ITEM_TRIGGERS


def normal(base: str) -> str:
    """``carrot_on_a_stick`` and ``minecraft:carrot_on_a_stick`` are the same base."""
    base = (base or "").strip().lower()
    return base if ":" in base else "minecraft:" + base


def can_report(base: str, trigger: str) -> bool:
    """True when an EverHear item on ``base`` can report ``trigger`` (``use``, ``hit``, ``held``,
    ``unheld``). An unknown base reports nothing; an unknown trigger is never reported."""
    base = normal(base)
    if base not in KNOWN_BASES:
        return False
    if trigger in ANY_ITEM_TRIGGERS:
        return True
    if trigger == "use":
        return base in USE_BY_SCOREBOARD
    return False


def why_not(base: str, trigger: str) -> str | None:
    """None when ``can_report`` is true; otherwise the reason in words, for the reload check's error."""
    if can_report(base, trigger):
        return None
    base = normal(base)
    if base not in KNOWN_BASES:
        return "%s is not a Minecraft 26.3 item, so it cannot be an EverHear item's base" % base
    if trigger not in TRIGGERS:
        return "%r is not a trigger an item reports (known: %s)" % (trigger, ", ".join(sorted(TRIGGERS)))
    return "%s cannot report %r: only %s can" % (base, trigger, " and ".join(sorted(USE_BY_SCOREBOARD)))
