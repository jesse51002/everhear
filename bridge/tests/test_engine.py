"""The game engine's bindings, on a fake engine socket (the game engine spec, "Incantations"): a
cast of an engine-bound phrase from the spotter, Layer 2 and the Jev filter goes to the engine, not
to a copy; the engine's statuses become the cast answers the mod knows; the engine down or late
answers in words; engine casts skip the bridge's cooldowns and keep its duplicate check; the merged
list and its version. The kit is the repo's scripts/_lib, the clock a FakeClock."""

import json
import os
import socket
import socketserver
import threading
import time
import unittest

import vfg_feed

from vfg_bridge import casts as castsmod, config, engine as enginemod

from test_casts import KIT, CastCase, entry


def binding(player, phrase, ability="fireball", **more):
    e = {"id": "%s.%s" % (player.lower(), phrase.replace(" ", "_")), "player": player, "phrase": phrase,
         "aliases": [], "everyday_aliases": [], "ability": ability, "cooldown_s": 5.0}
    e.update(more)
    return e


class FakeEngine:
    """The engine's control socket: one JSON line in, ``answer(request)``'s reply out. ``answer``
    may return None to close without a reply, or sleep first. Every request is kept."""

    def __init__(self, path):
        self.path = path
        self.requests = []
        self.answer = lambda req: {"ok": True, "status": "fired"}
        fake = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                req = json.loads(self.rfile.readline())
                fake.requests.append(req)
                reply = fake.answer(req)
                if reply is not None:
                    try:
                        self.wfile.write((json.dumps(reply) + "\n").encode())
                    except OSError:  # the bridge gave up waiting: as the engine, carry on
                        pass

        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.server = socketserver.ThreadingUnixStreamServer(path, Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    def casts(self):
        return [r for r in self.requests if r["op"] == "cast"]


class EngineCase(CastCase):
    """Today's store as CastCase has it (Steve: fire ball, ice wall; Alex: rock slide; @a: light
    up), and an engine that binds Steve's "fire ball" to the ability fireball."""

    def setUp(self):
        super().setUp()
        self.engine_dir = os.path.join(self.base, "engine")
        self.export_path = os.path.join(self.engine_dir, "incantations.json")
        self.fake = FakeEngine(os.path.join(self.engine_dir, "engine.sock"))
        self.addCleanup(self.fake.close)
        self.casts.engine = enginemod.Engine(self.fake.path, self.export_path)
        self.casts.engine_wait_s = 0.5
        self.write_export(binding("Steve", "fire ball"))

    def write_export(self, *entries, version=1):
        os.makedirs(self.engine_dir, exist_ok=True)
        tmp = self.export_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"version": version, "incantations": list(entries)}, f)
        os.replace(tmp, self.export_path)

    def copies(self):
        """The copies started: each has a meta file in run/."""
        try:
            return sorted(n[:-5] for n in os.listdir(self.run_dir) if n.endswith(".json"))
        except FileNotFoundError:
            return []

    def cast_rows(self):
        return self.rows("SELECT layer, incantation_id, action, copy, status, error FROM casts ORDER BY at, id")


