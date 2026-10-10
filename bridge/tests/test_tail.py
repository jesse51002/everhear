"""The console tail and the watchers: rows, GAME lines, watch.json, offsets, rotation, dates."""

import json
import os
import threading
import unittest

import vfg_feed
from vfg_bridge import console, watch as watchmod

from helpers import Box, FakeClock, at as at_

NOW = "2026-10-07T18:30:00.250Z"


def line(hms, message, thread="Server thread", level="INFO"):
    return "[%s] [%s/%s]: %s" % (hms, thread, level, message)


class TailCase(Box):
    def setUp(self):
        super().setUp()
        self.db = self.make_db()
        self.clock = FakeClock(NOW)
        self.watch = watchmod.WatchFile(self.watch_path, vfg_feed)

    def make_tail(self, clock=None, start=True, **kw):
        opts = {"watch_every": 0, "save_every": 0, "reopen_every": 0}
        opts.update(kw)
        self.watch.ensure()
        self.watch.check()
        tail = console.Tail(self.console_path, self.state_dir, self.db, self.watch, vfg_feed,
                            clock=clock or self.clock, **opts)
        if start:
            tail.start()
        self.addCleanup(tail.close)
        return tail

    def chats(self):
        return self.query("SELECT player_name, source, text FROM lines ORDER BY at, id")

    def events(self):
        return self.query("SELECT at, kind, player_name, text FROM events ORDER BY at, id")

    def game(self):
        return [ln for ln in self.feed_lines() if ln.startswith("GAME#")]

    def sys_lines(self):
        return [ln for ln in self.feed_lines() if ln.startswith("SYS#")]


class Ticks:
    """A stop event for Tail.run that never sleeps: its n-th wait runs the n-th step,
    and it is set once the last step has had a poll after it."""

    def __init__(self, *steps):
        self.steps = steps
        self.waits = []

    def is_set(self):
        return len(self.waits) > len(self.steps)

    def wait(self, seconds):
        self.waits.append(seconds)
        if len(self.waits) <= len(self.steps):
            self.steps[len(self.waits) - 1]()
        return self.is_set()


