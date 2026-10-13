"""Casts on port 0: a copy started from the store, 404s, cooldowns, duplicates, the record;
the list's long poll; Layer 2 and the drop rule on lines.  The clock is a FakeClock, so no
test waits out a cooldown; the kit is the repo's scripts/_lib."""

import datetime as _dt
import http.client
import json
import os
import sqlite3
import threading
import time
import unittest
import uuid
from unittest import mock

import vfg_feed

from vfg_bridge import api, casts as castsmod, timeutil
from vfg_bridge.kit import Kit

from helpers import BRIDGE_DIR, Box, FakeClock

KIT_DIR = os.path.join(os.path.dirname(BRIDGE_DIR), "scripts", "_lib")
KIT = Kit(KIT_DIR)  # one for every test: the dictionary is read once


def _feed_takes_cast():
    """Whether the feed's GAME grammar takes the cast kind: the brain branch's takes any
    lowercase kind, main's only its six."""
    try:
        vfg_feed._check("GAME", "cast Steve")
    except ValueError:
        return False
    return True


FEED_TAKES_CAST = _feed_takes_cast()
SCRIPT = """\
import os
with open(os.path.join(os.environ["VFG_RUN_DIR"], os.environ["VFG_SCRIPT"] + ".ran"), "w") as f:
    f.write("ran\\n")
"""


def entry(player, phrase, script="zap", **more):
    e = {"id": "%s.%s" % (player.lower(), phrase.replace(" ", "_")), "player": player,
         "phrase": phrase, "aliases": [], "action": script, "script": script, "source": "library",
         "server": "", "values": {}, "live": [], "runs": "once", "caster_fields": [],
         "cooldown_s": 3.0, "created": "2026-10-10T18:22:31.412Z", "by": "zap_spell_steve"}
    e.update(more)
    return e


