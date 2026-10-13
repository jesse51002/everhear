"""World settings: gamerules, time and weather, held by priority (the game engine spec, "Actors,
players and scenarios", World settings).

A hold is (holder, setting, value, priority). The highest priority wins, and on a tie the most
recent. When a holder lets go, the next one's value takes over; when none is left, the setting
goes back to what it was before the first hold, read with `Io.read_gamerule` just before that
hold first reaches Minecraft. A value that failed to apply is sent again next tick.

`time` and `weather` are held the same way (`time set <value>`, `weather <value>`), but 26.3 has
no way to read either back, so when the last hold on one goes, nothing is put back: the day and
the weather carry on from where they are.
"""

from __future__ import annotations

import logging

from .io import Command, Gamerule

log = logging.getLogger("vfg_engine")

UNREADABLE = ("time", "weather")


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

    def plan(self, read_gamerule) -> list:
        """The ops that put every setting at its winner's value (or back to its original)."""
        ops = []
        for name, s in list(self.settings.items()):
            if not s["read"]:
                if not s["holds"]:
                    del self.settings[name]
                    continue
                if name not in UNREADABLE:
                    try:
                        s["original"] = read_gamerule(name)
                    except Exception as exc:   # tried again next tick
                        log.warning("could not read gamerule %s before holding it: %s", name, exc)
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