class TestEngineCast(EngineCase):
    def test_a_spotter_cast_goes_to_the_engine_not_a_copy(self):
        status, doc = self.cast("fire ball")
        self.assertEqual((status, doc["status"]), (202, "fired"))
        self.assertNotIn("copy", doc)
        [req] = self.fake.casts()
        self.assertEqual(req["args"], {"player": "Steve", "phrase": "fire ball", "cast_id": doc["id"]})
        self.assertLessEqual(req["timeout_s"], 0.5)
        self.assertEqual(self.cast_rows(), [("spotter", "engine:steve.fire_ball", "fireball", None, "fired", None)])
        self.assertEqual(self.copies(), [])  # the store's "fire ball" (zap) is hidden
        self.assertTrue(self.events(1))
        self.assertEqual(self.rows("SELECT kind, text FROM events"),
                         [("cast", 'Steve cast "fire ball": fireball, fired in the engine')])

    def test_an_alias_casts_the_bindings_own_phrase(self):
        self.write_export(binding("Steve", "fire ball", aliases=["fireball", "fire bowl"]), version=2)
        _, doc = self.cast("fire bowl")
        self.assertEqual(doc["status"], "fired")
        self.assertEqual(self.fake.casts()[0]["args"]["phrase"], "fire ball")

    def test_the_hidden_store_entry_is_not_reached_by_its_alias(self):
        self.write_store(entry("Steve", "fire ball", aliases=["fireball"]), entry("Steve", "ice wall"))
        self.assertEqual(self.cast("fireball")[0], 404)
        self.assertEqual((self.fake.casts(), self.copies()), ([], []))

    def test_the_stores_other_entries_work_as_ever(self):
        _, doc = self.cast("ice wall")
        self.assertEqual((doc["status"], doc["copy"]), ("fired", "zap"))
        self.assertTrue(self.ran("zap"))
        self.clock.advance(5)
        self.assertEqual(self.cast("light up", player="Notch")[1]["status"], "fired")  # @a
        self.assertEqual(self.fake.casts(), [])

    def test_another_players_binding_is_not_theirs(self):
        self.write_export(binding("Alex", "frost nova", ability="frost"), version=2)
        self.assertEqual(self.cast("frost nova")[0], 404)
        self.assertEqual(self.cast("frost nova", player="alex")[1]["status"], "fired")  # names: any case
        self.assertEqual(self.fake.casts()[0]["args"]["player"], "alex")

    def test_a_repeated_client_cast_id_answers_the_first(self):
        started, ended = self.window()
        body = {"client_cast_id": "c-1", "player_name": "Steve", "phrase": "fire ball",
                "started_at": started, "ended_at": ended}
        first = self.request("POST", "/v1/casts", body)
        self.clock.advance(10)
        self.assertEqual(self.request("POST", "/v1/casts", body), first)
        self.assertEqual(len(self.fake.casts()), 1)

    def test_held_past_the_wait_is_stale_and_never_sent(self):
        late = time.monotonic() + 1.6
        self.casts.monotonic = lambda: late
        self.assertEqual(self.cast()[1]["status"], "stale")
        self.assertEqual(self.fake.casts(), [])

    def test_the_wait_is_never_past_the_bridges_own(self):
        now = time.monotonic()
        self.casts.monotonic = lambda: now + 1.3  # 0.2 s left of the 1.5 s
        self.cast()
        self.assertAlmostEqual(self.fake.casts()[0]["timeout_s"], 0.2, delta=0.05)


class TestEngineStatuses(EngineCase):
    def answers(self, reply):
        self.fake.answer = lambda req: reply
        status, doc = self.cast()
        self.assertEqual(status, 202)
        return doc

    def test_charging_is_fired_and_announced_so(self):
        self.assertEqual(self.answers({"ok": True, "status": "charging"})["status"], "fired")
        self.assertTrue(self.events(1))
        self.assertEqual(self.rows("SELECT text FROM events"),
                         [('Steve cast "fire ball": fireball, charging in the engine',)])

    def test_cooldown_with_the_time_left(self):
        doc = self.answers({"ok": True, "status": "cooldown", "remaining_s": 2.34})
        self.assertEqual((doc["status"], doc["error"]), ("cooldown", "2.3 s left of its cooldown"))
        time.sleep(0.2)
        self.assertEqual(self.rows("SELECT count(*) FROM events"), [(0,)])  # as today: not announced

    def test_not_granted_unknown_and_failed_are_failed_in_words(self):
        for reply, words in (({"ok": True, "status": "not_granted"}, "Steve has not been granted fireball"),
                             ({"ok": True, "status": "not_granted", "error": "Steve has no fireball"},
                              "Steve has no fireball"),
                             ({"ok": True, "status": "unknown"}, "no such incantation for Steve"),
                             ({"ok": True, "status": "failed", "error": "no player Steve"}, "no player Steve"),
                             ({"ok": False, "error": "bad args"}, "the engine refused the cast: bad args"),
                             ({"ok": True, "status": "sideways"}, "answered the cast with 'sideways'")):
            self.clock.advance(10)
            doc = self.answers(reply)
            self.assertEqual(doc["status"], "failed", reply)
            self.assertIn(words, doc["error"], reply)
        self.assertTrue(self.events(6))
        self.assertTrue(all(t.startswith('Steve cast "fire ball": failed — ')
                            for t, in self.rows("SELECT text FROM events")))

    def test_the_engines_own_timeout_and_stop(self):
        doc = self.answers({"ok": False, "error": "x", "reason": "timeout"})
        self.assertEqual(doc["status"], "failed")
        self.assertIn("did not answer the cast within 0.5 s", doc["error"])
        self.clock.advance(10)
        doc = self.answers({"ok": False, "error": "x", "reason": "stopping"})
        self.assertIn("is stopping", doc["error"])


