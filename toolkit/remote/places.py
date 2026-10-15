# places.py: a read of the saved places the old system kept in Minecraft's command storage
# `everhear:places`, one entry per name: {pos: [x, y, z], dim: "minecraft:overworld",
# by: "castle_steve"}, for `vfg engine migrate` (places are the engine's own now: its places op).
# A trimmed copy of the old kit's places.py (scripts/_lib/, removed with the old system), its
# reads only.
#
# Python 3.9+, standard library only, and pure: all() takes `rcon`, a callable that runs one
# server command and returns Minecraft's reply as text (migrate.py passes common.py's client).
#
# The reply it reads, as Minecraft 26.3 prints it:
#   Storage everhear:places has the following contents: {castle: {pos: [197, 68, 251], by: "castle_steve", dim: "minecraft:overworld"}}
import math
import re

STORAGE = "everhear:places"
NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
BY_RE = re.compile(r"^[A-Za-z0-9_-]{0,32}$")   # a task's name, or "" (an older vfg's task number)
DIM_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
_CONTENTS = re.compile(r"^Storage \S+ has the following contents: (.*)$", re.S)


class PlaceError(Exception):
    """Minecraft did not answer with the storage; the message says what it answered."""


def all(rcon):
    """Every saved place: {name: {"pos": [x, y, z], "dim": str, "by": str}}."""
    data = _contents(rcon("data get storage " + STORAGE))
    if not isinstance(data, dict):
        raise PlaceError("%s is not a compound" % STORAGE)
    out = {}
    for name, value in data.items():
        entry = _entry(value)
        if entry is not None and NAME_RE.match(str(name)):
            out[str(name)] = entry
    return out


def _contents(reply):
    m = _CONTENTS.match(reply or "")
    if not m:
        raise PlaceError("Minecraft answered: %s" % (reply or "nothing"))
    try:
        return snbt(m.group(1))
    except ValueError as e:
        raise PlaceError("cannot read %s: %s" % (STORAGE, e))


def _entry(v):
    if not isinstance(v, dict):
        return None
    pos = v.get("pos")
    if not isinstance(pos, list) or len(pos) != 3:
        return None
    try:
        p = [int(math.floor(float(c))) for c in pos]
    except (TypeError, ValueError):
        return None
    dim = v.get("dim") or "minecraft:overworld"
    by = v.get("by")
    if not isinstance(dim, str) or not DIM_RE.match(dim):
        return None  # written by hand, not by save(): never let it into a command
    return {"pos": p, "dim": dim, "by": by if isinstance(by, str) and BY_RE.match(by) else ""}


# ------------------------------------------------------------------ SNBT

_INT = re.compile(r"^[-+]?\d+[bBsSlLiI]?$")
_FLOAT = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?[fFdD]?$")
_BARE = frozenset("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-.+")
_ESC = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "s": " ", "t": "\t"}


def snbt(text):
    """Parse the SNBT Minecraft prints: compounds become dicts, lists and typed
    arrays ([I; 1, 2, 3]) lists, numbers int or float (by their suffix),
    strings str. Raises ValueError on anything else."""
    p = _Snbt(text)
    v = p.value()
    p.ws()
    if p.i != len(p.s):
        raise ValueError("unexpected %r at %d" % (p.s[p.i:p.i + 10], p.i))
    return v


class _Snbt(object):
    def __init__(self, s):
        self.s = s
        self.i = 0

    def ws(self):
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def peek(self):
        self.ws()
        if self.i >= len(self.s):
            raise ValueError("unexpected end")
        return self.s[self.i]

    def expect(self, c):
        if self.peek() != c:
            raise ValueError("expected %r at %d" % (c, self.i))
        self.i += 1

    def value(self):
        c = self.peek()
        if c == "{":
            return self.compound()
        if c == "[":
            return self.list()
        if c in "\"'":
            return self.quoted()
        return self.scalar(self.bare())

    def compound(self):
        self.expect("{")
        out = {}
        if self.peek() == "}":
            self.i += 1
            return out
        while True:
            c = self.peek()
            key = self.quoted() if c in "\"'" else self.bare()
            self.expect(":")
            out[key] = self.value()
            c = self.peek()
            self.i += 1
            if c == "}":
                return out
            if c != ",":
                raise ValueError("expected , or } at %d" % (self.i - 1))

    def list(self):
        self.expect("[")
        if self.s[self.i:self.i + 2] in ("B;", "I;", "L;"):
            self.i += 2
        out = []
        if self.peek() == "]":
            self.i += 1
            return out
        while True:
            out.append(self.value())
            c = self.peek()
            self.i += 1
            if c == "]":
                return out
            if c != ",":
                raise ValueError("expected , or ] at %d" % (self.i - 1))

    def bare(self):
        self.ws()
        j = self.i
        while j < len(self.s) and self.s[j] in _BARE:
            j += 1
        if j == self.i:
            raise ValueError("unexpected %r at %d" % (self.s[self.i:self.i + 10], self.i))
        tok, self.i = self.s[self.i:j], j
        return tok

    def quoted(self):
        q = self.s[self.i]
        self.i += 1
        out = []
        while True:
            if self.i >= len(self.s):
                raise ValueError("unclosed string")
            c = self.s[self.i]
            self.i += 1
            if c == q:
                return "".join(out)
            if c != "\\":
                out.append(c)
                continue
            if self.i >= len(self.s):
                raise ValueError("unclosed string")
            e = self.s[self.i]
            self.i += 1
            width = {"x": 2, "u": 4, "U": 8}.get(e)
            if width:
                try:
                    out.append(chr(int(self.s[self.i:self.i + width], 16)))
                except ValueError:
                    raise ValueError("bad escape at %d" % self.i)
                self.i += width
            else:
                out.append(_ESC.get(e, e))

    @staticmethod
    def scalar(tok):
        if _INT.match(tok):
            return int(tok.rstrip("bBsSlLiI"))
        if _FLOAT.match(tok):
            return float(tok.rstrip("fFdD"))
        return tok
