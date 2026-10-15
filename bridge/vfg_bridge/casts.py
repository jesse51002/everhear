"""Casts: an incantation's ability started in the game engine the moment its phrase is heard.

Every incantation is the engine's (the game engine spec, "Incantations"): a phrase bound, per
player, to an ability (or a scenario with its args), read from the engine's export (engine.py). A
cast comes from the mod's keyword spotter (POST /v1/casts, layer ``spotter``), from Layer 2, the
phoneme match on a voice line as it arrives (layer ``transcript``), or from the Jev filter, which
judged a stored voice line a cast before routing it (POST /v1/jev/casts, layer ``jev``). Each cast's
checks and record go through one lock, so each sees every cast before it:

1. a repeated client_cast_id answers what the first did (once the first has its answer);
2. a cast of an incantation whose window overlaps an earlier cast of it by that player that
   fired (or is still being asked about, ``pending``), or one the spotter made whatever became of
   it, is ``duplicate``: that utterance was decided, whichever layer came first. A transcript cast
   that did not fire decides nothing past its line: its window is the whole line's, and would
   swallow the next words;
3. ``stale`` when the bridge itself has held it VFG_CAST_WAIT_S since its request came, so it never
   fires seconds late;
4. otherwise its row is stored ``pending``, the lock is let go, and the engine is asked (``cast``
   on its control socket, waited for at most VFG_ENGINE_CAST_WAIT_S and never past VFG_CAST_WAIT_S,
   which the engine is told, so it drops a cast it has not begun by then); the row then takes the
   status the mod knows (``engine_status``). No cast waits on another's engine round trip.

Cooldowns are the ability's own, in the engine. A ``pending`` row lives only while the engine is
asked; one a stopped bridge left is made ``failed`` on the next start (``DB.fail_pending``).

The cast's row is stored before the answer; its events row and GAME line after it.
Windows are on the mod's audio clock, the stale check on the bridge's monotonic one.

The drop rule then decides a voice line: one that overlaps a cast of that player's and
has at most one word besides the phrase is stored with ``dropped = 'cast'``, and Jev
never sees it.  A spotter's cast that comes after its line drops it then, if Jev has not
judged it yet.  A line the Jev filter casts is dropped too, whatever became of its cast.
"""

import collections
import datetime as _dt
import logging
import threading
import time

from . import engine as enginemod
from . import sounds, timeutil, ulid

log = logging.getLogger("vfg_bridge")

