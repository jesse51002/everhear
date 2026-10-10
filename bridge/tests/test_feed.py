"""vfg_feed: the API, the cleaning and field rules, the lock, the command line."""

import datetime as _dt
import io
import os
import re
import sys
import threading
import unittest
from unittest import mock

import vfg_feed

from helpers import FEED_PY, UTC, Box, run

LINE_SHAPE = re.compile(r"^(LINE|GAME|AI|SYS)#(\d+) (\d\d:\d\d:\d\d) (.*)$")


class FeedCase(Box):
    def setUp(self):
        super().setUp()
        self.make_db()
        vfg_feed._warned_line = False


class TestAppend(FeedCase):
    def test_first_seq_is_one(self):
        self.assertEqual(vfg_feed.append("SYS", "", "hello"), 1)
        self.assertEqual(vfg_feed.append("SYS", "", "again"), 2)

    def test_sys_without_fields_renders_bare(self):
        seq = vfg_feed.append("SYS", "", "smoke", "2026-10-07T18:25:00.000Z")
        self.assertEqual(self.feed_lines(), ["SYS#%d 18:25:00 | smoke" % seq])

    def test_row_mirrors_the_file(self):
        vfg_feed.append("GAME", "death Steve", "Steve was slain by Zombie",
                        "2026-10-07T18:22:40.123Z")
        vfg_feed.append("AI", "result T183", "T183 done: ice trail running as ice_trail",
                        "2026-10-07T18:24:02Z")
        self.assertEqual(self.feed_lines(), [
            "GAME#1 18:22:40 death Steve | Steve was slain by Zombie",
            "AI#2 18:24:02 result T183 | T183 done: ice trail running as ice_trail",
        ])
        self.assertEqual(self.feed_rows(), [
            (1, "2026-10-07T18:22:40.123Z", "GAME#1 18:22:40 death Steve | Steve was slain by Zombie"),
            (2, "2026-10-07T18:24:02.000Z", "AI#2 18:24:02 result T183 | T183 done: ice trail running as ice_trail"),
        ])

    def test_contract_examples(self):
        vfg_feed.append("LINE", "Steve voice action_oriented 0.93 -8dB", "build me a castle",
                        "2026-10-07T18:22:31.412Z")
        vfg_feed.append("LINE", "Alex chat context 0.71 - was=not_relevant", "that cave by spawn",
                        "2026-10-07T18:22:33Z")
        vfg_feed.append("GAME", "server -", "server stopping", "2026-10-07T18:30:02Z")
        vfg_feed.append("AI", "action -", "command: give Steve diamond_pickaxe \u2014 ok",
                        "2026-10-07T18:30:03Z")
        self.assertEqual(self.feed_lines(), [
            "LINE#1 18:22:31 Steve voice action_oriented 0.93 -8dB | build me a castle",
            "LINE#2 18:22:33 Alex chat context 0.71 - was=not_relevant | that cave by spawn",
            "GAME#3 18:30:02 server - | server stopping",
            "AI#4 18:30:03 action - | command: give Steve diamond_pickaxe \u2014 ok",
        ])


