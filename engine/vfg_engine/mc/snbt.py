"""Reading the text Minecraft 26.3 prints for NBT (SNBT), as `data get` answers over RCON.

    parse('{d: {item: "fire_staff", count: 1}, u: [I; 1, 2, 3, 4], h: 20.0f}')
    -> {"d": {"item": "fire_staff", "count": 1}, "u": [1, 2, 3, 4], "h": 20.0}

Numbers lose their type letter (1b, 1s, 1, 1L are ints; 1.0f, 1.0d, 1.0 are floats); arrays
([I; …], [B; …], [L; …]) are lists of ints; a compound is a dict, a list a list, a string a str.
`data_reply` takes the whole reply ("Storage everhear:io has the following contents: {…}") and
returns the value, or raises NoData for "Found no elements matching …".

Standard library only.
"""

from __future__ import annotations

import re
import unicodedata

_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?[bBsSlLfFdD]?")
_BARE = re.compile(r"[A-Za-z0-9._+\-]+")
_ESCAPES = {"\\": "\\", "'": "'", '"': '"', "n": "\n", "t": "\t", "b": "\b", "f": "\f", "r": "\r",
            "s": " "}
# "<anything> has the following contents: " (storage) and "<name> has the following entity data: "
_PREFIX = re.compile(r"^.*? has the following (?:contents|entity data|block data): ", re.S)


class SnbtError(ValueError):
    """The text is not SNBT this reader understands; the message says where."""


class NoData(LookupError):
    """`data get` found nothing at the path ("Found no elements matching …")."""


def data_reply(reply: str):
    """The value in a `data get` reply. NoData when the path matched nothing; SnbtError when the
    reply is something else (an error, an unknown command), with the reply in the message."""
    if reply.startswith("Found no elements matching"):
        raise NoData(reply)
    m = _PREFIX.match(reply)
    if not m:
        raise SnbtError("not a `data get` reply: %r" % reply[:200])
    return parse(reply[m.end():])


def parse(text: str):
    """One SNBT value, the whole text."""
    p = _Parser(text)
    value = p.value()
    p.space()
    if p.i != len(text):
        raise SnbtError("unexpected %r at %d of %r" % (text[p.i:p.i + 20], p.i, text[:80]))
    return value


class _Parser:
    __slots__ = ("s", "i")

    def __init__(self, s: str):
        self.s, self.i = s, 0

    def space(self):
        s, i, n = self.s, self.i, len(self.s)
        while i < n and s[i] in " \t\r\n":
            i += 1
        self.i = i

    def fail(self, what):
        raise SnbtError("%s at %d: %r" % (what, self.i, self.s[max(0, self.i - 20):self.i + 20]))

    def value(self):
        self.space()
        if self.i >= len(self.s):
            self.fail("value expected")
        c = self.s[self.i]
        if c == "{":
            return self.compound()
        if c == "[":
            return self.list()
        if c in "\"'":
            return self.string()
        m = _NUMBER.match(self.s, self.i)
        if m and (m.end() == len(self.s) or self.s[m.end()] in ",]} \t\r\n"):
            self.i = m.end()
            return _number(m.group())
        m = _BARE.match(self.s, self.i)
        if m:
            self.i = m.end()
            word = m.group()
            if word == "true":
                return 1
            if word == "false":
                return 0
            return word
        self.fail("value expected")

    def string(self):
        s, quote = self.s, self.s[self.i]
        i, out = self.i + 1, []
        while True:
            j = s.find(quote, i)
            k = s.find("\\", i)
            if j < 0:
                self.fail("unterminated string")
            if 0 <= k < j:
                out.append(s[i:k])
                e = s[k + 1:k + 2]
                if e in _ESCAPES:
                    out.append(_ESCAPES[e])
                    i = k + 2
                elif e in ("x", "u", "U"):
                    width = {"x": 2, "u": 4, "U": 8}[e]
                    code = s[k + 2:k + 2 + width]
                    try:
                        out.append(chr(int(code, 16)))
                    except ValueError:
                        self.i = k
                        self.fail("bad escape")
                    i = k + 2 + width
                elif e == "N" and s[k + 2:k + 3] == "{":
                    end = s.find("}", k + 3)
                    try:
                        out.append(unicodedata.lookup(s[k + 3:end]))
                    except KeyError:
                        out.append("?")
                    i = end + 1
                else:
                    out.append(e)
                    i = k + 2
                continue
            out.append(s[i:j])
            self.i = j + 1
            return "".join(out)

    def key(self):
        self.space()
        c = self.s[self.i:self.i + 1]
        if c in ("\"", "'"):
            return self.string()
        m = _BARE.match(self.s, self.i)
        if not m:
            self.fail("key expected")
        self.i = m.end()
        return m.group()

    def compound(self):
        self.i += 1
        out = {}
        self.space()
        if self.s[self.i:self.i + 1] == "}":
            self.i += 1
            return out
        while True:
            k = self.key()
            self.space()
            if self.s[self.i:self.i + 1] != ":":
                self.fail("':' expected")
            self.i += 1
            out[k] = self.value()
            self.space()
            c = self.s[self.i:self.i + 1]
            if c == ",":
                self.i += 1
                continue
            if c == "}":
                self.i += 1
                return out
            self.fail("',' or '}' expected")

    def list(self):
        self.i += 1
        self.space()
        s = self.s
        # a typed array: [I; 1, 2], [B; 1b], [L; 1L]
        if s[self.i:self.i + 1] in ("I", "B", "L") and s[self.i + 1:self.i + 2] == ";":
            self.i += 2
        out = []
        self.space()
        if s[self.i:self.i + 1] == "]":
            self.i += 1
            return out
        while True:
            out.append(self.value())
            self.space()
            c = s[self.i:self.i + 1]
            if c == ",":
                self.i += 1
                continue
            if c == "]":
                self.i += 1
                return out
            self.fail("',' or ']' expected")


def _number(text: str):
    last = text[-1]
    if last in "bBsSlL":
        return int(text[:-1])
    if last in "fFdD":
        return float(text[:-1])
    if "." in text or "e" in text or "E" in text:
        return float(text)
    return int(text)


def quote(text: str) -> str:
    """A string as an SNBT (and JSON-compatible) double-quoted literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
