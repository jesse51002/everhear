"""Every rule of Behaviour, driven through `Filter.step()` with a fake clock, a
fake Jev and the stand-in feed writer. Nothing here sleeps."""
import os
import re
import sqlite3
import unittest
from unittest import mock

from fake_jev import verdict
from support import T0, FakeClock, Lines, load_fake_feed, make_db, tempdir

from filter import SYS_BACK, SYS_OPEN, Config, Filter
from jev_api import JevDown, JevRefused, Result
from prompt import estimate, state_json
from store import Store

QUOTED = re.compile(r"“(.*)”", re.S)


class FakeJev:
    """Answers by keyword in the quoted line, or as `verdicts[text]` says;
    raises on demand; records every call."""

    def __init__(self):
        self.calls = []
        self.probes = 0
        self.down = None      # a reason: every call and probe raises JevDown
        self.refuse = 0       # how many calls in a row raise JevRefused
        self.verdicts = {}

    def ask(self, state, questions):
        self.calls.append((state, questions))
        if self.down:
            raise JevDown(self.down)
        if self.refuse:
            self.refuse -= 1
            raise JevRefused("400 bad request")
        answers = {}
        for name, q in questions.items():
            text = QUOTED.search(q["instructions"]).group(1)
            answers[name] = self.verdicts.get(text) or verdict(text)
        return Result(answers, len(state_json(state)) // 4, 12)

    def probe(self):
        self.probes += 1
        if self.down:
            raise JevDown(self.down)
        return Result({"ping": (None, 0.9)}, 2, 9)


class FeedSpy:
    """The stand-in `vfg_feed`, watched, and broken on demand."""

    def __init__(self, path):
        self.mod = load_fake_feed()
        self.path = path
        self.batches = []
        self.sys = []
        self.broken = False

    def append(self, type, fields, text, at=None):
        if self.broken:
            raise OSError("feed.log is not writable")
        self.sys.append(text)
        return self.mod.append(type, fields, text, at)

    def append_batch(self, entries):
        if self.broken:
            raise OSError("feed.log is not writable")
        self.batches.append(list(entries))
        return self.mod.append_batch(entries)

    def lines(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return f.read().splitlines()


class Base(unittest.TestCase):
    batch_seconds = 5.0

    def setUp(self):
        d = tempdir(self)
        self.dir = d
        self.db = os.path.join(d, "vfg.db")
        self.conn = make_db(self.db)
        self.addCleanup(self.conn.close)
        self.lines = Lines(self.conn)
        log = os.path.join(d, "feed.log")
        env = mock.patch.dict(os.environ, {"FAKE_FEED_LOG": log})
        env.start()
        self.addCleanup(env.stop)
        self.feed = FeedSpy(log)
        self.jev = FakeJev()
        self.clock = FakeClock()
        self.cfg = Config(db=self.db, lookout=os.path.join(d, "lookout.md"), batch_seconds=self.batch_seconds,
                          state_tokens=8000)
        self.f = self.new_filter()

    def new_filter(self):
        store = Store(self.db)
        self.addCleanup(store.close)
        return Filter(store, self.jev, self.feed, self.clock, self.cfg)

    def step(self, advance=0.0):
        self.clock.advance(advance)
        return self.f.step()

    def row(self, line_id):
        return self.lines.get(line_id)

    def fed(self):
        """Every LINE entry handed to append_batch, flattened."""
        return [e for b in self.batches() for e in b]

    def batches(self):
        return self.feed.batches

    def sys_lines(self):
        return [l for l in self.feed.lines() if l.startswith("SYS#")]


class TestBatching(Base):
    def test_context_fed_5s_after_its_judgement_not_at_4_9(self):
        a = self.lines.add(T0, "Alex", "chat", "that cave by spawn")
        self.step()
        self.assertEqual(self.row(a)["jev_class"], "context")
        self.assertEqual(self.batches(), [])
        self.step(4.9)
        self.assertEqual(self.batches(), [])
        self.assertIsNone(self.row(a)["fed_at"])
        self.step(0.1)
        self.assertEqual(len(self.batches()), 1)
        self.assertEqual(self.batches()[0], [("LINE", "Alex chat context 0.70 -", "that cave by spawn",
                                              self.row(a)["at"])])
        self.assertEqual(self.row(a)["fed_at"], "2026-10-07T18:22:36.000Z")
        self.assertEqual(self.feed.lines(), ["LINE#1 18:22:31 Alex chat context 0.70 - | that cave by spawn"])
        self.step(10)
        self.assertEqual(len(self.batches()), 1)   # fed once

    def test_context_lines_arrive_together_after_the_first(self):
        a = self.lines.add(T0, "Alex", "chat", "that cave by spawn")
        self.step()
        b = self.lines.add(T0 + 2, "Steve", "voice", "our base is north", peak=-30)
        self.step(2)
        self.step(2.9)
        self.assertEqual(self.batches(), [])
        self.step(0.1)
        self.assertEqual([[e[2] for e in batch] for batch in self.batches()],
                         [["that cave by spawn", "our base is north"]])
        self.assertIsNotNone(self.row(a)["fed_at"])
        self.assertIsNotNone(self.row(b)["fed_at"])

    def test_a_line_for_the_ai_flushes_the_waiting_context_at_once_in_at_order(self):
        self.lines.add(T0, "Alex", "chat", "that cave by spawn")
        self.step()
        self.lines.add(T0 + 1, "Steve", "chat", "thanks AI")
        self.step(1)
        self.lines.add(T0 + 2, "Alex", "chat", "our base is north")
        self.step(1)
        self.lines.add(T0 + 3, "Steve", "voice", "build me a castle", peak=-8.2)
        self.step(1)
        self.assertEqual([[e[1].split()[2] for e in batch] for batch in self.batches()],
                         [["context", "conversational"], ["context", "action_oriented"]])

    def test_action_oriented_flushes_the_waiting_context_at_once_in_at_order(self):
        self.lines.add(T0, "Alex", "chat", "that cave by spawn")
        self.step()
        self.lines.add(T0 + 1, "Steve", "voice", "build me a castle", peak=-8.2)
        self.step(1)
        self.assertEqual(len(self.batches()), 1)
        self.assertEqual([e[2] for e in self.batches()[0]], ["that cave by spawn", "build me a castle"])
        self.assertEqual(self.feed.lines(), [
            "LINE#1 18:22:31 Alex chat context 0.70 - | that cave by spawn",
            "LINE#2 18:22:32 Steve voice action_oriented 0.90 -8dB | build me a castle",
        ])

    def test_not_relevant_is_never_fed(self):
        a = self.lines.add(T0, "Alex", "chat", "lol")
        self.step()
        self.step(30)
        self.assertEqual(self.row(a)["jev_class"], "not_relevant")
        self.assertEqual(self.batches(), [])

    def test_a_kept_line_11_minutes_old_is_never_fed(self):
        a = self.lines.add(T0 - 660, "Steve", "voice", "build me a castle")
        with self.assertLogs("vfg-jev", "INFO") as logs:
            self.step()
            self.step(10)
        self.assertEqual(self.row(a)["jev_class"], "action_oriented")
        self.assertIsNone(self.row(a)["fed_at"])
        self.assertEqual(self.batches(), [])
        self.assertEqual(sum("not feeding %s" % a in l for l in logs.output), 1)

    def test_a_failed_feed_write_leaves_fed_at_null_and_goes_next_step(self):
        a = self.lines.add(T0, "Steve", "chat", "build me a castle")
        self.feed.broken = True
        self.step()
        self.assertEqual(self.row(a)["jev_class"], "action_oriented")
        self.assertIsNone(self.row(a)["fed_at"])
        self.feed.broken = False
        self.step(0.2)
        self.assertIsNotNone(self.row(a)["fed_at"])
        self.assertEqual(self.feed.lines(), ["LINE#1 18:22:31 Steve chat action_oriented 0.90 - | build me a castle"])

    def test_restart_loses_no_kept_line(self):
        a = self.lines.add(T0, "Alex", "chat", "that cave by spawn")
        self.step()
        self.f = self.new_filter()          # the service restarts
        self.step(5)
        self.assertEqual([e[2] for e in self.fed()], ["that cave by spawn"])
        self.assertIsNotNone(self.row(a)["fed_at"])

    def test_stop_flushes_at_once(self):
        self.lines.add(T0, "Alex", "chat", "that cave by spawn")
        self.step()
        self.step(1)
        self.assertEqual(self.batches(), [])
        self.f.stopping = True
        self.f.flush_now()
        self.assertEqual(len(self.batches()), 1)

    def test_stopping_judges_nothing_more(self):
        self.lines.add(T0, "Alex", "chat", "that cave by spawn")
        self.f.stopping = True
        self.step()
        self.assertEqual(self.jev.calls, [])


class TestFields(Base):
    def test_the_three_examples(self):
        self.jev.verdicts = {"build me a castle": ("action_oriented", 0.93), "that cave by spawn": ("not_relevant", 0.8)}
        s = self.lines.add(T0, "Steve", "voice", "build me a castle", peak=-8.2)
        a = self.lines.add(T0 + 1, "Alex", "chat", "that cave by spawn")
        self.step()
        self.jev.verdicts["that cave by spawn"] = ("context", 0.71)
        self.lines.add(T0 + 3, "Alex", "chat", "lol")
        self.step(3)
        self.step(5)
        self.jev.down = "500"
        v = self.lines.add(T0 + 9, "Alex", "voice", "anyone got iron", peak=-21.4)
        self.step(1)
        self.step(10)
        self.step(5)
        fields = [e[1] for e in self.fed()]
        self.assertIn("Steve voice action_oriented 0.93 -8dB", fields)
        self.assertIn("Alex chat context 0.71 - was=not_relevant", fields)
        self.assertIn("Alex voice context - -21dB", fields)
        self.assertEqual(self.row(s)["jev_confidence"], 0.93)
        self.assertEqual(self.row(a)["jev_class"], "context")
        self.assertIsNone(self.row(v)["jev_confidence"])
        # every LINE carries the time the line was said
        for line in self.feed.lines():
            if line.startswith("LINE#"):
                self.assertRegex(line, r"^LINE#\d+ \d\d:\d\d:\d\d \S+ (voice|chat) (action_oriented|context) "
                                       r"(\d\.\d\d|-) (-?\d+dB|-)( was=not_relevant)? \| ")
        self.assertIn("LINE#1 18:22:31 Steve voice action_oriented 0.93 -8dB | build me a castle", self.feed.lines())
        self.assertIn("18:22:32 Alex chat context 0.71 - was=not_relevant | that cave by spawn",
                      "\n".join(self.feed.lines()))
        self.assertIn("18:22:40 Alex voice context - -21dB | anyone got iron", "\n".join(self.feed.lines()))

    def test_db_rounding_and_voice_without_peak(self):
        self.jev.verdicts = {"x build": ("action_oriented", 0.5)}
        for peak, want in ((-8.5, "-8dB"), (-0.4, "0dB"), (None, "-"), (-21.6, "-22dB")):
            row = {"player_name": "Steve", "source": "voice", "jev_class": "context", "jev_confidence": None,
                   "peak_dbfs": peak, "id": "x"}
            self.assertEqual(self.f.fields(row), "Steve voice context - " + want)
        self.assertEqual(self.f.fields({"player_name": "Alex", "source": "chat", "jev_class": "context",
                                        "jev_confidence": 0.7049, "peak_dbfs": -3, "id": "y"}),
                         "Alex chat context 0.70 -")


class TestJudging(Base):
    def test_one_call_and_one_usage_row_per_new_line(self):
        ids = [self.lines.add(T0 + n, "Steve", "voice", t) for n, t in
               enumerate(["build me a castle", "lol", "that cave by spawn"])]
        self.step()
        self.assertEqual(len(self.jev.calls), 3)
        usage = self.lines.usage()
        self.assertEqual([u["ref"] for u in usage], ids)
        for (state, _), u in zip(self.jev.calls, usage):
            self.assertEqual(u["component"], "jev")
            self.assertEqual(u["model"], "jev-latest")
            self.assertEqual(u["input_tokens"], len(state_json(state)) // 4)
            self.assertEqual(list(state), ["rules", "look_out_for", "history"])
            self.assertEqual(u["output_tokens"], 0)
            self.assertEqual(u["cost_usd"], u["input_tokens"] * 0.000000042)
            self.assertLessEqual(estimate(state), self.cfg.state_tokens)
        r = self.row(ids[0])
        self.assertEqual((r["jev_class"], r["jev_confidence"], r["jev_judged_at"]),
                         ("action_oriented", 0.9, "2026-10-07T18:22:31.000Z"))

    def test_blank_line_is_not_relevant_without_a_call(self):
        a = self.lines.add(T0, "Steve", "voice", "   \n ")
        self.step()
        self.assertEqual(self.jev.calls, [])
        r = self.row(a)
        self.assertEqual((r["jev_class"], r["jev_confidence"]), ("not_relevant", None))
        self.assertEqual(self.lines.usage(), [])

    def test_asking_again_the_newest_five(self):
        recent = []
        for n in range(7):   # seven Jev verdicts of not_relevant in the last 2 minutes
            judged = T0 - 100 + n * 10
            recent.append(self.lines.add(judged - 1, "Alex", "chat", "meh %d" % n, jev_class="not_relevant",
                                         jev_confidence=0.8, jev_judged_at=_iso(judged)))
        old = self.lines.add(T0 - 181, "Alex", "chat", "old meh", jev_class="not_relevant", jev_confidence=0.8,
                             jev_judged_at=_iso(T0 - 180))
        blank = self.lines.add(T0 - 5, "Alex", "chat", " ", jev_class="not_relevant", jev_judged_at=_iso(T0 - 5))
        before = {i: self.row(i) for i in recent + [old, blank]}
        new = self.lines.add(T0, "Steve", "voice", "hello there")
        self.step()
        self.assertEqual(len(self.jev.calls), 1)
        self.assertEqual(len(self.lines.usage()), 1)
        state, qs = self.jev.calls[0]
        self.assertEqual(list(qs), ["line_1", "line_2", "line_3", "line_4", "line_5", "line_6"])
        quoted = [QUOTED.search(q["instructions"]).group(1) for q in qs.values()]
        self.assertEqual(quoted, ["hello there", "meh 6", "meh 5", "meh 4", "meh 3", "meh 2"])
        hist = state["history"].split("\n")[1:]
        self.assertEqual([h.split(" ")[0] for h in hist if h.startswith("[")], ["[6]", "[5]", "[4]", "[3]", "[2]", "[1]"])
        for i in recent + [old, blank]:   # all stayed not_relevant: nothing written
            self.assertEqual(self.row(i), before[i])
        self.assertEqual(self.row(new)["jev_class"], "action_oriented")

    def test_a_flip_is_fed_with_was_not_relevant(self):
        self.jev.verdicts = {"that cave by spawn": ("not_relevant", 0.6)}
        a = self.lines.add(T0, "Alex", "chat", "that cave by spawn")
        self.step()
        self.assertEqual(self.row(a)["jev_class"], "not_relevant")
        del self.jev.verdicts["that cave by spawn"]
        self.lines.add(T0 + 30, "Steve", "voice", "put it there, by the cave")
        self.step(30)
        r = self.row(a)
        self.assertEqual((r["jev_class"], r["jev_confidence"], r["jev_judged_at"]),
                         ("context", 0.7, "2026-10-07T18:23:01.000Z"))
        self.assertEqual(len(self.jev.calls), 2)
        self.assertEqual(len(self.lines.usage()), 2)
        self.assertEqual([e[1] for e in self.fed()],
                         ["Alex chat context 0.70 - was=not_relevant", "Steve voice action_oriented 0.90 -"])
        # once fed, the mark is gone
        self.assertEqual(self.f.flipped, set())

    def test_a_line_judged_3_minutes_ago_is_not_asked_again(self):
        self.jev.verdicts = {"that cave by spawn": ("not_relevant", 0.6)}
        self.lines.add(T0, "Alex", "chat", "that cave by spawn")
        self.step()
        self.lines.add(T0 + 121, "Steve", "voice", "hello")
        self.step(121)
        self.assertEqual(list(self.jev.calls[1][1]), ["line_1"])

    def test_refused_once_asks_again_with_half_the_history_and_nothing_again(self):
        for n in range(300):
            self.lines.add(T0 - 3000 + n, "Alex", "chat", "filler line %d" % n + " x" * 30,
                           jev_class="context", fed_at=_iso(T0 - 3000 + n), jev_judged_at=_iso(T0 - 3000 + n))
        self.lines.add(T0 - 10, "Alex", "chat", "meh", jev_class="not_relevant", jev_confidence=0.8,
                       jev_judged_at=_iso(T0 - 10))
        a = self.lines.add(T0, "Steve", "voice", "build me a castle")
        self.cfg.state_tokens = 3000
        self.jev.refuse = 1
        self.step()
        self.assertEqual(len(self.jev.calls), 2)
        (first, q1), (second, q2) = self.jev.calls
        self.assertEqual(len(q1), 2)
        self.assertEqual(list(q2), ["line_1"])
        self.assertLess(len(second["history"]), len(first["history"]))
        self.assertEqual(self.row(a)["jev_class"], "action_oriented")
        self.assertEqual(len(self.lines.usage()), 1)
        self.assertIsNone(self.f.failing_since)

    def test_refused_twice_passes_as_context_without_a_sys_line(self):
        a = self.lines.add(T0, "Steve", "voice", "build me a castle")
        self.jev.refuse = 2
        with self.assertLogs("vfg-jev", "ERROR"):
            self.step()
        r = self.row(a)
        self.assertEqual((r["jev_class"], r["jev_confidence"]), ("context", None))
        self.assertIsNone(self.f.failing_since)
        self.step(5)
        self.assertEqual([e[1] for e in self.fed()], ["Steve voice context - -"])
        self.assertEqual(self.sys_lines(), [])
        self.assertEqual(self.lines.usage(), [])


class TestAiLines(Base):
    def test_never_judged_never_asked_never_fed_never_passed_but_in_history(self):
        ai = self.lines.add(T0, "AI", "ai", "Which colour should the tower be?")
        b = self.lines.add(T0 + 2, "Steve", "chat", "red")
        self.step(2)
        self.assertEqual(len(self.jev.calls), 1)
        state, qs = self.jev.calls[0]
        self.assertIn("18:22:31 AI (ai): Which colour should the tower be?", state["history"])
        self.assertEqual(list(qs), ["line_1"])
        self.assertNotIn("colour", qs["line_1"]["instructions"])
        self.assertIsNone(self.row(ai)["jev_class"])
        self.assertEqual(self.row(b)["jev_class"], "not_relevant")
        # failing open does not pass it either
        self.jev.down = "500"
        self.lines.add(T0 + 3, "Steve", "chat", "blue")
        self.step(1)
        self.step(10)
        self.step(5)
        r = self.row(ai)
        self.assertEqual((r["jev_class"], r["jev_judged_at"], r["fed_at"]), (None, None, None))
        self.assertNotIn("colour", "\n".join(self.feed.lines()))


class TestFailingOpen(Base):
    def test_open_at_10s_one_sys_each_way(self):
        self.jev.down = "500 internal error"
        a = self.lines.add(T0, "Steve", "voice", "build me a castle", peak=-12)
        self.assertEqual(self.step(), 1.0)     # fails: the clock starts now
        for t in (1, 2, 3, 4, 5, 6, 7, 8, 9):
            self.step(1)
        self.step(0.9)                          # 9.9 s
        self.assertEqual(self.sys_lines(), [])
        self.assertIsNone(self.row(a)["jev_class"])
        self.step(0.1)                          # 10 s
        self.assertEqual(len(self.sys_lines()), 1)
        self.assertTrue(self.sys_lines()[0].endswith(" | " + SYS_OPEN))
        r = self.row(a)
        self.assertEqual((r["jev_class"], r["jev_confidence"]), ("context", None))
        b = self.lines.add(T0 + 11, "Alex", "chat", "lol")
        calls = len(self.jev.calls)
        self.step(1)
        self.assertEqual(len(self.jev.calls), calls)    # nothing asked while open
        self.assertEqual(self.row(b)["jev_class"], "context")
        self.step(4)                                    # 5 s after the first went through
        self.assertEqual([e[1] for e in self.fed()], ["Steve voice context - -12dB", "Alex chat context - -"])
        self.assertEqual(self.jev.probes, 1)            # a probe every 5 s, still failing
        self.step(5)
        self.assertEqual(self.jev.probes, 2)
        self.assertEqual(len(self.sys_lines()), 1)
        self.jev.down = None
        self.step(5)                                    # the probe succeeds
        self.assertEqual(self.jev.probes, 3)
        self.assertEqual(len(self.sys_lines()), 2)
        self.assertTrue(self.sys_lines()[1].endswith(" | " + SYS_BACK))
        self.assertEqual(self.lines.usage()[-1]["ref"], "probe")
        c = self.lines.add(T0 + 31, "Steve", "voice", "make it taller")
        self.step(1)
        self.assertEqual(self.row(c)["jev_class"], "action_oriented")
        self.assertEqual(self.row(c)["jev_confidence"], 0.9)
        self.step(30)
        self.assertEqual(len(self.sys_lines()), 2)       # two SYS lines in all
        # the SYS line precedes the lines passed while open
        log = self.feed.lines()
        self.assertTrue(log[0].startswith("SYS#1 "))

    def test_a_success_clears_the_clock(self):
        self.jev.down = "timeout"
        self.lines.add(T0, "Steve", "voice", "build me a castle")
        self.step()
        self.jev.down = None
        self.step(6)
        self.assertIsNone(self.f.failing_since)
        self.jev.down = "timeout"
        self.lines.add(T0 + 7, "Steve", "voice", "make it bigger")
        self.step(1)
        self.step(9)       # 9 s after this new run of failures began
        self.assertEqual(self.sys_lines(), [])
        self.step(1)
        self.assertEqual(len(self.sys_lines()), 1)

    def test_an_owed_sys_line_is_retried_and_goes_first(self):
        self.jev.down = "500"
        self.lines.add(T0, "Steve", "voice", "build me a castle")
        self.step()
        self.feed.broken = True
        self.step(10)
        self.step(5)
        self.assertEqual(self.feed.lines(), [])
        self.feed.broken = False
        self.step(0.2)
        log = self.feed.lines()
        self.assertTrue(log[0].startswith("SYS#1 ") and log[0].endswith(SYS_OPEN))
        self.assertTrue(log[1].startswith("LINE#2 "))
        self.assertEqual(len(self.sys_lines()), 1)


class TestWritesOnly(Base):
    def test_no_column_but_jev_and_fed_at_no_table_but_usage(self):
        self.assertTrue(self.f.store.ready())
        seen = []

        def watch(action, a, b, dbname, source):
            seen.append((action, a, b))
            return sqlite3.SQLITE_OK

        self.f.store.conn.set_authorizer(watch)
        self.jev.verdicts = {"that cave by spawn": ("not_relevant", 0.6)}
        self.lines.add(T0, "Alex", "chat", "that cave by spawn")
        self.lines.add(T0 + 1, "AI", "ai", "Where should it go?")
        self.lines.add(T0 + 1, "Steve", "chat", " ")
        self.step(1)
        del self.jev.verdicts["that cave by spawn"]
        self.lines.add(T0 + 2, "Steve", "voice", "put it there, by the cave")
        self.step(1)
        self.jev.refuse = 2
        self.lines.add(T0 + 3, "Steve", "voice", "and make it red")
        self.step(1)
        self.jev.down = "500"
        self.lines.add(T0 + 4, "Steve", "voice", "lol")
        self.step(1)
        self.step(10)
        self.step(5)
        self.jev.down = None
        self.step(5)
        self.f.flush_now()
        updated = {(a, b) for action, a, b in seen if action == sqlite3.SQLITE_UPDATE}
        inserted = {a for action, a, b in seen if action == sqlite3.SQLITE_INSERT}
        self.assertTrue(updated)
        self.assertLessEqual(updated, {("lines", "jev_class"), ("lines", "jev_confidence"),
                                       ("lines", "jev_judged_at"), ("lines", "fed_at")})
        self.assertEqual(inserted, {"usage"})
        forbidden = {sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX,
                     sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_PRAGMA}
        self.assertEqual([s for s in seen if s[0] in forbidden], [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM feed").fetchone()[0], 0)


class TestMalformedTimes(Base):
    def test_a_line_with_a_bad_at_never_wedges_judging_or_feeding(self):
        ai = self.lines.add("sometime yesterday", "AI", "ai", "Where should it go?")
        bad = self.lines.add("2026-10-07 18:22", "Steve", "chat", "build it here")
        with self.assertLogs("vfg-jev", "WARNING") as logs:
            self.step()
            good = self.lines.add(T0 + 1, "Alex", "chat", "build me a castle")
            self.step(1)
            self.step(5)
        self.assertEqual(self.row(bad)["jev_class"], "action_oriented")
        self.assertIsNone(self.row(bad)["fed_at"])
        self.assertIsNotNone(self.row(good)["fed_at"])
        self.assertEqual(sum("not feeding %s" % bad in l for l in logs.output), 1)
        self.assertIn("??:??:?? AI (ai): Where should it go?", self.jev.calls[-1][0]["history"])
        self.assertEqual([e[2] for e in self.fed()], ["build me a castle"])
        self.assertIsNone(self.row(ai)["jev_class"])


class TestStandInFeed(unittest.TestCase):
    """The stand-in writer keeps §7's grammar, so the tests above mean something."""

    def setUp(self):
        d = tempdir(self)
        self.log = os.path.join(d, "feed.log")
        env = mock.patch.dict(os.environ, {"FAKE_FEED_LOG": self.log})
        env.start()
        self.addCleanup(env.stop)
        self.mod = load_fake_feed()

    def test_grammar_seq_and_cleaning(self):
        self.assertEqual(self.mod.append("SYS", "", "jev back — filtering again", "2026-10-07T18:27:12.000Z"), 1)
        self.assertEqual(self.mod.append_batch([
            ("LINE", "Steve voice action_oriented 0.93 -8dB", "build\nme a\ncastle", "2026-10-07T18:22:31.412Z"),
            ("LINE", "Alex chat context 0.71 - was=not_relevant", "x" * 600, "2026-10-07T18:22:10.000Z"),
        ]), [2, 3])
        with open(self.log, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], "SYS#1 18:27:12 | jev back — filtering again")
        self.assertEqual(lines[1], "LINE#2 18:22:31 Steve voice action_oriented 0.93 -8dB | build me a castle")
        self.assertEqual(lines[2], "LINE#3 18:22:10 Alex chat context 0.71 - was=not_relevant | " + "x" * 499 + "…")

    def test_refuses_what_the_contract_does_not_allow(self):
        for entry in (("LINE", "Steve voice maybe 0.9 -", "t", None), ("SYS", "x", "t", None),
                      ("NOPE", "", "t", None), ("LINE", "Steve chat context - -", "t", "18:22:31"),
                      ("LINE", "Steve chat context - -", "t", 1791397351.0)):
            with self.assertRaises(ValueError, msg=entry):
                self.mod.append_batch([entry])
        self.assertFalse(os.path.exists(self.log))


class Killed(BaseException):
    """Stands for SIGKILL: nothing after it in the filter runs."""


class TestKilledBetweenFeedAndMark(Base):
    """A run killed after append_batch wrote a batch and before fed_at was set
    must not feed those lines again when it comes back."""

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"FAKE_FEED_DB": self.db})   # the writer mirrors into `feed`
        env.start()
        self.addCleanup(env.stop)
        self.real_append_batch = self.feed.append_batch

    def die_after_the_next_batch(self):
        def append_batch(entries):
            self.real_append_batch(entries)
            self.feed.append_batch = self.real_append_batch
            raise Killed()
        self.feed.append_batch = append_batch

    def test_restart_marks_instead_of_feeding_twice(self):
        a = self.lines.add(T0, "Alex", "chat", "that cave by spawn")
        b = self.lines.add(T0 + 0.5, "Steve", "voice", "build\nme a castle | now", peak=-8.2)
        self.die_after_the_next_batch()
        with self.assertRaises(Killed):
            self.step()
        self.assertIsNone(self.row(b)["fed_at"])
        self.assertEqual(len(self.feed.lines()), 2)
        self.lines.add(T0 + 1, "Alex", "chat", "that cave by spawn")   # same words, a later line: not the same
        self.clock.advance(2)
        self.f = self.new_filter()                                      # the service restarts
        with self.assertLogs("vfg-jev", "WARNING") as logs:
            self.step()
            self.step(5)
        self.assertEqual(sum("already fed as LINE#" in l for l in logs.output), 2)
        self.assertIsNotNone(self.row(a)["fed_at"])
        self.assertIsNotNone(self.row(b)["fed_at"])
        lines = self.feed.lines()
        self.assertEqual(sum("| build me a castle | now" in l for l in lines), 1)
        self.assertEqual(sum(l.endswith("| that cave by spawn") for l in lines), 2)   # the second line, once
        self.step(10)
        self.assertEqual(len(self.feed.lines()), 3)

    def test_a_line_judged_but_never_fed_is_still_fed_after_a_restart(self):
        a = self.lines.add(T0, "Alex", "chat", "that cave by spawn")
        self.step()
        self.clock.advance(1)
        self.f = self.new_filter()
        self.step(5)
        self.assertIsNotNone(self.row(a)["fed_at"])
        self.assertEqual(len(self.feed.lines()), 1)

    def test_a_run_never_killed_never_reads_the_feed_table(self):
        self.lines.add(T0, "Alex", "chat", "that cave by spawn")
        with mock.patch.object(Store, "feed_lines_at", side_effect=AssertionError("read the feed")):
            self.step()
            self.step(5)
        self.assertEqual(len(self.feed.lines()), 1)


class TestNoDatabaseYet(unittest.TestCase):
    def test_waits_and_says_so_once(self):
        d = tempdir(self)
        path = os.path.join(d, "vfg.db")
        store = Store(path)
        self.addCleanup(store.close)
        f = Filter(store, FakeJev(), FeedSpy(os.path.join(d, "feed.log")), FakeClock(),
                   Config(db=path, lookout=os.path.join(d, "lookout.md")))
        with self.assertLogs("vfg-jev", "WARNING") as logs:
            self.assertEqual(f.step(), 2.0)
            self.assertEqual(f.step(), 2.0)
        self.assertEqual(len(logs.output), 1)
        self.assertFalse(os.path.exists(path))           # never created by the filter
        sqlite3.connect(path).close()                    # an empty file: no tables yet
        with self.assertLogs("vfg-jev", "WARNING"):
            self.assertEqual(f.step(), 2.0)
        os.remove(path)
        make_db(path).close()
        self.assertIsNone(f.step())


def _iso(t):
    from jev_clock import iso
    return iso(t)


if __name__ == "__main__":
    unittest.main()
