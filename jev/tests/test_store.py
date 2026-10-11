"""A line the bridge dropped (only an incantation) is never judged or in Jev's history;
a database without the column still works. The gather call's window."""
import os
import unittest

from support import T0, Lines, make_db, tempdir

from jev_clock import iso
from store import Store


class TestDroppedLines(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempdir(self), "vfg.db")
        self.conn = make_db(self.db)
        self.addCleanup(self.conn.close)
        self.lines = Lines(self.conn)
        self.store = Store(self.db)
        self.addCleanup(self.store.close)

    def test_a_dropped_line_is_never_judged_or_in_the_history(self):
        self.conn.execute("ALTER TABLE lines ADD COLUMN dropped TEXT")
        kept = self.lines.add(T0, "Steve", "voice", "fire ball and build me a wall")
        self.lines.add(T0 + 1, "Steve", "voice", "fire ball", dropped="cast")
        self.assertTrue(self.store.ready())
        self.assertEqual([r["id"] for r in self.store.unjudged()], [kept])
        self.assertEqual([r["id"] for r in self.store.history(iso(T0 - 60))], [kept])

    def test_failing_open_passes_no_dropped_line(self):
        self.conn.execute("ALTER TABLE lines ADD COLUMN dropped TEXT")
        self.lines.add(T0, "Steve", "voice", "build me a wall")
        dropped = self.lines.add(T0 + 1, "Steve", "voice", "fire ball", dropped="cast")
        self.assertTrue(self.store.ready())
        self.assertEqual(self.store.pass_all_as_context(iso(T0 + 2)), 1)
        row = self.conn.execute("SELECT jev_class FROM lines WHERE id = ?", (dropped,)).fetchone()
        self.assertIsNone(row[0])

    def test_a_database_without_the_column(self):
        line = self.lines.add(T0, "Steve", "voice", "build me a wall")
        self.assertTrue(self.store.ready())
        self.assertEqual([r["id"] for r in self.store.unjudged()], [line])
        self.assertEqual([r["id"] for r in self.store.history(iso(T0 - 60))], [line])


class TestGatherWindow(unittest.TestCase):
    """The lines the gather call asks about: the speaker's own, judged by Jev since their last line to the AI."""

    def setUp(self):
        self.db = os.path.join(tempdir(self), "vfg.db")
        self.conn = make_db(self.db)
        self.addCleanup(self.conn.close)
        self.conn.execute("ALTER TABLE lines ADD COLUMN dropped TEXT")
        self.lines = Lines(self.conn)
        self.store = Store(self.db)
        self.addCleanup(self.store.close)

    def add(self, at, player, text, cls=None, judged=None, confidence=0.8, **cols):
        """A line said at `at`, and given `cls` by Jev at T0 + 100 + `judged`."""
        if cls is not None:
            cols.update(jev_class=cls, jev_confidence=confidence, jev_judged_at=iso(T0 + 100 + judged))
        return self.lines.add(at, player, "voice", text, **cols)

    def window(self, ask, since=T0 - 180, sent_since=None):
        self.assertTrue(self.store.ready())
        row = [r for r in self.store.history(iso(T0 - 7200)) if r["id"] == ask][0]
        sent = None if sent_since is None else iso(sent_since)
        return [r["text"] for r in self.store.gather_window(row, iso(since), sent_since=sent)]

    def test_the_last_line_to_the_ai_is_looked_for_only_since_sent_since(self):
        self.add(T0 - 5000, "Steve", "build me a tower", "action_oriented", 9)   # judged after the piece, said long before
        self.add(T0 - 60, "Steve", "the big one", "not_relevant", 1)
        ask = self.add(T0, "Steve", "make it bigger", "action_oriented", 10)
        self.assertEqual(self.window(ask), [])                                    # unbounded, the old ask bounds it
        self.assertEqual(self.window(ask, sent_since=T0 - 3780), ["the big one"])  # said before sent_since: not looked at

    def test_the_speakers_lines_judged_since_his_last_line_to_the_ai_said_before_the_ask(self):
        add = self.add
        add(T0 - 120, "Steve", "judged before his last ask", "context", 1)
        add(T0 - 100, "Steve", "build me a tower", "action_oriented", 2)       # his last line to the AI
        add(T0 - 110, "Steve", "said before it, judged after it", "not_relevant", 3)
        add(T0 - 90, "Steve", "the big one", "not_relevant", 4)
        add(T0 - 80, "Alex", "thanks AI", "conversational", 5)                # not his: no bound
        add(T0 - 70, "Alex", "the big one is mine", "not_relevant", 6)
        add(T0 - 60, "Steve", "by our base", "context", 7)
        add(T0 - 55, "Steve", "passed while Jev failed open", "context", 8, confidence=None)   # or refused twice
        add(T0 - 50, "Steve", "not judged yet")
        add(T0 - 40, "Steve", " \n", "not_relevant", 9, confidence=None)     # blank
        add(T0 - 30, "Steve", "fire ball", "not_relevant", 10, dropped="cast")
        add(T0, "Steve", "the same moment, said first", "not_relevant", 11)
        ask = add(T0, "Steve", "make it bigger", "action_oriented", 12)
        add(T0, "Steve", "the same moment, said after", "not_relevant", 11)
        add(T0 + 5, "Steve", "said after", "context", 11)
        self.assertEqual(self.window(ask), ["said before it, judged after it", "the big one", "by our base",
                                            "the same moment, said first"])

    def test_since_and_the_newest_ten(self):
        self.add(T0 - 181, "Steve", "too long ago", "not_relevant", 0)
        for n in range(12):
            self.add(T0 - 60 + n, "Steve", "line %d" % n, "not_relevant", n)
        ask = self.add(T0, "Steve", "make it bigger", "conversational", 12)
        self.assertEqual(self.window(ask), ["line %d" % n for n in range(2, 12)])
        self.assertEqual(self.window(ask, since=T0 - 50), ["line %d" % n for n in range(10, 12)])


if __name__ == "__main__":
    unittest.main()