class TestRows(TailCase):
    def test_chat_events_and_noise(self):
        self.console("")  # the file exists; the first run starts at its end
        tail = self.make_tail()
        self.console(
            line("18:29:00", "<Steve> build me a castle"),
            line("18:29:01", "[Not Secure] <Alex>   that cave by spawn  "),
            line("18:29:02", "System chat: Steve joined the game"),
            line("18:29:03", "System chat: Steve was slain by Zombie"),
            line("18:29:04", "System chat: Alex has made the advancement [Stone Age]"),
            line("18:29:05", "Alex left the game"),
            line("18:29:06", 'Done (3.412s)! For help, type "help"'),
            line("18:29:07", "Stopping server"),
            line("18:29:08", "System chat: [Rcon: Summoned new Zombie]"),
            line("18:29:09", "System chat: [Steve: Played sound minecraft:x to Steve]"),
            line("18:29:10", "System chat: [Rcon: Set spawn point to 0, 64, 0 [0.0] in minecraft:overworld for Steve]"),
            line("18:29:11", "[Not Secure] [Bat] mod"),
            line("18:29:12", "Thread RCON Client /127.0.0.1 started", thread="RCON Listener #1"),
            line("18:29:13", "Alex lost connection: Disconnected"),
            line("18:29:14", "Steve was slain by Zombie", level="WARN"),
        )
        tail.poll_once()
        self.assertEqual(self.chats(), [("Steve", "chat", "build me a castle"),
                                         ("Alex", "chat", "that cave by spawn")])
        self.assertEqual([(k, p) for _, k, p, _ in self.events()], [
            ("join", "Steve"), ("death", "Steve"), ("advancement", "Alex"), ("leave", "Alex"),
            ("server", None), ("server", None)])
        self.assertEqual([t for _, _, _, t in self.events()][-2:],
                         ["server started", "server stopping"])
        row = self.query("SELECT ended_at, player_uuid, peak_dbfs, mean_dbfs, client_line_id,"
                         " jev_class, jev_confidence, jev_judged_at, fed_at FROM lines LIMIT 1")[0]
        self.assertEqual(row, (None,) * 9)

    def test_the_thread_polls_every_200_ms_and_saves_the_offset_on_the_way_out(self):
        self.console("")
        tail = self.make_tail(start=False)
        stop = Ticks(lambda: self.console(line("18:29:59", "<Steve> quick"),
                                          line("18:29:59", "Steve joined the game")))
        tail.run(stop)
        self.assertEqual(stop.waits, [0.2, 0.2])
        self.assertEqual((len(self.chats()), len(self.events())), (1, 1))
        with open(os.path.join(self.state_dir, "console.offset"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["offset"], os.path.getsize(self.console_path))
        self.assertIsNone(tail.fd)

    def test_first_run_starts_at_the_end(self):
        self.console(line("18:20:00", "<Steve> old history"))
        tail = self.make_tail()
        tail.poll_once()
        self.assertEqual(self.chats(), [])
        self.console(line("18:29:59", "<Steve> new"))
        tail.poll_once()
        self.assertEqual(self.chats(), [("Steve", "chat", "new")])

    def test_partial_line_waits_for_its_newline(self):
        self.console("")
        tail = self.make_tail()
        with open(self.console_path, "a", encoding="utf-8") as f:
            f.write(line("18:29:59", "<Steve> hel"))
        tail.poll_once()
        self.assertEqual(self.chats(), [])
        with open(self.console_path, "a", encoding="utf-8") as f:
            f.write("lo\n")
        tail.poll_once()
        self.assertEqual(self.chats(), [("Steve", "chat", "hello")])

    def test_a_line_that_fails_is_skipped(self):
        self.console("")
        tail = self.make_tail()
        real = self.db.insert_chat

        def flaky(at, player, text):
            if text == "boom":
                raise RuntimeError("disk on fire")
            return real(at, player, text)

        self.db.insert_chat = flaky
        self.console(line("18:29:58", "<Steve> boom"), line("18:29:59", "<Steve> after"))
        tail.poll_once()
        self.assertEqual(self.chats(), [("Steve", "chat", "after")])


class TestWatchers(TailCase):
    def test_default_feeds_every_kind(self):
        self.console("")
        tail = self.make_tail()
        self.console(line("18:29:58", "Steve joined the game"),
                     line("18:29:59", "Steve was slain by Zombie"),
                     line("18:30:00", "Stopping server"))
        tail.poll_once()
        self.assertEqual(self.game(), [
            "GAME#1 18:29:58 join Steve | Steve joined the game",
            "GAME#2 18:29:59 death Steve | Steve was slain by Zombie",
            "GAME#3 18:30:00 server - | server stopping",
        ])

    def test_only_death_watched(self):
        self.write_watch('{"events": ["death"]}')
        self.console("")
        tail = self.make_tail()
        self.console(line("18:29:58", "Steve joined the game"),
                     line("18:29:59", "Steve was slain by Zombie"))
        tail.poll_once()
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(self.game(), ["GAME#1 18:29:59 death Steve | Steve was slain by Zombie"])

    def test_players_limit_deaths_but_not_server(self):
        self.write_watch('{"events": ["death", "server"], "players": ["alex"]}')
        self.console("")
        tail = self.make_tail()
        self.console(line("18:29:57", "Steve was slain by Zombie"),
                     line("18:29:58", "Alex drowned"),
                     line("18:29:59", "Stopping server"))
        tail.poll_once()
        self.assertEqual(self.game(), ["GAME#1 18:29:58 death Alex | Alex drowned",
                                       "GAME#2 18:29:59 server - | server stopping"])
        self.assertEqual(len(self.events()), 3)

    def test_contains_feeds_one_raw_line(self):
        self.write_watch('{"events": [], "contains": ["keep up", "Overloaded|overloaded"]}')
        self.console("")
        tail = self.make_tail()
        self.console(line("18:29:58", "Can't keep up! Is the server overloaded? Running 2043ms",
                          level="WARN"),
                     line("18:29:59", "Steve joined the game"))
        tail.poll_once()
        self.assertEqual(self.game(), [
            "GAME#1 18:29:58 raw - | [Server thread/WARN]: Can't keep up! Is the server overloaded?"
            " Running 2043ms"])
        self.assertEqual(len(self.events()), 1)

    def test_contains_matches_chat_too_and_stack_traces_take_their_header_time(self):
        self.write_watch('{"events": [], "contains": ["castle", "StacklessClosed"]}')
        self.console("")
        tail = self.make_tail()
        self.console(line("18:29:50", "<Steve> build me a castle"),
                     line("18:29:55", "Failed to send packet", thread="Netty Epoll IO #7", level="WARN"),
                     "io.netty.channel.StacklessClosedChannelException: null")
        tail.poll_once()
        self.assertEqual(self.chats(), [("Steve", "chat", "build me a castle")])
        self.assertEqual(self.game(), [
            "GAME#1 18:29:50 raw - | [Server thread/INFO]: <Steve> build me a castle",
            "GAME#2 18:29:55 raw - | io.netty.channel.StacklessClosedChannelException: null",
        ])

    def test_a_fed_event_is_not_fed_again_as_raw(self):
        self.write_watch('{"contains": ["Steve"]}')
        self.console("")
        tail = self.make_tail()
        self.console(line("18:29:59", "Steve joined the game"))
        tail.poll_once()
        self.assertEqual(self.game(), ["GAME#1 18:29:59 join Steve | Steve joined the game"])

    def test_missing_watch_json_is_written_with_the_default(self):
        self.watch.ensure()
        with open(self.watch_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"events": ["join", "leave", "death", "advancement", "server"],
                                            "players": [], "contains": []})

    def test_replacing_watch_json_takes_effect_within_a_second(self):
        now = [100.0]
        self.console("")
        tail = self.make_tail(watch_every=1.0, monotonic=lambda: now[0])
        tail.poll_once()
        self.write_watch('{"events": ["leave"], "players": [], "contains": []}')
        now[0] += 0.9
        tail.poll_once()
        self.assertEqual(self.watch.current.events, watchmod.KINDS)
        now[0] += 0.1
        tail.poll_once()
        self.assertEqual(self.watch.current.events, ("leave",))
        self.console(line("18:29:58", "Steve joined the game"), line("18:29:59", "Steve left the game"))
        tail.poll_once()
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(self.game(), ["GAME#1 18:29:59 leave Steve | Steve left the game"])

    def test_bad_json_writes_one_sys_line_and_keeps_the_old_watchers(self):
        self.write_watch('{"events": ["death"]}')
        self.console("")
        tail = self.make_tail()
        self.write_watch("{not json")
        tail.poll_once()
        tail.poll_once()
        self.assertEqual(self.watch.current.events, ("death",))
        sys_lines = self.sys_lines()
        self.assertEqual(len(sys_lines), 1)
        self.assertRegex(sys_lines[0], r"^SYS#1 \d\d:\d\d:\d\d \| watch\.json: is not valid JSON "
                                       "\u2014 kept the previous watchers$")
        self.write_watch('{"events": "death"}')
        tail.poll_once()
        self.assertEqual(len(self.sys_lines()), 2)
        self.assertIn("wrong types", self.sys_lines()[1])
        self.console(line("18:29:59", "Steve joined the game"))
        tail.poll_once()
        self.assertEqual(self.game(), [])

    def test_unknown_kind_and_bad_pattern_are_dropped_in_one_line(self):
        self.console("")
        tail = self.make_tail()
        self.write_watch('{"events": ["death", "jump"], "contains": ["(", "ok"]}')
        tail.poll_once()
        self.assertEqual(self.watch.current.events, ("death",))
        self.assertEqual(self.watch.current.contains, ("ok",))
        sys_lines = self.sys_lines()
        self.assertEqual(len(sys_lines), 1)
        self.assertIn("unknown kind 'jump' \u2014 dropped it", sys_lines[0])
        self.assertIn("pattern '(' does not compile", sys_lines[0])

    def test_a_failing_feed_is_logged_and_the_tail_carries_on(self):
        self.console("")
        tail = self.make_tail()

        class Broken:
            @staticmethod
            def append(*args, **kwargs):
                raise OSError(28, "No space left on device")

        tail.feed = Broken
        self.console(line("18:29:58", "Steve was slain by Zombie"),
                     line("18:29:59", "<Steve> still here"))
        tail.poll_once()
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(len(self.chats()), 1)