# Layer 2 casts only from a line of at most this many words (normalised, fillers counted):
# VFG_INCANT_LAYER2_MAX_WORDS, 0 for any length. A cast is said on its own, and a longer line
# is talk. On 2026-10-12, with each player given that night's six incantations, the stored
# aliases cast falsely on 216 of the 843 lines of 10-08's talk, and with 20 of the alias call's
# on 397; at 6 words, 7 and 31. On the two sessions with casts, 10-11's false casts went from 28
# to 2 and that night's from 3 to 1, every cast found as before (78% and 94% of them, stored).
LAYER2_MAX_WORDS = 6
# How far back a player's casts are read: a player's clock up to 10 minutes behind (the clock
# guard's limit) and a long line's window.
LOOKBACK = _dt.timedelta(minutes=30)
ERROR_MAX = 300
# When /v1/lines received each line, kept in memory for a Jev cast's age (the bridge stores no
# such time): this long, and at most this many lines.
RECEIVED_KEEP = _dt.timedelta(minutes=30)
RECEIVED_MAX = 20000
# A Jev cast whose phrase was said this long before its line ended is stale: a long line comes when
# the whole utterance ends, so a phrase at its start would fire seconds late. Where the phrase is,
# the phoneme match (sounds.py) finds, as loose as this; its time is placed by word between at and ended_at.
PHRASE_LEAD_S = 3.0
PHRASE_ALLOWANCE = 0.4   # finds "variable" for fire ball (0.32), not noise (0.43+)
# A cast's record is under this before the binding's id (engine:steve.fire_ball), as since the casts
# of the old store, whose rows the history keeps, had their own ids.
ENGINE_KEY = "engine:"
# An engine cast's row while the engine is asked, outside the lock; never an answer.
PENDING = "pending"
# How long a repeated client_cast_id waits for its first request's engine answer, past that
# answer's own longest wait (the engine's, and the client's margin on it).
SETTLE_MARGIN_S = 1.0


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
    LIST_POLL_S = 0.2         # how often a held list request looks at the export
    LIST_WAIT_DEFAULT = 25.0
    LIST_WAIT_MAX = 30.0

    def __init__(self, db, feed, engine, slack_s=0.3, allowance=0.25, layer2=True, wait_s=1.5, clock=None,
                 monotonic=None, logger=None, jev_max_age_s=2.0, layer2_max_words=LAYER2_MAX_WORDS,
                 engine_wait_s=1.0):
        self.db = db
        self.feed = feed
        self.engine = engine          # engine.Engine: its export, and its socket for a cast
        self.slack_s = slack_s
        self.allowance = allowance
        self.layer2 = layer2
        self.layer2_max_words = layer2_max_words
        self.wait_s = wait_s
        self.jev_max_age_s = jev_max_age_s
        self.engine_wait_s = engine_wait_s
        self.clock = clock or timeutil.utcnow
        self.monotonic = monotonic or time.monotonic  # the request's arrival is on this clock
        self.log = logger or log
        self.lock = threading.Lock()  # every cast's checks and record, the spotter's and Layer 2's
        self._asking = {}             # a cast's id -> an Event set once its row is final
        self._received = collections.OrderedDict()  # client_line_id -> self.monotonic when it came
        self._received_lock = threading.Lock()

    @classmethod
    def from_config(cls, cfg, db, feed, clock=None):
        return cls(db, feed, enginemod.Engine(cfg.engine_socket, cfg.engine_export), cfg.cast_slack_s,
                   cfg.incant_allowance, cfg.incant_layer2, cfg.cast_wait_s, clock,
                   jev_max_age_s=cfg.jev_cast_max_age_s, layer2_max_words=cfg.incant_layer2_max_words,
                   engine_wait_s=cfg.engine_cast_wait_s)

    def _entries(self, player):
        """The player's incantations: the engine's bindings for them (case-insensitive)."""
        return self.engine.for_player(player)

    @staticmethod
    def _lookup(entries, heard):
        """The first of ``entries`` whose phrase or an alias, normalised, is ``heard``, or None."""
        h = sounds.normalize(heard)
        if not h:
            return None
        for e in entries:
            if h in sounds.targets(e):
                return e
        return None

    @staticmethod
    def _key(entry):
        """What a cast of ``entry`` is recorded under, as ``incantation_id``."""
        return ENGINE_KEY + entry["id"]

    # -- POST /v1/casts ------------------------------------------------------------

    def cast(self, player, phrase, heard, started, ended, client_cast_id, arrived=None):
        """A spotter's cast: (HTTP status, body, what to do once answered, or None).
        ``arrived``: when its request came, on ``self.monotonic``."""
        arrived = self.monotonic() if arrived is None else arrived
        with self.lock:
            first = self.db.cast_by_client_id(client_cast_id)
            if first is None:
                entry = self._lookup(self._entries(player), phrase)
                if entry is None:
                    return 404, {"error": "%s has no incantation %r" % (player, phrase)}, None
                cast = self._try(entry, player, heard, "spotter", started, ended, client_cast_id, arrived)
        if first is not None:
            first = self._settled(first)
            if first["status"] == PENDING:
                return 503, {"error": "cast %s is still waiting on the game engine" % first["id"]}, None
            return 202, answer(first), None
        self._ask(cast, entry, player)
        return 202, answer(cast), lambda: self._after(cast, entry)

    def _after(self, cast, entry):
        """Once a spotter's cast is answered: announce it, and drop a line of that utterance
        the line endpoint stored before the cast came, which Jev has not judged yet."""
        self.announce(cast)
        try:
            started, ended = timeutil.parse_time(cast["started_at"]), timeutil.parse_time(cast["ended_at"])
            pad = _dt.timedelta(seconds=self.slack_s)
            for line_id, text in self.db.unjudged_lines(
                    cast["player_name"], timeutil.format_time(started - pad),
                    timeutil.format_time(ended + pad), timeutil.format_time(started - LOOKBACK)):
                if sounds.drop(text, entry) and self.db.drop_line(line_id, cast["id"]):
                    break
        except Exception:
            self.log.exception("could not drop the line of cast %s", cast["id"])

    def _try(self, entry, player, heard, layer, started, ended, client_cast_id, arrived, line_id=None, late=None):
        """Check and store one cast; call under the lock. Answers its row: ``pending`` when it may
        go (the caller then asks the engine, outside the lock, with _ask), else ``duplicate`` or
        ``stale``. ``late``: why it is too late to fire (a Jev cast's line too old)."""
        now = self.clock()
        cast = {"id": ulid.new(), "at": timeutil.format_time(now),
                "started_at": timeutil.format_time(started), "ended_at": timeutil.format_time(ended),
                "player_name": player, "phrase": entry["phrase"], "heard": heard, "layer": layer,
                "incantation_id": self._key(entry), "action": entry.get("ability") or entry.get("scenario"),
                "client_cast_id": client_cast_id, "line_id": line_id}
        status, why = self._check(entry, player, started, ended, now)
        left = self.wait_s - (self.monotonic() - arrived)
        if status is None and left <= 0:
            status, why = "stale", "held %.1f s before it could start" % (self.wait_s - left)
        if status is None and late:
            status, why = "stale", late
        if status is None:  # reserved: the engine is asked outside the lock (_ask)
            status, cast["arrived"] = PENDING, arrived
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
        """(status, why, the engine's own status) of a cast, sent to the engine and waited for at
        most VFG_ENGINE_CAST_WAIT_S, and never past the bridge's own wait. With the engine down or
        late it ``failed``, in words, and is announced so."""
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
        status, why = engine_status(reply, player, entry.get("ability") or entry.get("scenario"))
        return status, why, reply.get("status") if reply.get("ok") else None

    def _near(self, player, started, ended, now):
        """The player's casts whose window, widened by VFG_CAST_SLACK_S on each side, meets
        [started, ended]."""
        pad = _dt.timedelta(seconds=self.slack_s)
        return self.db.overlapping_casts(player, timeutil.format_time(started - pad),
                                         timeutil.format_time(ended + pad),
                                         timeutil.format_time(min(started, now) - LOOKBACK))

    def _check(self, entry, player, started, ended, now):
        """(None, None) when the cast may go, else (``duplicate``, why): a cast of the same
        incantation in this window that fired or is being asked about, or any the spotter made.
        Nothing else: its cooldown is the ability's, in the engine."""
        key = self._key(entry)
        for c in self._near(player, started, ended, now):
            if c["incantation_id"] == key and (c["status"] in ("fired", PENDING) or c["layer"] == "spotter"):
                return "duplicate", "it was cast in this window already (%s, %s)" % (
                    c["id"], c["status"])
        return None, None

    def announce(self, cast):
        """A fired or failed cast's events row and GAME cast line, whatever watch.json says."""
        player, phrase = cast["player_name"], cast["phrase"]
        if cast["status"] == "fired":
            text = '%s cast "%s": %s, %s in the engine' % (
                player, phrase, cast["action"],
                "charging" if cast.get("engine_status") == "charging" else "fired")
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
        entries = self._entries(player)
        if entries:
            self.received(client_line_id, arrived)  # before it is stored: a Jev cast's age
        if not entries or self.db.line_id(client_line_id) is not None:
            return NOTHING
        words = sounds.words(text)
        if self.layer2 and (not self.layer2_max_words or len(words) <= self.layer2_max_words):
            taken = []  # the word spans cast already: one cast for the same words
            for m in sounds.match_line(text, self._one_per_phrase(entries), self.allowance):
                if any(m.start < end and start < m.end for start, end in taken):
                    continue
                taken.append((m.start, m.end))
                key = "line:%s:%s" % (client_line_id, self._key(m.entry))
                heard = " ".join(words[m.start:m.end]) or None
                with self.lock:
                    if self.db.cast_by_client_id(key) is not None:
                        continue
                    cast = self._try(m.entry, player, heard, "transcript", started, ended, key, arrived)
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
            if sounds.drop(text, entry):
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
                entry = self._lookup(self._entries(player), phrase)
                if entry is None:
                    return 404, {"error": "%s has no incantation %r" % (player, phrase)}, None
                started = timeutil.parse_time(line["at"])
                ended = timeutil.parse_time(line["ended_at"] or line["at"])
                heard = " ".join(sounds.words(line["text"]))[:200] or None
                late = self._too_old(line) or self._said_early(entry, line["text"], started, ended)
                cast = self._try(entry, player, heard, "jev", started, ended, key, arrived,
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
    def _said_early(entry, text, started, ended):
        """Why a Jev cast is too late because its phrase was said early in its line, or None: the
        last place the phoneme match (sounds.py; any length, PHRASE_ALLOWANCE) finds the phrase or an
        alias, its last word's time estimated by word between the line's start and end, more than
        PHRASE_LEAD_S before the end. None when the phrase is not found: then it is allowed."""
        words = sounds.words(text)
        span = (ended - started).total_seconds()
        if not words or span <= PHRASE_LEAD_S:
            return None
        last, offset = None, 0
        while offset < len(words):
            found = sounds.match_line(" ".join(words[offset:]), [entry], PHRASE_ALLOWANCE)
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
    def _one_per_phrase(entries):
        """The entries, one per normalised phrase, the first kept."""
        out, seen = [], set()
        for e in entries:
            phrase = sounds.normalize(e.get("phrase"))
            if phrase not in seen:
                seen.add(phrase)
                out.append(e)
        return out

    # -- GET /v1/incantations ------------------------------------------------------

    def listing(self, player, after=None, wait=None):
        """(200, the player's list) at once, or when ``after`` is the version, once it changes;
        (304, None) when it has not changed in ``wait`` s. The list holds the player's own
        incantations, and ``server_phrases``, every phrase on the server (server_phrases()). The
        version is the export's, which every binding change bumps whoever's it is, so a held request
        answers when any player's phrases change; a missing or unreadable export is version 0, with
        none."""
        wait = self.LIST_WAIT_DEFAULT if wait is None else min(wait, self.LIST_WAIT_MAX)
        deadline = time.monotonic() + wait
        key = self.engine.file_key()
        doc = self._list(player)
        while after is not None and doc["version"] == after:
            while self.engine.file_key() == key:
                left = deadline - time.monotonic()
                if left <= 0:
                    return 304, None
                time.sleep(min(self.LIST_POLL_S, left))
            key = self.engine.file_key()
            doc = self._list(player)
        return 200, doc

    def _list(self, player):
        """The player's list: the engine's bindings for them, one per phrase, each with the aliases
        the spotter listens for and its ability's cooldown_s (0 while it is not loaded); and every
        phrase on the server."""
        export = self.engine.bindings()
        out = [{"phrase": e["phrase"], "aliases": self.spotted(e), "cooldown_s": e.get("cooldown_s") or 0.0}
               for e in self._one_per_phrase(self._entries(player))]
        return {"version": export["version"], "player": player, "incantations": out,
                "server_phrases": self.server_phrases(export["incantations"])}

    @staticmethod
    def server_phrases(entries):
        """Every incantation phrase on the server, whoever's (the player's own and every other
        player's), normalised, once each, sorted; phrases alone, no aliases. The mod boosts them as
        speech-to-text hotwords, since players talk about each other's spells, and a hotword boosts
        the real word, not another spelling of it (Prototype_Addressing_The_AI)."""
        return sorted(set(p for p in (sounds.normalize(e.get("phrase")) for e in entries) if p))

    @staticmethod
    def spotted(entry):
        """The aliases the mod's keyword spotter listens for: the entry's aliases less its
        ``everyday_aliases``, everyday talk in their own right ("ice cream" for "ice beam"), which
        would cast on ordinary conversation; Layer 2 and the Jev cast check still use them. An
        entry without the field has none (Contracts §10)."""
        everyday = set(sounds.normalize(a) for a in entry.get("everyday_aliases") or ())
        return [a for a in entry.get("aliases") or () if sounds.normalize(a) not in everyday]

