"""A countdown in chat and big titles (detonation in ten minutes), then the script goes on: True at zero, False if it was stopped first."""
import json
import math
import time

import mc
from kit import fx, loop
from actions._types import Field, Live, Players, Values  # the values class: read on the PC only


class Countdown(Values):
    countdown_seconds: int = Field(10, ge=1, le=3600)
    """how long it counts down, in seconds"""
    countdown_text: Live[str] = "Detonation"
    """what it counts down to, in chat ("Detonation in 5m 00s.") and under the big numbers of the last ten seconds"""
    countdown_to: Live[Players] = "@a"
    """who sees and hears it: @a, or names separated by commas"""


def countdown(v):
    """Count countdown_seconds down for countdown_to ("@a", or names): chat says
    "<countdown_text> in 5m 00s." at the start and at 10 and 5 minutes, 2, 1, 30 and
    10 seconds left; the last ten seconds each show a title and tick. Returns True at zero,
    with nothing shown for it (what comes next is the script's), or False if the script
    stops first, and chat then says it was called off."""
    seconds = v["countdown_seconds"]
    end = time.time() + seconds
    going = [True]
    mc.on_stop(lambda: going[0] and _countdown_say(v, "%s called off." % v["countdown_text"], "green"))
    _countdown_say(v, "%s in %s." % (v["countdown_text"], _countdown_span(seconds)))
    said = set(m for m in (600, 300, 120, 60, 30, 10) if m >= seconds)    # chat already said
    shown = set()                                                         # titles already shown
    while True:
        left = end - time.time()
        if left <= 0:
            going[0] = False
            return True
        for m in (600, 300, 120, 60, 30, 10):
            if m not in said and left <= m:
                said.add(m)
                _countdown_say(v, "%s in %s." % (v["countdown_text"], _countdown_span(m)))
        n = int(math.ceil(left))
        if n <= 10 and n not in shown:
            shown.add(n)
            for to in _countdown_who(v):
                fx.title(to, str(n), sub=v["countdown_text"], color="red", fade=(0, 25, 5))
                fx.sound("block.note_block.bass", None, to=to, volume=2, pitch=0.5)
                fx.sound("block.note_block.bit", None, to=to, volume=2, pitch=2.0 if n > 3 else 1.0)
        if not loop.wait(min(0.1, left)):
            return False


def _countdown_who(v):
    """Who sees it: "@a" as it is, else each of the named players online."""
    return ["@a"] if v["countdown_to"] == "@a" else loop.who(v["countdown_to"])


def _countdown_say(v, text, color="red"):
    for to in _countdown_who(v):
        mc.rcon("tellraw %s %s" % (to, json.dumps({"text": text, "color": color, "bold": True})))


def _countdown_span(s):
    """Seconds as "5m 00s" or "30s"."""
    return "%dm %02ds" % (s // 60, s % 60) if s >= 60 else "%ds" % s
