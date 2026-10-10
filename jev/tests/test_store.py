"""A line the bridge dropped (only an incantation) is never judged or in Jev's history;
a database without the column still works."""
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


if __name__ == "__main__":
    unittest.main()
