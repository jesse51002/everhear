"""Casts on port 0, every incantation the engine's (a fake engine socket and export): a cast sent
to the engine, 404s, the duplicate window and the stale wait, the record and its announcement; the
list's long poll; Layer 2 and the drop rule on lines; the Jev filter's casts. The clock is a
FakeClock; the phoneme matching is the bridge's own (sounds.py)."""

import datetime as _dt
import os
import sqlite3
import threading
import time
import unittest
from unittest import mock

import vfg_feed

from vfg_bridge import api, casts as castsmod, config, engine as enginemod, timeutil

from fake_engine import CastCase, binding


def _feed_takes_cast():
    """Whether the feed's GAME grammar takes the cast kind: the brain branch's takes any
    lowercase kind, main's only its six."""
    try:
        vfg_feed._check("GAME", "cast Steve")
    except ValueError:
        return False
    return True


FEED_TAKES_CAST = _feed_takes_cast()


class TestFire(CastCase):
    def test_a_cast_goes_to_the_engine_and_is_recorded(self):
        status, doc = self.cast()
        self.assertEqual((status, doc), (202, {"id": doc["id"], "status": "fired"}))
        [req] = self.fake.casts()
        self.assertEqual(req["args"], {"player": "Steve", "phrase": "fire ball", "cast_id": doc["id"]})
        self.assertEqual(self.rows("SELECT id, player_name, phrase, heard, layer, incantation_id, action, copy,"
                                   " status FROM casts"),
                         [(doc["id"], "Steve", "fire ball", "fireball", "spotter", "engine:steve.fire_ball", "zap",
                           None, "fired")])

    def test_the_event_row(self):
        self.cast()
        self.assertTrue(self.events(1))
        self.assertEqual(self.rows("SELECT at, kind, player_name, text FROM events"),
                         [(timeutil.format_time(self.clock.now), "cast", "Steve",
                           'Steve cast "fire ball": zap, fired in the engine')])

    def test_a_failed_cast_with_its_event(self):
        self.fake.answer = lambda req: {"ok": True, "status": "failed", "error": "zap is not loaded"}
        status, doc = self.cast()
        self.assertEqual((status, doc["status"], doc["error"]), (202, "failed", "zap is not loaded"))
        self.assertEqual(self.rows("SELECT status, error FROM casts"), [("failed", "zap is not loaded")])
        self.assertTrue(self.events(1))
        self.assertEqual(self.rows("SELECT kind, text FROM events"),
                         [("cast", 'Steve cast "fire ball": failed — zap is not loaded')])

    def test_a_scenario_binding_is_cast_the_same_way(self):
        self.write_export(binding("Steve", "meteor", ability=None, scenario="meteor",
                                  args={"count": 3, "at": "caster"}, cooldown_s=4), version=2)
        self.fake.answer = lambda req: {"ok": True, "status": "fired", "instance": "scenario:meteor#3"}
        self.assertEqual(self.cast("meteor")[1]["status"], "fired")
        self.assertEqual(self.fake.casts()[0]["args"], {"player": "Steve", "phrase": "meteor",
                                                        "cast_id": self.rows("SELECT id FROM casts")[0][0]})
        self.assertEqual(self.rows("SELECT incantation_id, action FROM casts"), [("engine:steve.meteor", "meteor")])
        self.assertTrue(self.events(1))
        self.assertEqual(self.rows("SELECT text FROM events"), [('Steve cast "meteor": meteor, fired in the engine',)])