class CastCase(Box):
    def setUp(self):
        super().setUp()
        self.base = os.path.join(self.tmp, "vfg")
        self.run_dir = os.path.join(self.base, "run")
        self.store_path = os.path.join(self.base, "incantations.json")
        self.deploy("zap")
        self.db = self.make_db()
        self.clock = FakeClock(timeutil.utcnow())
        self.casts = castsmod.Casts(self.db, KIT, vfg_feed, self.store_path, self.base,
                                    mc=self.tmp, clock=self.clock)
        self.casts.LIST_POLL_S = 0.01
        self.write_store(entry("Steve", "fire ball"), entry("Steve", "ice wall"),
                         entry("Alex", "rock slide"), entry("@a", "light up"))
        self.server = api.make_server("127.0.0.1", 0, self.db, "test", self.clock, self.casts)
        thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(5)
        self.addCleanup(stop)
        self.addCleanup(self.join_reapers)
        self.port = self.server.server_address[1]

    @staticmethod
    def join_reapers():
        """Every copy started ends before the box is removed."""
        for t in threading.enumerate():
            if t.name.startswith("reap-"):
                t.join(5)

    # -- the box ---------------------------------------------------------------

    def deploy(self, name):
        folder = os.path.join(self.base, "scripts", name)
        os.makedirs(folder)
        with open(os.path.join(folder, "main.py"), "w") as f:
            f.write(SCRIPT)

    def write_store(self, *entries, version=1):
        os.makedirs(self.base, exist_ok=True)
        tmp = self.store_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"version": version, "incantations": list(entries)}, f)
        os.replace(tmp, self.store_path)

    @staticmethod
    def eventually(check):
        """Whether `check()` holds within 5 s: for a copy's process, or the record written after
        the answer."""
        deadline = time.monotonic() + 5
        while not check() and time.monotonic() < deadline:
            time.sleep(0.01)
        return bool(check())

    def ran(self, copy):
        return self.eventually(lambda: os.path.exists(os.path.join(self.run_dir, copy + ".ran")))

    def events(self, n):
        """Whether n events rows are written, which comes after the answer."""
        return self.eventually(lambda: self.rows("SELECT count(*) FROM events")[0][0] >= n)

    def fed(self, n):
        """Whether n feed lines are written, after the events rows."""
        return self.eventually(lambda: len(self.feed_lines()) >= n)

    def meta(self, copy):
        with open(os.path.join(self.run_dir, copy + ".json")) as f:
            return json.load(f)

    # -- requests ----------------------------------------------------------------

    def request(self, method, path, body=None, token="test"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Authorization": "Bearer " + token} if token else {}
        payload = None if body is None else json.dumps(body).encode()
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, (json.loads(data) if data else None)

    def window(self, start=-2.0, length=0.6):
        """A window `start` s from the clock's now, as the mod's audio clock gives it."""
        began = self.clock.now + _dt.timedelta(seconds=start)
        return (timeutil.format_time(began),
                timeutil.format_time(began + _dt.timedelta(seconds=length)))

    def cast(self, phrase="fire ball", player="Steve", start=-2.0, length=0.6, changes=None):
        started, ended = self.window(start, length)
        body = {"client_cast_id": str(uuid.uuid4()), "player_uuid": None, "player_name": player,
                "phrase": phrase, "heard": phrase.replace(" ", ""), "started_at": started,
                "ended_at": ended}
        body.update(changes or {})
        return self.request("POST", "/v1/casts", body)

    def line(self, text, player="Steve", start=-2.5, length=1.5):
        started, ended = self.window(start, length)
        body = {"client_line_id": str(uuid.uuid4()), "player_uuid": None, "player_name": player,
                "text": text, "started_at": started, "ended_at": ended, "peak_dbfs": -8.0,
                "mean_dbfs": -20.0}
        return self.request("POST", "/v1/lines", body)

    def rows(self, sql, args=()):
        return self.query(sql, args)


class TestFire(CastCase):
    def test_a_cast_fires_and_its_copy_starts(self):
        status, doc = self.cast()
        self.assertEqual(status, 202)
        self.assertEqual((doc["status"], doc["copy"]), ("fired", "zap"))
        self.assertTrue(self.ran("zap"))
        meta = self.meta("zap")
        self.assertEqual((meta["action"], meta["script"], meta["task"]), ("zap", "zap", "cast"))
        self.assertEqual(self.rows("SELECT id, player_name, phrase, heard, layer, incantation_id,"
                                   " status, copy FROM casts"),
                         [(doc["id"], "Steve", "fire ball", "fireball", "spotter", "steve.fire_ball",
                           "fired", "zap")])
        pid = os.path.join(self.run_dir, "zap.pid")  # the copy is reaped, and its pid file goes
        self.assertTrue(self.eventually(lambda: not os.path.exists(pid)))

    def test_caster_fields_are_the_caster(self):
        self.write_store(entry("@a", "fire ball", values={"player": "", "target": "Notch", "power": 2},
                               caster_fields=["player"]))
        status, doc = self.cast(player="Alex")
        self.assertEqual(doc["status"], "fired")
        meta = self.meta(doc["copy"])
        self.assertEqual(meta["values"], {"player": "Alex", "target": "Notch", "power": 2})

    def test_a_value_the_action_gained_after_the_binding_takes_its_default(self):
        # vfg deployed zap with its action as it is now: it has gained `radius` since the binding
        with open(os.path.join(self.base, "scripts", "zap", "action_defaults.json"), "w") as f:
            json.dump({"zap": {"values": {"player": "@a", "target": "", "power": 1, "reach": 30, "radius": 5},
                               "live": []}}, f)
        self.write_store(entry("@a", "fire ball", values={"player": "", "target": "Notch", "power": 2, "reach": 20},
                               asked={"target": "Notch", "power": 2}, caster_fields=["player"]))
        status, doc = self.cast(player="Alex")
        self.assertEqual(doc["status"], "fired")
        self.assertTrue(self.ran(doc["copy"]))
        # what it asked and the caster are the binding's; reach follows the action; radius its default
        self.assertEqual(self.meta(doc["copy"])["values"],
                         {"player": "Alex", "target": "Notch", "power": 2, "reach": 30, "radius": 5})

    def test_the_event_row(self):
        self.cast()
        self.assertTrue(self.events(1))
        self.assertEqual(self.rows("SELECT at, kind, player_name, text FROM events"),
                         [(timeutil.format_time(self.clock.now), "cast", "Steve",
                           'Steve cast "fire ball": zap, running as zap')])

    def test_not_deployed_fails_with_its_event(self):
        self.write_store(entry("Steve", "fire ball", script="gone", action="gone"))
        status, doc = self.cast()
        self.assertEqual((status, doc["status"]), (202, "failed"))
        self.assertIn("gone is not deployed", doc["error"])
        self.assertEqual(self.rows("SELECT status, error FROM casts"), [("failed", doc["error"])])
        self.assertTrue(self.events(1))
        self.assertEqual(self.rows("SELECT kind, text FROM events"),
                         [("cast", 'Steve cast "fire ball": failed — ' + doc["error"])])


@unittest.skipUnless(FEED_TAKES_CAST, "the feed's GAME grammar takes `cast` on the brain branch")
class TestFeedLine(CastCase):
    def test_a_fired_cast(self):
        self.cast()
        at = timeutil.format_time(self.clock.now)
        self.assertTrue(self.fed(1))
        self.assertEqual(self.feed_lines(), ['GAME#1 %s cast Steve | Steve cast "fire ball": zap,'
                                             ' running as zap' % at[11:19]])

    def test_a_failed_cast_whatever_watch_json_says(self):
        self.write_watch('{"events": [], "players": ["Notch"], "contains": []}')
        self.write_store(entry("Steve", "fire ball", script="gone", action="gone"))
        _, doc = self.cast()
        self.assertTrue(self.fed(1))
        self.assertTrue(self.feed_lines()[0].endswith(
            'cast Steve | Steve cast "fire ball": failed — ' + doc["error"]))

    def test_not_a_cooldown(self):
        self.cast(start=-2.0)
        self.cast(start=-1.0)  # its own words, 0.4 s on
        self.assertTrue(self.events(1))
        self.assertTrue(self.fed(1))
        self.assertEqual(self.rows("SELECT status FROM casts"), [("fired",), ("cooldown",)])
        self.assertEqual(len(self.feed_lines()), 1)


class TestWho(CastCase):
    def test_an_unknown_phrase_is_404(self):
        status, doc = self.cast("water bolt")
        self.assertEqual(status, 404)
        self.assertIn("water bolt", doc["error"])
        self.assertEqual(self.rows("SELECT * FROM casts"), [])

    def test_another_players_phrase_is_404(self):
        self.assertEqual(self.cast("rock slide")[0], 404)

    def test_an_everyone_phrase_fires_for_anyone(self):
        self.assertEqual(self.cast("light up", player="Notch")[1]["status"], "fired")

    def test_a_store_change_is_seen_on_the_next_cast(self):
        self.assertEqual(self.cast("water bolt")[0], 404)
        self.write_store(entry("Steve", "water bolt"), version=2)
        self.assertEqual(self.cast("water bolt")[1]["status"], "fired")

    def test_an_alias_as_the_phrase(self):
        self.write_store(entry("Steve", "fire ball", aliases=["fireball"]))
        self.assertEqual(self.cast("fireball")[1]["status"], "fired")


class TestCooldowns(CastCase):
    def test_a_players_casts_are_a_second_apart(self):
        self.deploy("frost")
        self.write_store(entry("Steve", "fire ball"), entry("Steve", "ice wall", script="frost",
                                                            action="frost"))
        self.assertEqual(self.cast()[1]["status"], "fired")
        self.clock.advance(0.9)
        status, doc = self.cast("ice wall", start=-1.2, length=0.3)
        self.assertEqual(doc["status"], "cooldown")
        self.assertNotIn("copy", doc)
        self.clock.advance(0.1)  # said again, 0.5 s after the refused one
        self.assertEqual(self.cast("ice wall", start=-0.5, length=0.4)[1]["status"], "fired")

    def test_an_incantations_own_cooldown(self):
        self.assertEqual(self.cast()[1]["status"], "fired")
        self.clock.advance(2.9)
        self.assertEqual(self.cast()[1]["status"], "cooldown")
        self.clock.advance(0.1)  # said again, after the refused one
        self.assertEqual(self.cast(start=-0.6)[1]["status"], "fired")
        self.assertTrue(self.events(2))
        self.assertEqual(self.rows("SELECT count(*) FROM events"), [(2,)])  # not the cooldown's

    def test_a_zero_cooldown_leaves_the_gap(self):
        self.write_store(entry("Steve", "fire ball", cooldown_s=0))
        self.assertEqual(self.cast()[1]["status"], "fired")
        self.clock.advance(1.0)
        self.assertEqual(self.cast()[1]["status"], "fired")


class TestStale(CastCase):
    def test_a_cast_held_past_the_wait_is_stale(self):
        late = time.monotonic() + 1.6  # the bridge's own clock, 1.6 s after the request came
        self.casts.monotonic = lambda: late
        status, doc = self.cast()
        self.assertEqual((status, doc["status"]), (202, "stale"))
        self.assertNotIn("copy", doc)
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "zap.json")))  # not started
        self.casts.monotonic = time.monotonic
        self.assertEqual(self.cast(start=-1.0, length=0.3)[1]["status"], "fired")  # said again
        self.assertTrue(self.events(1))
        self.assertEqual(self.rows("SELECT count(*) FROM events"), [(1,)])  # not the stale one