class TestEngineDown(EngineCase):
    def test_no_socket_answers_in_words_and_is_announced(self):
        self.fake.close()
        status, doc = self.cast()
        self.assertEqual((status, doc["status"]), (202, "failed"))
        self.assertIn("the game engine is not running, so 'fire ball' was not cast", doc["error"])
        self.assertIn("no engine socket at", doc["error"])
        self.assertEqual(self.cast_rows()[0][4:], ("failed", doc["error"]))
        self.assertTrue(self.events(1))
        self.assertEqual(self.copies(), [])  # never the hidden store entry instead

    def test_a_socket_nobody_listens_on(self):
        self.fake.close()
        dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        dead.bind(self.fake.path)  # bound, never listening: refused
        self.addCleanup(dead.close)
        self.assertIn("nothing answers on", self.cast()[1]["error"])

    def test_closed_without_a_reply(self):
        self.fake.answer = lambda req: None
        self.assertIn("without a reply", self.cast()[1]["error"])

    def test_late_is_failed_and_does_not_hold_past_the_wait(self):
        self.fake.answer = lambda req: time.sleep(1.5) or {"ok": True, "status": "fired"}
        start = time.monotonic()
        doc = self.cast()[1]
        self.assertLess(time.monotonic() - start, 1.2)
        self.assertEqual(doc["status"], "failed")
        self.assertIn("did not answer the cast within 0.5 s", doc["error"])


class TestEngineChecks(EngineCase):
    def test_no_cooldown_and_no_gap_from_the_bridge(self):
        self.assertEqual(self.cast(start=-2.0)[1]["status"], "fired")
        self.clock.advance(0.3)
        self.assertEqual(self.cast(start=-1.0, length=0.3)[1]["status"], "fired")  # its own words
        self.assertEqual(len(self.fake.casts()), 2)  # the engine decides what a second cast does

    def test_engine_casts_and_store_casts_do_not_hold_each_other(self):
        self.assertEqual(self.cast("ice wall", start=-2.0)[1]["status"], "fired")
        self.clock.advance(0.2)
        self.assertEqual(self.cast("fire ball", start=-1.0, length=0.3)[1]["status"], "fired")
        self.deploy("frost")
        self.write_store(entry("Steve", "ice wall"), entry("Steve", "rock wall", script="frost", action="frost"))
        self.clock.advance(0.9)  # 1.1 s after ice wall, 0.9 s after the engine's cast, which holds nothing
        self.assertEqual(self.cast("rock wall", start=-0.6, length=0.3)[1]["status"], "fired")

    def test_one_saying_heard_twice_is_one_cast(self):
        self.assertEqual(self.cast(start=-2.0, length=0.6)[1]["status"], "fired")
        self.clock.advance(1.5)
        self.line("fire ball", start=-4.0, length=1.5)  # Layer 2 hears the same words
        self.assertEqual([r[0::4] for r in self.cast_rows()], [("spotter", "fired"), ("transcript", "duplicate")])
        self.assertEqual(len(self.fake.casts()), 1)
        self.assertEqual(self.rows("SELECT dropped FROM lines"), [("cast",)])

    def test_a_cast_the_engine_refused_is_still_one_saying(self):
        self.fake.answer = lambda req: {"ok": True, "status": "cooldown", "remaining_s": 1}
        self.assertEqual(self.cast(start=-2.0, length=0.6)[1]["status"], "cooldown")
        self.fake.answer = lambda req: {"ok": True, "status": "fired"}
        self.clock.advance(1.5)
        self.line("fire ball", start=-4.0, length=1.5)
        self.assertEqual(self.cast_rows()[1][4], "duplicate")  # the spotter decided that utterance
        self.assertEqual(len(self.fake.casts()), 1)


