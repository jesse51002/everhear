"""When a player crouches (sneaks): while they hold it, on each tap, or on three quick taps (crouch three times), with an item in hand or not."""
import time

import mc
from kit import items, loop
from actions._types import Field, ItemName, Literal, Live, Values  # the values class: read on the PC only


class Sneak(Values):
    sneak_mode: Live[Literal["while", "tap", "three_taps"]] = "while"
    """while: about ten times a second as long as they crouch; tap: each crouch; three_taps: three within sneak_seconds"""
    sneak_seconds: Live[float] = Field(1.5, ge=0.3, le=5)
    """for three_taps: the seconds the three crouches must come within"""
    sneak_item: Live[ItemName] = ""
    """only while they hold the item of this name a script gave ("Spirit Bomb"); empty: whatever is in hand"""


def sneak(player, v):
    """Yield a player's name (of those `player` names: "@a", "@r" or names) when they sneak:
    "while" on every pass, about ten a second, as long as they crouch; "tap" once each time
    they crouch; "three_taps" when they crouch three times within sneak_seconds. With
    sneak_item set (an item's name, as items.clicks gives it) only while they hold it.
    Only crouching counts, never a click. Each player is followed on their own.

        for p in loop(sneak(v["player"], v)):
            ...
    """
    stat = items.Stat(*items.SNEAK)     # this generator's own look at the shared sneak_time objective
    down, taps = {}, {}                 # per player: crouched at the last look; when their taps came
    while not mc.stopping():
        people = loop.who(player)
        if not people:
            stat.forget()
            down.clear()
            taps.clear()
            time.sleep(1.0)
            continue
        name = v["sneak_item"]
        hold = "if items entity @s weapon.* %s" % items.match(name) if name else ""
        got, now, mode, due = stat.changes(hold), time.time(), v["sneak_mode"], []
        for p in people:
            crouched, was = got.get(p, 0) > 0, down.get(p, False)
            down[p] = crouched
            if mode == "while":
                if crouched:
                    due.append(p)
            elif crouched and not was:                          # a tap: they went down since the last look
                if mode == "tap":
                    due.append(p)
                    continue
                recent = [t for t in taps.get(p, []) if now - t <= v["sneak_seconds"]] + [now]
                taps[p] = [] if len(recent) >= 3 else recent
                if len(recent) >= 3:
                    due.append(p)
        for p in due:
            yield p
        if due:
            stat.changes(hold)          # crouching while the loop's body ran does not count
        time.sleep(0.1)
