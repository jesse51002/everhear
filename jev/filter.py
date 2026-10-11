"""The filter: `Filter(store, jev, feed, clock, cfg).step()` is one cycle.

Each step: judge new lines, one Jev call each (or, while failing open, pass
them all as `context`), and for a line that goes to the AI one more, the gather
call; then feed what is waiting. Everything that has to survive a restart is in
the database — a verdict, `fed_at` — so the batch is read from the database
each time rather than held here. What is held here is only what the contract
lets go on a restart: the lines gathered into an ask not yet fed, the failing
clock, and an owed `SYS` line.

The clock, the Jev client and the feed writer are arguments, so the tests drive
every rule with fakes and no test sleeps through a 10-second rule.
"""
import logging
import math
import os
import re
import sqlite3

from jev_clock import iso, parse_iso
from jev_api import ENDPOINT, MODEL, JevDown, JevRefused
from prompt import (GATHER_HEADING, GATHER_RULES, HISTORY_HEADING, KEPT, PART_OF_IT, RULES, Lookout, Menu,
                    build_state, gather_questions, in_short, questions, route)

log = logging.getLogger("vfg-jev")

# Fixed in code, because the contract fixes them.
FAIL_OPEN_AFTER = 10.0     # seconds of Jev failing before every line passes as context
AT_ONCE = ("action_oriented", "conversational")   # both get a live answer: the batch goes at once
GATHER_LOOKBACK = 180.0    # the gather call looks this far back from the ask, at most
GATHER_MAX = 10            # and asks about this many of the speaker's lines, the newest
GROUP_JOIN = " … "         # a group's lines, in the order said, the ask last
TEXT_MAX = 500             # §7: the writer cuts a text longer than this
PROBE_EVERY = 5.0          # while open
FEED_MAX_AGE = 600.0       # a kept line older than this is never fed
STALE_LOOKBACK = 3600.0    # how far back the feed looks for kept lines to report as too old
JUDGE_PER_STEP = 32
FAILURE_PAUSE = 1.0
NO_DB_PAUSE = 2.0
# The cap on VFG_JEV_STATE_TOKENS: the state plus the gather call's ten questions at their largest,
# quotes of 4-byte characters (about 7,300 tokens by the same estimate), stays under Jev's 32k.
MAX_STATE_TOKENS = 24000

SYS_OPEN = "jev unavailable — passing every line as context"
SYS_BACK = "jev back — filtering again"


class Config:
    """The environment, with the defaults that are right for EC2."""

    DEFAULTS = {
        "db": ("VFG_DB", "/opt/vfg/vfg.db", str),
        "lookout": ("VFG_LOOKOUT", "/opt/vfg/lookout.md", str),
        "menu": ("VFG_MENU", "/opt/vfg/menu.md", str),
        "lib": ("VFG_LIB", "/opt/vfg/lib", str),
        "env_file": ("VFG_ENV_FILE", "/opt/vfg/.env", str),
        "batch_seconds": ("VFG_BATCH_SECONDS", 5.0, float),
        "poll_ms": ("VFG_POLL_MS", 50, int),
        "jev_url": ("VFG_JEV_URL", ENDPOINT, str),
        "model": ("VFG_JEV_MODEL", MODEL, str),
        "timeout": ("VFG_JEV_TIMEOUT", 3.0, float),
        "state_tokens": ("VFG_JEV_STATE_TOKENS", 8000, int),
        "history_hours": ("VFG_JEV_HISTORY_HOURS", 3.0, float),
    }

    def __init__(self, **values):
        for name, (_, default, _) in self.DEFAULTS.items():
            setattr(self, name, values.pop(name, default))
        if values:
            raise TypeError("unknown settings: %s" % ", ".join(sorted(values)))
        self.poll_ms = max(10, min(int(self.poll_ms), 250))                    # §5: 250 ms or less
        self.state_tokens = max(1, min(int(self.state_tokens), MAX_STATE_TOKENS))

    @classmethod
    def from_env(cls, env=None):
        """Answers `(cfg, problems)`; a value that does not parse keeps its default."""
        env = os.environ if env is None else env
        values, problems = {}, []
        for name, (var, default, kind) in cls.DEFAULTS.items():
            raw = env.get(var)
            if raw is None or raw.strip() == "":
                continue
            try:
                values[name] = kind(raw.strip())
            except ValueError:
                problems.append("%s=%r does not parse; using %r" % (var, raw, default))
        return cls(**values), problems


