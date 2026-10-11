"""Casts: an incantation's action started on the box the moment its phrase is heard.

A cast comes from the mod's keyword spotter (POST /v1/casts, layer ``spotter``) or from
Layer 2, the phoneme match on a voice line as it arrives (layer ``transcript``).  Both go
through one lock, so each cast's checks see every cast before it:

1. a repeated client_cast_id answers what the first did;
2. a cast of an incantation whose window overlaps an earlier cast of it by that player that
   fired, or one the spotter made whatever became of it, is ``duplicate``: that utterance was
   decided, whichever layer came first. A transcript cast that did not fire decides nothing
   past its line: its window is the whole line's, and would swallow the next words;
3. the incantation's own ``cooldown_s`` since it last fired for that player, then
   VFG_CAST_GAP_S since any of the player's casts fired, are ``cooldown``;
4. otherwise its copy is started (instances.Box.start, from the entry, with no round trip
   to the PC), and the cast is ``fired``, or ``failed`` when the copy could not start;
   ``stale`` when the bridge itself has held it VFG_CAST_WAIT_S since its request came
   (on this lock, or on instances' copies.lock), so it never fires seconds late.

The cast's row is stored before the answer; its events row and GAME line after it.
Windows are on the mod's audio clock, cooldowns on the bridge's.

The drop rule then decides a voice line: one that overlaps a cast of that player's and
has at most one word besides the phrase is stored with ``dropped = 'cast'``, and Jev
never sees it.  A spotter's cast that comes after its line drops it then, if Jev has not
judged it yet.
"""

import datetime as _dt
import logging
import os
import threading
import time

from . import timeutil, ulid

log = logging.getLogger("vfg_bridge")

DEFAULT_COOLDOWN_S = 3.0
# How far back a player's casts are read: past the longest cooldown (600 s), a player's
# clock up to 10 minutes behind (the clock guard's limit) and a long line's window.
LOOKBACK = _dt.timedelta(minutes=30)
ERROR_MAX = 300


class Unavailable(Exception):
    """The kit or the store cannot be used: casts and the list answer 503."""


def _one_line(exc):
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text[:ERROR_MAX]


def _seconds(delta):
    return delta.total_seconds()


def answer(cast):
    """The 202 body for a stored cast."""
    doc = {"id": cast["id"], "status": cast["status"]}
    if cast.get("copy"):
        doc["copy"] = cast["copy"]
    if cast.get("error"):
        doc["error"] = cast["error"]
    return doc


class LineCasts:
    """What a voice line's casts decide for it: its dropped and cast_id columns, the casts
    it becomes the line of, and the casts Layer 2 made, to announce once it is answered."""

    def __init__(self, dropped=None, cast_id=None, cast_ids=(), made=()):
        self.dropped = dropped
        self.cast_id = cast_id
        self.cast_ids = list(cast_ids)
        self.made = list(made)


NOTHING = LineCasts()


