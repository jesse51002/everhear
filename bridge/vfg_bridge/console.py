"""The console tail: /opt/mc/console.log in, chat lines and game events out.

The parser (``split``, ``parse``) is pure, and is what ``--parse FILE`` prints.
The ``Tail`` follows the file by byte offset, survives rotation and
truncation, and keeps its place in /opt/vfg/state/console.offset.
"""

import datetime as _dt
import json
import logging
import os
import re

from . import timeutil

log = logging.getLogger("vfg_bridge")

N = r"([A-Za-z0-9_]{1,16})"
# A display name: the player's name with any team prefix or suffix, written as [tags],
# as in "[Crew] Alex joined the game". Group 1 is the name.
DECOR = r"\[[^\[\]]*\]"
D = r"(?:" + DECOR + r" ?)*" + N + r"(?: ?" + DECOR + r")*"
DISPLAY_RE = re.compile(D)

STAMP_RE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] \[([^\]]+)/([A-Z]+)\]: (.*)$")
STAMP_PREFIX_RE = re.compile(r"^\[\d\d:\d\d:\d\d\] ")
CHAT_RE = re.compile(r"^(?:\[Not Secure\] )?<" + D + r"> (.*)$")
SYSTEM_CHAT = "System chat: "

# Tried in this order; the first that matches names the event.
EVENT_RES = (
    ("join", re.compile(r"^" + D + r"(?: \(formerly known as " + N + r"\))? joined the game$")),
    ("leave", re.compile(r"^" + D + r" left the game$")),
    ("advancement", re.compile(
        r"^" + D + r" has (?:made the advancement|completed the challenge|reached the goal) \[.+\]$")),
    ("death", re.compile(
        r"^" + D + r" (?:was |were |died|drowned|blew up|burned|fell |froze|starved|suffocated"
        r"|withered|went |walked into|tried to swim|hit the ground|experienced kinetic"
        r"|discovered the floor|didn't want|left the confines)")),
)
# A death whose message the server has no translation for prints its bare key, and no name.
UNTRANSLATED_DEATH_RE = re.compile(r"^death\.[A-Za-z0-9_.]+$")
SERVER_RES = (
    (re.compile(r'^Done \([0-9.]+s\)! For help, type "help"$'), "server started"),
    (re.compile(r"^Stopping server$"), "server stopping"),
)
# 26.3 logs nothing when systemd stops it, so minecraft.service's ExecStopPost writes this line.
STOP_MARKER_THREAD = "vfg"
STOP_MARKER = "Server stopped"
# /say sources that are not players: [Rcon] hi, [Server] hi, [Not Secure] [Bat] mod.
SAY_SOURCES = frozenset(("Rcon", "Server", "Not Secure", "@"))
UUID_RE = re.compile(r"^UUID of player " + N + r" is ")
LOGIN_RE = re.compile(r"^" + N + r"\[.*\] logged in with entity id ")

CHAT_MAX = 2000
ONE_MS = _dt.timedelta(milliseconds=1)
FEED_MAX_AGE = _dt.timedelta(minutes=10)


def same_second_floor(at, previous):
    """Keep lines that share a log second in order: never before the previous line's ``at``.

    A stamp read in a later second than it was written takes the clock's
    milliseconds, which can fall before an earlier line's. Within one second,
    such a line is moved to 1 ms after the previous one, and no further than
    .999, so it never changes the second it shows.
    """
    if previous is None or at > previous:
        return at
    if at.replace(microsecond=0) != previous.replace(microsecond=0):
        return at  # an earlier second: a backlog after truncation keeps its own times
    return min(previous + ONE_MS, previous.replace(microsecond=999000))


def split(raw):
    """(hour, minute, second, thread, level, message) of a stamped console line, or None."""
    m = STAMP_RE.match(raw)
    if not m:
        return None
    hour, minute, second = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour, minute, second, m.group(4), m.group(5), m.group(6)


def leading_bracket(message):
    """(inside, rest) of a message that starts with a bracketed group, nesting allowed; or None."""
    depth = 0
    for i, ch in enumerate(message):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return message[1:i], message[i + 1:].lstrip(" ")
    return None


def is_echo_or_say(message, known=frozenset()):
    """Whether a message that starts with ``[`` is command feedback or a /say, not a team tag.

    Feedback is wholly bracketed with a source: ``[Rcon: Summoned new Zombie]``. A /say
    is ``[source] text``, the source Rcon, Server, an entity behind ``[Not Secure]``,
    or a known player (with or without a team tag): ``[[Crew] Alex] pos-check``.
    """
    parts = leading_bracket(message)
    if parts is None:
        return True
    inside, rest = parts
    if not rest or ": " in inside or inside in SAY_SOURCES:
        return True
    m = DISPLAY_RE.fullmatch(inside)
    return bool(m and m.group(1).lower() in known)