class TestLiveRun(TailCase):
    """A live server's shapes: a team-tagged player's events were once dropped as echoes."""

    def test_team_tagged_join_and_leave_are_stored_and_fed(self):
        self.console("")
        tail = self.make_tail()
        self.console(
            line("18:29:50", "UUID of player Alex is 00000000-0000-4000-8000-000000000001",
                 thread="User Authenticator #1"),
            line("18:29:50", "Alex[IP hidden] logged in with entity id 312 at (1.5, 64.0, 2.5)"),
            line("18:29:50", "System chat: [Crew] Alex joined the game"),
            line("18:29:51", "[Not Secure] [[Crew] Alex] pos-check"),
            line("18:29:52", "System chat: death.attack.magic.item"),
            line("18:29:53", "System chat: [Crew] Alex left the game"))
        tail.poll_once()
        self.assertEqual([(k, p, t) for _, k, p, t in self.events()], [
            ("join", "Alex", "[Crew] Alex joined the game"),
            ("death", None, "death.attack.magic.item"),
            ("leave", "Alex", "[Crew] Alex left the game")])
        self.assertEqual(self.game(), [
            "GAME#1 18:29:50 join Alex | [Crew] Alex joined the game",
            "GAME#2 18:29:52 death - | death.attack.magic.item",
            "GAME#3 18:29:53 leave Alex | [Crew] Alex left the game"])

    def test_a_restarted_tail_knows_players_from_the_history(self):
        self.db.insert_event("2026-10-07T18:00:00.000Z", "join", "Alex", "Alex joined the game")
        self.console("")
        tail = self.make_tail()
        self.console(line("18:29:58", "[Alex] Steve died"),
                     line("18:29:59", "System chat: [Crew] Steve died"))
        tail.poll_once()
        self.assertEqual([(k, p) for _, k, p, _ in self.events()][1:], [("death", "Steve")])

    def test_the_units_stop_line_feeds_server_stopped(self):
        self.console("")
        tail = self.make_tail()
        self.console("[18:29:59] [vfg/INFO]: Server stopped")
        tail.poll_once()
        self.assertEqual(self.game(), ["GAME#1 18:29:59 server - | server stopped"])