_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_FEED_LINE = re.compile(r"^LINE#(\d+) \d\d:\d\d:\d\d (\S+) (\S+) .*? \| (.*)$", re.S)


def feed_text(text):
    """The text as §7's writer cleans it, before any cut: newlines and other
    control characters to spaces."""
    return _CONTROL.sub(" ", str(text).replace("\r\n", " "))


def same_text(written, text):
    """Whether `written` (from the feed) is `text`, allowing the writer's cut
    at 500 characters with an ellipsis, or a group that ends with it. A group
    is fitted to 500 characters (`fit_group`), so it is never cut."""
    full = feed_text(text)
    if written == full or written.endswith(GROUP_JOIN + full):
        return True
    return written.endswith("…") and len(written) > 1 and full.startswith(written[:-1]) and len(full) >= 500


def group_text(gathered, ask):
    """The ask's text with the lines gathered into it before it, in the order
    said, joined with " … "."""
    return GROUP_JOIN.join([r["text"] for r in gathered] + [ask["text"]])


def fit_group(gathered, ask):
    """The gathered lines that fit with the ask in the feed's 500 characters,
    dropping the oldest first, so the ask is always whole at the end. With
    none left the ask goes alone, and the writer cuts it as ever."""
    kept = list(gathered)
    while kept and len(feed_text(group_text(kept, ask))) > TEXT_MAX:
        kept.pop(0)
    return kept


def _listed(answered):
    """`<id> <confidence>, …` for the journal, `-` for a missing answer or none."""
    return ", ".join("%s %s" % (row["id"], "-" if got is None else "%.2f" % got[1]) for row, got in answered) or "-"


def round_half_up(x):
    return int(math.floor(x + 0.5))


def said_at(row):
    """The line's `at` in epoch seconds, or None when it does not parse."""
    try:
        return parse_iso(row["at"])
    except (TypeError, ValueError):
        return None