class TestCleaning(FeedCase):
    def test_text_newlines_tabs_and_controls_become_spaces(self):
        vfg_feed.append("SYS", "", "one\r\ntwo\nthree\rfour\tfive\x00six\x1bseven\u2028eight",
                        "2026-10-07T00:00:00Z")
        self.assertEqual(self.feed_lines(),
                         ["SYS#1 00:00:00 | one two three four five six seven eight"])

    def test_long_text_is_cut_with_an_ellipsis(self):
        vfg_feed.append("SYS", "", "x" * 600, "2026-10-07T00:00:00Z")
        text = self.feed_lines()[0].split(" | ", 1)[1]
        self.assertEqual(len(text), 500)
        self.assertEqual(text, "x" * 499 + "\u2026")
        vfg_feed.append("SYS", "", "y" * 500, "2026-10-07T00:00:00Z")
        self.assertEqual(self.feed_lines()[1].split(" | ", 1)[1], "y" * 500)

    def test_fields_collapse_spaces_trim_and_lose_pipes(self):
        vfg_feed.append("GAME", "  death   Steve \n", "x", "2026-10-07T00:00:00Z")
        self.assertEqual(self.feed_lines(), ["GAME#1 00:00:00 death Steve | x"])
        with mock.patch.object(sys, "stderr", io.StringIO()):
            vfg_feed.append("LINE", "Steve  voice|chat", "y", "2026-10-07T00:00:00Z")
        self.assertTrue(self.feed_lines()[1].startswith("LINE#2 00:00:00 Steve voice/chat | y"))

    def test_none_fields_and_text_read_as_empty(self):
        vfg_feed.append("SYS", None, None, "2026-10-07T00:00:00Z")
        self.assertEqual(self.feed_lines(), ["SYS#1 00:00:00 | "])

    def test_pipe_in_text_is_kept(self):
        vfg_feed.append("SYS", "", "a | b", "2026-10-07T00:00:00Z")
        self.assertEqual(self.feed_lines(), ["SYS#1 00:00:00 | a | b"])


