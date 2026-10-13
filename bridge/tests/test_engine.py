"""The game engine as the bridge reaches it (engine.py, and casts.py's side of it), on a fake engine
socket: the engine's statuses as the cast answers the mod knows; the engine down or late answered in
words; the round trip outside the cast lock, with the duplicate check and the idempotent repeat still
holding; a stopped bridge's pending rows; the export's entries; the client."""

import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest

from vfg_bridge import engine as enginemod

from fake_engine import CastCase, FakeEngine, binding


class TestEngineStatuses(CastCase):
    def answers(self, reply):
        self.fake.answer = lambda req: reply
        status, doc = self.cast()
        self.assertEqual(status, 202)
        return doc

    def test_charging_is_fired_and_announced_so(self):
        self.assertEqual(self.answers({"ok": True, "status": "charging"})["status"], "fired")
        self.assertTrue(self.events(1))
        self.assertEqual(self.rows("SELECT text FROM events"),
                         [('Steve cast "fire ball": zap, charging in the engine',)])

    def test_not_granted_unknown_and_failed_are_failed_in_words(self):
        for reply, words in (({"ok": True, "status": "not_granted"}, "Steve has not been granted zap"),
                             ({"ok": True, "status": "not_granted", "error": "Steve has no zap"}, "Steve has no zap"),
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


class TestEngineDown(CastCase):
    def test_no_socket_answers_in_words_and_is_announced(self):
        self.fake.close()
        status, doc = self.cast()
        self.assertEqual((status, doc["status"]), (202, "failed"))
        self.assertIn("the game engine is not running, so 'fire ball' was not cast", doc["error"])
        self.assertIn("no engine socket at", doc["error"])
        self.assertEqual(self.cast_rows()[0][4:], ("failed", doc["error"]))
        self.assertTrue(self.events(1))

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


class TestOutsideTheLock(CastCase):
    """The engine's round trip is outside the cast lock: a slow engine holds no other cast, and a
    duplicate or a repeated client_cast_id arriving while it is asked is still caught."""

    def setUp(self):
        super().setUp()
        self.casts.engine_wait_s = 1.0
        self.received = threading.Event()   # the engine has a cast of Steve's
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
        b, alex = self.in_thread(lambda: self.cast("rock slide", player="Alex"))
        a.join(5)
        b.join(5)
        self.assertLess(time.monotonic() - start, 0.75)   # one after the other would be 0.8 s at least
        self.assertEqual((steve[0][1]["status"], alex[0][1]["status"]), ("fired", "fired"))
        self.assertEqual(sorted(r["args"]["player"] for r in self.fake.casts()), ["Alex", "Steve"])

    def test_a_pending_row_while_the_engine_is_asked(self):
        self.fake.answer = self.gated
        t, steve = self.in_thread(lambda: self.cast())
        self.assertTrue(self.received.wait(5))
        self.assertEqual(self.rows("SELECT status FROM casts"), [("pending",)])
        start = time.monotonic()
        self.assertEqual(self.cast("rock slide", player="Alex")[1]["status"], "fired")   # not held
        self.assertLess(time.monotonic() - start, 0.5)
        self.release.set()
        t.join(5)
        self.assertEqual(steve[0][1]["status"], "fired")
        self.assertEqual(self.rows("SELECT status FROM casts WHERE player_name = 'Steve'"), [("fired",)])

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
        _, other = self.cast("rock slide", player="Alex")   # ... outside the lock: others still cast
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


class TestExport(CastCase):
    def test_a_bad_export_has_none_and_bad_entries_are_left_out(self):
        with open(self.export_path, "w") as f:
            f.write("{not json")
        self.assertEqual(self.cast()[0], 404)
        self.write_export(binding("Steve", "fire ball"), {"player": "Steve", "phrase": 7}, "x",
                          binding("Steve", "dash", aliases="dsh"), binding("Steve", "both", scenario="meteor"),
                          binding("Steve", "neither", ability=None), binding("Steve", "argh", ability=None,
                                                                             scenario="m", args=[1]),
                          version=3)
        self.assertEqual([e["phrase"] for e in self.casts.engine.bindings()["incantations"]], ["fire ball"])
        self.write_export(binding("Steve", "fire ball"), version=None)        # no version: none
        self.assertEqual(self.casts.engine.bindings(), {"version": 0, "incantations": []})

    def test_an_entry_without_an_id_or_cooldown(self):
        e = binding("Steve", "Fire Ball")
        del e["id"], e["cooldown_s"]
        self.write_export(e, version=2)
        [got] = self.casts.engine.bindings()["incantations"]
        self.assertEqual((got["id"], got["cooldown_s"]), ("steve.fire_ball", 0.0))


class TestEngineClient(unittest.TestCase):
    def setUp(self):
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
        with open(os.path.join(self.tmp, "none.json"), "w") as f:
            json.dump({"version": 2, "incantations": [binding("Steve", "x")]}, f)
        self.assertEqual(client.for_player("STEVE")[0]["phrase"], "x")


if __name__ == "__main__":
    unittest.main()
