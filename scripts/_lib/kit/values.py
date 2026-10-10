"""A script's values: v = values(VALUES).

Each value is its VALUES default, then what the command gave (key=value after the script's
name), then, for a value marked "live", what `vfg script set` wrote into the instance's live
file, /opt/vfg/run/<instance>.values.json, read again at most every VFG_LIVE_POLL_S (0.5) s.
Each is checked against its type and limits; a number out of range is clamped and logged; a
bad value is logged and the one before kept. Reading a name VALUES does not declare raises.

A live value handed to the kit as it is (items.clicks(..., cooldown=v["cooldown"])) stays
live: the kit reads it again each time (values.now). A live bool cannot do that: read v[...]
again on each pass.

VALUES entry keys: type (int, number, bool, enum, str, player, block, mob, item, particle,
sound, list), default (required), min and max (numbers), of (enum options; for a list, the
options or the type of each item), live (default False).
"""
import json
import os
import re
import sys
import threading
import time
import types
from collections.abc import Mapping

import mc
from . import world

TYPES = ("int", "number", "bool", "enum", "str", "player", "block", "mob", "item", "particle", "sound", "list")
KEYS = ("type", "default", "min", "max", "of", "live")

_ID = r"(?:[a-z0-9_.-]+:)?[a-z0-9_./-]+"
_PATTERNS = {
    "block": re.compile(r"^#?%s(\[[^\]]*\])?(\{.*\})?$" % _ID),
    "mob": re.compile(r"^%s$" % _ID),
    "item": re.compile(r"^#?%s(\[.*\])?$" % _ID),
    "particle": re.compile(r"^%s(\{.*\})?$" % _ID),
    "sound": re.compile(r"^%s$" % _ID),
}
_NAME = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_TRUE = ("true", "1", "yes", "on", "y")
_FALSE = ("false", "0", "no", "off", "n")


def _plain(raw):
    return raw.strip() if isinstance(raw, str) else raw


def _one(kind, raw, spec, key):
    """One value of a kind other than list."""
    raw = _plain(raw)
    if kind in ("int", "number"):
        if isinstance(raw, bool):
            raise ValueError("%s must be a number, not %r" % (key, raw))
        try:
            x = float(raw)
        except (TypeError, ValueError):
            raise ValueError("%s must be a number, not %r" % (key, raw))
        if x != x or x in (float("inf"), float("-inf")):
            raise ValueError("%s must be a number, not %r" % (key, raw))
        return int(round(x)) if kind == "int" else x
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        s = str(raw).lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
        raise ValueError("%s must be true or false, not %r" % (key, raw))
    if kind == "enum":
        opts = [str(o) for o in spec.get("of") or []]
        for o in opts:
            if str(raw).lower() == o.lower():
                return o
        raise ValueError("%s must be one of %s, not %r" % (key, ", ".join(opts), raw))
    if not isinstance(raw, str):
        raw = str(raw)
    if kind == "str":
        return raw
    if kind == "player":
        if raw in ("@a", "@r", "@p"):
            return raw
        names = [n.strip() for n in raw.split(",") if n.strip()]
        if names and all(_NAME.match(n) for n in names):
            return ",".join(names)
        raise ValueError("%s must be @a, @r, @p or player names separated by commas, not %r" % (key, raw))
    if kind in _PATTERNS:
        low = raw if kind in ("block", "item", "particle") else raw.lower()
        if not _PATTERNS[kind].match(low):
            raise ValueError("%s is not a %s id: %r" % (key, kind, raw))
        bare = low.split("[")[0].split("{")[0]
        short = bare.replace("minecraft:", "")
        if kind == "mob" and short in world.GONE:
            raise ValueError("%s: %s is gone in 26.3, use %s" % (key, raw, world.GONE[short]))
        if bare.startswith("#") and ("#minecraft:" + bare.lstrip("#").replace("minecraft:", "")) in world.GONE:
            tag = "#minecraft:" + bare.lstrip("#").replace("minecraft:", "")
            raise ValueError("%s: %s is gone in 26.3, use %s" % (key, raw, world.GONE[tag]))
        return low
    raise ValueError("%s has an unknown type %r" % (key, kind))


def check(key, spec, raw):
    """Check one raw value (a string from the command, or JSON from the live file) against
    its VALUES entry. Returns (value, note): note says what was clamped, else "". Raises
    ValueError saying what is wrong."""
    kind = spec.get("type")
    if kind == "list":
        if isinstance(raw, str):
            s = raw.strip()
            if s.startswith("["):
                try:
                    raw = json.loads(s)
                except ValueError:
                    raise ValueError("%s is not a JSON list: %r" % (key, raw))
            else:
                raw = [p for p in s.split(",") if p.strip()]
        if not isinstance(raw, (list, tuple)):
            raise ValueError("%s must be a list, not %r" % (key, raw))
        of = spec.get("of")
        out = []
        for item in raw:
            if isinstance(of, (list, tuple)):
                out.append(_one("enum", item, {"of": of}, key))
            elif isinstance(of, str) and of in TYPES and of != "list":
                out.append(_one(of, item, {}, key))
            else:
                out.append(_plain(item) if isinstance(item, str) else item)
        return out, ""
    x = _one(kind, raw, spec, key)
    note = ""
    if kind in ("int", "number"):
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and x < lo:
            x, note = (int(lo) if kind == "int" else float(lo)), "%s=%s is under its min, clamped to %s" % (key, raw, lo)
        elif hi is not None and x > hi:
            x, note = (int(hi) if kind == "int" else float(hi)), "%s=%s is over its max, clamped to %s" % (key, raw, hi)
    return x, note