class TestRepeats(CastCase):
    def test_a_duplicate_window(self):
        self.assertEqual(self.cast(start=-2.0, length=0.6)[1]["status"], "fired")
        self.clock.advance(10)  # past both cooldowns, so only the windows decide
        self.assertEqual(self.cast(start=-12.0 + 1.0, length=0.5)[1]["status"], "fired")  # 0.4 s on
        status, doc = self.cast(start=-12.0 + 0.8, length=0.1)  # 0.2 s after the first
        self.assertEqual(doc["status"], "duplicate")

    def test_a_refused_cast_is_not_cast_again_by_layer_2(self):
        self.cast(start=-2.0)
        self.clock.advance(2.0)
        self.assertEqual(self.cast(start=-0.6)[1]["status"], "cooldown")
        self.clock.advance(1.5)  # its line comes once the cooldown is over
        self.line("fire ball", start=-2.2, length=0.75)
        self.assertEqual(self.rows("SELECT layer, status FROM casts ORDER BY at"),
                         [("spotter", "fired"), ("spotter", "cooldown"), ("transcript", "duplicate")])

    def test_a_refused_transcript_cast_does_not_hold_the_next_words(self):
        self.cast(start=-2.0)  # fired: its window is -2.0 .. -1.4
        self.clock.advance(0.5)
        # its long line (-2.0 .. +1.8) holds a transcript cast that did not fire
        self.line("fire ball and build me a wall now", start=-2.5, length=3.8)
        self.clock.advance(3.0)  # past the cooldown: the next words, said at +1.6 .. +2.2
        self.assertEqual(self.cast(start=-1.9)[1]["status"], "fired")
        self.assertEqual(self.rows("SELECT layer, status FROM casts ORDER BY at"),
                         [("spotter", "fired"), ("transcript", "duplicate"), ("spotter", "fired")])

    def test_a_repeated_client_cast_id_answers_the_first(self):
        started, ended = self.window()
        body = {"client_cast_id": "c-1", "player_name": "Steve", "phrase": "fire ball",
                "started_at": started, "ended_at": ended}
        first = self.request("POST", "/v1/casts", body)
        self.clock.advance(10)
        again = self.request("POST", "/v1/casts", body)
        self.assertEqual(again, first)
        self.assertEqual(self.rows("SELECT count(*) FROM casts"), [(1,)])