@unittest.skipUnless(FEED_TAKES_CAST, "the feed's GAME grammar takes `cast` on the brain branch")
class TestFeedLine(CastCase):
    def test_a_fired_cast(self):
        self.cast()
        at = timeutil.format_time(self.clock.now)
        self.assertTrue(self.fed(1))
        self.assertEqual(self.feed_lines(), ['GAME#1 %s cast Steve | Steve cast "fire ball": zap, fired in the'
                                             ' engine' % at[11:19]])

    def test_a_failed_cast_whatever_watch_json_says(self):
        self.write_watch('{"events": [], "players": ["Notch"], "contains": []}')
        self.fake.answer = lambda req: {"ok": True, "status": "not_granted"}
        _, doc = self.cast()
        self.assertTrue(self.fed(1))
        self.assertTrue(self.feed_lines()[0].endswith(
            'cast Steve | Steve cast "fire ball": failed — ' + doc["error"]))
        self.assertEqual(doc["error"], "Steve has not been granted zap")

    def test_not_a_cooldown(self):
        self.cast(start=-2.0)
        self.fake.answer = lambda req: {"ok": True, "status": "cooldown", "remaining_s": 2.5}
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
        self.assertEqual((self.rows("SELECT * FROM casts"), self.fake.casts()), ([], []))

    def test_another_players_phrase_is_404_and_names_are_any_case(self):
        self.assertEqual(self.cast("rock slide")[0], 404)
        self.assertEqual(self.cast("rock slide", player="alex")[1]["status"], "fired")
        self.assertEqual(self.fake.casts()[0]["args"]["player"], "alex")

    def test_an_export_change_is_seen_on_the_next_cast(self):
        self.assertEqual(self.cast("water bolt")[0], 404)
        self.write_export(binding("Steve", "water bolt"), version=2)
        self.assertEqual(self.cast("water bolt")[1]["status"], "fired")

    def test_an_alias_casts_the_bindings_own_phrase(self):
        self.write_export(binding("Steve", "fire ball", aliases=["fireball", "fire bowl"]), version=2)
        self.assertEqual(self.cast("fire bowl")[1]["status"], "fired")
        self.assertEqual(self.fake.casts()[0]["args"]["phrase"], "fire ball")

    def test_no_export_no_incantations_and_lines_go_on(self):
        os.remove(self.export_path)
        self.assertEqual(self.cast()[0], 404)
        self.assertEqual(self.request("GET", "/v1/incantations?player=Steve"),
                         (200, {"version": 0, "player": "Steve", "incantations": []}))
        self.assertEqual(self.line("fire ball")[0], 202)
        self.assertEqual(self.rows("SELECT text, dropped FROM lines"), [("fire ball", None)])


class TestNoBridgeCooldown(CastCase):
    def test_the_engine_decides_what_a_second_cast_does(self):
        self.assertEqual(self.cast(start=-2.0)[1]["status"], "fired")
        self.clock.advance(0.3)
        self.assertEqual(self.cast(start=-1.0, length=0.3)[1]["status"], "fired")   # its own words
        self.assertEqual(self.cast("ice wall", start=-0.5, length=0.3)[1]["status"], "fired")
        self.assertEqual(len(self.fake.casts()), 3)

    def test_the_engines_cooldown_answers_cooldown(self):
        self.fake.answer = lambda req: {"ok": True, "status": "cooldown", "remaining_s": 2.34}
        _, doc = self.cast()
        self.assertEqual((doc["status"], doc["error"]), ("cooldown", "2.3 s left of its cooldown"))
        time.sleep(0.2)
        self.assertEqual(self.rows("SELECT count(*) FROM events"), [(0,)])   # not announced


class TestStale(CastCase):
    def test_a_cast_held_past_the_wait_is_stale_and_never_sent(self):
        late = time.monotonic() + 1.6  # the bridge's own clock, 1.6 s after the request came
        self.casts.monotonic = lambda: late
        status, doc = self.cast()
        self.assertEqual((status, doc["status"]), (202, "stale"))
        self.assertEqual(self.fake.casts(), [])
        self.casts.monotonic = time.monotonic
        self.assertEqual(self.cast(start=-1.0, length=0.3)[1]["status"], "fired")  # said again
        self.assertTrue(self.events(1))
        self.assertEqual(self.rows("SELECT count(*) FROM events"), [(1,)])  # not the stale one

    def test_the_engines_wait_is_never_past_the_bridges_own(self):
        now = time.monotonic()
        self.casts.monotonic = lambda: now + 1.3  # 0.2 s left of the 1.5 s
        self.cast()
        self.assertAlmostEqual(self.fake.casts()[0]["timeout_s"], 0.2, delta=0.05)


