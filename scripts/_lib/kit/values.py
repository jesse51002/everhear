"""A script's values, v = values(), and what it reports, result(summary, **facts).

A copy's values were checked on the PC against its action before it started (vfg action run),
and are in its meta file, run/<copy>.json: "values" holds every field, "live" names the Live
ones. values() gives them as a mapping, v["cooldown"]; a live field is read again from the
copy's live file, run/<copy>.values.json, which an edit writes, at most every VFG_LIVE_POLL_S
(0.5) s. Nothing is checked again here. A key in the live file that is not a live field is
logged once and ignored; reading a name the meta has no value for raises KeyError.

    v = values()                                   # a script
    v = values(meta={"values": {...}, "live": [...]}, run_dir=d, instance="gun")   # a test

A live value handed to the kit as it is (items.clicks(..., cooldown=v["cooldown"])) stays
live: the kit reads it again each time (values.now). A live bool cannot do that: read v[...]
again on each pass.

result(summary, **facts) is the copy's answer: one line, and the facts the conversation needs
(who, where, how many), written atomically to run/<copy>.result.json as {"summary", "facts",
"at"} and logged as `result: <summary>`; the last call wins. vfg action run answers a once
action with the last, an until-stopped one with the first.

Until every script is ported, v = values(VALUES) works as before: each value its VALUES default,
then the copy's meta values (with no meta file, key=value words after the script's name), then
for a value marked "live" the live file; each checked against its type and limits, a number out
of range clamped and logged, a bad value logged and the one before kept. Reading a name VALUES
does not declare raises. VALUES entry keys: type (int, number, bool, enum, str, player, block,
mob, item, particle, sound, list), default (required), min and max (numbers), of (enum options;
for a list, the options or the type of each item), live (default False).
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

def _run_dir(run_dir=None):
    return run_dir or os.environ.get("VFG_RUN_DIR", "/opt/vfg/run")


def _poll(poll=None):
    if poll is None:
        try:
            poll = float(os.environ.get("VFG_LIVE_POLL_S", "0.5"))
        except ValueError:
            poll = 0.5
    return max(0.05, poll)


def _read_meta(path):
    """A copy's meta file as a dict, or None when there is none."""
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


class _Values(Mapping):
    """The values as a mapping, with the live file read again at most every poll seconds.
    A subclass says which keys are live (_is_live) and takes a value from the file (_take)."""

    def _start_live(self, run_dir, instance, poll):
        self._path = os.path.join(_run_dir(run_dir), (instance or mc.INSTANCE) + ".values.json")
        self._poll = _poll(poll)
        self._mtime = None
        self._looked = 0.0
        self._said = set()
        self._lock = threading.Lock()
        self._refresh(force=True)

    def _show(self, key):
        x = self._v[key]
        return json.dumps(x) if isinstance(x, (list, bool, dict)) else str(x)

    def _ignore(self, key, why):
        if key not in self._said:
            self._said.add(key)
            mc.log("live file: ignored %s, %s" % (key, why))

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
                x = self._take(key, raw)
                if x is _SKIP:
                    continue
                if x != self._v[key]:
                    mc.log("live: %s=%s (was %s)" % (key, json.dumps(x) if isinstance(x, (list, bool, dict)) else x,
                                                       self._show(key)))
                    self._v[key] = x

    def __getitem__(self, key):
        if key not in self._v:
            raise KeyError(self._missing(key))
        self._refresh()
        x = self._v[key]
        return _wrap(x, self, key) if self._is_live(key) else x

    def __iter__(self):
        return iter(self._v)

    def __len__(self):
        return len(self._v)

    def __repr__(self):
        return "values(%s)" % ", ".join("%s=%s" % (k, self._show(k)) for k in self._v)


_SKIP = object()


class Copy(_Values):
    """A copy's values: its meta's, checked on the PC, and the live file for its live fields."""

    def __init__(self, meta=None, run_dir=None, instance=None, poll=None):
        if not isinstance(meta, dict):
            path = meta or os.path.join(_run_dir(run_dir), (instance or mc.INSTANCE) + ".json")
            meta = _read_meta(path)
            if meta is None:
                raise RuntimeError("no values: %s is missing — a script runs as a copy of its action "
                                   "(vfg action run)" % path)
        given = meta.get("values")
        self._v = dict(given) if isinstance(given, dict) else {}
        self._live = set(k for k in meta.get("live") or [] if k in self._v)
        self._start_live(run_dir, instance, poll)

    def _is_live(self, key):
        return key in self._live

    def _missing(self, key):
        return "%r is not one of this copy's values (its action has no such field)" % (key,)

    def _take(self, key, raw):
        if key not in self._v:
            self._ignore(key, "not one of its values")
            return _SKIP
        if key not in self._live:
            self._ignore(key, "not live")
            return _SKIP
        return raw


class Values(_Values):
    """values(VALUES): a script not yet ported to its action, read the old way, each value
    checked against its VALUES entry."""

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
        given = []
        if argv is None:
            meta = _read_meta(os.path.join(_run_dir(run_dir), (instance or mc.INSTANCE) + ".json"))
            if meta is not None and isinstance(meta.get("values"), dict):
                given = list(meta["values"].items())  # what the action sent, as JSON values
            else:
                argv = sys.argv[1:]
        for a in argv or []:
            if "=" not in a:
                mc.log("ignored %r: values are key=value" % a)
                continue
            key, raw = a.split("=", 1)
            given.append((key.strip(), raw))
        changed = []
        for key, raw in given:
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
            if x != self._v[key] or isinstance(raw, str):
                changed.append(key)
            self._v[key] = x
        if changed:
            mc.log("values:", " ".join("%s=%s" % (k, self._show(k)) for k in changed))
        self._start_live(run_dir, instance, poll)

    def _is_live(self, key):
        return bool(self._spec[key].get("live"))

    def _missing(self, key):
        return "%r is not declared in VALUES" % (key,)

    def _take(self, key, raw):
        spec = self._spec.get(key)
        if spec is None or not spec.get("live"):
            self._ignore(key, "not in VALUES" if spec is None else "not live")
            return _SKIP
        try:
            x, note = check(key, spec, raw)
        except ValueError as e:
            mc.log("live file: bad value kept out:", e)
            return _SKIP
        if note:
            mc.log("live file:", note)
        return x


def values(VALUES=None, argv=None, run_dir=None, instance=None, poll=None, meta=None):
    """The copy's values as a mapping v: values(), from its meta file and live file. meta,
    run_dir, instance and poll are for tests. values(VALUES) is the old way, until every
    script is ported (argv is for its tests)."""
    if VALUES is None:
        return Copy(meta, run_dir, instance, poll)
    return Values(VALUES, argv, run_dir, instance, poll)


def result(summary, **facts):
    """Report what the copy did: one line, and the facts the conversation needs (who, where,
    how many), as run/<copy>.result.json; the last call wins. Returns the summary."""
    summary = " ".join(str(summary).split())
    path = os.path.join(_run_dir(), mc.INSTANCE + ".result.json")
    data = json.dumps({"summary": summary, "facts": facts, "at": time.time()}, ensure_ascii=False, default=str)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data + "\n")
        os.replace(tmp, path)
    except OSError as e:
        mc.log("result not written (%s):" % e, summary)
    mc.log("result:", summary)
    return summary


class _Callable(types.ModuleType):
    """So that `from kit import values` can be called: v = values()."""

    def __call__(self, *args, **kwargs):
        return values(*args, **kwargs)


sys.modules[__name__].__class__ = _Callable