class TestBadCasts(CastCase):
    def test_401_before_the_body(self):
        self.assertEqual(self.request("POST", "/v1/casts", {}, token="wrong"),
                         (401, {"error": "unauthorized"}))
        self.assertEqual(self.request("POST", "/v1/casts", {})[0], 400)
        self.assertEqual(self.request("GET", "/v1/incantations?player=Steve", token="wrong")[0], 401)

    def test_400(self):
        started, ended = self.window()
        for field, change in (("client_cast_id", {"client_cast_id": ""}),
                              ("player_name", {"player_name": "Steve!"}),
                              ("phrase", {"phrase": " "}), ("phrase", {"phrase": 7}),
                              ("heard", {"heard": 7}), ("started_at", {"started_at": "now"}),
                              ("ended_at", {"started_at": ended, "ended_at": started})):
            status, doc = self.cast(changes=change)
            self.assertEqual(status, 400, change)
            self.assertTrue(doc["error"].startswith(field + ": "), doc)
        for query in ("", "?player=", "?player=Steve&after=x", "?player=Steve&wait=-1"):
            self.assertEqual(self.request("GET", "/v1/incantations" + query)[0], 400, query)
        self.assertEqual(self.rows("SELECT * FROM casts"), [])


class TestNoKit(CastCase):
    def test_casts_and_the_list_are_503_and_lines_go_on(self):
        self.casts.kit = Kit(os.path.join(self.tmp, "nowhere"))
        status, doc = self.cast()
        self.assertEqual(status, 503)
        self.assertIn("not on this box", doc["error"])
        self.assertEqual(self.request("GET", "/v1/incantations?player=Steve")[0], 503)
        self.assertEqual(self.line("fire ball")[0], 202)
        self.assertEqual(self.rows("SELECT text, dropped FROM lines"), [("fire ball", None)])


