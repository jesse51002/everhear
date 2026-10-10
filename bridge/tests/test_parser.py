"""The console parser: the 26.3 fixture and its expected parse, --parse, and dating a stamp."""

import datetime as _dt
import gzip
import io
import json
import os
import shutil
import sys
import unittest
from unittest import mock

from vfg_bridge import console, timeutil

from helpers import FIXTURES, UTC, Box, at

LOG = os.path.join(FIXTURES, "console-26.3.log")
EXPECTED = os.path.join(FIXTURES, "console-26.3.expected.json")


def parsed(path):
    parser = console.Parser()
    out = []
    for n, raw in enumerate(console.iter_file_lines(path), 1):
        found = parser.parse(raw)
        if found is not None:
            record = {"n": n}
            record.update(found)
            out.append(record)
    return out


class TestFixture(unittest.TestCase):
    def test_fixture_parses_as_expected(self):
        with open(EXPECTED, encoding="utf-8") as f:
            expected = json.load(f)
        self.assertEqual(parsed(LOG), expected)

    def test_fixture_has_no_real_addresses(self):
        import re
        with open(LOG, encoding="utf-8") as f:
            text = f.read()
        for ip in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
            self.assertTrue(ip.startswith("203.0.113.") or ip in ("127.0.0.1", "0.0.0.0"), ip)


class TestNoise(unittest.TestCase):
    def check(self, message, expected, thread="Server thread", level="INFO"):
        found = console.parse("[12:00:00] [%s/%s]: %s" % (thread, level, message))
        if expected is None:
            self.assertIsNone(found, message)
        else:
            self.assertEqual((found["kind"], found["player"], found["text"]), expected, message)

    def test_echoes_are_nothing(self):
        for message in ("[Rcon: Summoned new Zombie]", "System chat: [Rcon: Summoned new Zombie]",
                        "System chat: [Steve: Played sound minecraft:block.bell.use to Steve]",
                        "System chat: [Rcon: Set spawn point to 0, 64, 0 [0.0] in minecraft:overworld for Steve]",
                        "[Not Secure] [Bat] mod", "[Not Secure] [Steve] said with /say"):
            self.check(message, None)

    def test_other_threads_and_levels_are_nothing(self):
        self.check("Steve was slain by Zombie", None, level="WARN")
        self.check("Steve joined the game", None, thread="RCON Client /127.0.0.1 #2")
        self.check("<Steve> hi", None, thread="Server thread", level="ERROR")

    def test_chat_is_trimmed_and_empty_chat_is_nothing(self):
        self.check("<Steve>   hi there  ", ("chat", "Steve", "hi there"))
        self.check("<Steve>    ", None)
        self.check("<Steve> " + "x" * 2500, ("chat", "Steve", "x" * 2000))

    def test_names_longer_than_sixteen_are_nothing(self):
        self.check("ThreadedAnvilChunkStorage (world): All chunks are saved", None)
        self.check("Abcdefghijklmnopq joined the game", None)

    def test_unstamped_and_garbage(self):
        self.assertIsNone(console.parse("\tat java.base/java.lang.Thread.run(Thread.java:1474)"))
        self.assertIsNone(console.parse("[99:00:00] [Server thread/INFO]: Steve joined the game"))
        self.assertIsNone(console.parse(""))