class TestAt(FeedCase):
    def test_string_with_offset(self):
        vfg_feed.append("SYS", "", "s", "2026-10-07T20:22:31.123456789+02:00")
        self.assertEqual(self.feed_lines(), ["SYS#1 18:22:31 | s"])
        self.assertEqual(self.feed_rows()[0][1], "2026-10-07T18:22:31.123Z")

    def test_aware_datetime(self):
        moment = _dt.datetime(2026, 10, 7, 13, 5, 9, 870000,
                              tzinfo=_dt.timezone(_dt.timedelta(hours=-5)))
        vfg_feed.append("SYS", "", "d", moment)
        self.assertEqual(self.feed_lines(), ["SYS#1 18:05:09 | d"])
        self.assertEqual(self.feed_rows()[0][1], "2026-10-07T18:05:09.870Z")

    def test_none_is_now(self):
        before = _dt.datetime.now(UTC).replace(microsecond=0)
        vfg_feed.append("SYS", "", "n")
        after = _dt.datetime.now(UTC)
        stored = vfg_feed.parse_time(self.feed_rows()[0][1])
        self.assertTrue(before <= stored <= after)
        self.assertEqual(self.feed_lines()[0], "SYS#1 %s | n" % stored.strftime("%H:%M:%S"))

    def test_naive_datetime_raises(self):
        with self.assertRaises(ValueError):
            vfg_feed.append("SYS", "", "x", _dt.datetime(2026, 10, 7, 18, 0, 0))
        self.assertEqual(self.feed_lines(), [])

    def test_bad_strings_and_types_raise(self):
        for bad in ("2026-10-07 18:00:00Z", "2026-10-07T18:00:00", "yesterday",
                    "2026-13-07T18:00:00Z", 1696700000, True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                vfg_feed.append("SYS", "", "x", bad)
        self.assertEqual(self.feed_lines(), [])

    def test_one_now_for_a_batch(self):
        seqs = vfg_feed.append_batch([("SYS", "", "a"), ("SYS", "", "b", None)])
        rows = self.feed_rows()
        self.assertEqual(seqs, [1, 2])
        self.assertEqual(rows[0][1], rows[1][1])


class TestFieldRules(FeedCase):
    def test_good_fields_pass(self):
        for type_, fields in (("GAME", "death Steve"), ("GAME", "server -"), ("GAME", "raw -"),
                              ("GAME", "advancement Notch_2"), ("GAME", "cast Steve"),
                              ("AI", "say -"), ("AI", "action T7"), ("AI", "task T183"),
                              ("AI", "result T183"), ("AI", "note -"), ("SYS", "")):
            vfg_feed.append(type_, fields, "ok")
        self.assertEqual(len(self.feed_lines()), 11)

    def test_bad_fields_raise_and_write_nothing(self):
        for type_, fields in (("GAME", "death"), ("GAME", "Death Steve"), ("GAME", "death Ste ve"),
                              ("AI", "done T7"), ("AI", "say 7"), ("AI", "say"), ("AI", "task T7 x"),
                              ("SYS", "extra")):
            with self.assertRaises(ValueError, msg="%s %r" % (type_, fields)):
                vfg_feed.append(type_, fields, "no")
        self.assertEqual(self.feed_lines(), [])
        self.assertEqual(self.feed_rows(), [])

    def test_bad_type_raises(self):
        for bad in ("CHAT", "ORCH", "line", "", None, 3):
            with self.assertRaises(ValueError):
                vfg_feed.append(bad, "", "x")
        self.assertEqual(self.feed_lines(), [])

    def test_a_bad_entry_stops_the_whole_batch(self):
        with self.assertRaises(ValueError):
            vfg_feed.append_batch([("SYS", "", "fine"), ("GAME", "death", "bad")])
        self.assertEqual(self.feed_lines(), [])

    def test_malformed_line_is_written_with_one_warning(self):
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            vfg_feed.append("LINE", "Steve shouted loudly", "first")
            vfg_feed.append("LINE", "Steve voice direct_ask 0.93 -8dB", "second")
        self.assertEqual(len(self.feed_lines()), 2)
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        self.assertIn("warning", err.getvalue())

    def test_well_formed_line_does_not_warn(self):
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            vfg_feed.append("LINE", "Steve voice action_oriented 0.93 -8dB", "a")
            vfg_feed.append("LINE", "Alex chat context - - was=not_relevant", "b")
            vfg_feed.append("LINE", "Alex chat conversational 0.80 -", "c")
        self.assertEqual(err.getvalue(), "")


class TestBatch(FeedCase):
    def test_consecutive_seqs_in_one_write(self):
        vfg_feed.append("SYS", "", "before")
        real_write = os.write
        calls = []

        def counting_write(fd, data):
            calls.append(len(data))
            return real_write(fd, data)

        with mock.patch.object(vfg_feed.os, "write", side_effect=counting_write):
            seqs = vfg_feed.append_batch([
                ("LINE", "Steve voice context 0.70 -12dB", "one", "2026-10-07T18:00:01Z"),
                ("LINE", "Alex chat context 0.61 -", "two", "2026-10-07T18:00:02Z"),
                ("LINE", "Steve voice action_oriented 0.95 -6dB", "three", "2026-10-07T18:00:03Z"),
            ])
        self.assertEqual(seqs, [2, 3, 4])
        self.assertEqual(len(calls), 1)
        self.assertEqual([r[0] for r in self.feed_rows()], [1, 2, 3, 4])

    def test_three_item_entries_and_empty_batch(self):
        self.assertEqual(vfg_feed.append_batch([]), [])
        self.assertFalse(os.path.exists(self.feed_path))
        self.assertEqual(vfg_feed.append_batch([("SYS", "", "three items")]), [1])

    def test_failed_write_is_tried_twice_more(self):
        real_write = os.write
        attempts = []

        def flaky(fd, data):
            attempts.append(1)
            if len(attempts) < 3:
                raise OSError(28, "No space left on device")
            return real_write(fd, data)

        with mock.patch.object(vfg_feed.os, "write", side_effect=flaky):
            self.assertEqual(vfg_feed.append("SYS", "", "third time lucky"), 1)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(self.feed_lines()), 1)

    def test_write_that_keeps_failing_raises_and_leaves_a_gap_not_a_repeat(self):
        with mock.patch.object(vfg_feed.os, "write", side_effect=OSError(28, "full")):
            with self.assertRaises(OSError):
                vfg_feed.append("SYS", "", "lost")
        self.assertEqual(vfg_feed.append("SYS", "", "next"), 2)
        lines = self.feed_lines()
        self.assertEqual(len(lines), 1)
        self.assertRegex(lines[0], r"^SYS#2 \d\d:\d\d:\d\d \| next$")