class Filter:
    def __init__(self, store, jev, feed, clock, cfg):
        self.store = store
        self.jev = jev
        self.feed_writer = feed
        self.clock = clock
        self.cfg = cfg
        self.lookout = Lookout(cfg.lookout)
        self.menu = Menu(cfg.menu)
        self._menu_cut = None        # the menu last said not to fit, so it is said once
        self.open = False            # failing open
        self.failing_since = None    # when the first failing call after a success began
        self.last_probe = None
        self.owed = []               # SYS texts not yet written to the feed
        self.groups = {}             # ask id -> the lines gathered into it, oldest first, until it is fed
        self.unmarked = []           # fed, but fed_at not yet written
        self.stale = set()           # kept lines already reported as too old to feed
        self.stopping = False        # set by the signal handler: finish the call in hand
        # A run killed between writing a batch and setting fed_at leaves lines
        # in the feed that look unfed. Lines judged before this run started
        # are looked up in the `feed` table once each before they are fed.
        self.started = iso(clock.time())
        self.looked_up = set()

    # -- the cycle ---------------------------------------------------------------

    def step(self):
        """One cycle. Answers None to run again after the usual poll interval,
        or a number of seconds to pause first."""
        if not self.store.ready():
            return NO_DB_PAUSE
        now = self.clock.time()
        if not self.open and self.failing_since is not None and now - self.failing_since >= FAIL_OPEN_AFTER:
            self._go_open(now)
        if self.open and now - self.last_probe >= PROBE_EVERY:
            self._probe(now)
        pause = None
        if self.open:
            self._pass_open()
        else:
            pause = self._judge_new()
        self.feed()
        return pause

    def flush_now(self):
        """On stop: feed everything kept and waiting, whatever the batch timer."""
        if self.store.ready():
            self.feed(force=True)

    # -- judging -----------------------------------------------------------------

    def _judge_new(self):
        for row in self.store.unjudged(JUDGE_PER_STEP):
            if self.stopping:
                break
            if not row["text"].strip():
                self.store.set_class(row["id"], "not_relevant", None, iso(self.clock.time()))
                log.info("judge %s %s %s not_relevant - · blank, no call", row["id"], row["player_name"], row["source"])
                continue
            if not self._judge_one(row):
                return FAILURE_PAUSE
            self.feed()   # so a line for the AI goes before the next call
        return None

    def _state(self, marked, began, share=1.0, rules=RULES, heading=HISTORY_HEADING):
        since = iso(began - self.cfg.history_hours * 3600)
        # the lines said and the brain's records of what it did, newest first
        others = sorted(list(self.store.history(since, exclude=[r["id"] for _, r in marked]))
                        + self.store.ai_records(since), key=lambda r: (r["at"], r["id"]), reverse=True)
        menu = self.menu.text()
        state, _ = build_state(self.lookout.text(), marked, others, self.cfg.state_tokens, share, menu, rules, heading)
        if menu and state.get("actions") != menu and menu != self._menu_cut:
            self._menu_cut = menu
            log.warning("menu %s is over half the state's room; Jev sees its first lines", self.cfg.menu)
        return state

    def _ask(self, row, began, share=1.0):
        return self.jev.ask(self._state([(1, row)], began, share), questions(row), required="d_what")

    def _judge_one(self, row):
        """The routing call for `row`, and the gather call when it goes to the AI.
        Answers False when Jev failed (the line stays unjudged)."""
        began = self.clock.time()
        try:
            result = self._ask(row, began)
        except JevRefused as e:
            log.warning("refused %s: %s — asking once more, half the history", row["id"], e)
            began = self.clock.time()
            try:
                result = self._ask(row, began, share=0.5)
            except JevRefused as e2:
                self.store.set_class(row["id"], "context", None, iso(self.clock.time()))
                log.error("refused again %s: %s — passed as context", row["id"], e2)
                return True
            except JevDown as e2:
                return self._failed(began, row, e2)
        except JevDown as e:
            return self._failed(began, row, e)

        self.failing_since = None
        now = self.clock.time()
        at = iso(now)
        cls, conf = route(result.answers)
        usage = {"at": at, "model": self.cfg.model, "input_tokens": result.input_tokens,
                 "cost_usd": result.cost_usd, "ref": row["id"]}
        written = self.store.judge(row["id"], cls, round(conf, 3), at, usage)
        log.info("judge %s %s %s %s %.2f · %s · %dms %stok", row["id"], row["player_name"], row["source"], cls, conf,
                 in_short(result.answers), result.ms, "?" if result.input_tokens is None else result.input_tokens)
        if not written:   # the bridge dropped it while Jev judged it: never fed, nothing gathered
            log.warning("no verdict written for %s: the bridge dropped it (only an incantation) while Jev judged it",
                        row["id"])
            return True
        said = said_at(row)
        if cls in KEPT and (said is None or said < now - FEED_MAX_AGE):
            self._too_old(row, said)
        elif cls in AT_ONCE:
            self._gather(row, said)
        return True

    def _gather(self, ask, said):
        """The gather call: is each of the speaker's unsent lines judged since their
        last line to the AI, said 3 minutes before `ask` at most, part of it
        (`store.gather_window`)? The yes lines wait in `groups` and go with the
        ask when it is fed. No lines, no call. A call that fails leaves the ask to
        go alone; it is not Jev failing to judge a line, so the failing clock is
        left as it is."""
        window = self.store.gather_window(ask, iso(said - GATHER_LOOKBACK), GATHER_MAX)
        if not window:
            return
        marked = [("ask", ask)] + list(enumerate(window, 1))
        try:
            state = self._state(marked, self.clock.time(), rules=GATHER_RULES, heading=GATHER_HEADING)
            result = self.jev.ask(state, gather_questions(ask, window), required=None)
        except (JevDown, JevRefused) as e:
            log.warning("gather %s failed: %s — the ask goes alone", ask["id"], e)
            return
        yes, no = [], []
        for k, row in enumerate(window, 1):
            got = result.answers.get("line_%d" % k)   # missing or malformed: a no
            (yes if got is not None and got[0] == PART_OF_IT else no).append((row, got))
        kept = fit_group([row for row, _ in yes], ask)
        if kept:
            self.groups[ask["id"]] = kept
        try:
            self.store.add_usage(at=iso(self.clock.time()), model=self.cfg.model, input_tokens=result.input_tokens,
                                 cost_usd=result.cost_usd, ref="gather:%s" % ask["id"])
        except sqlite3.Error as e:
            log.warning("could not write the gather call's usage row: %s", e)
        dropped = len(yes) - len(kept)
        log.info("gather %s %s · %d asked, %d yes: %s · no: %s · %dms %stok%s", ask["id"], ask["player_name"],
                 len(window), len(yes), _listed(yes), _listed(no), result.ms,
                 "?" if result.input_tokens is None else result.input_tokens,
                 " · the oldest %d dropped to fit 500 characters" % dropped if dropped else "")

    def _failed(self, began, row, err):
        if self.failing_since is None:
            self.failing_since = began
        log.warning("jev failed on %s: %s · failing %.1fs", row["id"], err, self.clock.time() - self.failing_since)
        return False

    # -- failing open --------------------------------------------------------------

    def _go_open(self, now):
        self.open = True
        self.last_probe = now
        self.owed.append(SYS_OPEN)
        log.warning("mode open: jev failing %.1fs — passing every line as context", now - self.failing_since)

    def _probe(self, now):
        self.last_probe = now
        try:
            result = self.jev.probe()
        except (JevDown, JevRefused) as e:
            log.info("probe failed: %s", e)
            return
        self.open = False
        self.failing_since = None
        self.owed.append(SYS_BACK)
        log.warning("mode filtering: jev back (probe %dms)", result.ms)
        try:
            self.store.add_usage(at=iso(self.clock.time()), model=self.cfg.model,
                                 input_tokens=result.input_tokens, cost_usd=result.cost_usd, ref="probe")
        except sqlite3.Error as e:
            log.warning("could not write the probe's usage row: %s", e)

    def _pass_open(self):
        n = self.store.pass_all_as_context(iso(self.clock.time()))
        if n:
            log.info("open: passed %d line(s) as context", n)

    # -- feeding -------------------------------------------------------------------

    def fields(self, row):
        """`<player> <voice|chat> <action_oriented|conversational|context> <confidence|-> <NdB|->`"""
        conf = row["jev_confidence"]
        peak = row["peak_dbfs"]
        parts = [
            self.player_field(row),
            row["source"],
            row["jev_class"],
            "-" if conf is None else "%.2f" % conf,
            "%ddB" % round_half_up(peak) if row["source"] == "voice" and peak is not None else "-",
        ]
        return " ".join(parts)

    def _too_old(self, row, said):
        self.groups.pop(row["id"], None)
        if row["id"] not in self.stale:
            self.stale.add(row["id"])
            why = "over 10 minutes ago" if said is not None else "not an ISO-8601 time"
            log.warning("not feeding %s: said at %r, %s", row["id"], row["at"], why)

    def player_field(self, row):
        return "_".join(row["player_name"].split()) or "-"

    def _already_fed(self, waiting, now):
        """Drop, and mark fed, any waiting line an earlier run already wrote to
        the feed. Only lines judged before this run began are looked up, each
        once, so a run that was never killed never reads the feed table.
        The key is the line's `at` (to the millisecond), its player, its
        source and its text: the same words from the same player at the same
        millisecond are the same line."""
        suspects = [(r, t) for r, t in waiting if r["id"] not in self.looked_up
                    and (r["jev_judged_at"] or "") < self.started]
        if not suspects:
            return waiting
        try:
            written = self.store.feed_lines_at({iso(t) for _, t in suspects})
        except sqlite3.Error as e:
            log.warning("could not read the feed table to check for lines fed before a restart: %s", e)
            written = []
        found, used = {}, set()
        for r, t in suspects:
            at = iso(t)
            for seq, written_at, line in written:
                m = _FEED_LINE.match(line)
                if seq in used or written_at != at or not m:
                    continue
                if (m.group(2), m.group(3)) == (self.player_field(r), r["source"]) and same_text(m.group(4), r["text"]):
                    found[r["id"]] = seq
                    used.add(seq)
                    break
        if found:
            self.store.mark_fed(list(found), iso(now))
        self.looked_up.update(r["id"] for r, _ in suspects)   # only once the marks are in
        if not found:
            return waiting
        for line_id, seq in found.items():
            log.warning("%s was already fed as LINE#%s before a restart — marked fed, not fed again", line_id, seq)
        return [(r, t) for r, t in waiting if r["id"] not in found]

    def feed(self, force=False):
        """Feed what is waiting: owed SYS lines first, then the batch, if an
        action_oriented or conversational line is in it or `VFG_BATCH_SECONDS`
        have passed since the earliest verdict in it (or `force`)."""
        now = self.clock.time()
        if self.unmarked:
            self.store.mark_fed(self.unmarked, iso(now))
            self.unmarked = []
        while self.owed:
            try:
                self.feed_writer.append("SYS", "", self.owed[0])
            except Exception as e:
                log.warning("feed write failed (SYS): %s — trying again next step", e)
                return
            log.info("sys %s", self.owed.pop(0))
        waiting = []   # (row, when it was said)
        for row in self.store.kept_unfed(iso(now - STALE_LOOKBACK)):
            said = said_at(row)
            if said is None or said < now - FEED_MAX_AGE:
                self._too_old(row, said)
            else:
                waiting.append((row, said))
        waiting = self._already_fed(waiting, now)
        if not waiting:
            return
        if not force and not any(r["jev_class"] in AT_ONCE for r, _ in waiting):
            first = min(parse_iso(r["jev_judged_at"]) if r["jev_judged_at"] else now for r, _ in waiting)
            if now - first < self.cfg.batch_seconds - 1e-6:
                return
        # `at` goes in canonical form: the very string the bridge stores, and
        # one the writer is sure to accept even if a row was stored otherwise
        entries = [("LINE", self.fields(r), group_text(self.groups.get(r["id"], []), r), iso(said))
                   for r, said in waiting]
        try:
            seqs = self.feed_writer.append_batch(entries)
        except Exception as e:
            log.warning("feed write failed: %s — %d line(s) wait for the next step", e, len(entries))
            return
        ids = [r["id"] for r, _ in waiting]
        for line_id in ids:
            self.groups.pop(line_id, None)
        try:
            self.store.mark_fed(ids, iso(self.clock.time()))
        except sqlite3.Error as e:
            self.unmarked = ids
            log.warning("fed %d line(s) but could not mark them: %s — marking next step", len(ids), e)
            return
        seqs = list(seqs or [])
        where = ("#%s–#%s" % (seqs[0], seqs[-1]) if len(seqs) > 1 else "#%s" % seqs[0]) if seqs else ""
        log.info("fed %d line%s %s", len(ids), "" if len(ids) == 1 else "s", where)