class TestEngineOutsideTheLock(EngineCase):
    """The engine's round trip is outside the cast lock: a slow engine holds no other cast, and a
    duplicate or a repeated client_cast_id arriving while it is asked is still caught."""

    def setUp(self):
        super().setUp()
        self.casts.engine_wait_s = 1.0
        self.write_export(binding("Steve", "fire ball"), binding("Alex", "frost nova", ability="frost"))
        self.received = threading.Event()   # the engine has a cast
        self.release = threading.Event()    # and answers it now

        def gated(req):  # Steve's casts wait for `release`; anyone else's are answered at once
            if req["args"]["player"] == "Steve":
                self.received.set()
                self.release.wait(5)
            return {"ok": True, "status": "fired"}
        self.gated = gated
        self.addCleanup(self.release.set)

    def in_thread(self, fn):
        out = []
        t = threading.Thread(target=lambda: out.append(fn()), daemon=True)
        t.start()
        self.addCleanup(t.join, 5)
        return t, out

    def test_two_players_casts_do_not_wait_on_each_other(self):
        self.fake.answer = lambda req: time.sleep(0.4) or {"ok": True, "status": "fired"}
        start = time.monotonic()
        a, steve = self.in_thread(lambda: self.cast("fire ball", player="Steve"))
        b, alex = self.in_thread(lambda: self.cast("frost nova", player="Alex"))
        a.join(5)
        b.join(5)
        self.assertLess(time.monotonic() - start, 0.75)   # one after the other would be 0.8 s at least
        self.assertEqual((steve[0][1]["status"], alex[0][1]["status"]), ("fired", "fired"))
        self.assertEqual(sorted(r["args"]["player"] for r in self.fake.casts()), ["Alex", "Steve"])

    def test_an_action_cast_is_not_held_by_an_engine_casts_wait(self):
        self.fake.answer = self.gated
        self.write_export(binding("Steve", "fire ball"), binding("Alex", "frost nova", ability="frost"),
                          binding("Steve", "frost nova", ability="frost"), version=2)
        t, steve = self.in_thread(lambda: self.cast("frost nova", player="Steve"))
        self.assertTrue(self.received.wait(5))
        start = time.monotonic()
        _, doc = self.cast("ice wall")   # the store's: a copy, while his engine cast waits
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual((doc["status"], doc["copy"]), ("fired", "zap"))
        self.assertTrue(t.is_alive())
        frost = "SELECT status FROM casts WHERE incantation_id = 'engine:steve.frost_nova'"
        self.assertEqual(self.rows(frost), [("pending",)])
        self.release.set()
        t.join(5)
        self.assertEqual(steve[0][1]["status"], "fired")
        self.assertEqual(self.rows(frost), [("fired",)])

    def test_a_duplicate_arriving_during_the_wait_is_caught(self):
        self.fake.answer = self.gated
        t, first = self.in_thread(lambda: self.cast(start=-2.0, length=0.6))
        self.assertTrue(self.received.wait(5))
        _, again = self.cast(start=-1.9, length=0.4)      # the same saying, heard by Vosk too
        self.assertEqual(again["status"], "duplicate")
        self.assertIn("pending", again["error"])
        self.line("fire ball", start=-2.2, length=1.0)     # and its line: Layer 2 casts nothing more
        self.release.set()
        t.join(5)
        self.assertEqual(first[0][1]["status"], "fired")
        self.assertEqual(len(self.fake.casts()), 1)
        self.assertEqual([r[0::4] for r in self.cast_rows()],
                         [("spotter", "fired"), ("spotter", "duplicate"), ("transcript", "duplicate")])

    def test_a_repeated_client_cast_id_during_the_wait_answers_what_the_first_did(self):
        self.fake.answer = self.gated
        started, ended = self.window()
        body = {"client_cast_id": "c-1", "player_name": "Steve", "phrase": "fire ball",
                "started_at": started, "ended_at": ended}
        t, first = self.in_thread(lambda: self.request("POST", "/v1/casts", body))
        self.assertTrue(self.received.wait(5))
        r, again = self.in_thread(lambda: self.request("POST", "/v1/casts", body))   # the mod's retry
        time.sleep(0.2)
        self.assertTrue(r.is_alive())                   # it waits for the first's answer ...
        _, other = self.cast("frost nova", player="Alex")   # ... outside the lock: others still cast
        self.assertEqual(other["status"], "fired")
        self.release.set()
        t.join(5)
        r.join(5)
        self.assertEqual(again[0], first[0])
        self.assertEqual(first[0][1]["status"], "fired")
        self.assertEqual([r["args"]["player"] for r in self.fake.casts()], ["Steve", "Alex"])
        self.assertEqual(self.rows("SELECT count(*) FROM casts WHERE client_cast_id = 'c-1'"), [(1,)])

    def test_a_jev_cast_and_its_repeat_during_the_wait(self):
        self.casts.layer2 = False
        _, line = self.line("Affordable.", start=-2.5, length=1.0)
        self.fake.answer = self.gated
        body = {"line_id": line["id"], "phrase": "fire ball"}
        t, first = self.in_thread(lambda: self.request("POST", "/v1/jev/casts", body))
        self.assertTrue(self.received.wait(5))
        r, again = self.in_thread(lambda: self.request("POST", "/v1/jev/casts", body))
        time.sleep(0.1)
        self.release.set()
        t.join(5)
        r.join(5)
        self.assertEqual(first[0][0], 202)
        self.assertEqual((first[0][1]["status"], first[0][1]["line"]), ("fired", "dropped"))
        self.assertEqual(again[0][1]["status"], "fired")
        self.assertEqual(len(self.fake.casts()), 1)
        self.assertEqual(self.rows("SELECT dropped, cast_id FROM lines"), [("cast", first[0][1]["id"])])

    def test_held_past_the_wait_between_the_lock_and_the_engine_is_stale(self):
        base = time.monotonic()
        times = iter([base + 0.1, base + 3.0])   # _try's look, then _ask's: 1.5 s gone between them
        self.casts.monotonic = lambda: next(times, base + 3.0)
        _, doc = self.cast()
        self.assertEqual(doc["status"], "stale")
        self.assertIn("before it could start", doc["error"])
        self.assertEqual(self.fake.casts(), [])
        self.assertEqual(self.cast_rows()[0][4], "stale")

    def test_a_pending_row_a_stopped_bridge_left(self):
        started, ended = self.window()
        self.db.insert_cast({"id": "C-old", "at": started, "started_at": started, "ended_at": ended,
                             "player_name": "Steve", "phrase": "fire ball", "layer": "spotter",
                             "incantation_id": "engine:steve.fire_ball", "status": "pending",
                             "client_cast_id": "c-old"})
        status, doc = self.request("POST", "/v1/casts", {"client_cast_id": "c-old", "player_name": "Steve",
                                                        "phrase": "fire ball", "started_at": started,
                                                        "ended_at": ended})
        self.assertEqual(status, 503)                  # nothing in this bridge will finish it
        self.assertIn("still waiting on the game engine", doc["error"])
        self.assertEqual(self.db.fail_pending(), 1)    # what a start does
        self.assertEqual(self.rows("SELECT status, error FROM casts WHERE id = 'C-old'"),
                         [("failed", "the bridge stopped before the game engine answered")])


