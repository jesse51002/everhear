"""World settings: gamerules, time and weather, held by priority (the game engine spec, "Actors,
players and scenarios", World settings).

A hold is (holder, setting, value, priority). The highest priority wins, and on a tie the most
recent. When a holder lets go, the next one's value takes over; when none is left, the setting
goes back to what it was before the first hold, read with `Io.read_gamerule` just before that
hold first reaches Minecraft. A value that failed to apply is sent again next tick.

`weather` is held the same way (`weather <value>`: clear, rain or thunder), and read before its first
hold with two `execute if predicate` weather checks (raining, thundering), so it is put back too.
`time` (`time set <value>`) cannot be read on 26.3 in a form `time set` takes back, so when the last
hold on it goes, nothing is put back: the day carries on from where it is.
"""

from __future__ import annotations

import logging

from .io import Command, Gamerule

log = logging.getLogger("vfg_engine")

UNREADABLE = ("time",)
WEATHER_CHECK = 'execute if predicate {type:"minecraft:weather_check",%s:true}'


def read_weather(query) -> str | None:
    """"thunder", "rain" or "clear", from two weather checks (`query` runs one command). A reply
    that is neither "Test passed" nor "Test failed" (an error, a refusal) is no reading: ValueError,
    in words, so the caller tries again rather than take the weather for clear."""
    def passed(what):
        reply = str(query(WEATHER_CHECK % what)).strip()
        if reply.lower().startswith("test passed"):
            return True
        if reply.lower().startswith("test failed"):
            return False
        raise ValueError("the weather check (%s) answered %r, neither 'Test passed' nor 'Test failed'"
                         % (what, reply[:120]))
    if passed("thundering"):
        return "thunder"
    return "rain" if passed("raining") else "clear"


def text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class WorldSettings:
    def __init__(self):
        # name -> {"original": str|None, "read": bool, "applied": str|None, "holds": [...]}
        self.settings: dict[str, dict] = {}
        self.seq = 0

    def hold(self, holder: str, name: str, value, priority: int, now: float):
        name = str(name).strip()
        s = self.settings.setdefault(name, {"original": None, "read": False, "applied": None, "holds": []})
        s["holds"] = [h for h in s["holds"] if h["holder"] != holder]
        self.seq += 1
        s["holds"].append({"holder": holder, "value": text(value), "priority": int(priority),
                           "since": now, "seq": self.seq})

    def release(self, holder: str, name: str | None = None):
        for key, s in self.settings.items():
            if name is None or key == name:
                s["holds"] = [h for h in s["holds"] if h["holder"] != holder]

    def release_prefix(self, prefix: str):
        """Every hold of a holder whose id starts with `prefix` (an instance and its runs)."""
        for s in self.settings.values():
            s["holds"] = [h for h in s["holds"]
                          if not (h["holder"] == prefix or h["holder"].startswith(prefix + "/"))]

    def winner(self, name: str) -> dict | None:
        holds = self.settings.get(name, {}).get("holds") or []
        return max(holds, key=lambda h: (h["priority"], h["seq"])) if holds else None

    def plan(self, read_gamerule, query=None) -> list:
        """The ops that put every setting at its winner's value (or back to its original).
        `query` runs one command (to read the weather before its first hold)."""
        ops = []
        for name, s in list(self.settings.items()):
            if not s["read"]:
                if not s["holds"]:
                    del self.settings[name]
                    continue
                if name not in UNREADABLE:
                    try:
                        if name == "weather":
                            s["original"] = read_weather(query) if query is not None else None
                        else:
                            s["original"] = read_gamerule(name)
                    except Exception as exc:   # tried again next tick
                        log.warning("could not read %s before holding it (tried again next tick): %s",
                                    "the weather" if name == "weather" else "gamerule " + name, exc)
                        continue
                    if s["original"] is None:
                        log.warning("gamerule %s has no value to read (unknown name?); it is held "
                                    "anyway, and nothing is put back when released", name)
                s["read"] = True
            w = self.winner(name)
            want = w["value"] if w else s["original"]
            if want is not None and want != s["applied"]:
                ops.append((name, want, _op(name, want)))
            elif not w:
                del self.settings[name]          # back where it started, nobody holding it
        return ops

    def took(self, name: str, value: str, ok: bool, error: str | None):
        s = self.settings.get(name)
        if s is None:
            return
        if ok:
            s["applied"] = value
            if not s["holds"] and (value == s["original"] or s["original"] is None):
                del self.settings[name]
        else:
            log.warning("world setting %s=%s did not take (%s); trying again next tick", name, value, error)

    def listing(self) -> list[dict]:
        out = []
        for name, s in sorted(self.settings.items()):
            w = self.winner(name)
            out.append({"name": name, "value": w["value"] if w else s["original"],
                        "original": s["original"],
                        "holds": [{"holder": h["holder"], "value": h["value"], "priority": h["priority"],
                                   "since": h["since"]}
                                  for h in sorted(s["holds"], key=lambda h: (-h["priority"], -h["seq"]))]})
        return out

    def to_dict(self) -> dict:
        return {"seq": self.seq, "settings": self.settings}

    def load(self, doc):
        doc = doc or {}
        self.seq = int(doc.get("seq", 0))
        self.settings = dict(doc.get("settings") or {})


def _op(name: str, value: str):
    if name == "time":
        return Command("time set %s" % value)
    if name == "weather":
        return Command("weather %s" % value)
    return Gamerule(name, value)