# ---- live values that kit functions read again -----------------------------------------

class _Live(object):
    """A live value as a plain int, float, str or list that knows where it came from."""
    __slots__ = ()

    def now(self):
        """This value as it is now."""
        return self._values[self._key]


class _LiveInt(_Live, int):
    pass


class _LiveFloat(_Live, float):
    pass


class _LiveStr(_Live, str):
    pass


class _LiveList(_Live, list):
    pass


def _wrap(x, values, key):
    if isinstance(x, bool):
        return x
    for base, cls in ((int, _LiveInt), (float, _LiveFloat), (str, _LiveStr)):
        if isinstance(x, base):
            w = cls(x)
            break
    else:
        if not isinstance(x, list):
            return x
        w = _LiveList(x)
    w._values, w._key = values, key
    return w


def now(x):
    """A value as it is now: a live value (v["cooldown"] passed along) is read again; anything
    else is itself. Every kit function that takes a number, a player or a list calls this."""
    return x.now() if isinstance(x, _Live) else x


# ---- the mapping ---------------------------------------------------------------------

class Values(Mapping):
    """The script's values, read like a dict: v["cooldown"]."""

    def __init__(self, VALUES, argv=None, run_dir=None, instance=None, poll=None):
        self._spec = {}
        self._v = {}
        for key, spec in VALUES.items():
            bad = [k for k in spec if k not in KEYS]
            if spec.get("type") not in TYPES or "default" not in spec or bad:
                raise ValueError("VALUES[%r] needs a type of %s and a default, and only the keys %s"
                                 % (key, ", ".join(TYPES), ", ".join(KEYS)))
            self._spec[key] = spec
            x, note = check(key, spec, spec["default"])
            if note:
                raise ValueError("VALUES[%r]: the default is outside its limits" % key)
            self._v[key] = x
        changed = []
        for a in sys.argv[1:] if argv is None else argv:
            if "=" not in a:
                mc.log("ignored %r: values are key=value" % a)
                continue
            key, raw = a.split("=", 1)
            key = key.strip()
            if key not in self._spec:
                mc.log("ignored %s: not in VALUES" % key)
                continue
            try:
                x, note = check(key, self._spec[key], raw)
            except ValueError as e:
                mc.log("bad value, default kept:", e)
                continue
            if note:
                mc.log(note)
            self._v[key] = x
            changed.append(key)
        if changed:
            mc.log("values:", " ".join("%s=%s" % (k, self._show(k)) for k in changed))
        run_dir = run_dir or os.environ.get("VFG_RUN_DIR", "/opt/vfg/run")
        self._path = os.path.join(run_dir, (instance or mc.INSTANCE) + ".values.json")
        if poll is None:
            try:
                poll = float(os.environ.get("VFG_LIVE_POLL_S", "0.5"))
            except ValueError:
                poll = 0.5
        self._poll = max(0.05, poll)
        self._mtime = None
        self._looked = 0.0
        self._said = set()
        self._lock = threading.Lock()
        self._refresh(force=True)

    def _show(self, key):
        x = self._v[key]
        return json.dumps(x) if isinstance(x, (list, bool)) else str(x)

    def _refresh(self, force=False):
        """Read the live file again when it has changed, at most every poll seconds."""
        with self._lock:
            now = time.time()
            if not force and now - self._looked < self._poll:
                return
            self._looked = now
            try:
                mtime = os.stat(self._path).st_mtime
            except OSError:
                return
            if mtime == self._mtime:
                return
            try:
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                return  # half written: try again next time
            self._mtime = mtime
            if not isinstance(data, dict):
                return
            for key, raw in data.items():
                spec = self._spec.get(key)
                if spec is None or not spec.get("live"):
                    if key not in self._said:
                        self._said.add(key)
                        mc.log("live file: ignored %s, %s" % (key, "not in VALUES" if spec is None else "not live"))
                    continue
                try:
                    x, note = check(key, spec, raw)
                except ValueError as e:
                    mc.log("live file: bad value kept out:", e)
                    continue
                if note:
                    mc.log("live file:", note)
                if x != self._v[key]:
                    mc.log("live: %s=%s (was %s)" % (key, json.dumps(x) if isinstance(x, (list, bool)) else x,
                                                       self._show(key)))
                    self._v[key] = x

    def __getitem__(self, key):
        if key not in self._spec:
            raise KeyError("%r is not declared in VALUES" % (key,))
        self._refresh()
        x = self._v[key]
        return _wrap(x, self, key) if self._spec[key].get("live") else x

    def __iter__(self):
        return iter(self._spec)

    def __len__(self):
        return len(self._spec)

    def __repr__(self):
        return "values(%s)" % ", ".join("%s=%s" % (k, self._show(k)) for k in self._spec)


def values(VALUES, argv=None, run_dir=None, instance=None, poll=None):
    """The script's values as a mapping v: VALUES defaults, then the command's key=value,
    then the live file for live keys, each checked. argv, run_dir, instance and poll are for
    tests; a script passes only VALUES."""
    return Values(VALUES, argv, run_dir, instance, poll)


class _Callable(types.ModuleType):
    """So that `from kit import values` can be called: v = values(VALUES)."""

    def __call__(self, *args, **kwargs):
        return values(*args, **kwargs)


sys.modules[__name__].__class__ = _Callable