class TestLiveRunShapes(unittest.TestCase):
    """Shapes from a live 26.3 server (team tags, /say, feedback) that the first parser dropped as echoes."""

    def kinds(self, *messages, known=()):
        parser = console.Parser(known)
        out = []
        for message in messages:
            found = parser.parse("[06:00:00] [Server thread/INFO]: " + message)
            out.append(found and (found["kind"], found["player"]))
        return out

    def test_team_tagged_names_are_players(self):
        self.assertEqual(self.kinds(
            "System chat: [Crew] Alex joined the game",
            "System chat: [Crew] Alex left the game",
            "System chat: [Crew] Alex was slain by [Red] Steve",
            "System chat: [Crew] Alex has made the advancement [Stone Age]",
            "System chat: Alex [VIP] fell from a high place",
            "<[Crew] Alex> typed with a team tag",
            "[Not Secure] <[A][B] Alex> two tags",
        ), [("join", "Alex"), ("leave", "Alex"), ("death", "Alex"),
            ("advancement", "Alex"), ("death", "Alex"), ("chat", "Alex"),
            ("chat", "Alex")])

    def test_says_and_feedback_stay_noise(self):
        self.assertEqual(self.kinds(
            "[Not Secure] [[Crew] Alex] pos-check",
            "[Not Secure] [Rcon] Alex_STUCK",
            "[Rcon] Alex joined the game",
            "[Server] Steve died",
            "[Alex] Steve died",
            "[[Crew] Alex] Steve died",
            "System chat: [Alex: Set own game mode to Creative Mode]",
            "System chat: [Alex: Teleported Alex to Steve]",
            "System chat: [Rcon: Gave 1 [Diamond] to Alex]",
            "Named entity Zombie['Bob'/2041, l='ServerLevel[world]', x=1.5, y=64.0, z=2.5]"
            " died: Bob was slain by Alex",
            known=("Alex",),
        ), [None] * 10)

    def test_untranslated_death_has_no_player(self):
        self.assertEqual(self.kinds("System chat: death.attack.magic.item"), [("death", None)])

    def test_logins_teach_the_parser_who_is_a_player(self):
        parser = console.Parser()
        parser.parse("[06:00:00] [User Authenticator #3/INFO]: UUID of player Steve is "
                     "00000000-0000-4000-8000-000000000001")
        self.assertIsNone(parser.parse("[06:00:01] [Server thread/INFO]: [Steve] Alex died"))
        parser.parse("[06:00:02] [Server thread/INFO]: Alex[IP hidden] logged in with entity id 7"
                     " at (0.5, 64.0, 0.5)")
        self.assertIsNone(parser.parse("[06:00:03] [Server thread/INFO]: [Alex] Steve died"))

    def test_the_units_stop_line_is_a_server_event(self):
        import shlex
        import subprocess
        unit = os.path.join(os.path.dirname(FIXTURES), "..", "deploy", "systemd", "minecraft.service")
        if not os.path.exists(unit):
            self.skipTest("deploy/ is not shipped to the box")
        with open(unit, encoding="utf-8") as f:
            stop = [l.split("=", 1)[1] for l in f.read().splitlines() if l.startswith("ExecStopPost=")]
        self.assertEqual(len(stop), 1)
        argv = shlex.split(stop[0].replace("%%", "%"))
        out = subprocess.run(argv, stdout=subprocess.PIPE, universal_newlines=True, check=True).stdout
        found = console.parse(out.rstrip("\n"))
        self.assertEqual((found["kind"], found["player"], found["text"]),
                         ("server", None, "server stopped"))


class TestParseCommand(Box):
    def parse(self, path):
        from vfg_bridge import __main__ as entry
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            code = entry.main(["--parse", path])
        return code, out.getvalue()

    def test_parse_prints_json_and_touches_nothing(self):
        code, out = self.parse(LOG)
        self.assertEqual(code, 0)
        with open(EXPECTED, encoding="utf-8") as f:
            self.assertEqual([json.loads(line) for line in out.splitlines()], json.load(f))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_parse_reads_gzip(self):
        gz = os.path.join(self.tmp, "2026-10-07-1.log.gz")
        with open(LOG, "rb") as src, gzip.open(gz, "wb") as dst:
            shutil.copyfileobj(src, dst)
        self.assertEqual(self.parse(gz), self.parse(LOG))

    def test_parse_missing_file_fails(self):
        self.assertEqual(self.parse(os.path.join(self.tmp, "nope.log"))[0], 1)


class TestConsoleAt(unittest.TestCase):
    def test_same_day(self):
        now = at("2026-10-07T18:22:40.517Z")
        self.assertEqual(timeutil.format_time(timeutil.console_at(18, 22, 40, now)),
                         "2026-10-07T18:22:40.517Z")

    def test_before_midnight_read_after_keeps_yesterday(self):
        now = at("2026-10-08T00:00:01.250Z")
        self.assertEqual(timeutil.format_time(timeutil.console_at(23, 59, 58, now)),
                         "2026-10-07T23:59:58.250Z")

    def test_old_stamp_gets_zero_milliseconds(self):
        now = at("2026-10-07T18:30:00.999Z")
        self.assertEqual(timeutil.format_time(timeutil.console_at(18, 10, 0, now)),
                         "2026-10-07T18:10:00.000Z")

    def test_up_to_sixty_seconds_ahead_stays_today(self):
        now = at("2026-10-07T18:00:00.000Z")
        self.assertEqual(timeutil.console_at(18, 0, 59, now).date(), _dt.date(2026, 10, 7))
        self.assertEqual(timeutil.console_at(18, 1, 1, now).date(), _dt.date(2026, 10, 6))

    def test_just_after_midnight_read_just_before_is_tomorrow(self):
        now = at("2026-10-07T23:59:59.900Z")
        self.assertEqual(timeutil.console_at(0, 0, 0, now),
                         _dt.datetime(2026, 10, 8, 0, 0, 0, 900000, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
