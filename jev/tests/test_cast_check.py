"""The cast check (casting.py), driven through `Filter.step()` with a fake clock, a fake Jev and a
fake bridge: a cast goes to the bridge and is never routed; cooldown and duplicate still drop the
line; talk, a low answer, a failure or a bridge that keeps the line go on to routing; no call for a
player without incantations, a typed line or a long one. Then the question, the state and RECENT,
the store reader, and the bridge client over HTTP. Nothing here sleeps."""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from support import T0, iso
from test_filter import Base, FakeJev

import casting
from filter import Filter
from jev_api import JevDown, JevRefused, Result
from prompt import state_json
from store import Store

CASTS_DDL = """CREATE TABLE casts (id TEXT PRIMARY KEY, at TEXT NOT NULL, started_at TEXT,
  ended_at TEXT, player_name TEXT NOT NULL, phrase TEXT NOT NULL, heard TEXT, layer TEXT NOT NULL,
  incantation_id TEXT, action TEXT, copy TEXT, status TEXT NOT NULL, error TEXT, line_id TEXT,
  client_cast_id TEXT UNIQUE)"""


def entry(player, phrase, action="hot_fireball", aliases=(), **more):
    e = {"id": "%s.%s" % (player.lower(), phrase.replace(" ", "_")), "player": player, "phrase": phrase,
         "aliases": list(aliases), "action": action, "script": action, "source": "made", "values": {},
         "cooldown_s": 3.0}
    e.update(more)
    return e


