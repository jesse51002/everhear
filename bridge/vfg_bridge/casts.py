"""Casts: an incantation's ability or action started the moment its phrase is heard.

A cast comes from the mod's keyword spotter (POST /v1/casts, layer ``spotter``), from
Layer 2, the phoneme match on a voice line as it arrives (layer ``transcript``), or from the
Jev filter, which judged a stored voice line a cast before routing it (POST /v1/jev/casts,
layer ``jev``).  All go through one lock, so each cast's checks see every cast before it:

1. a repeated client_cast_id answers what the first did;
2. a cast of an incantation whose window overlaps an earlier cast of it by that player that
   fired, or one the spotter made whatever became of it, is ``duplicate``: that utterance was
   decided, whichever layer came first. A transcript cast that did not fire decides nothing
   past its line: its window is the whole line's, and would swallow the next words;
3. the incantation's own ``cooldown_s`` since it last fired for that player, then
   VFG_CAST_GAP_S since any of the player's casts fired, are ``cooldown``;
4. otherwise its copy is started (instances.Box.start, with the kit's incantations.cast_meta of
   the entry, with no round trip to the PC), and the cast is ``fired``, or ``failed`` when the copy could not start;
   ``stale`` when the bridge itself has held it VFG_CAST_WAIT_S since its request came
   (on this lock, or on instances' copies.lock), so it never fires seconds late.

The game engine's bindings come first (the game engine spec, "Incantations"): the incantations bound
to its abilities, per player, read from its export (engine.py). A phrase the engine binds for the
player hides an entry of today's store with the same phrase, in every path: the spotter's casts,
Layer 2, the Jev filter's casts, the drop rule and the list. Such a cast is sent to the engine as
``cast`` and waits for its answer at most VFG_ENGINE_CAST_WAIT_S; it skips step 3, since cooldowns
are the ability's own, in the engine, and keeps steps 1, 2 and 4's ``stale``. That wait is outside
the lock, so no cast waits on another's engine round trip: under the lock the cast is checked and
its row stored as ``pending``, which the duplicate check counts as deciding its utterance and a
repeated client_cast_id waits on (``_settled``); then the engine is asked, and the row takes the
status the mod knows. A ``pending`` row lives only that long; one a stopped bridge left is made
``failed`` on the next start (``DB.fail_pending``). Its record's
``incantation_id`` is ``engine:<the binding's id>`` and its ``action`` the ability, with no copy.
The engine's answer becomes one the mod already knows (``engine_status``). Today's store keeps
working as before for action bindings, ``@a`` ones included, until the actions move onto the engine.

The cast's row is stored before the answer; its events row and GAME line after it.
Windows are on the mod's audio clock, cooldowns on the bridge's.

The drop rule then decides a voice line: one that overlaps a cast of that player's and
has at most one word besides the phrase is stored with ``dropped = 'cast'``, and Jev
never sees it.  A spotter's cast that comes after its line drops it then, if Jev has not
judged it yet.  A line the Jev filter casts is dropped too, whatever became of its cast.
"""

import collections
import datetime as _dt
import hashlib
import json
import logging
import os
import threading
import time

from . import engine as enginemod
from . import timeutil, ulid

log = logging.getLogger("vfg_bridge")