class TestRepeats(CastCase):
    def test_a_duplicate_window(self):
        self.assertEqual(self.cast(start=-2.0, length=0.6)[1]["status"], "fired")
        self.clock.advance(10)
        self.assertEqual(self.cast(start=-12.0 + 1.0, length=0.5)[1]["status"], "fired")  # 0.4 s on
        status, doc = self.cast(start=-12.0 + 0.8, length=0.1)  # 0.2 s after the first
        self.assertEqual(doc["status"], "duplicate")
        self.assertEqual(len(self.fake.casts()), 2)

    def test_a_refused_cast_is_not_cast_again_by_layer_2(self):
        self.cast(start=-2.0)
        self.clock.advance(2.0)
        self.fake.answer = lambda req: {"ok": True, "status": "cooldown", "remaining_s": 1}
        self.assertEqual(self.cast(start=-0.6)[1]["status"], "cooldown")
        self.clock.advance(1.5)  # its line comes later
        self.line("fire ball", start=-2.2, length=0.75)
        self.assertEqual(self.rows("SELECT layer, status FROM casts ORDER BY at"),
                         [("spotter", "fired"), ("spotter", "cooldown"), ("transcript", "duplicate")])

    def test_a_refused_transcript_cast_does_not_hold_the_next_words(self):
        self.cast(start=-2.0)  # fired: its window is -2.0 .. -1.4
        self.clock.advance(0.5)
        # its long line (-2.0 .. +1.8) holds a transcript cast that did not fire
        self.line("fire ball and build a wall", start=-2.5, length=3.8)
        self.clock.advance(3.0)  # the next words, said at +1.6 .. +2.2
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
        self.assertEqual(len(self.fake.casts()), 1)


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


class TestList(CastCase):
    def get(self, query):
        return self.request("GET", "/v1/incantations?" + query)

    def test_a_players_own_with_the_exports_version(self):
        status, doc = self.get("player=steve")
        self.assertEqual(status, 200)
        self.assertEqual((doc["version"], doc["player"]), (1, "steve"))
        self.assertEqual(doc["incantations"], [{"phrase": "fire ball", "aliases": [], "cooldown_s": 3.0},
                                               {"phrase": "ice wall", "aliases": [], "cooldown_s": 3.0}])
        self.assertEqual([e["phrase"] for e in self.get("player=Alex")[1]["incantations"]], ["rock slide"])
        self.assertEqual(self.get("player=Notch")[1]["incantations"], [])
        self.assertEqual(self.get("player=Steve&after=0")[0], 200)  # another version: at once

    def test_the_everyday_aliases_are_left_out_for_the_spotter(self):
        self.write_export(binding("Steve", "ice beam", aliases=["ice been", "ice cream", "i scream", "nice beam"],
                                  everyday_aliases=["ice cream", "I Scream"]),
                          binding("Steve", "fire ball", aliases=["fireball", "fire bowl"], cooldown_s=None),
                          version=2)
        got = self.get("player=Steve")[1]["incantations"]
        self.assertEqual(got, [{"phrase": "ice beam", "aliases": ["ice been", "nice beam"], "cooldown_s": 3.0},
                               {"phrase": "fire ball", "aliases": ["fireball", "fire bowl"], "cooldown_s": 0.0}])

    def test_a_scenario_bindings_cooldown_is_its_own(self):
        self.write_export(binding("Steve", "fire ball", cooldown_s=None),
                          binding("Steve", "meteor", ability=None, scenario="meteor", args={"at": "caster"},
                                  cooldown_s=4), version=2)
        self.assertEqual(self.get("player=Steve")[1]["incantations"],
                         [{"phrase": "fire ball", "aliases": [], "cooldown_s": 0.0},     # its ability not loaded
                          {"phrase": "meteor", "aliases": [], "cooldown_s": 4.0}])

    def test_304_when_nothing_changes(self):
        start = time.monotonic()
        self.assertEqual(self.get("player=Steve&after=1&wait=0.1"), (304, None))
        self.assertGreaterEqual(time.monotonic() - start, 0.1)

    def test_a_held_request_answers_when_the_export_changes(self):
        got = []
        t = threading.Thread(target=lambda: got.append(self.get("player=Steve&after=1&wait=10")))
        t.start()
        time.sleep(0.2)
        self.assertEqual(self.request("GET", "/v1/health", token=None)[0], 200)  # not blocked
        self.write_export(binding("Steve", "fire ball"), version=2)
        t.join(5)
        self.assertEqual(got[0][0], 200)
        self.assertEqual((got[0][1]["version"], len(got[0][1]["incantations"])), (2, 1))


