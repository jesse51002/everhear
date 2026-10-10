"""Every SQL statement the filter runs, against `/opt/vfg/vfg.db` (§4).

The bridge creates the schema and owns the journal mode; this never creates or
alters either. It writes only `jev_class`, `jev_confidence`, `jev_judged_at` and
`fed_at` on `lines`, and inserts `usage` rows. Each write is one short
transaction, and every update guards itself so it can never overwrite a verdict
somebody else (or an earlier step) already wrote.

One connection, autocommit (`isolation_level=None`), a 5 s busy timeout. It is
opened read-write without create, so a missing database is waited for rather
than made empty.
"""
import logging
import os
import re
import sqlite3
import urllib.parse

log = logging.getLogger("vfg-jev")

COLUMNS = "id, at, player_name, source, text, peak_dbfs, jev_class, jev_confidence, jev_judged_at, fed_at"
RECORDS = ("action", "task", "result")   # the brain's AI rows that go into Jev's history
_AI_ROW = re.compile(r"^AI#\d+ \d\d:\d\d:\d\d (\S+) \S+ \| (.*)$", re.S)


class Store:
    def __init__(self, path):
        self.path = path
        self.conn = None
        self._said = None   # the last "not ready" reason logged, so it is logged once

    # -- opening ---------------------------------------------------------------

    def ready(self):
        """True once the database and its `lines` and `usage` tables exist.
        Until then it logs why, once, and answers False."""
        if self.conn is not None:
            return True
        if not os.path.exists(self.path):
            return self._not_ready("no database at %s yet; looking again every 2 s" % self.path)
        uri = "file:%s?mode=rw" % urllib.parse.quote(os.path.abspath(self.path))
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
            conn.row_factory = sqlite3.Row
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('lines', 'usage')")}
        except sqlite3.Error as e:
            return self._not_ready("cannot open %s yet (%s); looking again every 2 s" % (self.path, e))
        missing = {"lines", "usage"} - names
        if missing:
            conn.close()
            return self._not_ready("%s has no %s table yet; looking again every 2 s"
                                   % (self.path, " or ".join(sorted(missing))))
        self.conn = conn
        self._said = None
        log.info("database %s open", self.path)
        return True

    def _not_ready(self, why):
        if why != self._said:
            log.warning(why)
            self._said = why
        return False

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def _tx(self, statements):
        """Run `[(sql, params)]` as one transaction; answers the rowcounts."""
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            counts = [c.execute(sql, params).rowcount for sql, params in statements]
            c.execute("COMMIT")
        except BaseException:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass   # SQLite already rolled back on its own
            raise
        return counts

    # -- reading ---------------------------------------------------------------

    def unjudged(self, limit=32):
        """Lines to judge: no verdict yet and not the AI's own, oldest first."""
        return self.conn.execute(
            "SELECT %s FROM lines WHERE jev_class IS NULL AND source != 'ai' "
            "ORDER BY at, id LIMIT ?" % COLUMNS, (limit,)).fetchall()

    def ask_again(self, judged_since, limit=5):
        """Jev's own `not_relevant` verdicts (a confidence, so not a blank line)
        judged since `judged_since`, newest `at` first."""
        return self.conn.execute(
            "SELECT %s FROM lines WHERE jev_class = 'not_relevant' AND jev_confidence IS NOT NULL "
            "AND jev_judged_at >= ? ORDER BY at DESC, id DESC LIMIT ?" % COLUMNS,
            (judged_since, limit)).fetchall()

    def history(self, since, exclude=(), limit=2000):
        """Every line said since `since`, the AI's included, newest first,
        leaving out `exclude` (the lines asked about, which go in regardless)."""
        exclude = list(exclude)
        holes = ",".join("?" * len(exclude))
        skip = (" AND id NOT IN (%s)" % holes) if exclude else ""
        return self.conn.execute(
            "SELECT %s FROM lines WHERE at >= ?%s ORDER BY at DESC, id DESC LIMIT ?" % (COLUMNS, skip),
            [since] + exclude + [limit]).fetchall()

    def ai_records(self, since, scan=2000):
        """The brain's own records said since `since` — the feed's `AI` rows of
        kind action, task and result, among its last `scan` rows — as history
        rows whose `source` is the kind, newest first. A `say` is left out:
        `vfg say` keeps it in `lines` too."""
        rows = self.conn.execute(
            "SELECT seq, at, line FROM (SELECT seq, at, line FROM feed ORDER BY seq DESC LIMIT ?) "
            "WHERE line LIKE 'AI#%' AND at >= ? ORDER BY at DESC, seq DESC", (scan, since)).fetchall()
        records = []
        for seq, at, line in rows:
            m = _AI_ROW.match(line)
            if m and m.group(1) in RECORDS:
                records.append({"id": "AI#%d" % seq, "at": at, "player_name": "AI", "source": m.group(1),
                                "text": m.group(2), "peak_dbfs": None})
        return records

    def kept_unfed(self, since):
        """Kept lines not yet fed, said since `since`, oldest first."""
        return self.conn.execute(
            "SELECT %s FROM lines WHERE jev_class IN ('action_oriented', 'conversational', 'context') "
            "AND fed_at IS NULL AND at >= ? ORDER BY at, id" % COLUMNS, (since,)).fetchall()

    def feed_lines_at(self, ats):
        """`(seq, at, line)` of every LINE already in the `feed` table whose `at`
        is one of `ats` — read only, to find a batch an earlier run wrote but
        never marked (it was killed between the two)."""
        ats = list(ats)
        return self.conn.execute(
            "SELECT seq, at, line FROM feed WHERE line LIKE 'LINE#%%' AND at IN (%s) ORDER BY seq"
            % ",".join("?" * len(ats)), ats).fetchall()

    # -- writing ---------------------------------------------------------------

    def judge(self, line_id, cls, confidence, judged_at, flips=(), usage=None):
        """One transaction: the new line's verdict, each flip of a line asked
        about again, and the call's `usage` row. Answers the ids that flipped."""
        statements = [(
            "UPDATE lines SET jev_class = ?, jev_confidence = ?, jev_judged_at = ? "
            "WHERE id = ? AND jev_class IS NULL", (cls, confidence, judged_at, line_id))]
        flips = list(flips)
        for fid, fcls, fconf in flips:
            statements.append((
                "UPDATE lines SET jev_class = ?, jev_confidence = ?, jev_judged_at = ? "
                "WHERE id = ? AND jev_class = 'not_relevant'", (fcls, fconf, judged_at, fid)))
        if usage is not None:
            statements.append(self._usage_sql(**usage))
        counts = self._tx(statements)
        return [fid for (fid, _, _), n in zip(flips, counts[1:]) if n]

    def set_class(self, line_id, cls, confidence, judged_at):
        """A verdict written without Jev's answer (a blank line, a refusal)."""
        return self._tx([(
            "UPDATE lines SET jev_class = ?, jev_confidence = ?, jev_judged_at = ? "
            "WHERE id = ? AND jev_class IS NULL", (cls, confidence, judged_at, line_id))])[0]

    def pass_all_as_context(self, judged_at):
        """Failing open: every unjudged line but the AI's becomes `context`
        with no confidence. Answers how many."""
        return self._tx([(
            "UPDATE lines SET jev_class = 'context', jev_confidence = NULL, jev_judged_at = ? "
            "WHERE jev_class IS NULL AND source != 'ai'", (judged_at,))])[0]

    def mark_fed(self, ids, fed_at):
        ids = list(ids)
        holes = ",".join("?" * len(ids))
        return self._tx([(
            "UPDATE lines SET fed_at = ? WHERE fed_at IS NULL AND id IN (%s)" % holes,
            [fed_at] + ids)])[0]

    def add_usage(self, **usage):
        self._tx([self._usage_sql(**usage)])

    @staticmethod
    def _usage_sql(at, model, input_tokens, cost_usd, ref):
        return ("INSERT INTO usage (at, component, model, input_tokens, output_tokens, cost_usd, ref) "
                "VALUES (?, 'jev', ?, ?, 0, ?, ?)", (at, model, input_tokens, cost_usd, ref))