DEFAULT_COOLDOWN_S = 3.0
# Layer 2 casts only from a line of at most this many words (normalised, fillers counted):
# VFG_INCANT_LAYER2_MAX_WORDS, 0 for any length. A cast is said on its own, and a longer line
# is talk. On 2026-10-12, with each player given that night's six incantations, the stored
# aliases cast falsely on 216 of the 843 lines of 10-08's talk, and with 20 of the alias call's
# on 397; at 6 words, 7 and 31. On the two sessions with casts, 10-11's false casts went from 28
# to 2 and that night's from 3 to 1, every cast found as before (78% and 94% of them, stored).
LAYER2_MAX_WORDS = 6
# How far back a player's casts are read: past the longest cooldown (600 s), a player's
# clock up to 10 minutes behind (the clock guard's limit) and a long line's window.
LOOKBACK = _dt.timedelta(minutes=30)
ERROR_MAX = 300
# When /v1/lines received each line, kept in memory for a Jev cast's age (the bridge stores no
# such time): this long, and at most this many lines.
RECEIVED_KEEP = _dt.timedelta(minutes=30)
RECEIVED_MAX = 20000
# A Jev cast whose phrase was said this long before its line ended is stale: a long line comes when
# the whole utterance ends, so a phrase at its start would fire seconds late. Where the phrase is,
# the kit's phoneme match finds, as loose as this; its time is placed by word between at and ended_at.
PHRASE_LEAD_S = 3.0
PHRASE_ALLOWANCE = 0.4   # finds "variable" for fire ball (0.32), not noise (0.43+)
# An engine cast's record is under this before the binding's id, so it never meets a store entry's
# id in the duplicate check or the cooldowns.
ENGINE_KEY = "engine:"
# An engine cast's row while the engine is asked, outside the lock; never an answer.
PENDING = "pending"
# How long a repeated client_cast_id waits for its first request's engine answer, past that
# answer's own longest wait (the engine's, and the client's margin on it).
SETTLE_MARGIN_S = 1.0


class Unavailable(Exception):
    """The kit or the store cannot be used: casts and the list answer 503."""


def _one_line(exc):
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text[:ERROR_MAX]


def _seconds(delta):
    return delta.total_seconds()