class TestList(CastCase):
    def get(self, query):
        return self.request("GET", "/v1/incantations?" + query)

    def test_a_players_own_then_everyones(self):
        status, doc = self.get("player=steve")
        self.assertEqual(status, 200)
        self.assertEqual(doc["version"], 1)
        self.assertEqual([e["phrase"] for e in doc["incantations"]], ["fire ball", "ice wall", "light up"])
        self.assertEqual(doc["incantations"][0], {"phrase": "fire ball", "aliases": [], "cooldown_s": 3.0})
        self.assertEqual([e["phrase"] for e in self.get("player=Notch")[1]["incantations"]], ["light up"])
        self.assertEqual(self.get("player=Steve&after=0")[0], 200)  # another version: at once

    def test_304_when_nothing_changes(self):
        start = time.monotonic()
        self.assertEqual(self.get("player=Steve&after=1&wait=0.1"), (304, None))
        self.assertGreaterEqual(time.monotonic() - start, 0.1)

    def test_a_held_request_answers_when_the_store_changes(self):
        looked = threading.Event()
        key = self.casts._file_key
        calls = []

        def watched():
            calls.append(1)
            if len(calls) >= 2:  # the second look is inside the hold
                looked.set()
            return key()
        self.casts._file_key = watched
        got = []
        t = threading.Thread(target=lambda: got.append(self.get("player=Steve&after=1&wait=10")))
        t.start()
        self.assertTrue(looked.wait(5))
        self.assertEqual(self.request("GET", "/v1/health", token=None)[0], 200)  # not blocked
        self.write_store(entry("Steve", "fire ball"), version=2)
        t.join(5)
        self.assertEqual(got[0][0], 200)
        self.assertEqual((got[0][1]["version"], len(got[0][1]["incantations"])), (2, 1))


class TestLayer2(CastCase):
    def test_a_line_fires_a_transcript_cast(self):
        status, doc = self.line("fire bowl")
        self.assertEqual(status, 202)
        rows = self.rows("SELECT id, layer, heard, status, line_id, started_at FROM casts")
        self.assertEqual([r[1:5] for r in rows], [("transcript", "fire bowl", "fired", doc["id"])])
        self.assertEqual(rows[0][5], self.window(-2.5)[0])  # the line's window
        self.assertTrue(self.ran("zap"))
        self.assertEqual(self.rows("SELECT dropped, cast_id FROM lines"), [("cast", rows[0][0])])
        self.assertTrue(self.events(1))

    def test_not_when_a_spotter_cast_covers_its_window(self):
        _, spotted = self.cast(start=-2.0)
        self.clock.advance(1.5)  # the line arrives after the cast, past the 1 s gap
        self.line("fire ball", start=-4.0, length=1.5)
        self.assertEqual(self.rows("SELECT layer, status FROM casts ORDER BY layer"),
                         [("spotter", "fired"), ("transcript", "duplicate")])
        self.assertEqual(self.rows("SELECT dropped, cast_id FROM lines"), [("cast", spotted["id"])])

    def test_one_cast_for_the_same_words(self):
        self.write_store(entry("Steve", "fire ball"), entry("Steve", "fire wall"),
                         entry("@a", "fire ball"))
        self.line("fire ball")
        self.assertEqual(self.rows("SELECT incantation_id FROM casts"), [("steve.fire_ball",)])

    def test_a_cast_is_announced_when_its_line_is_not_stored(self):
        with mock.patch.object(self.db, "insert_voice", side_effect=sqlite3.OperationalError("locked")):
            self.assertEqual(self.line("fire ball")[0], 503)
        self.assertEqual(self.rows("SELECT layer, status FROM casts"), [("transcript", "fired")])
        self.assertTrue(self.events(1))

    def test_a_repeated_line_does_not_cast_again(self):
        started, ended = self.window(-2.5, 1.5)
        body = {"client_line_id": "l-1", "player_name": "Steve", "text": "ice wall",
                "started_at": started, "ended_at": ended}
        first = self.request("POST", "/v1/lines", body)
        self.clock.advance(10)
        self.assertEqual(self.request("POST", "/v1/lines", body), first)
        self.assertEqual(self.rows("SELECT count(*) FROM casts"), [(1,)])


