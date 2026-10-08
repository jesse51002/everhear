"""Game watchers, /opt/vfg/watch.json (contracts section 6): load, check, match.

    {"events": ["join","leave","death","advancement","server"], "players": [], "contains": []}

``events`` are the kinds to feed; ``players`` limits them to those players
(empty means everyone; events with no player are never limited);
``contains`` holds regexes tried on raw console lines, fed as ``GAME raw``.
"""

import json
import logging
import os
import re

KINDS = ("join", "leave", "death", "advancement", "server")

log = logging.getLogger("vfg_bridge")


class WatchError(ValueError):
    """The file cannot be used at all; the previous watchers stay in force."""


class Watchers:
    def __init__(self, events=KINDS, players=(), contains=()):
        self.events = tuple(k for k in KINDS if k in set(events))
        self.players = tuple(players)
        self._players = frozenset(p.lower() for p in players)
        self.contains = tuple(contains)
        self._patterns = [re.compile(p) for p in contains]

    def wants(self, kind, player):
        """Whether an event of this kind, by this player (or None), is fed."""
        if kind not in self.events:
            return False
        if not self._players or player is None:
            return True
        return player.lower() in self._players

    def raw_match(self, text):
        """Whether a console line matches any ``contains`` pattern."""
        return any(p.search(text) for p in self._patterns)

    def as_dict(self):
        return {"events": list(self.events), "players": list(self.players),
                "contains": list(self.contains)}


def default_text():
    return json.dumps(Watchers().as_dict()) + "\n"


def _string_list(doc, key):
    if key not in doc:
        return None
    value = doc[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise WatchError("holds the wrong types (%r is not a list of strings)" % key)
    return value


def parse(text):
    """(Watchers, problems) from the file's text; raises WatchError if it cannot be used.

    A missing key takes its default; an unknown kind and a pattern that does
    not compile are dropped, each named in ``problems``.
    """
    try:
        doc = json.loads(text)
    except ValueError:
        raise WatchError("is not valid JSON") from None
    if not isinstance(doc, dict):
        raise WatchError("holds the wrong types (not a JSON object)")
    events = _string_list(doc, "events")
    players = _string_list(doc, "players")
    contains = _string_list(doc, "contains")
    problems = []
    if events is None:
        events = list(KINDS)
    unknown = [k for k in events if k not in KINDS]
    for kind in unknown:
        problems.append("unknown kind %r" % kind)
    events = [k for k in events if k in KINDS]
    good = []
    for pattern in contains or []:
        try:
            re.compile(pattern)
        except Exception as exc:  # re.error, RecursionError, OverflowError...
            problems.append("pattern %r does not compile (%s)" % (pattern, exc))
        else:
            good.append(pattern)
    return Watchers(events, players or [], good), problems


class WatchFile:
    """watch.json on disk: written with the default when missing, reread when it changes."""

    def __init__(self, path, feed, logger=None):
        self.path = path
        self.feed = feed
        self.log = logger or log
        self.current = Watchers()
        self._key = None
        self._loaded = False

    def ensure(self):
        """Write the default file if there is none, so every reader sees the same watchers."""
        if os.path.exists(self.path):
            return
        tmp = "%s.tmp.%d" % (self.path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(default_text())
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.path)
        self.log.info("watch.json was missing; wrote the default")

    def check(self):
        """Reload if the file's inode, size or modification time changed. Returns True on a reload."""
        try:
            f = open(self.path, "rb")
        except FileNotFoundError:
            if self._key != "missing":
                self._key = "missing"
                self.current = Watchers()
                self._loaded = True
                self.log.warning("watch.json is missing; using the default watchers")
            return False
        with f:
            st = os.fstat(f.fileno())
            key = (st.st_ino, st.st_size, st.st_mtime_ns)
            if key == self._key:
                return False
            raw = f.read()
        self._key = key
        try:
            watchers, problems = parse(raw.decode("utf-8", "replace"))
        except WatchError as exc:
            kept = "kept the previous watchers" if self._loaded else "using the default watchers"
            self._report("%s — %s" % (exc, kept))
            return False
        self.current = watchers
        self._loaded = True
        if problems:
            self._report("; ".join("%s — dropped it" % p for p in problems))
        self.log.info("watch.json loaded: events=%s players=%d contains=%d",
                      ",".join(watchers.events) or "none", len(watchers.players),
                      len(watchers.contains))
        return True

    def _report(self, problem):
        text = "watch.json: %s" % problem
        self.log.warning("%s", text)
        try:
            self.feed.append("SYS", "", text)
        except Exception:
            self.log.exception("could not write the watch.json notice to the feed")