class CastJev(FakeJev):
    """FakeJev, and the cast question answered from `casts[text]`, a `(label, probability,
    confidence)` (probability None: the answer gives none); `not_a_cast` 0.95 for any other
    line. `raises` and `down` apply to it too."""

    def __init__(self):
        super().__init__()
        self.casts = {}

    def ask(self, state, questions, required="line_1"):
        if casting.QUESTION not in questions:
            return super().ask(state, questions, required)
        self.calls.append((state, questions))
        if self.down:
            raise JevDown(self.down)
        if self.raises:
            err = self.raises.pop(0)
            if err is not None:
                raise err
        text = questions[casting.QUESTION]["instructions"].split("“", 1)[1].rsplit("”", 1)[0]
        label, p, conf = self.casts.get(text, (casting.NOT_A_CAST, 0.95, 0.95))
        probs = {} if p is None else {casting.QUESTION: {label: p}}
        return Result({casting.QUESTION: (label, conf)}, len(state_json(state)) // 4, 12, probs)

    def kinds(self):
        """Each call so far: `cast` for a cast check, `route` for a routing call, `gather` else."""
        return ["cast" if casting.QUESTION in q else "route" if "d_what" in q else "gather" for _, q in self.calls]


class FakeBridge:
    """The bridge's cast path: drops the line as the real one does (unless `keeps`), answers `said`,
    or raises `fails`. Records every cast asked for."""

    def __init__(self, db):
        self.db = db
        self.asked = []
        self.said = "fired"
        self.keeps = False
        self.fails = None

    def cast(self, line_id, phrase):
        self.asked.append((line_id, phrase))
        if self.fails:
            raise casting.BridgeError(self.fails)
        if self.keeps:
            return casting.Outcome(False, "409 Jev judged line %s already" % line_id)
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE lines SET dropped = 'cast', cast_id = 'C1' WHERE id = ?", (line_id,))
        conn.commit()
        conn.close()
        return casting.Outcome(True, self.said)


class CastBase(Base):
    def setUp(self):
        super().setUp()
        self.conn.execute("ALTER TABLE lines ADD COLUMN dropped TEXT")
        self.conn.execute("ALTER TABLE lines ADD COLUMN cast_id TEXT")
        self.conn.execute(CASTS_DDL)
        self.jev = CastJev()
        self.bridge = FakeBridge(self.db)
        self.cfg.incantations = os.path.join(self.dir, "incantations.json")
        self.write_store(entry("Steve", "hot fireball", aliases=["hot fire ball", "hut fireball"]),
                         entry("Steve", "ice beam", action="ice_beam_cast"),
                         entry("@a", "juicy steak", action="command",
                               values={"commands": ["give @s minecraft:cooked_beef 64"]}))
        self.f = self.new_filter()

    def new_filter(self):
        store = Store(self.db)
        self.addCleanup(store.close)
        return Filter(store, self.jev, self.feed, self.clock, self.cfg, bridge=getattr(self, "bridge", None))

    def write_store(self, *entries):
        with open(self.cfg.incantations + ".tmp", "w") as f:
            json.dump({"version": 1, "incantations": list(entries)}, f)
        os.replace(self.cfg.incantations + ".tmp", self.cfg.incantations)

    def cast(self, text, label, p=0.92, confidence=None):
        """Jev's answer to the cast check on `text`: `label`, with probability `p` (None: none
        given) and `confidence` (`p` unless given)."""
        self.jev.casts[text] = (label, p, p if confidence is None else confidence)


class TestCasting(CastBase):
    def test_a_cast_goes_to_the_bridge_and_the_line_is_never_routed(self):
        line = self.lines.add(T0, "Steve", "voice", "Hot variable.", peak=-10)
        self.cast("Hot variable.", "cast_hot_fireball")
        with self.assertLogs("vfg-jev", "INFO") as logs:
            self.step()
            self.step(10)
        self.assertEqual(self.bridge.asked, [(line, "hot fireball")])
        self.assertEqual(self.jev.kinds(), ["cast"])
        r = self.row(line)
        self.assertEqual((r["dropped"], r["jev_class"], r["fed_at"]), ("cast", None, None))
        self.assertEqual(self.batches(), [])
        usage = self.lines.usage()
        self.assertEqual([(u["ref"], u["component"], u["output_tokens"]) for u in usage], [("cast:" + line, "jev", 0)])
        self.assertEqual(usage[0]["cost_usd"], usage[0]["input_tokens"] * 0.000000042)
        self.assertIn("cast-check %s Steve · hot fireball p 0.92 → fired · 12ms %dtok" % (line, usage[0]["input_tokens"]),
                      "\n".join(logs.output))

    def test_cooldown_and_duplicate_still_drop_the_line(self):
        for n, said in enumerate(("cooldown (0.4 s left of its 3 s cooldown)", "duplicate (it was cast in this "
                                                                               "window already)")):
            self.bridge.said = said
            line = self.lines.add(T0 + 10 * n, "Steve", "voice", "Ice.")
            self.cast("Ice.", "cast_ice_beam", 0.88)
            with self.assertLogs("vfg-jev", "INFO") as logs:
                self.step(10)
            self.assertEqual(self.row(line)["dropped"], "cast")
            self.assertIsNone(self.row(line)["jev_class"])
            self.assertIn("cast-check %s Steve · ice beam p 0.88 → %s · " % (line, said), "\n".join(logs.output))
        self.assertEqual(self.jev.kinds(), ["cast", "cast"])
        self.assertEqual(self.batches(), [])

    def test_talk_about_an_incantation_goes_on_to_routing(self):
        line = self.lines.add(T0, "Steve", "voice", "make hot fireball stronger")
        with self.assertLogs("vfg-jev", "INFO") as logs:
            self.step()
        self.assertEqual(self.jev.kinds(), ["cast", "route"])
        self.assertEqual(self.bridge.asked, [])
        self.assertEqual(self.row(line)["jev_class"], "action_oriented")
        self.assertEqual([u["ref"] for u in self.lines.usage()], ["cast:" + line, line])
        self.assertIn("cast-check %s Steve · no p 0.95 → routing · " % line, "\n".join(logs.output))
        self.assertEqual([e[2] for e in self.fed()], ["make hot fireball stronger"])

    def test_under_the_threshold_goes_on_to_routing_and_the_threshold_is_a_setting(self):
        a = self.lines.add(T0, "Steve", "voice", "Hester.")
        self.cast("Hester.", "cast_hot_fireball", 0.45)
        with self.assertLogs("vfg-jev", "INFO") as logs:
            self.step()
        self.assertEqual(self.bridge.asked, [])
        self.assertEqual(self.row(a)["jev_class"], "not_relevant")
        self.assertIn("· hot fireball p 0.45 → under 0.50, routing · ", "\n".join(logs.output))
        self.cfg.cast_min = 0.4
        b = self.lines.add(T0 + 1, "Steve", "voice", "Hester again.")
        self.cast("Hester again.", "cast_hot_fireball", 0.45)
        self.step(1)
        self.assertEqual(self.bridge.asked, [(b, "hot fireball")])

    def test_the_threshold_is_on_the_probability_not_the_confidence(self):
        a = self.lines.add(T0, "Steve", "voice", "Call fireball.")
        self.cast("Call fireball.", "cast_hot_fireball", p=0.63, confidence=0.45)   # as Jev answered it on 10-11
        b = self.lines.add(T0 + 1, "Steve", "voice", "Hester.")
        self.cast("Hester.", "cast_hot_fireball", p=0.45, confidence=0.9)
        with self.assertLogs("vfg-jev", "INFO") as logs:
            self.step()
        self.assertEqual(self.bridge.asked, [(a, "hot fireball")])
        self.assertEqual((self.row(a)["dropped"], self.row(b)["jev_class"]), ("cast", "not_relevant"))
        self.assertIn("cast-check %s Steve · hot fireball p 0.63 → fired" % a, "\n".join(logs.output))

    def test_an_answer_without_its_probability_goes_on_to_routing(self):
        line = self.lines.add(T0, "Steve", "voice", "Hot variable.")
        self.cast("Hot variable.", "cast_hot_fireball", p=None, confidence=0.99)
        with self.assertLogs("vfg-jev", "INFO") as logs:
            self.step()
        self.assertEqual(self.bridge.asked, [])
        self.assertEqual(self.row(line)["jev_class"], "not_relevant")
        self.assertIn("cast-check %s Steve · hot fireball p - → no probability, routing · " % line,
                      "\n".join(logs.output))

    def test_cast_lines_are_checked_before_any_line_is_routed(self):
        self.lines.add(T0, "Alex", "voice", "build me a big castle by the river")    # 8 words: no check
        self.lines.add(T0, "Alex", "chat", "and a moat around it")
        cast = self.lines.add(T0 + 1, "Steve", "voice", "Hot variable.")
        self.cast("Hot variable.", "cast_hot_fireball")
        self.step(1)
        self.assertEqual(self.jev.kinds()[0], "cast")
        self.assertEqual(self.bridge.asked, [(cast, "hot fireball")])
        self.assertEqual(self.jev.kinds().count("cast"), 1)

    def test_a_cast_said_while_another_line_is_routed_is_checked_before_the_next(self):
        a = self.lines.add(T0, "Alex", "voice", "build me a big castle by the river")
        b = self.lines.add(T0 + 0.5, "Alex", "voice", "tell me about the castle walls now please")
        ask, added = self.jev.ask, []

        def ask_while_steve_casts(state, questions, required="line_1"):
            if not added:
                added.append(self.lines.add(T0 + 1, "Steve", "voice", "Hot variable."))
            return ask(state, questions, required)

        self.jev.ask = ask_while_steve_casts
        self.cast("Hot variable.", "cast_hot_fireball")
        self.step(1)
        self.assertEqual(self.jev.kinds(), ["route", "cast", "route"])
        self.assertEqual(self.bridge.asked, [(added[0], "hot fireball")])
        self.assertEqual((self.row(a)["jev_class"], self.row(b)["jev_class"]), ("action_oriented", "not_relevant"))

    def test_no_call_for_a_line_too_old_to_cast(self):
        said_long_ago = self.lines.add(T0 - 20, "Steve", "voice", "Hot variable.")   # 20 s: over 2 + 10
        self.cast("Hot variable.", "cast_hot_fireball")
        with self.assertLogs("vfg-jev", "INFO") as logs:
            self.step()
        self.assertEqual(self.jev.kinds(), ["route"])
        self.assertIn("cast-check %s Steve · too old to cast (20.0 s since it was said) → routing, no call"
                      % said_long_ago, "\n".join(logs.output))
        self.assertEqual(self.row(said_long_ago)["jev_class"], "not_relevant")

    def test_no_call_for_a_line_that_waited_here_past_the_max_age(self):
        first = self.lines.add(T0, "Steve", "voice", "Ice.")
        second = self.lines.add(T0, "Steve", "voice", "Beam")
        self.cast("Ice.", "cast_ice_beam")
        self.cast("Beam", "cast_ice_beam")
        ask = self.jev.ask

        def slow(state, questions, required="line_1"):   # the first check takes 2.5 s
            self.clock.advance(2.5)
            return ask(state, questions, required)

        self.jev.ask = slow
        with self.assertLogs("vfg-jev", "INFO") as logs:
            self.step()
        self.assertEqual(self.bridge.asked, [(first, "ice beam")])
        self.assertIn("cast-check %s Steve · too old to cast (2.5 s since it was read here) → routing, no call"
                      % second, "\n".join(logs.output))
        self.assertEqual(self.row(second)["jev_class"], "not_relevant")

    def test_recent_lines_that_cannot_be_read_route_the_line_once(self):
        line = self.lines.add(T0, "Steve", "voice", "Hot variable.")
        self.cast("Hot variable.", "cast_hot_fireball")

        def broken(*a, **k):
            raise sqlite3.OperationalError("database is locked")

        self.f.store.recent = broken
        with self.assertLogs("vfg-jev", "WARNING") as logs:
            self.step()
            self.step(1)
        self.assertIn("cast-check %s Steve · could not read its recent lines: database is locked → routing, "
                      "no call" % line, "\n".join(logs.output))
        self.assertEqual(self.jev.kinds(), ["route"])
        self.assertEqual(self.row(line)["jev_class"], "not_relevant")

    def test_over_1_turns_it_off(self):
        self.cfg.cast_min = 2
        self.f = self.new_filter()
        self.lines.add(T0, "Steve", "voice", "Hot variable.")
        self.cast("Hot variable.", "cast_hot_fireball", 1.0)
        self.step()
        self.assertEqual(self.jev.kinds(), ["route"])

    def test_a_failed_or_refused_check_goes_on_to_routing_and_does_not_start_failing_open(self):
        for n, err in enumerate((JevDown("timed out"), JevRefused("400 bad request"))):
            self.jev.raises = [err]
            line = self.lines.add(T0 + n, "Steve", "voice", "Hot variable %d." % n)
            with self.assertLogs("vfg-jev", "WARNING") as logs:
                self.step(1)
            self.assertIn("cast-check %s Steve · failed: %s → routing" % (line, err), "\n".join(logs.output))
            self.assertEqual(self.row(line)["jev_class"], "not_relevant")
            self.assertIsNone(self.f.failing_since)
        self.assertEqual(self.jev.kinds(), ["cast", "route", "cast", "route"])
        self.assertNotIn("cast:", " ".join(u["ref"] for u in self.lines.usage()))

    def test_a_line_is_checked_once_even_when_its_routing_call_fails(self):
        line = self.lines.add(T0, "Steve", "voice", "Hot variable.")
        self.jev.raises = [None, JevDown("500")]   # the check answers, the routing call fails
        with self.assertLogs("vfg-jev", "WARNING"):
            self.assertEqual(self.step(), 1.0)
        self.assertEqual(self.f.failing_since, T0)
        self.step(1)
        self.assertEqual(self.jev.kinds(), ["cast", "route", "route"])
        self.assertEqual(self.row(line)["jev_class"], "not_relevant")

    def test_a_bridge_that_does_not_answer_or_keeps_the_line_goes_on_to_routing(self):
        self.bridge.fails = "no connection: [Errno 111] Connection refused"
        a = self.lines.add(T0, "Steve", "voice", "Hot variable.")
        self.cast("Hot variable.", "cast_hot_fireball")
        with self.assertLogs("vfg-jev", "WARNING") as logs:
            self.step()
        self.assertIn("· hot fireball p 0.92 → the bridge failed: no connection", "\n".join(logs.output))
        self.assertEqual(self.row(a)["jev_class"], "not_relevant")
        self.bridge.fails, self.bridge.keeps = None, True
        b = self.lines.add(T0 + 1, "Steve", "voice", "Not fireboat")
        self.cast("Not fireboat", "cast_hot_fireball")
        with self.assertLogs("vfg-jev", "INFO") as logs:
            self.step(1)
        self.assertIn("→ 409 Jev judged line %s already, routing" % b, "\n".join(logs.output))
        self.assertEqual(self.row(b)["jev_class"], "not_relevant")
        self.assertEqual(self.jev.kinds(), ["cast", "route", "cast", "route"])

    def test_no_call_for_a_player_without_incantations_a_typed_line_or_a_long_one(self):
        self.write_store(entry("Steve", "hot fireball"), entry("Steve", "ice beam"))   # no @a one
        self.lines.add(T0, "Alex", "voice", "Hot variable.")                       # no incantation of Alex's
        self.lines.add(T0 + 1, "Steve", "chat", "hot fireball")                    # typed
        self.lines.add(T0 + 2, "Steve", "voice", "ok so the hot fireball is too weak")   # 8 words
        self.lines.add(T0 + 3, "Steve", "voice", "   ")                            # blank: no call at all
        self.step()
        self.assertEqual(self.jev.kinds(), ["route", "route", "route"])
        os.remove(self.cfg.incantations)                                           # no store: none for anyone
        self.lines.add(T0 + 4, "Steve", "voice", "Hot variable.")
        self.step(1)
        self.assertEqual(self.jev.kinds(), ["route"] * 4)
        self.assertEqual(self.bridge.asked, [])

    def test_an_everyone_incantation_counts_and_the_store_is_read_again_when_it_changes(self):
        self.lines.add(T0, "Notch", "voice", "Juicy stake.")
        self.step()
        self.assertEqual(self.jev.kinds(), ["cast", "route"])
        self.write_store(entry("Steve", "hot fireball"))
        self.lines.add(T0 + 1, "Notch", "voice", "Juicy stake.")
        self.step(1)
        self.assertEqual(self.jev.kinds(), ["cast", "route", "route"])

    def test_writes_only_usage_rows(self):
        self.assertTrue(self.f.store.ready())
        seen = []
        self.f.store.conn.set_authorizer(lambda action, a, b, db, src: seen.append((action, a, b)) or sqlite3.SQLITE_OK)
        self.lines.add(T0, "Steve", "voice", "Hot variable.")
        self.cast("Hot variable.", "cast_hot_fireball")
        self.lines.add(T0 + 1, "Steve", "voice", "nice one")
        self.step(1)
        self.step(10)
        self.assertEqual({a for action, a, b in seen if action == sqlite3.SQLITE_INSERT}, {"usage"})
        self.assertLessEqual({(a, b) for action, a, b in seen if action == sqlite3.SQLITE_UPDATE},
                             {("lines", "jev_class"), ("lines", "jev_confidence"), ("lines", "jev_judged_at"),
                              ("lines", "fed_at")})


class TestTheCall(CastBase):
    def test_the_question(self):
        self.write_store(entry("Steve", "hot fireball", aliases=["Hot fire ball", "hot fireball", "hut fireball"]),
                         entry("@a", "Hot Fireball", action="other"),       # Steve's own comes first: once
                         entry("steve", "juicy steak", action="command",
                               values={"commands": ["give Steve minecraft:cooked_beef 64"]}),
                         entry("@a", "ice beam", action="ice_beam_cast"), entry("Alex", "rock slide"))
        with open(self.cfg.menu, "w") as f:
            f.write("command — Run vanilla commands, in order\nice_beam_cast — Ice Beam: fire an ice beam where you look.\n")
        line = self.lines.add(T0, "Steve", "voice", "Ice.")
        self.step()
        state, qs = self.jev.calls[0]
        q = qs[casting.QUESTION]
        self.assertEqual(list(qs), [casting.QUESTION])
        self.assertEqual(q["type"], "choice")
        self.assertEqual(q["instructions"], "Line [1], Steve at 18:22:31: “Ice.”. Which of Steve's incantations is "
                                            "line [1], said to cast it, misheard or cut short as it may be; or is it "
                                            "not a cast?")
        self.assertEqual(list(q["criteria"]), ["cast_hot_fireball", "cast_juicy_steak", "cast_ice_beam", "not_a_cast"])
        self.assertEqual(q["criteria"]["cast_hot_fireball"],
                         'Cast "hot fireball": line [1] is "hot fireball", or words that sound like it, or as '
                         '"hot fire ball", "hut fireball", or a piece of it. It runs the action hot_fireball.')
        self.assertIn('It runs give Steve minecraft:cooked_beef 64.', q["criteria"]["cast_juicy_steak"])
        self.assertIn("It Ice Beam: fire an ice beam where you look.", q["criteria"]["cast_ice_beam"])
        self.assertEqual(q["criteria"]["not_a_cast"], casting.NOT_A_CAST_TEXT)
        self.assertEqual(list(state), ["rules", "examples", "recent"])
        self.assertEqual((state["rules"], state["examples"]), (casting.RULES, casting.EXAMPLES))
        self.assertTrue(state["recent"].endswith("\n[1] 18:22:31 Steve: Ice."))
        self.assertEqual(self.row(line)["jev_class"], "not_relevant")

    def test_recent_is_the_speakers_lines_and_casts_and_the_ais(self):
        self.lines.add(T0 - 70, "Steve", "voice", "too long ago", jev_class="not_relevant")
        cast_line = self.lines.add(T0 - 5, "Steve", "voice", "Ice Beam.", dropped="cast")
        for k, (started, status) in enumerate(((T0 - 5, "fired"), (T0 - 4.9, "duplicate"), (T0 - 2, "cooldown"))):
            self.conn.execute("INSERT INTO casts (id, at, started_at, ended_at, player_name, phrase, layer, status, "
                              "line_id) VALUES (?, ?, ?, ?, 'Steve', 'ice beam', 'spotter', ?, ?)",
                              ("C%d" % k, iso(started), iso(started), iso(started + 0.5), status, cast_line))
        self.lines.add(T0 - 4, "Alex", "voice", "not Steve's", jev_class="not_relevant")
        self.lines.add(T0 - 3, "AI", "ai", "Ice beam fired!")
        self.lines.add(T0 + 5, "Steve", "voice", "said after it")
        line = self.lines.add(T0, "Steve", "voice", "Beam")
        self.cast("Beam", "cast_ice_beam", 0.97)
        self.step()
        state, _ = self.jev.calls[0]
        self.assertEqual(state["recent"].split("\n"), [
            casting.HEADING % "Steve",
            '18:22:26 Steve: Ice Beam.', '18:22:26 Steve cast "ice beam"', "18:22:28 AI: Ice beam fired!",
            '18:22:29 Steve cast "ice beam"', "[1] 18:22:31 Steve: Beam"])
        self.assertEqual(self.bridge.asked, [(line, "ice beam")])

    def test_recent_keeps_the_newest_and_works_without_the_casts_table(self):
        self.conn.execute("DROP TABLE casts")
        for n in range(12):
            self.lines.add(T0 - 30 + n, "Steve", "voice", "line %d" % n, jev_class="not_relevant")
        self.lines.add(T0, "Steve", "voice", "Ice.")
        self.step()
        recent = self.jev.calls[0][0]["recent"].split("\n")
        self.assertEqual(len(recent), 1 + casting.RECENT_MAX + 1)
        self.assertEqual(recent[1], "18:22:05 Steve: line 4")


class TestIncantations(unittest.TestCase):
    def test_own_then_everyones_one_per_phrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "incantations.json")
            store = casting.Incantations(path)
            self.assertEqual(store.for_player("Steve"), [])                   # no file
            with open(path, "w") as f:
                f.write("{not json")
            self.assertEqual(store.for_player("Steve"), [])                   # unreadable
            with open(path, "w") as f:
                json.dump({"version": 2, "incantations": [
                    entry("@a", "fire ball", action="a"), entry("STEVE", "Fire-ball", action="b"),
                    entry("Alex", "ice wall"), entry("steve", "")]}, f)
            got = store.for_player("steve")
            self.assertEqual([(e["player"], e["action"]) for e in got], [("STEVE", "b")])
            self.assertEqual([e["phrase"] for e in store.for_player("Notch")], ["fire ball"])
            with open(path, "w") as f:   # as the kit: one entry that is not an object, and none is read
                json.dump({"version": 3, "incantations": [entry("Steve", "fire ball"), "junk"]}, f)
            self.assertEqual(store.for_player("Steve"), [])

    def test_short_enough_labels_and_what_it_does(self):
        self.assertTrue(casting.short_enough("Hot fire bomb. Fireball."))
        self.assertTrue(casting.short_enough("one two three four five six"))
        self.assertFalse(casting.short_enough("one two three four five six seven"))
        self.assertEqual(casting.label("Fire-Ball!", {}), "cast_fire_ball")
        self.assertEqual(casting.label("fire ball", {"cast_fire_ball": 1}), "cast_fire_ball_2")
        self.assertEqual(casting.label("ゆき", {}), "cast_it")
        self.assertEqual(casting.short("cast_hot_fireball"), "hot fireball")
        self.assertEqual(casting.short(casting.NOT_A_CAST), "no")
        menu = casting.menu_lines("- dash — Dash forward.\nbroken line\nwall — Build a wall.")
        self.assertEqual(menu, {"dash": "Dash forward.", "wall": "Build a wall."})
        self.assertEqual(casting.does({"action": "dash"}, menu), "Dash forward.")
        self.assertEqual(casting.does({"action": "speed_dash"}, menu), "runs the action speed_dash")
        self.assertEqual(casting.does({"action": "command", "values": {"commands": ["say hi", "say bye"]}}, menu),
                         "runs say hi; say bye")