class TestTimes(TailCase):
    def test_game_line_shows_the_event_time_not_the_read_time(self):
        self.console("")
        tail = self.make_tail()
        self.console(line("18:25:00", "Steve was slain by Zombie"))
        tail.poll_once()
        self.assertEqual(self.events()[0][0], "2026-10-07T18:25:00.000Z")
        self.assertEqual(self.game(), ["GAME#1 18:25:00 death Steve | Steve was slain by Zombie"])
        self.assertEqual(self.feed_rows()[0][1], "2026-10-07T18:25:00.000Z")

    def test_a_fresh_line_gets_the_clock_milliseconds(self):
        self.console("")
        tail = self.make_tail()
        self.console(line("18:29:59", "<Steve> now"))
        tail.poll_once()
        self.assertEqual(self.query("SELECT at FROM lines")[0][0], "2026-10-07T18:29:59.250Z")

    def test_a_log_second_read_across_a_clock_second_stays_in_order(self):
        self.console("")
        clock = FakeClock("2026-10-07T18:29:59.900Z")
        tail = self.make_tail(clock=clock)
        self.console(line("18:29:59", "Steve joined the game"))
        tail.poll_once()
        clock.now = at_("2026-10-07T18:30:00.200Z")
        self.console(line("18:29:59", "<Steve> hi"), line("18:29:59", "Steve left the game"))
        tail.poll_once()
        self.assertEqual([a for a, _, _, _ in self.events()],
                         ["2026-10-07T18:29:59.900Z", "2026-10-07T18:29:59.902Z"])
        self.assertEqual(self.query("SELECT at FROM lines"), [("2026-10-07T18:29:59.901Z",)])

    def test_a_restart_keeps_a_log_second_in_order(self):
        self.console("")
        clock = FakeClock("2026-10-07T18:29:59.900Z")
        first = self.make_tail(clock=clock)
        self.console(line("18:29:59", "Steve joined the game"))
        first.poll_once()
        stop = threading.Event()
        stop.set()
        first.run(stop)
        clock.now = at_("2026-10-07T18:30:00.200Z")
        self.console(line("18:29:59", "Steve left the game"))
        second = self.make_tail(clock=clock)
        second.poll_once()
        self.assertEqual([(a, k) for a, k, _, _ in self.events()],
                         [("2026-10-07T18:29:59.900Z", "join"), ("2026-10-07T18:29:59.901Z", "leave")])

    def test_the_floor_stops_at_999_and_leaves_other_seconds_alone(self):
        floor = at_("2026-10-07T18:29:59.999Z")
        self.assertEqual(console.same_second_floor(at_("2026-10-07T18:29:59.100Z"), floor), floor)
        earlier = at_("2026-10-07T18:29:58.100Z")
        self.assertEqual(console.same_second_floor(earlier, floor), earlier)
        later = at_("2026-10-07T18:30:00.000Z")
        self.assertEqual(console.same_second_floor(later, floor), later)

    def test_an_event_twenty_minutes_old_is_stored_and_not_fed(self):
        self.write_watch('{"contains": ["Zombie"]}')
        self.console("")
        tail = self.make_tail()
        self.console(line("18:10:00", "Steve was slain by Zombie"))
        tail.poll_once()
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.feed_lines(), [])

    def test_before_midnight_read_after_keeps_yesterday(self):
        self.console("")
        tail = self.make_tail(clock=FakeClock("2026-10-08T00:00:01.250Z"))
        self.console(line("23:59:58", "Steve joined the game"))
        tail.poll_once()
        self.assertEqual(self.events()[0][0], "2026-10-07T23:59:58.250Z")
        self.assertEqual(self.game(), ["GAME#1 23:59:58 join Steve | Steve joined the game"])