def engine_status(reply, player="the player", ability="that ability"):
    """(status, why) of a cast for the engine's reply to it (THE CONTROL OPS, ``cast``), as the
    cast answers the mod already knows: ``fired`` (fired, or charging: the cast was taken, and the
    ability fires once its charge-up is done), ``cooldown``, or ``failed`` with the engine's words
    (not granted, unknown, failed, and any refusal)."""
    status = reply.get("status") if reply.get("ok") else None
    error = reply.get("error")
    ability = reply.get("ability") or ability
    if status in ("fired", "charging"):
        return "fired", None
    if status == "cooldown":
        left = reply.get("remaining_s")
        if isinstance(left, (int, float)) and not isinstance(left, bool):
            return "cooldown", "%.1f s left of its cooldown" % left
        return "cooldown", error or "it is cooling down"
    if status == "not_granted":
        return "failed", error or "%s has not been granted %s" % (player, ability)
    if status == "unknown":
        return "failed", error or "the engine has no such incantation for %s" % player
    if status == "failed":
        return "failed", error or "the engine could not cast it"
    if not reply.get("ok"):
        return "failed", "the engine refused the cast: %s" % (error or "no reason given")
    return "failed", "the engine answered the cast with %r" % (status,)


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
                 monotonic=None, logger=None, jev_max_age_s=2.0, layer2_max_words=LAYER2_MAX_WORDS,
                 engine=None, engine_wait_s=1.0):
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
        self.layer2_max_words = layer2_max_words
        self.wait_s = wait_s
        self.jev_max_age_s = jev_max_age_s
        self.engine = engine          # engine.Engine, or None: today's store alone
        self.engine_wait_s = engine_wait_s
        self.clock = clock or timeutil.utcnow
        self.monotonic = monotonic or time.monotonic  # the request's arrival is on this clock
        self.log = logger or log
        self.lock = threading.Lock()  # every cast's checks and record, the spotter's and Layer 2's
        self._asking = {}             # an engine cast's id -> an Event set once its row is final
        self._read = (None, None)     # the store's file key, and its parse
        self._received = collections.OrderedDict()  # client_line_id -> self.monotonic when it came
        self._received_lock = threading.Lock()

    @classmethod
    def from_config(cls, cfg, db, kit, feed, clock=None):
        return cls(db, kit, feed, cfg.incantations, cfg.base, cfg.mc, cfg.owner, cfg.live_poll_s,
                   cfg.cast_gap_s, cfg.cast_slack_s, cfg.incant_allowance, cfg.incant_layer2,
                   cfg.cast_wait_s, clock, jev_max_age_s=cfg.jev_cast_max_age_s,
                   layer2_max_words=cfg.incant_layer2_max_words,
                   engine=enginemod.Engine(cfg.engine_socket, cfg.engine_export),
                   engine_wait_s=cfg.engine_cast_wait_s)

    # -- the kit and the store -----------------------------------------------------

    def _modules(self):
        mods = self.kit.modules()
        if mods is None:
            raise Unavailable("incantations are not on this box: %s" % (
                getattr(self.kit, "why", None) or "the kit in %s has no incantations module" % self.kit.path))
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

    def _entries(self, inc, player, store=None):
        """Every incantation that applies to ``player``: the engine's bindings for them first, then
        the store's (their own, then @a) whose phrase the engine does not bind for them."""
        store = self._store(inc) if store is None else store
        own = self.engine.for_player(player) if self.engine is not None else []
        if not own:
            return store.for_player(player)
        bound = set(inc.normalize(e["phrase"]) for e in own)
        return own + [e for e in store.for_player(player) if inc.normalize(e.get("phrase")) not in bound]

    @staticmethod
    def _lookup(inc, entries, heard):
        """The first of ``entries`` whose phrase or an alias, normalised, is ``heard``, or None
        (the store's lookup, over the merged list)."""
        h = inc.normalize(heard)
        if not h:
            return None
        for e in entries:
            if h in inc.targets(e):
                return e
        return None

    @staticmethod
    def _key(entry):
        """What a cast of ``entry`` is recorded under, as ``incantation_id``."""
        return ENGINE_KEY + entry["id"] if entry.get("engine") else entry.get("id")

    # -- POST /v1/casts ------------------------------------------------------------

    def cast(self, player, phrase, heard, started, ended, client_cast_id, arrived=None):
        """A spotter's cast: (HTTP status, body, what to do once answered, or None).
        ``arrived``: when its request came, on ``self.monotonic``."""
        arrived = self.monotonic() if arrived is None else arrived
        inc, ins = self._modules()
        with self.lock:
            first = self.db.cast_by_client_id(client_cast_id)
            if first is None:
                entry = self._lookup(inc, self._entries(inc, player), phrase)
                if entry is None:
                    return 404, {"error": "%s has no incantation %r" % (player, phrase)}, None
                cast = self._try((inc, ins), entry, player, heard, "spotter", started, ended, client_cast_id,
                                 arrived)
        if first is not None:
            first = self._settled(first)
            if first["status"] == PENDING:
                return 503, {"error": "cast %s is still waiting on the game engine" % first["id"]}, None
            return 202, answer(first), None
        self._ask(cast, entry, player)
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

    def _try(self, mods, entry, player, heard, layer, started, ended, client_cast_id, arrived,
             line_id=None, late=None):
        """Check, start and store one cast; call under the lock. Answers the stored row. ``mods``:
        the kit's (incantations, instances). ``late``: why it is too late to fire (a Jev cast's
        line too old), so it is ``stale``. An engine binding's cast is sent to the engine."""
        inc, ins = mods
        now = self.clock()
        cast = {"id": ulid.new(), "at": timeutil.format_time(now),
                "started_at": timeutil.format_time(started), "ended_at": timeutil.format_time(ended),
                "player_name": player, "phrase": entry["phrase"], "heard": heard, "layer": layer,
                "incantation_id": self._key(entry),
                "action": entry.get("ability") if entry.get("engine") else entry.get("action"),
                "client_cast_id": client_cast_id, "line_id": line_id}
        status, why = self._check(entry, player, started, ended, now)
        left = self.wait_s - (self.monotonic() - arrived)
        if status is None and left <= 0:
            status, why = "stale", "held %.1f s before it could start" % (self.wait_s - left)
        if status is None and late:
            status, why = "stale", late
        if status is None and entry.get("engine"):
            # Reserved: the engine is asked outside the lock (_ask), and the row then takes its status.
            status, cast["arrived"] = PENDING, arrived
        elif status is None:
            try:
                box = ins.Box(self.base, self.mc, owner=self.owner, poll=self.poll)
                copy, _, proc = box.start(inc.cast_meta(entry, player), wait=left)
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
        if status == PENDING:  # stored: a repeat of its client_cast_id now waits on this
            self._asking[cast["id"]] = threading.Event()
        return cast

    def _ask(self, cast, entry, player):
        """An engine cast reserved by _try (``pending``): ask the engine, outside the lock, and
        store what came of it. A cast _try settled already is left as it is. The row is final
        before a repeat waiting on it (_settled) is let go, whatever went wrong."""
        if cast["status"] != PENDING:
            return
        try:
            left = self.wait_s - (self.monotonic() - cast["arrived"])
            if left <= 0:  # held past the bridge's wait between the lock and here: never sent late
                status, why = "stale", "held %.1f s before it could start" % (self.wait_s - left)
            else:
                status, why, cast["engine_status"] = self._engine_cast(entry, player, cast["id"], left)
            self.db.finish_cast(cast["id"], status, why)
            cast["status"], cast["error"] = status, why
        finally:
            done = self._asking.pop(cast["id"], None)
            if done is not None:
                done.set()

    def _settled(self, cast):
        """A stored cast, once it is not ``pending``: an engine cast a repeated client_cast_id
        finds still waiting on the engine is waited for (outside the lock), and read again. Still
        ``pending`` only when its first request never finished (a stopped bridge's row, or the
        engine's answer could not be stored)."""
        if cast["status"] != PENDING:
            return cast
        done = self._asking.get(cast["id"])
        if done is not None:
            done.wait(self.engine_wait_s + enginemod.MARGIN_S + SETTLE_MARGIN_S)
        return self.db.cast_by_client_id(cast["client_cast_id"]) or cast

    def _engine_cast(self, entry, player, cast_id, left):
        """(status, why, the engine's own status) of an engine binding's cast, sent to the engine
        and waited for at most VFG_ENGINE_CAST_WAIT_S, and never past the bridge's own wait. With
        the engine down or late it ``failed``, in words, and is announced so."""
        phrase = entry["phrase"]
        wait = max(0.05, min(self.engine_wait_s, left))
        try:
            reply = self.engine.cast(player, phrase, cast_id, wait)
        except enginemod.Down as exc:
            return "failed", "the game engine is not running, so %r was not cast (%s)" % (
                phrase, _one_line(exc)), None
        except enginemod.Late:
            return "failed", "the game engine did not answer the cast within %.1f s" % wait, None
        except Exception as exc:  # never lose a cast's record over the engine's client
            self.log.exception("casting %r through the engine failed", phrase)
            return "failed", "the cast could not be sent to the game engine: %s" % _one_line(exc), None
        if reply.get("reason") == "timeout":
            return "failed", "the game engine did not answer the cast within %.1f s" % wait, None
        if reply.get("reason") == "stopping":
            return "failed", "the game engine is stopping, so %r was not cast" % phrase, None
        status, why = engine_status(reply, player, entry.get("ability"))
        return status, why, reply.get("status") if reply.get("ok") else None

    def _near(self, player, started, ended, now):
        """The player's casts whose window, widened by VFG_CAST_SLACK_S on each side, meets
        [started, ended]."""
        pad = _dt.timedelta(seconds=self.slack_s)
        return self.db.overlapping_casts(player, timeutil.format_time(started - pad),
                                         timeutil.format_time(ended + pad),
                                         timeutil.format_time(min(started, now) - LOOKBACK))

    def _check(self, entry, player, started, ended, now):
        """(None, None) when the cast may fire, else (``duplicate`` or ``cooldown``, why). An
        engine binding's cast is checked only for a duplicate: its cooldown is the ability's, in
        the engine. The store's cooldowns and gap count only the store's casts."""
        key = self._key(entry)
        for c in self._near(player, started, ended, now):
            if c["incantation_id"] == key and (c["status"] in ("fired", PENDING) or c["layer"] == "spotter"):
                return "duplicate", "it was cast in this window already (%s, %s)" % (
                    c["id"], c["status"])
        if entry.get("engine"):
            return None, None
        last = dict((k, at) for k, at in self.db.last_fired(
            player, timeutil.format_time(min(started, now) - LOOKBACK)).items()
            if not str(k or "").startswith(ENGINE_KEY))
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
        if cast["status"] == "fired" and str(cast.get("incantation_id") or "").startswith(ENGINE_KEY):
            text = '%s cast "%s": %s, %s in the engine' % (
                player, phrase, cast["action"],
                "charging" if cast.get("engine_status") == "charging" else "fired")
        elif cast["status"] == "fired":
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

        Nothing for a player with no incantations, or a line already stored. Layer 2 only on a
        line of at most ``layer2_max_words`` words (0: any). Each cast Layer 2 makes goes into
        ``made`` (the caller's list) as soon as it is stored, so the caller announces it
        whatever happens after."""
        arrived = self.monotonic() if arrived is None else arrived
        made = [] if made is None else made
        mods = self.kit.modules()
        if mods is None:
            return NOTHING
        inc, ins = mods
        entries = self._entries(inc, player)
        if entries:
            self.received(client_line_id, arrived)  # before it is stored: a Jev cast's age
        if not entries or self.db.line_id(client_line_id) is not None:
            return NOTHING
        words = inc.words(text)
        if self.layer2 and (not self.layer2_max_words or len(words) <= self.layer2_max_words):
            taken = []  # the word spans cast already: one cast for the same words
            for m in inc.match_line(text, self._one_per_phrase(inc, entries), self.allowance):
                if any(m.start < end and start < m.end for start, end in taken):
                    continue
                taken.append((m.start, m.end))
                key = "line:%s:%s" % (client_line_id, self._key(m.entry))
                heard = " ".join(words[m.start:m.end]) or None
                with self.lock:
                    if self.db.cast_by_client_id(key) is not None:
                        continue
                    cast = self._try((inc, ins), m.entry, player, heard, "transcript", started, ended,
                                     key, arrived)
                made.append(cast)  # stored: announced whatever happens next, as it stands then
                self._ask(cast, m.entry, player)
        with self.lock:  # a spotter's cast being started right now is stored first
            near = self._near(player, started, ended, self.clock())
        if not near:
            return LineCasts(made=made)
        near.sort(key=lambda c: c["status"] != "fired")  # a line's cast is one that fired, if any
        by_id = {self._key(e): e for e in entries}
        for c in near:
            entry = by_id.get(c["incantation_id"]) or {"phrase": c["phrase"], "aliases": []}
            if inc.drop(text, entry):
                return LineCasts("cast", c["id"], [c["id"] for c in near], made)
        kept = [c for c in near if c["status"] == "fired"]
        return LineCasts(None, kept[0]["id"] if kept else None, [c["id"] for c in near], made)

    # -- POST /v1/jev/casts ---------------------------------------------------------

    def jev_cast(self, line_id, phrase, arrived=None, keep=False):
        """The Jev filter's cast of a stored voice line it judged a cast of ``phrase``, before
        routing it: (HTTP status, body, what to do once answered, or None).

        With ``keep`` (a line over 6 words, the cast said inside other talk, or a short one Jev is
        not sure of) the cast is the same but the line is kept, to be routed as ever, so the rest of
        it reaches the AI; it takes the cast's id as its cast_id when the cast fired and it has none
        (``"line": "kept"``). Jev may ask it after routing the line (a long line's routing never
        waits on its cast check), so a kept cast is not refused for a line Jev judged.

        The same checks as every cast, on the line's window (its ``at`` and ``ended_at``, the
        clock guard's already), layer ``jev``, ``client_cast_id`` ``jev:<line id>``, so asking
        again answers what the first did. Whatever becomes of the cast, the line is then dropped
        (``dropped = 'cast'``), so Jev never routes it; its cast_id is the cast, unless that did
        not fire and the line carries a fired one already. A line dropped already (a spotter's
        cast that came meanwhile) answers 200 and casts nothing.

        A Jev cast never fires late: one whose line /v1/lines received more than
        VFG_JEV_CAST_MAX_AGE_S (2 s) before, on the bridge's monotonic clock (``received``), or at a time the
        bridge no longer has (before it last started), is ``stale``, recorded and not started;
        its line is dropped all the same (kept, with ``keep``). So is one whose phrase was said
        more than PHRASE_LEAD_S before its line ended (``_said_early``), as at the start of a long
        line, which comes only when the whole utterance ends."""
        arrived = self.monotonic() if arrived is None else arrived
        inc, ins = self._modules()
        key = "jev:%s" % line_id
        new = False
        with self.lock:
            line = self.db.line(line_id)
            if line is None:
                return 404, {"error": "no line %s" % line_id}, None
            cast = self.db.cast_by_client_id(key)
            if cast is None:
                if line["source"] != "voice":
                    return 400, {"error": "line %s is not a voice line" % line_id}, None
                if line["dropped"] is not None:
                    return 200, {"line": "dropped", "cast_id": line["cast_id"]}, None
                if line["jev_class"] is not None and not keep:  # a kept line's cast may come after its routing
                    return 409, {"error": "Jev judged line %s already" % line_id, "line": "judged"}, None
                player = line["player_name"]
                entry = self._lookup(inc, self._entries(inc, player), phrase)
                if entry is None:
                    return 404, {"error": "%s has no incantation %r" % (player, phrase)}, None
                started = timeutil.parse_time(line["at"])
                ended = timeutil.parse_time(line["ended_at"] or line["at"])
                heard = " ".join(inc.words(line["text"]))[:200] or None
                late = self._too_old(line) or self._said_early(inc, entry, line["text"], started, ended)
                cast = self._try((inc, ins), entry, player, heard, "jev", started, ended, key, arrived,
                                 line_id=line_id, late=late)
                new = True
        if new:
            self._ask(cast, entry, player)  # an engine cast's round trip, outside the lock
        else:
            cast = self._settled(cast)
            if cast["status"] == PENDING:
                return 503, {"error": "cast %s is still waiting on the game engine" % cast["id"]}, None
        with self.lock:
            line = self.db.line(line_id) or line  # read again: a spotter's cast may have dropped it meanwhile
            if keep:
                if cast["status"] == "fired":
                    self.db.line_cast(line_id, cast["id"])
                kept = "dropped" if line["dropped"] is not None else "kept"
            else:
                dropped = line["dropped"] is not None or self.db.drop_judged_cast(
                    line_id, cast["id"], cast["status"] == "fired")
                kept = "dropped" if dropped else "judged"
        doc = dict(answer(cast), line=kept)
        return 202, doc, (lambda: self.announce(cast)) if new else None

    @staticmethod
    def _said_early(inc, entry, text, started, ended):
        """Why a Jev cast is too late because its phrase was said early in its line, or None: the
        last place the kit's phoneme match (any length, PHRASE_ALLOWANCE) finds the phrase or an
        alias, its last word's time estimated by word between the line's start and end, more than
        PHRASE_LEAD_S before the end. None when the phrase is not found: then it is allowed."""
        words = inc.words(text)
        span = (ended - started).total_seconds()
        if not words or span <= PHRASE_LEAD_S:
            return None
        last, offset = None, 0
        while offset < len(words):
            found = inc.match_line(" ".join(words[offset:]), [entry], PHRASE_ALLOWANCE)
            if not found:
                break
            last = offset + found[0].end
            offset = last
        if last is None:
            return None
        lead = span * (len(words) - last) / len(words)
        if lead > PHRASE_LEAD_S:
            return ("the phrase was said about %.1f s before its line ended, past the %g s a Jev cast"
                    " may be" % (lead, PHRASE_LEAD_S))
        return None

    def received(self, client_line_id, arrived):
        """Note when /v1/lines received a line of a player with an incantation (only such a line
        can get a Jev cast), ``arrived`` on ``self.monotonic``, for a Jev cast's age; the first time
        a client_line_id came is kept. In memory, for 30 minutes: the bridge stores no such time."""
        keep = RECEIVED_KEEP.total_seconds()
        with self._received_lock:
            self._received.setdefault(client_line_id, arrived)
            while self._received:
                first = next(iter(self._received.values()))
                if arrived - first <= keep and len(self._received) <= RECEIVED_MAX:
                    break
                self._received.popitem(last=False)

    def _too_old(self, line):
        """Why a Jev cast of ``line`` is too late to fire, or None: its age, from when
        /v1/lines received it to now, on ``self.monotonic`` (no wall clock step can move it), over
        VFG_JEV_CAST_MAX_AGE_S."""
        with self._received_lock:
            received = self._received.get(line["client_line_id"])
        if received is None:
            return ("the bridge has no time it received the line (it came before the bridge last"
                    " started), so it may be past the %g s a Jev cast may be" % self.jev_max_age_s)
        age = self.monotonic() - received
        if age > self.jev_max_age_s:
            return "the line is %.1f s old, past the %g s a Jev cast may be" % (age, self.jev_max_age_s)
        return None

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

    def _keys(self):
        """Both stores' file keys: the list changes only when one of them does."""
        return self._file_key(), self.engine.file_key() if self.engine is not None else None

    def listing(self, player, after=None, wait=None):
        """(200, the player's list) at once, or when ``after`` is the version, once it changes;
        (304, None) when it has not changed in ``wait`` s. The long poll wakes on a change to
        either store."""
        inc, _ = self._modules()
        wait = self.LIST_WAIT_DEFAULT if wait is None else min(wait, self.LIST_WAIT_MAX)
        deadline = time.monotonic() + wait
        keys = self._keys()
        doc = self._list(inc, player)
        while after is not None and doc["version"] == after:
            while self._keys() == keys:
                left = deadline - time.monotonic()
                if left <= 0:
                    return 304, None
                time.sleep(min(self.LIST_POLL_S, left))
            keys = self._keys()
            doc = self._list(inc, player)
        return 200, doc

    def _list(self, inc, player):
        """The player's list: the engine's bindings, then the store's own and @a entries whose
        phrase the engine does not bind for them, one per phrase. Its version is the store's when
        the engine has no bindings (a missing or unreadable store is version 0, with none); with
        some, a number made from both stores' versions and the list (``mixed_version``)."""
        store = self._store(inc)
        version = store.read()["version"]
        out = []
        for e in self._one_per_phrase(inc, self._entries(inc, player, store)):
            cooldown = e.get("cooldown_s")
            if cooldown is None:  # the engine's always say; the store's default is the bridge's
                cooldown = 0.0 if e.get("engine") else DEFAULT_COOLDOWN_S
            out.append({"phrase": e["phrase"], "aliases": self.spotted(inc, e), "cooldown_s": cooldown})
        bound = self.engine.bindings() if self.engine is not None else enginemod.EMPTY
        if bound["version"] or bound["incantations"]:
            version = mixed_version(version, bound["version"], out)
        return {"version": version, "player": player, "incantations": out}

    @staticmethod
    def spotted(inc, entry):
        """The aliases the mod's keyword spotter listens for: the entry's aliases less its
        ``everyday_aliases``, everyday talk in their own right ("ice cream" for "ice beam"), which
        would cast on ordinary conversation; Layer 2 and the Jev cast check still use them. An
        entry without the field has none (Contracts §10)."""
        everyday = set(inc.normalize(a) for a in entry.get("everyday_aliases") or ())
        return [a for a in entry.get("aliases") or () if inc.normalize(a) not in everyday]


def mixed_version(store_version, engine_version, listed):
    """The list's version once the engine has bindings: the first 48 bits of a sha256 of both
    stores' versions and the player's list, so it changes whenever either store does, stays the same
    across a bridge restart, and fits the mod's ``after`` (15 digits at most)."""
    text = json.dumps([store_version, engine_version, listed], sort_keys=True, separators=(",", ":"))
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], 16)
