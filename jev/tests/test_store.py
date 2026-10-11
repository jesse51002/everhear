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
    """The lines the gather call asks about: the speaker's own, unsent, since their last line to the AI."""

    def setUp(self):
        self.db = os.path.join(tempdir(self), "vfg.db")
        self.conn = make_db(self.db)
        self.addCleanup(self.conn.close)
        self.conn.execute("ALTER TABLE lines ADD COLUMN dropped TEXT")
        self.add = Lines(self.conn).add
        self.store = Store(self.db)
        self.addCleanup(self.store.close)

    def window(self, ask, since=T0 - 180):
        self.assertTrue(self.store.ready())
        row = [r for r in self.store.history(iso(T0 - 3600)) if r["id"] == ask][0]
        return [r["text"] for r in self.store.gather_window(row, iso(since))]

    def test_the_speakers_unsent_lines_since_their_last_line_to_the_ai_oldest_first(self):
        add = self.add
        add(T0 - 120, "Steve", "voice", "before his last ask", jev_class="context")
        add(T0 - 100, "Steve", "voice", "build me a tower", jev_class="action_oriented")
        add(T0 - 90, "Steve", "voice", "the big one", jev_class="not_relevant")
        add(T0 - 80, "Alex", "chat", "thanks AI", jev_class="conversational")         # not the speaker's: no stop
        add(T0 - 70, "Alex", "chat", "the big one is mine", jev_class="not_relevant")
        add(T0 - 60, "Steve", "chat", "by our base", jev_class="context")
        add(T0 - 50, "Steve", "voice", "not judged yet")
        add(T0 - 40, "Steve", "voice", " \n", jev_class="not_relevant")
        add(T0 - 30, "Steve", "voice", "fire ball", jev_class="not_relevant", dropped="cast")
        add(T0, "Steve", "voice", "the same moment, said first", jev_class="not_relevant")
        ask = add(T0, "Steve", "voice", "make it bigger", jev_class="action_oriented")
        add(T0, "Steve", "voice", "the same moment, said after", jev_class="not_relevant")
        add(T0 + 5, "Steve", "voice", "said after", jev_class="context")
        self.assertEqual(self.window(ask), ["the big one", "by our base", "the same moment, said first"])

    def test_since_and_the_newest_ten(self):
        self.add(T0 - 181, "Steve", "voice", "too long ago", jev_class="not_relevant")
        for n in range(12):
            self.add(T0 - 60 + n, "Steve", "voice", "line %d" % n, jev_class="not_relevant")
        ask = self.add(T0, "Steve", "voice", "make it bigger", jev_class="conversational")
        self.assertEqual(self.window(ask), ["line %d" % n for n in range(2, 12)])
        self.assertEqual(self.window(ask, since=T0 - 50), ["line %d" % n for n in range(10, 12)])


if __name__ == "__main__":
    unittest.main()