class TestLayer2(CastCase):
    def test_a_line_casts_through_the_engine(self):
        status, doc = self.line("fire bowl")
        self.assertEqual(status, 202)
        rows = self.rows("SELECT id, layer, heard, status, line_id, started_at, client_cast_id FROM casts")
        self.assertEqual([r[1:5] for r in rows], [("transcript", "fire bowl", "fired", doc["id"])])
        self.assertEqual(rows[0][5], self.window(-2.5)[0])  # the line's window
        cid = self.rows("SELECT client_line_id FROM lines")[0][0]
        self.assertEqual(rows[0][6], "line:%s:engine:steve.fire_ball" % cid)
        self.assertEqual(self.fake.casts()[0]["args"]["phrase"], "fire ball")
        self.assertEqual(self.rows("SELECT dropped, cast_id FROM lines"), [("cast", rows[0][0])])
        self.assertTrue(self.events(1))

    def test_not_when_a_spotter_cast_covers_its_window(self):
        _, spotted = self.cast(start=-2.0)
        self.clock.advance(1.5)  # the line arrives after the cast
        self.line("fire ball", start=-4.0, length=1.5)
        self.assertEqual(self.rows("SELECT layer, status FROM casts ORDER BY layer"),
                         [("spotter", "fired"), ("transcript", "duplicate")])
        self.assertEqual(self.rows("SELECT dropped, cast_id FROM lines"), [("cast", spotted["id"])])
        self.assertEqual(len(self.fake.casts()), 1)

    def test_one_cast_for_the_same_words(self):
        self.write_export(binding("Steve", "fire ball"), binding("Steve", "fire wall"), version=2)
        self.line("fire ball")
        self.assertEqual(self.rows("SELECT incantation_id FROM casts"), [("engine:steve.fire_ball",)])

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


class TestLayer2MaxWords(CastCase):
    """Layer 2 only on a line of at most VFG_INCANT_LAYER2_MAX_WORDS words (6; 0 any)."""

    def test_six_words_cast_and_seven_do_not(self):
        self.line("uh fire ball on them now", start=-2.5)          # 6 words, the filler counted
        self.line("uh fire ball on all of them", start=-12.5)      # 7
        self.assertEqual(self.rows("SELECT heard FROM casts"), [("fire ball",)])
        self.assertEqual(self.rows("SELECT count(*) FROM lines"), [(2,)])

    def test_zero_is_any_length_and_the_setting_reaches_casts(self):
        self.casts.layer2_max_words = 0
        self.line("could you please cast the fire ball on all of them right now", start=-2.5)
        self.assertEqual(self.rows("SELECT layer, heard FROM casts"), [("transcript", "fire ball")])
        cfg = config.load({"VFG_INCANT_LAYER2_MAX_WORDS": "9"})
        self.assertEqual(castsmod.Casts.from_config(cfg, self.db, vfg_feed).layer2_max_words, 9)
        self.assertEqual(castsmod.Casts.from_config(config.load({}), self.db, vfg_feed).layer2_max_words, 6)

    def test_a_long_line_is_still_dropped_after_a_spotters_cast(self):
        _, spotted = self.cast(start=-2.0, length=0.6)
        self.line("uh um fire ball uh um uh", start=-2.2, length=1.0)   # 7 words, all fillers but the phrase
        self.assertEqual(self.rows("SELECT dropped, cast_id FROM lines"), [("cast", spotted["id"])])
        self.assertEqual(self.rows("SELECT layer FROM casts"), [("spotter",)])


class TestEverydayAliases(CastCase):
    """An everyday alias, left out of the spotter's list, still casts through Layer 2, and the
    lookup still finds it."""

    def setUp(self):
        super().setUp()
        self.write_export(binding("Steve", "ice beam", aliases=["ice been", "ice cream", "i scream"],
                                  everyday_aliases=["ice cream", "i scream"]), version=2)

    def test_layer_2_casts_it(self):
        self.line("Ice cream.")
        self.assertEqual(self.rows("SELECT incantation_id, layer, heard, status FROM casts"),
                         [("engine:steve.ice_beam", "transcript", "ice cream", "fired")])
        self.assertEqual(self.rows("SELECT dropped FROM lines"), [("cast",)])

    def test_the_drop_rule_takes_it_out(self):
        self.casts.layer2 = False
        _, spotted = self.cast("ice beam", start=-2.0, length=0.4)
        self.line("i scream", start=-2.2, length=1.0)
        self.assertEqual(self.rows("SELECT dropped, cast_id FROM lines"), [("cast", spotted["id"])])


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