class TestEngineLayer2AndJev(EngineCase):
    def test_layer_2_casts_through_the_engine(self):
        _, doc = self.line("fire bowl")
        self.assertEqual(self.cast_rows(), [("transcript", "engine:steve.fire_ball", "fireball", None, "fired", None)])
        self.assertEqual(self.rows("SELECT client_cast_id FROM casts"),
                         [("line:%s:engine:steve.fire_ball" % self.rows("SELECT client_line_id FROM lines")[0][0],)])
        self.assertEqual(self.fake.casts()[0]["args"]["phrase"], "fire ball")
        self.assertEqual(self.rows("SELECT dropped, cast_id FROM lines"),
                         [("cast", self.rows("SELECT id FROM casts")[0][0])])
        self.assertEqual(self.copies(), [])

    def test_the_drop_rule_after_a_spotters_engine_cast(self):
        self.casts.layer2 = False
        _, spotted = self.cast(start=-2.0, length=0.6)
        self.line("fire ball now", start=-2.2, length=2.0)
        self.assertEqual(self.rows("SELECT dropped, cast_id FROM lines"), [("cast", spotted["id"])])

    def test_a_jev_cast_goes_to_the_engine(self):
        self.casts.layer2 = False
        _, line = self.line("Affordable.", start=-2.5, length=1.0)
        status, doc = self.request("POST", "/v1/jev/casts", {"line_id": line["id"], "phrase": "fire ball"})
        self.assertEqual((status, doc["status"], doc["line"]), (202, "fired", "dropped"))
        self.assertEqual(self.cast_rows(), [("jev", "engine:steve.fire_ball", "fireball", None, "fired", None)])
        self.assertEqual(self.fake.casts()[0]["args"]["cast_id"], doc["id"])
        self.assertTrue(self.events(1))


