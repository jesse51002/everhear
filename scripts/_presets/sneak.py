"""When a player sneaks: on every pass while they crouch, on each tap of the key, or on three quick taps."""
import time

import mc
from kit import items, loop

VALUES = {
    "sneak_mode": {"type": "enum", "of": ["while", "tap", "three_taps"], "default": "while", "live": True},
    "sneak_seconds": {"type": "number", "min": 0.3, "max": 5, "default": 1.5, "live": True},
    "sneak_item": {"type": "str", "default": "", "live": True},
}


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
    down, taps, scored = {}, {}, set()  # per player: crouched at the last look; when their taps came
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
        if not scored.issuperset(people):
            # Everyone gets a score (adding 0 changes none): a player with none yet makes
            # items.Stat misread the name after theirs in the same reply.
            mc.rcon("scoreboard players add @a %s 0" % stat.objective)
            scored.update(people)
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