class TestOneWord(CastCase):
    """A one-word phrase (Specs/2026-10-11-Prototype_Fast_Casts): cast by the spotter, by Layer
    2 in any sentence and at the start of a longer word, never in everyday talk; the drop rule."""

    def setUp(self):
        super().setUp()
        self.write_export(binding("Steve", "shield"), binding("Steve", "heal"), binding("Steve", "dash"), version=2)

    def test_the_spotters_cast_fires(self):
        status, doc = self.cast("shield")
        self.assertEqual((status, doc["status"]), (202, "fired"))
        self.assertEqual(self.rows("SELECT incantation_id, layer FROM casts"), [("engine:steve.shield", "spotter")])

    def test_layer_2_casts_it_in_any_sentence_and_keeps_the_line(self):
        self.casts.layer2_max_words = 0  # any length
        _, doc = self.line("Craft me an iron sword and a shield for the fight.")
        rows = self.rows("SELECT id, incantation_id, heard, status FROM casts")
        self.assertEqual([r[1:] for r in rows], [("engine:steve.shield", "shield", "fired")])
        self.assertEqual(self.rows("SELECT dropped, cast_id FROM lines"), [(None, rows[0][0])])

    def test_layer_2_casts_it_at_the_start_of_a_longer_word(self):
        self.line("I need some healing potions.")
        self.assertEqual(self.rows("SELECT incantation_id, heard FROM casts"), [("engine:steve.heal", "healing")])
        self.assertEqual(self.rows("SELECT dropped FROM lines"), [(None,)])

    def test_a_line_of_only_the_word_is_dropped(self):
        self.line("Shield now!")
        self.assertEqual(self.rows("SELECT layer, status FROM casts"), [("transcript", "fired")])
        self.assertEqual(self.rows("SELECT dropped FROM lines"), [("cast",)])

    def test_not_in_everyday_talk(self):
        for k, text in enumerate(("help me build it", "it's really all of it", "she'll do it",
                                  "the field is big", "go up the hill and deal with it")):
            self.line(text, start=-2.5 - 10 * k)
        self.assertEqual(self.rows("SELECT count(*) FROM casts"), [(0,)])
        self.assertEqual(self.rows("SELECT count(*) FROM lines WHERE dropped IS NULL"), [(5,)])

    def test_the_drop_rule_after_the_spotters_cast(self):
        self.casts.layer2 = False
        _, spotted = self.cast("heal", start=-2.0, length=0.4)
        ids = [self.line(text, start=-2.2, length=2.0)[1]["id"] for text in (
            "heal", "heal now", "don't heal", "heal me and build a wall", "help me")]
        got = dict((r[0], r[1]) for r in self.rows("SELECT id, dropped FROM lines"))
        self.assertEqual([got[i] for i in ids], ["cast", "cast", None, None, None])
        self.assertEqual(self.rows("SELECT line_id FROM casts"), [(ids[0],)])


