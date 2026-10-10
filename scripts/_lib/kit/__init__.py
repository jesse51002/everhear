"""The kit: what every script and preset imports and nobody copies, written once for
Minecraft Java 26.3 and tested once.

    from kit import values, items, aim, area, mobs, fx, world, exclude, loop

values(VALUES) and loop(...) are called; the others are modules of functions. Each module's
docstring says what it is for and shows it in use. Everything a kit function makes (an item
given, a bossbar, a forceload, a gamerule changed, an objective) is put back when the script
stops, before mc kills @e[tag=TAG].
"""
from . import world, exclude, values, loop, items, aim, area, mobs, fx  # noqa: F401
