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

COLUMNS = "id, at, ended_at, player_name, source, text, peak_dbfs, jev_class, jev_confidence, jev_judged_at, fed_at"
RECORDS = ("action", "task", "result")   # the brain's AI rows that go into Jev's history
_AI_ROW = re.compile(r"^AI#\d+ \d\d:\d\d:\d\d (\S+) (\S+) \| (.*)$", re.S)


def named(task, text):
    """A record's text with the task it is about in front (`frost_bow_alex: new: …`), unless the
    text already starts with it, as a result's does, or it is about no task (`-`)."""
    if task == "-" or text.startswith(task):
        return text
    return "%s: %s" % (task, text)


def _field(row, name):
    """`row[name]`, or None when the row has no such column."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


class Store:
    def __init__(self, path):
        self.path = path
        self.conn = None
        self._said = None   # the last "not ready" reason logged, so it is logged once
        self._dropped = False
        self._casts = False   # whether the bridge's `casts` table is there, once it is known to be

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

    def _live(self):
        """` AND dropped IS NULL` once `lines` has the bridge's `dropped` column (a line that
        was only an incantation), so Jev never sees one; nothing on an older database."""
        if not self._dropped:
            cols = self.conn.execute("SELECT * FROM lines LIMIT 0").description
            self._dropped = any(c[0] == "dropped" for c in cols)
        return " AND dropped IS NULL" if self._dropped else ""

    # -- reading ---------------------------------------------------------------

    def unjudged(self, limit=32):
        """Lines to judge: no verdict yet and not the AI's own, oldest first."""
        return self.conn.execute(
            "SELECT %s FROM lines WHERE jev_class IS NULL AND source != 'ai'%s "
            "ORDER BY at, id LIMIT ?" % (COLUMNS, self._live()), (limit,)).fetchall()

    def gather_window(self, ask, since, limit=10, sent_since=None):
        """The gather call's lines for `ask`: the same speaker's lines, not the
        AI's and not dropped, said before the ask (by `at`, then `id`) and since
        `since`, that Jev classed `context` or `not_relevant` with a confidence
        (so never a blank line, nor one passed as context while Jev failed open
        or after it refused twice), judged after that speaker's last line classed
        `action_oriented` or `conversational` was judged (by `jev_judged_at`, then
        `id`). The newest `limit` of them, oldest first.

        Judge order, not the order said, because a voice line is stored when its
        speech is turned into text, after a line typed later may have been judged:
        each line is offered to the speaker's next line to the AI judged after it
        and to no other, so a line gathered into one ask is never gathered again,
        and a piece stored after the ask it was said before reaches the next.

        The last line to the AI is looked for among those said since `sent_since`
        (all of them when None), on the (player_name, at) index: one judged after
        a line in the window but said long before it cannot be, since lines are
        judged in the order they are stored."""
        bound, bound_params = ("", []) if sent_since is None else (" AND at >= ?", [sent_since])
        last = self.conn.execute(
            "SELECT jev_judged_at, id FROM lines WHERE player_name = ? AND source != 'ai' AND id != ?%s "
            "AND jev_class IN ('action_oriented', 'conversational') ORDER BY jev_judged_at DESC, id DESC LIMIT 1"
            % bound, [ask["player_name"], ask["id"]] + bound_params).fetchone()
        params = [ask["player_name"], ask["at"], ask["at"], ask["id"], since]
        after = ""
        if last is not None:
            after = " AND (jev_judged_at > ? OR (jev_judged_at = ? AND id > ?))"
            params += [last["jev_judged_at"], last["jev_judged_at"], last["id"]]
        return self.conn.execute(
            "SELECT %s FROM lines WHERE player_name = ? AND source != 'ai' AND (at < ? OR (at = ? AND id < ?)) "
            "AND at >= ? AND jev_class IN ('context', 'not_relevant') AND jev_confidence IS NOT NULL%s%s "
            "ORDER BY at DESC, id DESC LIMIT ?" % (COLUMNS, after, self._live()), params + [limit]).fetchall()[::-1]

    def history(self, since, exclude=(), limit=2000):
        """Every line said since `since`, the AI's included, newest first,
        leaving out `exclude` (the lines asked about, which go in regardless)."""
        exclude = list(exclude)
        holes = ",".join("?" * len(exclude))
        skip = (" AND id NOT IN (%s)" % holes) if exclude else ""
        return self.conn.execute(
            "SELECT %s FROM lines WHERE at >= ?%s%s ORDER BY at DESC, id DESC LIMIT ?"
            % (COLUMNS, skip, self._live()),
            [since] + exclude + [limit]).fetchall()

    def ai_records(self, since, scan=2000):
        """The brain's own records said since `since` — the feed's `AI` rows of
        kind action, task and result, among its last `scan` rows — as history
        rows whose `source` is the kind and whose text names its task (named()),
        newest first. A `say` is left out:
        `vfg say` keeps it in `lines` too."""
        rows = self.conn.execute(
            "SELECT seq, at, line FROM (SELECT seq, at, line FROM feed ORDER BY seq DESC LIMIT ?) "
            "WHERE line LIKE 'AI#%' AND at >= ? ORDER BY at DESC, seq DESC", (scan, since)).fetchall()
        records = []
        for seq, at, line in rows:
            m = _AI_ROW.match(line)
            if m and m.group(1) in RECORDS:
                records.append({"id": "AI#%d" % seq, "at": at, "player_name": "AI", "source": m.group(1),
                                "text": named(m.group(2), m.group(3)), "peak_dbfs": None})
        return records

    def recent(self, line, since, limit=8):
        """For the cast check: `line`'s speaker's lines, a dropped one too (it was a cast), and the
        AI's, said since `since` and before `line` (by `at`, then `id`); and that speaker's casts
        (the bridge's `casts`, when it has the table) started since `since` and by the time `line`
        ended, but no `duplicate`, the same words cast twice; a `pending` one, an engine cast the
        bridge is asking the engine about, is shown: the saying was taken as a cast. The newest
        `limit` of them, oldest first, as dicts `{at, player_name, kind ("line" or "cast"), text}`
        (a cast's text its phrase)."""
        rows = self.conn.execute(
            "SELECT at, player_name, text FROM lines WHERE ((player_name = ? AND source != 'ai') OR source = 'ai') "
            "AND at >= ? AND (at < ? OR (at = ? AND id < ?)) ORDER BY at DESC, id DESC LIMIT ?",
            (line["player_name"], since, line["at"], line["at"], line["id"], limit)).fetchall()
        items = [{"at": r["at"], "player_name": r["player_name"], "kind": "line", "text": r["text"]} for r in rows]
        if self._has_casts():
            casts = self.conn.execute(
                "SELECT started_at, phrase FROM casts WHERE player_name = ? AND started_at >= ? AND started_at <= ? "
                "AND status != 'duplicate' ORDER BY started_at DESC LIMIT ?",
                (line["player_name"], since, _field(line, "ended_at") or line["at"], limit)).fetchall()
            items += [{"at": r[0], "player_name": line["player_name"], "kind": "cast", "text": r[1]} for r in casts]
        items.sort(key=lambda i: (i["at"], i["kind"] == "cast"), reverse=True)   # a line before its own cast
        return items[:limit][::-1]

    def _has_casts(self):
        if not self._casts:
            self._casts = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'casts'").fetchone() is not None
        return self._casts

    def kept_unfed(self, since):
        """Kept lines not yet fed, said since `since`, oldest first; never a dropped one."""
        return self.conn.execute(
            "SELECT %s FROM lines WHERE jev_class IN ('action_oriented', 'conversational', 'context') "
            "AND fed_at IS NULL AND at >= ?%s ORDER BY at, id" % (COLUMNS, self._live()), (since,)).fetchall()

    def feed_lines_at(self, ats):
        """`(seq, at, line)` of every LINE already in the `feed` table whose `at`
        is one of `ats` — read only, to find a batch an earlier run wrote but
        never marked (it was killed between the two)."""
        ats = list(ats)
        return self.conn.execute(
            "SELECT seq, at, line FROM feed WHERE line LIKE 'LINE#%%' AND at IN (%s) ORDER BY seq"
            % ",".join("?" * len(ats)), ats).fetchall()

    # -- writing ---------------------------------------------------------------

    def judge(self, line_id, cls, confidence, judged_at, usage=None):
        """One transaction: the line's verdict and the call's `usage` row. Answers
        whether the verdict was written: not when the bridge dropped the line (only
        an incantation) while Jev judged it, or it had a verdict already. The
        usage row is written either way: the call was made."""
        statements = [(
            "UPDATE lines SET jev_class = ?, jev_confidence = ?, jev_judged_at = ? "
            "WHERE id = ? AND jev_class IS NULL" + self._live(), (cls, confidence, judged_at, line_id))]
        if usage is not None:
            statements.append(self._usage_sql(**usage))
        return self._tx(statements)[0] == 1

    def set_class(self, line_id, cls, confidence, judged_at):
        """A verdict written without Jev's answer (a blank line, a refusal); none
        on a line dropped meanwhile."""
        return self._tx([(
            "UPDATE lines SET jev_class = ?, jev_confidence = ?, jev_judged_at = ? "
            "WHERE id = ? AND jev_class IS NULL" + self._live(), (cls, confidence, judged_at, line_id))])[0]

    def pass_all_as_context(self, judged_at):
        """Failing open: every unjudged line but the AI's becomes `context`
        with no confidence. Answers how many."""
        return self._tx([(
            "UPDATE lines SET jev_class = 'context', jev_confidence = NULL, jev_judged_at = ? "
            "WHERE jev_class IS NULL AND source != 'ai'" + self._live(), (judged_at,))])[0]

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