class TestFile(TailCase):
    def test_truncation_resumes_from_zero(self):
        self.console("")
        tail = self.make_tail()
        self.console(line("18:29:50", "<Steve> one"), line("18:29:51", "<Steve> two"))
        tail.poll_once()
        with open(self.console_path, "w", encoding="utf-8") as f:
            f.write(line("18:29:52", "<Steve> 3") + "\n")
        tail.poll_once()
        self.assertEqual([t for _, _, t in self.chats()], ["one", "two", "3"])

    def test_restart_resumes_from_the_offset_with_no_duplicates(self):
        self.console("")
        first = self.make_tail()
        self.console(line("18:29:50", "<Steve> one"), line("18:29:51", "Steve joined the game"))
        first.poll_once()
        stop = threading.Event()
        stop.set()
        first.run(stop)  # saves the offset and closes, as SIGTERM does
        self.console(line("18:29:52", "<Steve> two"), line("18:29:53", "Steve left the game"))
        second = self.make_tail()
        second.poll_once()
        self.assertEqual([t for _, _, t in self.chats()], ["one", "two"])
        self.assertEqual([k for _, k, _, _ in self.events()], ["join", "leave"])
        with open(os.path.join(self.state_dir, "console.offset"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["inode"], os.stat(self.console_path).st_ino)

    def test_a_replaced_log_with_the_same_offset_file_is_read_from_its_start(self):
        self.console("")
        first = self.make_tail()
        self.console(line("18:29:50", "<Steve> one"))
        first.poll_once()
        first.save_offset()
        first.close()
        os.rename(self.console_path, self.console_path + ".old")
        self.console(line("18:29:51", "<Steve> fresh"))
        second = self.make_tail()
        second.poll_once()
        self.assertEqual([t for _, _, t in self.chats()], ["one", "fresh"])

    def test_no_file_then_one_appears_and_is_read_from_its_start(self):
        tail = self.make_tail()
        tail.poll_once()
        self.console(line("18:29:50", "<Steve> first"), line("18:29:51", "<Steve> second"))
        tail.poll_once()
        self.assertEqual([t for _, _, t in self.chats()], ["first", "second"])

    def test_rotation_reads_the_old_file_to_its_end_then_the_new(self):
        self.console("")
        tail = self.make_tail()
        self.console(line("18:29:50", "<Steve> one"))
        tail.poll_once()
        self.console(line("18:29:51", "<Steve> last of the old"))
        os.rename(self.console_path, self.console_path + ".1")
        self.console(line("18:29:52", "<Steve> first of the new"))
        tail.poll_once()
        self.assertEqual([t for _, _, t in self.chats()],
                         ["one", "last of the old", "first of the new"])
        self.assertEqual(tail.inode, os.stat(self.console_path).st_ino)


if __name__ == "__main__":
    unittest.main()