def classify(message, known=frozenset()):
    """What a Server thread/INFO message is: a dict, or None for noise.

    {"kind": "chat", "player", "text"} or {"kind": <event kind>, "player", "text"}.
    ``known`` holds lower-cased player names, to tell a player's /say from a team tag.
    """
    m = CHAT_RE.match(message)
    if m:
        text = m.group(2).strip()[:CHAT_MAX]
        if not text:
            return None
        return {"kind": "chat", "player": m.group(1), "text": text}
    if message.startswith(SYSTEM_CHAT):
        message = message[len(SYSTEM_CHAT):]
    if UNTRANSLATED_DEATH_RE.match(message):
        return {"kind": "death", "player": None, "text": message}
    if message.startswith("[") and is_echo_or_say(message, known):
        return None
    for kind, rx in EVENT_RES:
        m = rx.match(message)
        if m:
            return {"kind": kind, "player": m.group(1), "text": message}
    for rx, text in SERVER_RES:
        if rx.match(message):
            return {"kind": "server", "player": None, "text": text}
    return None


class Parser:
    """Reads console lines in order, learning player names from logins as it goes."""

    def __init__(self, known=()):
        self.known = {name.lower() for name in known if name}

    def learn(self, name):
        if name:
            self.known.add(name.lower())

    def parse_parts(self, parts):
        """The event or chat in a split line, as {"kind", "player", "text"}, or None."""
        thread, level, message = parts[3], parts[4], parts[5]
        if level != "INFO":
            return None
        if thread == STOP_MARKER_THREAD:
            if message == STOP_MARKER:
                return {"kind": "server", "player": None, "text": "server stopped"}
            return None
        if thread.startswith("User Authenticator"):
            m = UUID_RE.match(message)
            if m:
                self.learn(m.group(1))
            return None
        if thread != "Server thread":
            return None
        m = LOGIN_RE.match(message)
        if m:
            self.learn(m.group(1))
            return None
        found = classify(message, self.known)
        if found is not None:
            self.learn(found["player"])
        return found

    def parse(self, raw):
        """A recognised console line as {"time", "kind", "player", "text"}, or None."""
        parts = split(raw)
        if parts is None:
            return None
        found = self.parse_parts(parts)
        if found is None:
            return None
        result = {"time": "%02d:%02d:%02d" % parts[:3]}
        result.update(found)
        return result


def parse(raw):
    """One console line on its own, with no players known yet."""
    return Parser().parse(raw)


def unstamped(raw):
    """The line without its ``[HH:MM:SS] `` stamp."""
    return STAMP_PREFIX_RE.sub("", raw, count=1)


def iter_file_lines(path):
    """The lines of a console log, plain or gzipped, decoded leniently."""
    import gzip
    with open(path, "rb") as probe:
        magic = probe.read(2)
    opener = gzip.open if magic == b"\x1f\x8b" else open
    with opener(path, "rb") as f:
        for raw in f:
            yield raw.decode("utf-8", "replace").rstrip("\r\n")


def parse_file(path, out):
    """``--parse``: print each recognised line of a log as one JSON object. Touches nothing."""
    parser = Parser()
    count = 0
    for n, raw in enumerate(iter_file_lines(path), 1):
        found = parser.parse(raw)
        if found is not None:
            record = {"n": n}
            record.update(found)
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count