class TestDropRule(CastCase):
    def test_the_specs_four_lines(self):
        self.casts.layer2 = False
        _, spotted = self.cast(start=-2.0, length=0.6)
        ids = [self.line(text, start=-2.2, length=2.0)[1]["id"] for text in (
            "fire ball", "fire ball now", "fire ball and build me a wall", "don't cast fire ball")]
        got = dict((r[0], r[1:]) for r in self.rows("SELECT id, dropped, cast_id FROM lines"))
        self.assertEqual([got[i][0] for i in ids], ["cast", "cast", None, None])
        self.assertEqual(got[ids[2]][1], spotted["id"])
        self.assertEqual(self.rows("SELECT line_id FROM casts"), [(ids[0],)])

    def test_a_cast_after_its_line_drops_it(self):
        self.casts.layer2 = False
        _, line = self.line("fire ball", start=-2.2, length=1.0)
        _, cast = self.cast(start=-2.0, length=0.6)
        self.assertTrue(self.eventually(
            lambda: self.rows("SELECT dropped FROM lines") == [("cast",)]))
        self.assertEqual(self.rows("SELECT cast_id FROM lines"), [(cast["id"],)])
        self.assertEqual(self.rows("SELECT line_id FROM casts"), [(line["id"],)])

    def test_a_line_away_from_the_cast_is_kept(self):
        self.casts.layer2 = False
        self.cast(start=-2.0, length=0.6)
        self.line("fire ball", start=-10.0, length=1.0)
        self.assertEqual(self.rows("SELECT dropped, cast_id FROM lines"), [(None, None)])


class TestClockGuard(CastCase):
    def test_a_players_shift_is_kept_for_their_line(self):
        said = self.clock.now + _dt.timedelta(hours=1)  # Steve's clock is an hour fast
        self.clock.advance(0.4)
        cast = self.server.clock_guard({"player_name": "Steve", "started_at": said,
                                        "ended_at": said})
        self.clock.advance(3.1)  # the line of the same words comes later
        line = self.server.clock_guard({"player_name": "steve", "started_at": said,
                                        "ended_at": said})
        self.assertEqual(line, cast)
        right = self.clock.now - _dt.timedelta(seconds=1)  # their clock is put right
        self.assertEqual(self.server.clock_guard({"player_name": "Steve", "started_at": right,
                                                  "ended_at": right}), (right, right))


class TestConfig(unittest.TestCase):
    def test_defaults_and_settings(self):
        from vfg_bridge import config
        cfg = config.load({})
        self.assertEqual((cfg.base, cfg.mc, cfg.kit, cfg.incantations, cfg.owner),
                         ("/opt/vfg", "/opt/mc", "/opt/vfg/scripts/_lib", "/opt/vfg/incantations.json", ""))
        self.assertEqual((cfg.live_poll_s, cfg.cast_gap_s, cfg.cast_slack_s, cfg.cast_wait_s,
                          cfg.incant_allowance, cfg.incant_layer2), (0.5, 1.0, 0.3, 1.5, 0.25, True))
        cfg = config.load({"VFG_BASE": "/b", "VFG_INCANT_LAYER2": "off", "VFG_CAST_GAP_S": "2"})
        self.assertEqual((cfg.kit, cfg.incantations, cfg.incant_layer2, cfg.cast_gap_s),
                         ("/b/scripts/_lib", "/b/incantations.json", False, 2.0))
        with self.assertRaises(SystemExit):
            config.load({"VFG_CAST_SLACK_S": "soon"})


if __name__ == "__main__":
    unittest.main()