class Answers(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.server.got.append((self.path, dict(self.headers), json.loads(self.rfile.read(n))))
        status, body = self.server.answer
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class TestBridgeClient(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Answers)
        self.server.got = []
        threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.bridge = casting.Bridge("http://127.0.0.1:%d/" % self.server.server_address[1], "tok", timeout=2)

    def answer(self, status, body):
        self.server.answer = (status, body)
        return self.bridge.cast("01LINE", "hot fireball")

    def test_what_the_bridge_answered(self):
        got = self.answer(202, {"id": "C1", "status": "fired", "copy": "zap", "line": "dropped"})
        self.assertEqual((got.dropped, got.said), (True, "fired"))
        path, headers, body = self.server.got[0]
        self.assertEqual((path, body), ("/v1/jev/casts", {"line_id": "01LINE", "phrase": "hot fireball"}))
        self.assertEqual(headers["Authorization"], "Bearer tok")
        got = self.answer(202, {"id": "C2", "status": "cooldown", "error": "0.4 s left", "line": "dropped"})
        self.assertEqual((got.dropped, got.said), (True, "cooldown (0.4 s left)"))
        got = self.answer(200, {"line": "dropped", "cast_id": "C0"})
        self.assertEqual((got.dropped, got.said), (True, "dropped already"))
        got = self.answer(404, {"error": "Steve has no incantation 'hot fireball'"})
        self.assertEqual((got.dropped, got.said), (False, "404 Steve has no incantation 'hot fireball'"))
        got = self.answer(409, {"error": "Jev judged line 01LINE already", "line": "judged"})
        self.assertFalse(got.dropped)

    def test_no_answer_to_decide_on_is_a_bridge_error(self):
        for status, body in ((500, {"error": "internal"}), (503, {"error": "busy"}), (401, {"error": "unauthorized"}),
                             (403, {"error": "only from the box itself"}), (202, b"not json"), (202, b"[1]")):
            with self.assertRaises(casting.BridgeError, msg=status):
                self.answer(status, body)
        port = self.server.server_address[1]
        self.server.shutdown()
        self.server.server_close()
        with self.assertRaises(casting.BridgeError):
            casting.Bridge("http://127.0.0.1:%d" % port, "tok", timeout=1).cast("01LINE", "x")


if __name__ == "__main__":
    unittest.main()