class TestMergedList(EngineCase):
    def get(self, query="player=Steve"):
        return self.request("GET", "/v1/incantations?" + query)

    def test_engine_bindings_first_hiding_the_same_phrase(self):
        self.write_export(binding("Steve", "fire ball", aliases=["fireball", "fire bowl", "fire bull"],
                                  everyday_aliases=["fire bull"], cooldown_s=5),
                          binding("Steve", "Light Up", ability="glow", cooldown_s=0),
                          binding("Alex", "frost nova", ability="frost"), version=4)
        status, doc = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(doc["incantations"], [
            {"phrase": "fire ball", "aliases": ["fireball", "fire bowl"], "cooldown_s": 5.0},
            {"phrase": "Light Up", "aliases": [], "cooldown_s": 0.0},
            {"phrase": "ice wall", "aliases": [], "cooldown_s": 3.0},   # the store's, as today
        ])  # the store's "fire ball" and @a "light up" are hidden
        self.assertEqual([e["phrase"] for e in self.get("player=Alex")[1]["incantations"]],
                         ["frost nova", "rock slide", "light up"])
        self.assertEqual([e["phrase"] for e in self.get("player=Notch")[1]["incantations"]], ["light up"])

    def test_the_version_changes_with_either_store_and_only_then(self):
        first = self.get()[1]["version"]
        self.assertEqual(self.get()[1]["version"], first)
        self.assertEqual(first, castsmod.mixed_version(1, 1, self.get()[1]["incantations"]))
        self.write_store(entry("Steve", "ice wall"), version=2)
        second = self.get()[1]["version"]
        self.write_export(binding("Steve", "fire ball"), version=2)  # the same list, a new version
        third = self.get()[1]["version"]
        self.assertEqual(len({first, second, third}), 3)
        self.assertTrue(all(0 <= v < 10 ** 15 for v in (first, second, third)))  # the mod's `after`
        self.assertEqual(self.get("player=Steve&after=%d&wait=0.1" % third), (304, None))
        self.assertEqual(self.get("player=Steve&after=%d" % first)[0], 200)  # an older one: at once

    def test_without_engine_bindings_the_version_is_the_stores(self):
        os.remove(self.export_path)
        self.assertEqual(self.get()[1]["version"], 1)

    def test_304_and_the_long_poll_wakes_on_the_engines_export(self):
        version = self.get()[1]["version"]
        self.assertEqual(self.get("player=Steve&after=%d&wait=0.1" % version), (304, None))
        got = []
        t = threading.Thread(target=lambda: got.append(self.get("player=Steve&after=%d&wait=10" % version)))
        t.start()
        time.sleep(0.2)
        self.write_export(binding("Steve", "fire ball"), binding("Steve", "dash", ability="dash"), version=2)
        t.join(5)
        self.assertEqual(got[0][0], 200)
        self.assertNotEqual(got[0][1]["version"], version)
        self.assertEqual(got[0][1]["incantations"][1]["phrase"], "dash")

    def test_a_bad_export_leaves_the_engines_bindings_out(self):
        with open(self.export_path, "w") as f:
            f.write("{not json")
        self.assertEqual([e["phrase"] for e in self.get()[1]["incantations"]], ["fire ball", "ice wall", "light up"])
        self.assertEqual(self.cast()[1]["copy"], "zap")  # the store's fire ball, as before the engine
        self.write_export(binding("Steve", "fire ball"), {"player": "Steve", "phrase": 7}, "x",
                          binding("Steve", "dash", ability="dash", aliases="dsh"), version=3)
        self.assertEqual([e["phrase"] for e in self.casts.engine.bindings()["incantations"]], ["fire ball"])