class Casts:
    LIST_POLL_S = 0.2         # how often a held list request looks at the store file
    LIST_WAIT_DEFAULT = 25.0
    LIST_WAIT_MAX = 30.0

    def __init__(self, db, kit, feed, store_path, base, mc="/opt/mc", owner="", poll=0.5,
                 gap_s=1.0, slack_s=0.3, allowance=0.25, layer2=True, wait_s=1.5, clock=None,
                 monotonic=None, logger=None):
        self.db = db
        self.kit = kit
        self.feed = feed
        self.store_path = store_path
        self.base = base
        self.mc = mc
        self.owner = owner
        self.poll = poll
        self.gap_s = gap_s
        self.slack_s = slack_s
        self.allowance = allowance
        self.layer2 = layer2
        self.wait_s = wait_s
        self.clock = clock or timeutil.utcnow
        self.monotonic = monotonic or time.monotonic  # the request's arrival is on this clock
        self.log = logger or log
        self.lock = threading.Lock()  # every cast, the spotter's and Layer 2's
        self._read = (None, None)     # the store's file key, and its parse

    @classmethod
    def from_config(cls, cfg, db, kit, feed, clock=None):
        return cls(db, kit, feed, cfg.incantations, cfg.base, cfg.mc, cfg.owner, cfg.live_poll_s,
                   cfg.cast_gap_s, cfg.cast_slack_s, cfg.incant_allowance, cfg.incant_layer2,
                   cfg.cast_wait_s, clock)

    # -- the kit and the store -----------------------------------------------------

    def _modules(self):
        mods = self.kit.modules()
        if mods is None:
            raise Unavailable("incantations are not on this box: the kit in %s has no"
                              " incantations module" % self.kit.path)
        return mods

    def _store(self, inc):
        """The store, parsed again only when its file changes (writes replace it whole)."""
        store = inc.Store(self.store_path)
        key = self._file_key()
        seen, doc = self._read
        if key is None or key != seen:
            doc = store.read()
            self._read = (key, doc)
        store.read = lambda: doc  # for_player and lookup read it through read()
        return store

    # -- POST /v1/casts ------------------------------------------------------------

    def cast(self, player, phrase, heard, started, ended, client_cast_id, arrived=None):
        """A spotter's cast: (HTTP status, body, what to do once answered, or None).
        ``arrived``: when its request came, on ``self.monotonic``."""
        arrived = self.monotonic() if arrived is None else arrived
        inc, ins = self._modules()
        with self.lock:
            first = self.db.cast_by_client_id(client_cast_id)
            if first is not None:
                return 202, answer(first), None
            entry = self._store(inc).lookup(player, phrase)
            if entry is None:
                return 404, {"error": "%s has no incantation %r" % (player, phrase)}, None
            cast = self._try(ins, entry, player, heard, "spotter", started, ended, client_cast_id,
                             arrived)
        return 202, answer(cast), lambda: self._after(inc, cast, entry)

    def _after(self, inc, cast, entry):
        """Once a spotter's cast is answered: announce it, and drop a line of that utterance
        the line endpoint stored before the cast came, which Jev has not judged yet."""
        self.announce(cast)
        try:
            started, ended = timeutil.parse_time(cast["started_at"]), timeutil.parse_time(cast["ended_at"])
            pad = _dt.timedelta(seconds=self.slack_s)
            for line_id, text in self.db.unjudged_lines(
                    cast["player_name"], timeutil.format_time(started - pad),
                    timeutil.format_time(ended + pad), timeutil.format_time(started - LOOKBACK)):
                if inc.drop(text, entry) and self.db.drop_line(line_id, cast["id"]):
                    break
        except Exception:
            self.log.exception("could not drop the line of cast %s", cast["id"])

    def _try(self, ins, entry, player, heard, layer, started, ended, client_cast_id, arrived):
        """Check, start and store one cast; call under the lock. Answers the stored row."""
        now = self.clock()
        cast = {"id": ulid.new(), "at": timeutil.format_time(now),
                "started_at": timeutil.format_time(started), "ended_at": timeutil.format_time(ended),
                "player_name": player, "phrase": entry["phrase"], "heard": heard, "layer": layer,
                "incantation_id": entry.get("id"), "action": entry.get("action"),
                "client_cast_id": client_cast_id}
        status, why = self._check(entry, player, started, ended, now)
        left = self.wait_s - (self.monotonic() - arrived)
        if status is None and left <= 0:
            status, why = "stale", "held %.1f s before it could start" % (self.wait_s - left)
        if status is None:
            try:
                box = ins.Box(self.base, self.mc, owner=self.owner, poll=self.poll)
                copy, _, proc = box.start(self.meta(entry, player), wait=left)
            except ins.Busy as exc:  # copies.lock held past the wait: too late to fire
                status, why = "stale", _one_line(exc)
            except Exception as exc:  # NotDeployed, CopyError, OSError: the cast failed
                status, why = "failed", _one_line(exc)
            else:
                status, cast["copy"] = "fired", copy
                threading.Thread(target=self._reap, args=(box, copy, proc),
                                 name="reap-" + copy, daemon=True).start()
        cast["status"], cast["error"] = status, why
        self.db.insert_cast(cast)
        return cast

    def _near(self, player, started, ended, now):
        """The player's casts whose window, widened by VFG_CAST_SLACK_S on each side, meets
        [started, ended]."""
        pad = _dt.timedelta(seconds=self.slack_s)
        return self.db.overlapping_casts(player, timeutil.format_time(started - pad),
                                         timeutil.format_time(ended + pad),
                                         timeutil.format_time(min(started, now) - LOOKBACK))

    def _check(self, entry, player, started, ended, now):
        """(None, None) when the cast may fire, else (``duplicate`` or ``cooldown``, why)."""
        for c in self._near(player, started, ended, now):
            if c["incantation_id"] == entry.get("id") and (c["status"] == "fired" or c["layer"] == "spotter"):
                return "duplicate", "it was cast in this window already (%s, %s)" % (
                    c["id"], c["status"])
        last = self.db.last_fired(player, timeutil.format_time(min(started, now) - LOOKBACK))
        cooldown = entry.get("cooldown_s")
        cooldown = DEFAULT_COOLDOWN_S if cooldown is None else float(cooldown)
        if entry.get("id") in last:
            left = cooldown - _seconds(now - timeutil.parse_time(last[entry.get("id")]))
            if left > 0:
                return "cooldown", "%.1f s left of its %g s cooldown" % (left, cooldown)
        if last:
            left = self.gap_s - _seconds(now - timeutil.parse_time(max(last.values())))
            if left > 0:
                return "cooldown", "%.1f s left of the %g s between a player's casts" % (
                    left, self.gap_s)
        return None, None

    @staticmethod
    def meta(entry, player):
        """What instances.Box.start takes, from a store entry; each caster field is the caster."""
        values = dict(entry.get("values") or {})
        for field in entry.get("caster_fields") or ():
            values[field] = player
        return {"action": entry.get("action"), "script": entry.get("script") or entry.get("action"),
                "source": entry.get("source") or "library", "server": entry.get("server") or "",
                "values": values, "live": list(entry.get("live") or ()),
                "runs": entry.get("runs") or "once", "task": "cast"}

    def _reap(self, box, copy, proc):
        """Wait for a cast's copy, so it never stays a zombie; then its pid file goes, if still its."""
        code = proc.wait()
        path = box.path(copy, ".pid")
        try:
            with box.lock():  # the lock a copy's name is picked under
                with open(path) as f:
                    held = int(f.read().strip())
                if held == proc.pid:
                    os.remove(path)
        except (OSError, ValueError):
            pass
        if code:
            self.log.info("cast copy %s ended with code %d", copy, code)

    def announce(self, cast):
        """A fired or failed cast's events row and GAME cast line, whatever watch.json says."""
        player, phrase = cast["player_name"], cast["phrase"]
        if cast["status"] == "fired":
            text = '%s cast "%s": %s, running as %s' % (player, phrase, cast["action"], cast["copy"])
        elif cast["status"] == "failed":
            text = '%s cast "%s": failed — %s' % (player, phrase, cast["error"])
        else:
            return
        try:
            self.db.insert_event(cast["at"], "cast", player, text)
        except Exception:
            self.log.exception("could not store cast %s as an event", cast["id"])
        try:
            self.feed.append("GAME", "cast " + player, text, cast["at"])
        except Exception:
            self.log.exception("could not append cast %s to the feed", cast["id"])

    # -- POST /v1/lines ------------------------------------------------------------

    def on_line(self, player, text, started, ended, client_line_id, arrived=None, made=None):
        """Layer 2 and the drop rule for a voice line, before it is stored: a LineCasts.

        Nothing for a player with no incantations, or a line already stored. Each cast Layer
        2 makes goes into ``made`` (the caller's list) as soon as it is stored, so the caller
        announces it whatever happens after."""
        arrived = self.monotonic() if arrived is None else arrived
        made = [] if made is None else made
        mods = self.kit.modules()
        if mods is None:
            return NOTHING
        inc, ins = mods
        entries = self._store(inc).for_player(player)
        if not entries or self.db.line_id(client_line_id) is not None:
            return NOTHING
        if self.layer2:
            words = inc.words(text)
            taken = []  # the word spans cast already: one cast for the same words
            for m in inc.match_line(text, self._one_per_phrase(inc, entries), self.allowance):
                if any(m.start < end and start < m.end for start, end in taken):
                    continue
                taken.append((m.start, m.end))
                key = "line:%s:%s" % (client_line_id, m.entry.get("id"))
                heard = " ".join(words[m.start:m.end]) or None
                with self.lock:
                    if self.db.cast_by_client_id(key) is not None:
                        continue
                    cast = self._try(ins, m.entry, player, heard, "transcript", started, ended,
                                     key, arrived)
                made.append(cast)
        with self.lock:  # a spotter's cast being started right now is stored first
            near = self._near(player, started, ended, self.clock())
        if not near:
            return LineCasts(made=made)
        near.sort(key=lambda c: c["status"] != "fired")  # a line's cast is one that fired, if any
        by_id = {e.get("id"): e for e in entries}
        for c in near:
            entry = by_id.get(c["incantation_id"]) or {"phrase": c["phrase"], "aliases": []}
            if inc.drop(text, entry):
                return LineCasts("cast", c["id"], [c["id"] for c in near], made)
        kept = [c for c in near if c["status"] == "fired"]
        return LineCasts(None, kept[0]["id"] if kept else None, [c["id"] for c in near], made)

    @staticmethod
    def _one_per_phrase(inc, entries):
        """The entries, one per normalised phrase: the player's own before an @a one."""
        out, seen = [], set()
        for e in entries:
            phrase = inc.normalize(e.get("phrase"))
            if phrase not in seen:
                seen.add(phrase)
                out.append(e)
        return out

    # -- GET /v1/incantations ------------------------------------------------------

    def _file_key(self):
        try:
            st = os.stat(self.store_path)
        except OSError:
            return None
        return st.st_ino, st.st_size, st.st_mtime_ns

    def listing(self, player, after=None, wait=None):
        """(200, the player's list) at once, or when ``after`` is the version, once it changes;
        (304, None) when it has not changed in ``wait`` s."""
        inc, _ = self._modules()
        wait = self.LIST_WAIT_DEFAULT if wait is None else min(wait, self.LIST_WAIT_MAX)
        deadline = time.monotonic() + wait
        key = self._file_key()
        doc = self._store(inc).read()  # a missing or unreadable store is version 0, with none
        while after is not None and doc["version"] == after:
            while self._file_key() == key:
                left = deadline - time.monotonic()
                if left <= 0:
                    return 304, None
                time.sleep(min(self.LIST_POLL_S, left))
            key = self._file_key()
            doc = self._store(inc).read()
        entries = [e for e in doc["incantations"] if inc.applies_to(e, player)]
        entries.sort(key=lambda e: str(e.get("player")).lower() == "@a")  # their own first
        out = []
        for e in self._one_per_phrase(inc, entries):
            cooldown = e.get("cooldown_s")
            out.append({"phrase": e["phrase"], "aliases": list(e.get("aliases") or ()),
                        "cooldown_s": DEFAULT_COOLDOWN_S if cooldown is None else cooldown})
        return 200, {"version": doc["version"], "player": player, "incantations": out}