class TestJevCasts(CastCase):
    """POST /v1/jev/casts: the Jev filter judged a stored voice line a cast, before routing it.
    The same checks as every cast, on the line's window; the line is dropped whatever came of it."""

    def setUp(self):
        super().setUp()
        self.casts.layer2 = False  # these lines' casts are the Jev filter's alone

    def jev(self, line_id, phrase="fire ball", token="test", keep=None):
        body = {"line_id": line_id, "phrase": phrase}
        if keep is not None:
            body["keep"] = keep
        return self.request("POST", "/v1/jev/casts", body, token=token)

    def lines(self):
        return self.rows("SELECT id, dropped, cast_id FROM lines WHERE source = 'voice' ORDER BY at")

    def test_a_cast_goes_to_the_engine_with_the_lines_window_and_drops_it(self):
        _, line = self.line("Affordable.", start=-2.5, length=1.0)
        status, doc = self.jev(line["id"])
        self.assertEqual(status, 202)
        self.assertEqual((doc["status"], doc["line"]), ("fired", "dropped"))
        self.assertEqual(self.fake.casts()[0]["args"]["cast_id"], doc["id"])
        started, ended = self.window(-2.5, 1.0)
        self.assertEqual(self.rows("SELECT id, layer, heard, incantation_id, started_at, ended_at, line_id,"
                                   " client_cast_id FROM casts"),
                         [(doc["id"], "jev", "affordable", "engine:steve.fire_ball", started, ended, line["id"],
                           "jev:" + line["id"])])
        self.assertEqual(self.lines(), [(line["id"], "cast", doc["id"])])
        self.assertTrue(self.events(1))
        self.assertEqual(self.rows("SELECT kind, text FROM events"),
                         [("cast", 'Steve cast "fire ball": zap, fired in the engine')])

    def test_cooldown_still_drops_the_line(self):
        self.fake.answer = lambda req: {"ok": True, "status": "cooldown", "remaining_s": 1.5}
        _, line = self.line("Fire.", start=-1.0, length=0.5)
        status, doc = self.jev(line["id"])
        self.assertEqual((status, doc["status"], doc["line"]), (202, "cooldown", "dropped"))
        self.assertEqual(self.lines(), [(line["id"], "cast", doc["id"])])
        time.sleep(0.2)
        self.assertEqual(self.rows("SELECT count(*) FROM events"), [(0,)])

    def test_duplicate_still_drops_the_line_and_keeps_its_fired_cast(self):
        _, spotted = self.cast(start=-2.0, length=0.6)
        _, line = self.line("fire ball, fire bowl", start=-2.2, length=2.0)
        self.assertEqual(self.lines(), [(line["id"], None, spotted["id"])])  # kept: two words left
        self.clock.advance(5)
        status, doc = self.jev(line["id"])
        self.assertEqual((status, doc["status"], doc["line"]), (202, "duplicate", "dropped"))
        self.assertEqual(self.lines(), [(line["id"], "cast", spotted["id"])])
        self.assertEqual(self.rows("SELECT line_id FROM casts WHERE id = ?", (doc["id"],)), [(line["id"],)])

    def test_another_incantation_than_the_lines_own_cast(self):
        _, line = self.line("Ice. Wall", start=-2.5, length=1.0)
        status, doc = self.jev(line["id"], "ice wall")
        self.assertEqual(doc["status"], "fired")
        self.assertEqual(self.fake.casts()[0]["args"]["phrase"], "ice wall")

    def received_ago(self, line_id, seconds):
        """Move when /v1/lines received the line `seconds` further back on the bridge's monotonic
        clock, as if that long had passed."""
        cid = self.rows("SELECT client_line_id FROM lines WHERE id = ?", (line_id,))[0][0]
        self.casts._received[cid] -= seconds

    def test_a_line_older_than_the_max_age_is_stale_and_still_dropped(self):
        _, line = self.line("Affordable.")
        self.received_ago(line["id"], 1.9)  # under 2 s since /v1/lines received it: in time
        _, doc = self.jev(line["id"], "ice wall")
        self.assertEqual(doc["status"], "fired")
        self.clock.advance(2.0)
        _, late = self.line("Hot variable.", start=-1.5, length=1.0)
        self.clock.advance(2.5)
        self.received_ago(late["id"], 2.5)
        status, doc = self.jev(late["id"])
        self.assertEqual((status, doc["status"], doc["line"]), (202, "stale", "dropped"))
        self.assertRegex(doc["error"], r"^the line is 2\.5 s old, past the 2 s a Jev cast may be$")
        self.assertEqual(self.rows("SELECT status, error, line_id FROM casts WHERE id = ?", (doc["id"],)),
                         [("stale", doc["error"], late["id"])])
        self.assertEqual(self.lines()[1], (late["id"], "cast", doc["id"]))
        self.assertTrue(self.events(1))
        self.assertEqual(self.rows("SELECT count(*) FROM events"), [(1,)])  # the stale one is not announced
        self.assertEqual(len(self.fake.casts()), 1)

    def test_the_max_age_is_a_setting_and_a_line_from_before_a_restart_is_stale(self):
        self.casts.jev_max_age_s = 10
        _, line = self.line("Affordable.")
        self.received_ago(line["id"], 9)
        self.assertEqual(self.jev(line["id"])[1]["status"], "fired")
        _, old = self.line("Hot variable.", start=-30, length=1.0)
        self.casts._received.clear()  # the bridge restarted since it received the line
        self.clock.advance(5)
        _, doc = self.jev(old["id"])
        self.assertEqual(doc["status"], "stale")
        self.assertIn("no time it received the line", doc["error"])
        self.assertEqual(self.lines()[0], (old["id"], "cast", doc["id"]))

    def test_only_a_line_of_a_player_with_an_incantation_is_noted(self):
        self.line("hello there", player="Notch")   # Notch has none: not noted
        self.line("hello there", player="Alex")    # Alex has one
        self.assertEqual(len(self.casts._received), 1)

    def test_keep_casts_the_same_and_keeps_the_line(self):
        _, line = self.line("I still have the floaty effect. Can you remove it? Fire ball.")
        status, doc = self.jev(line["id"], keep=True)
        self.assertEqual((status, doc["status"], doc["line"]), (202, "fired", "kept"))
        self.assertEqual(self.lines(), [(line["id"], None, doc["id"])])   # kept, carrying the cast
        self.assertEqual(self.rows("SELECT layer, line_id, client_cast_id FROM casts"),
                         [("jev", line["id"], "jev:" + line["id"])])
        self.assertTrue(self.events(1))
        self.assertEqual(self.jev(line["id"], keep=True), (status, doc))   # asked again: the first

    def test_keep_with_a_cast_that_does_not_fire_leaves_the_lines_cast_id(self):
        self.fake.answer = lambda req: {"ok": True, "status": "cooldown", "remaining_s": 1}
        _, line = self.line("watch this everybody, look at me now, fire ball", start=-1.0, length=0.5)
        status, doc = self.jev(line["id"], keep=True)
        self.assertEqual((doc["status"], doc["line"]), ("cooldown", "kept"))
        self.assertEqual(self.lines(), [(line["id"], None, None)])
        self.fake.answer = lambda req: {"ok": True, "status": "fired"}
        self.clock.advance(2.0)
        _, spotted = self.cast(start=-0.9, length=0.3)
        self.assertEqual(spotted["status"], "fired")
        self.clock.advance(5)
        _, other = self.line("ok watch this one too everybody, fire ball", start=-6.0, length=1.0)
        self.assertEqual(self.lines()[-1], (other["id"], None, spotted["id"]))
        _, doc = self.jev(other["id"], keep=True)
        self.assertEqual((doc["status"], doc["line"]), ("duplicate", "kept"))
        self.assertEqual(self.lines()[-1], (other["id"], None, spotted["id"]))

    def test_a_phrase_said_early_in_a_long_line_is_stale(self):
        """An 8 s line: the phrase at its start was said about 6 s before it ended, too late."""
        _, early = self.line("Fire ball. Can you also give me some food please and a sword", start=-9, length=8)
        status, doc = self.jev(early["id"], keep=True)
        self.assertEqual((status, doc["status"], doc["line"]), (202, "stale", "kept"))
        self.assertRegex(doc["error"], r"^the phrase was said about 6\.\d s before its line ended, past the 3 s")
        self.assertEqual(self.lines(), [(early["id"], None, None)])
        self.assertEqual(self.fake.casts(), [])

    def test_a_phrase_at_the_end_or_said_again_there_or_not_found_fires(self):
        for n, text in enumerate(("Can you also give me some food please and then fire ball",
                                  "Fire ball, I said, can you give me food please, fire ball",
                                  "watch this everybody look at me now right now kaboom")):
            self.clock.advance(10)
            _, line = self.line(text, start=-9, length=8)
            self.assertEqual(self.jev(line["id"], keep=True)[1]["status"], "fired", text)

    def test_a_kept_cast_may_come_after_jev_routed_the_line(self):
        _, line = self.line("ok watch this everybody, look at me, fire ball")
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE lines SET jev_class = 'action_oriented'")   # routed first, as a long line is
        conn.commit()
        conn.close()
        status, doc = self.jev(line["id"], keep=True)
        self.assertEqual((status, doc["status"], doc["line"]), (202, "fired", "kept"))
        self.assertEqual(self.jev(line["id"])[0], 202)   # asked again: the first, whatever it asks

    def test_keep_is_true_or_false(self):
        _, line = self.line("Affordable.")
        status, doc = self.jev(line["id"], keep="yes")
        self.assertEqual(status, 400)
        self.assertTrue(doc["error"].startswith("keep: "))
        self.assertEqual(self.jev(line["id"], keep=False)[1]["line"], "dropped")

    def test_asked_again_answers_the_first(self):
        _, line = self.line("Affordable.")
        first = self.jev(line["id"])
        self.clock.advance(10)
        self.assertEqual(self.jev(line["id"]), first)
        self.assertEqual(self.rows("SELECT count(*) FROM casts"), [(1,)])

    def test_a_line_dropped_meanwhile_casts_nothing(self):
        _, spotted = self.cast(start=-2.0, length=0.6)
        _, line = self.line("fire ball", start=-2.2, length=1.0)  # dropped by the drop rule
        self.assertEqual(self.jev(line["id"]), (200, {"line": "dropped", "cast_id": spotted["id"]}))
        self.assertEqual(self.rows("SELECT count(*) FROM casts"), [(1,)])

    def test_a_judged_line_is_409_and_kept(self):
        _, line = self.line("Affordable.")
        conn = sqlite3.connect(self.db_path)  # as the Jev filter writes its verdict
        conn.execute("UPDATE lines SET jev_class = 'not_relevant'")
        conn.commit()
        conn.close()
        status, doc = self.jev(line["id"])
        self.assertEqual((status, doc["line"]), (409, "judged"))
        self.assertEqual(self.lines(), [(line["id"], None, None)])
        self.assertEqual(self.rows("SELECT count(*) FROM casts"), [(0,)])

    def test_404_400_and_nothing_cast(self):
        _, line = self.line("Affordable.")
        self.assertEqual(self.jev("01NOSUCHLINE")[0], 404)
        status, doc = self.jev(line["id"], "water bolt")
        self.assertEqual(status, 404)
        self.assertIn("water bolt", doc["error"])
        chat = self.db.insert_chat(timeutil.format_time(self.clock.now), "Steve", "fire ball")
        self.assertEqual(self.jev(chat)[0], 400)
        for body in ({}, {"line_id": "", "phrase": "fire ball"}, {"line_id": line["id"], "phrase": " "}):
            self.assertEqual(self.request("POST", "/v1/jev/casts", body)[0], 400, body)
        self.assertEqual(self.lines(), [(line["id"], None, None)])
        self.assertEqual(self.rows("SELECT count(*) FROM casts"), [(0,)])

    def test_only_from_the_box_itself_with_the_token(self):
        _, line = self.line("Affordable.")
        self.assertEqual(self.jev(line["id"], token="wrong"), (401, {"error": "unauthorized"}))
        with mock.patch.object(api, "LOOPBACK", ()):
            self.assertEqual(self.jev(line["id"]), (403, {"error": "only from the box itself"}))
        self.assertEqual(self.rows("SELECT count(*) FROM casts"), [(0,)])


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
        cfg = config.load({})
        self.assertEqual((cfg.cast_slack_s, cfg.cast_wait_s, cfg.incant_allowance, cfg.incant_layer2,
                          cfg.incant_layer2_max_words, cfg.jev_cast_max_age_s),
                         (0.3, 1.5, 0.25, True, 6, 2.0))
        self.assertEqual((cfg.engine_socket, cfg.engine_export, cfg.engine_cast_wait_s),
                         ("/opt/vfg/engine/engine.sock", "/opt/vfg/engine/incantations.json", 1.0))
        self.assertEqual(config.load({"VFG_INCANT_LAYER2_MAX_WORDS": "0"}).incant_layer2_max_words, 0)
        for bad in ("6.5", "-1", "six"):
            with self.assertRaises(SystemExit):
                config.load({"VFG_INCANT_LAYER2_MAX_WORDS": bad})
        self.assertEqual(config.load({"VFG_JEV_CAST_MAX_AGE_S": "3.5"}).jev_cast_max_age_s, 3.5)
        cfg = config.load({"VFG_BASE": "/b", "VFG_INCANT_LAYER2": "off", "VFG_ENGINE_CAST_WAIT_S": "0.4"})
        self.assertEqual((cfg.engine_socket, cfg.engine_export, cfg.incant_layer2, cfg.engine_cast_wait_s),
                         ("/b/engine/engine.sock", "/b/engine/incantations.json", False, 0.4))
        cfg = config.load({"VFG_ENGINE_SOCKET": "/s.sock", "VFG_ENGINE_EXPORT": "/e.json"})
        self.assertEqual((cfg.engine_socket, cfg.engine_export), ("/s.sock", "/e.json"))
        for bad in ({"VFG_CAST_SLACK_S": "soon"}, {"VFG_ENGINE_CAST_WAIT_S": "soon"}):
            with self.assertRaises(SystemExit):
                config.load(bad)
        casts = castsmod.Casts.from_config(cfg, None, vfg_feed)
        self.assertEqual((casts.engine.socket_path, casts.engine.export_path, casts.engine_wait_s),
                         ("/s.sock", "/e.json", 1.0))
        self.assertIsInstance(casts.engine, enginemod.Engine)


if __name__ == "__main__":
    unittest.main()