class TestEngineClient(unittest.TestCase):
    def setUp(self):
        import tempfile
        import shutil
        self.tmp = tempfile.mkdtemp(prefix="vfg-engine-client-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "engine.sock")

    def test_a_request_and_its_reply(self):
        fake = FakeEngine(self.path)
        self.addCleanup(fake.close)
        fake.answer = lambda req: {"ok": True, "tick": 7}
        client = enginemod.Engine(self.path, os.path.join(self.tmp, "none.json"))
        self.assertEqual(client.request("status", {}, 1.0), {"ok": True, "tick": 7})
        self.assertEqual(fake.requests, [{"op": "status", "args": {}, "timeout_s": 1.0}])
        fake.answer = lambda req: ["not", "a", "reply"]
        with self.assertRaises(enginemod.Down):
            client.request("status", {}, 1.0)

    def test_no_socket_and_late(self):
        client = enginemod.Engine(self.path, os.path.join(self.tmp, "none.json"))
        with self.assertRaises(enginemod.Down):
            client.cast("Steve", "fire ball", "c1", 0.2)
        fake = FakeEngine(self.path)
        self.addCleanup(fake.close)
        fake.answer = lambda req: time.sleep(1) or {"ok": True}
        with self.assertRaises(enginemod.Late):
            client.cast("Steve", "fire ball", "c1", 0.1)
        self.assertEqual(client.bindings(), {"version": 0, "incantations": []})


class TestEngineConfig(unittest.TestCase):
    def test_defaults_and_settings(self):
        cfg = config.load({})
        self.assertEqual((cfg.engine_socket, cfg.engine_export, cfg.engine_cast_wait_s),
                         ("/opt/vfg/engine/engine.sock", "/opt/vfg/engine/incantations.json", 1.0))
        cfg = config.load({"VFG_BASE": "/b", "VFG_ENGINE_CAST_WAIT_S": "0.4"})
        self.assertEqual((cfg.engine_socket, cfg.engine_export, cfg.engine_cast_wait_s),
                         ("/b/engine/engine.sock", "/b/engine/incantations.json", 0.4))
        cfg = config.load({"VFG_ENGINE_SOCKET": "/s.sock", "VFG_ENGINE_EXPORT": "/e.json"})
        self.assertEqual((cfg.engine_socket, cfg.engine_export), ("/s.sock", "/e.json"))
        with self.assertRaises(SystemExit):
            config.load({"VFG_ENGINE_CAST_WAIT_S": "soon"})
        casts = castsmod.Casts.from_config(cfg, None, KIT, vfg_feed)
        self.assertEqual((casts.engine.socket_path, casts.engine.export_path, casts.engine_wait_s),
                         ("/s.sock", "/e.json", 1.0))


if __name__ == "__main__":
    unittest.main()