class TestSeqSurvives(FeedCase):
    def test_db_replaced_seq_carries_on_from_the_file(self):
        for i in range(5):
            vfg_feed.append("SYS", "", "line %d" % i)
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db_path + suffix)
            except FileNotFoundError:
                pass
        self.make_db()
        self.assertEqual(vfg_feed.append("SYS", "", "after"), 6)

    def test_feed_log_gone_seq_carries_on_from_the_db(self):
        for i in range(3):
            vfg_feed.append("SYS", "", "line %d" % i)
        os.remove(self.feed_path)
        self.assertEqual(vfg_feed.append("SYS", "", "after"), 4)
        self.assertEqual(len(self.feed_lines()), 1)

    def test_torn_last_line_does_not_swallow_the_next(self):
        vfg_feed.append("SYS", "", "whole")
        with open(self.feed_path, "a", encoding="utf-8") as f:
            f.write("SYS#2 00:00:00 | torn")
        self.assertEqual(vfg_feed.append("SYS", "", "next"), 3)
        self.assertTrue(self.feed_lines()[-1].startswith("SYS#3 "))

    def test_missing_db_raises(self):
        os.remove(self.db_path)
        with self.assertRaises(Exception):
            vfg_feed.append("SYS", "", "x")
        self.assertFalse(os.path.exists(self.db_path))


class TestConcurrency(FeedCase):
    def test_four_writers_make_consecutive_distinct_seqs(self):
        # each append opens its own lock file, so threads contend for the flock as processes do
        def write(n):
            for i in range(25):
                vfg_feed.append("SYS", "", "writer %d line %d" % (n, i))

        writers = [threading.Thread(target=write, args=(n,)) for n in range(4)]
        for t in writers:
            t.start()
        for t in writers:
            t.join(10)
        lines = self.feed_lines()
        rows = self.feed_rows()
        seqs = [int(LINE_SHAPE.match(line).group(2)) for line in lines]
        self.assertEqual(seqs, list(range(1, 101)))
        self.assertEqual([r[0] for r in rows], list(range(1, 101)))
        self.assertEqual([r[2] for r in rows], lines)


class TestCommandLine(FeedCase):
    def main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            code = vfg_feed.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_post_as_a_program_prints_the_seq(self):
        vfg_feed.append("SYS", "", "first")
        result = run([FEED_PY, "post", "AI", "task T183", "T183: an ice trail\nbehind Steve"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "2\n")
        self.assertRegex(self.feed_lines()[-1],
                         r"^AI#2 \d\d:\d\d:\d\d task T183 \| T183: an ice trail behind Steve$")

    def test_wrong_arguments_exit_2(self):
        for args in ([], ["post"], ["post", "SYS", ""], ["send", "SYS", "", "x"],
                     ["post", "SYS", "", "x", "extra"]):
            code, _, err = self.main(*args)
            self.assertEqual(code, 2, args)
            self.assertEqual(len(err.strip().splitlines()), 1)
            self.assertIn("usage", err)

    def test_bad_type_or_fields_exit_1_with_one_line(self):
        for args in (["post", "CHAT", "", "x"], ["post", "GAME", "death", "x"],
                     ["post", "ORCH", "T7 done", "x"], ["post", "AI", "said -", "x"],
                     ["post", "SYS", "fields", "x"]):
            code, out, err = self.main(*args)
            self.assertEqual(code, 1, args)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertEqual(out, "")
        self.assertEqual(self.feed_lines(), [])

    def test_imports_nothing_from_the_bridge(self):
        with open(FEED_PY, encoding="utf-8") as f:
            source = f.read()
        self.assertIsNone(re.search(r"^\s*(from|import)\s+vfg_bridge", source, re.M))
        imported = set(re.findall(r"^\s*import\s+(\w+)", source, re.M))
        self.assertEqual(imported, {"datetime", "fcntl", "os", "re", "sqlite3", "sys", "urllib"})


if __name__ == "__main__":
    unittest.main()