class Tail:
    """Follows console.log; stores chat and events; feeds watched events and contains matches."""

    POLL_SECONDS = 0.2
    CHUNK = 1 << 20

    def __init__(self, path, state_dir, db, watch, feed, clock=None, logger=None,
                 watch_every=1.0, save_every=1.0, reopen_every=1.0, monotonic=None):
        import time
        self.path = path
        self.offset_path = os.path.join(state_dir, "console.offset")
        self.state_dir = state_dir
        self.db = db
        self.watch = watch
        self.feed = feed
        self.clock = clock or timeutil.utcnow
        self.log = logger or log
        self.monotonic = monotonic or time.monotonic
        self.watch_every = watch_every
        self.save_every = save_every
        self.reopen_every = reopen_every
        self.fd = None
        self.inode = None
        self.offset = 0
        self._started = False
        self._saved = None
        self._last_save = None
        self._last_watch = None
        self._last_open_try = None
        self._last_at = None
        self.parser = Parser()

    # -- the offset ----------------------------------------------------------

    def _load_offset(self):
        try:
            with open(self.offset_path, encoding="utf-8") as f:
                doc = json.load(f)
            return int(doc["inode"]), int(doc["offset"])
        except FileNotFoundError:
            return None
        except (ValueError, KeyError, TypeError):
            self.log.warning("console.offset is unreadable; starting at the end of the log")
            return None

    def save_offset(self):
        if self.inode is None:
            return
        state = (self.inode, self.offset)
        if state == self._saved:
            return
        os.makedirs(self.state_dir, exist_ok=True)
        tmp = self.offset_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"inode": self.inode, "offset": self.offset}, f)
        os.replace(tmp, self.offset_path)
        self._saved = state

    # -- opening -------------------------------------------------------------

    def _open(self, first):
        """Open the log if it exists. At the first try, resume from the saved offset."""
        try:
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        except FileNotFoundError:
            return False
        st = os.fstat(fd)
        if first:
            saved = self._load_offset()
            if saved is None:
                offset = self._end_of_last_line(fd, st.st_size)
            elif saved[0] == st.st_ino and st.st_size >= saved[1]:
                offset = saved[1]
            else:
                offset = 0
        else:
            offset = 0  # a file that appears is read from its start
        self.fd, self.inode, self.offset = fd, st.st_ino, offset
        self.log.info("following %s from byte %d", self.path, offset)
        return True

    @staticmethod
    def _end_of_last_line(fd, size):
        start = max(0, size - 65536)
        data = os.pread(fd, size - start, start) if size else b""
        i = data.rfind(b"\n")
        if i >= 0:
            return start + i + 1
        return size if start > 0 else 0

    def start(self):
        """The first open, with the saved offset; later opens poll once a second."""
        self._started = True
        self._last_open_try = self.monotonic()
        last = getattr(self.db, "last_console_at", None)
        if last is not None:
            self._last_at = last()  # so a restart keeps a log second's lines in order
        players = getattr(self.db, "known_players", None)
        if players is not None:
            self.parser = Parser(players())  # players already online when the bridge restarts
        if not self._open(first=True):
            self.log.info("%s does not exist yet; waiting for it", self.path)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    # -- one pass ------------------------------------------------------------

    def _due(self, last, every):
        return last is None or self.monotonic() - last >= every

    def poll_once(self):
        if not self._started:
            self.start()
        if self.fd is None:
            if self._due(self._last_open_try, self.reopen_every):
                self._last_open_try = self.monotonic()
                self._open(first=False)
        if self.fd is not None:
            self._follow()
        if self._due(self._last_save, self.save_every):
            self._last_save = self.monotonic()
            self.save_offset()
        if self._due(self._last_watch, self.watch_every):
            self._last_watch = self.monotonic()
            self.watch.check()

    def _follow(self):
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            st = None  # gone for now: finish what is open, wait for a new one
        if st is not None and st.st_ino != self.inode:
            self._read(final=True)
            self.log.info("%s was replaced; reading the new file from its start", self.path)
            self.close()
            self._open(first=False)
            if self.fd is None:
                return
        elif os.fstat(self.fd).st_size < self.offset:
            self.log.info("%s was truncated; reading from its start", self.path)
            self.offset = 0
        self._read(final=False)

    def _read(self, final):
        while True:
            data = os.pread(self.fd, self.CHUNK, self.offset)
            if not data:
                return
            end = data.rfind(b"\n")
            if end >= 0:
                chunk = data[:end + 1]
                lines = chunk[:-1].split(b"\n")
            elif final or len(data) >= self.CHUNK:
                chunk = data  # a last line that will never end, or one longer than a chunk
                lines = [data]
            else:
                return  # a partial last line waits for its newline
            for line in lines:
                self.handle(line.decode("utf-8", "replace").rstrip("\r"))
            self.offset += len(chunk)
            if len(data) < self.CHUNK and end == len(data) - 1:
                return

    # -- one line ------------------------------------------------------------

    def handle(self, raw):
        """Handle one console line; an exception is logged and the line skipped."""
        try:
            self._handle(raw)
        except Exception:
            self.log.exception("could not handle console line: %s", raw[:200])

    def _handle(self, raw):
        now = self.clock()
        parts = split(raw)
        if parts is not None:
            at = same_second_floor(timeutil.console_at(parts[0], parts[1], parts[2], now),
                                   self._last_at)
            self._last_at = at
        else:
            at = self._last_at or now  # a continuation line (a stack trace) takes its header's time
        at_text = timeutil.format_time(at)
        fresh = now - at <= FEED_MAX_AGE
        watchers = self.watch.current
        fed = False

        found = self.parser.parse_parts(parts) if parts is not None else None
        if found is not None and found["kind"] == "chat":
            self.db.insert_chat(at_text, found["player"], found["text"])
        elif found is not None:
            kind, player, text = found["kind"], found["player"], found["text"]
            self.db.insert_event(at_text, kind, player, text)
            if fresh and watchers.wants(kind, player):
                fed = self._feed("%s %s" % (kind, player or "-"), text, at)

        if not fed and fresh and watchers.contains:
            line = unstamped(raw)
            if watchers.raw_match(line):
                self._feed("raw -", line, at)

    def _feed(self, fields, text, at):
        try:
            self.feed.append("GAME", fields, text, at)
            return True
        except Exception:
            self.log.exception("could not append a GAME line to the feed")
            return False

    # -- the thread ----------------------------------------------------------

    def run(self, stop):
        """Poll every 200 ms until ``stop`` is set; save the offset on the way out."""
        try:
            while not stop.is_set():
                self.poll_once()
                stop.wait(self.POLL_SECONDS)
        finally:
            self.save_offset()
            self.close()
