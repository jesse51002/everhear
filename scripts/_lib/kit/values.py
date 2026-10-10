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
"""
import json
import os
import sys
import threading
import time
import types
from collections.abc import Mapping

import mc


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


class Copy(Mapping):
    """A copy's values: its meta's, checked on the PC, and the live file for its live fields,
    read again at most every poll seconds."""

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
            for key, x in data.items():
                if key not in self._v:
                    self._ignore(key, "not one of its values")
                elif key not in self._live:
                    self._ignore(key, "not live")
                elif x != self._v[key]:
                    mc.log("live: %s=%s (was %s)" % (key, json.dumps(x) if isinstance(x, (list, bool, dict)) else x,
                                                       self._show(key)))
                    self._v[key] = x

    def __getitem__(self, key):
        if key not in self._v:
            raise KeyError("%r is not one of this copy's values (its action has no such field)" % (key,))
        self._refresh()
        x = self._v[key]
        return _wrap(x, self, key) if key in self._live else x

    def __iter__(self):
        return iter(self._v)

    def __len__(self):
        return len(self._v)

    def __repr__(self):
        return "values(%s)" % ", ".join("%s=%s" % (k, self._show(k)) for k in self._v)


def values(*, meta=None, run_dir=None, instance=None, poll=None):
    """The copy's values as a mapping v: values(), from its meta file and live file. meta (the
    meta file's dict, or its path), run_dir, instance and poll are for tests."""
    return Copy(meta, run_dir, instance, poll)


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
