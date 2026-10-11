"""The state, the questions and the walk: what Jev is shown, and how its answers are read."""
import json
import os
import unittest

from support import T0, Lines, make_db, tempdir

import prompt
from jev_clock import iso
from prompt import (D2, DEFAULT_LOOKOUT, EXAMPLES, GATHER_CRITERIA, GATHER_HEADING, GATHER_RULES, HISTORY_HEADING,
                    RULES, Lookout, Menu, build_state, estimate, gather_questions, in_short, questions, read_lookout,
                    render_line, route, state_json)
from store import Store


def row(n, at, player="Steve", source="voice", text="hello", peak=None):
    return {"id": "01L%07d" % n, "at": iso(at), "player_name": "AI" if source == "ai" else player,
            "source": source, "text": text, "peak_dbfs": peak}


class TestState(unittest.TestCase):
    def test_an_object_of_four_fields_in_order(self):
        new = row(9, T0, text="put it there, by the cave")
        state, _ = build_state("watch for caves", [(1, new)], [row(1, T0 - 50, "Alex", "chat", "anyone got iron")],
                               8000)
        self.assertEqual(list(state), ["rules", "look_out_for", "examples", "history"])
        self.assertEqual(state["rules"], RULES)
        self.assertEqual(state["look_out_for"], "watch for caves")
        self.assertEqual(state["examples"], EXAMPLES)
        self.assertEqual(state["history"], HISTORY_HEADING + "\n18:21:41 Alex (chat): anyone got iron"
                                           "\n[1] 18:22:31 Steve (voice): put it there, by the cave")
        self.assertTrue(state_json(state).startswith('{"rules": "You read what players say'))

    def test_the_spec_example(self):
        hist = [
            row(1, T0 - 51, "Steve", "voice", "build me a castle", -20),
            row(2, T0 - 47, source="ai", text="Where should it go?"),
            row(3, T0 - 33, "Alex", "chat", "anyone got iron"),
            row(4, T0 - 21, "Alex", "chat", "that cave by spawn"),
            row(5, T0 - 11, "Steve", "voice", "AAAAH", -2.5),
        ]
        new = row(6, T0, "Steve", "voice", "put it there, by the cave", -14)
        state, kept = build_state(DEFAULT_LOOKOUT, [(1, new)], list(reversed(hist)), 8000)
        self.assertEqual(kept, 5)
        self.assertEqual(state["history"], ("\n".join([
            HISTORY_HEADING,
            "18:21:40 Steve (voice): build me a castle",
            "18:21:44 AI (ai): Where should it go?",
            "18:21:58 Alex (chat): anyone got iron",
            "18:22:10 Alex (chat): that cave by spawn",
            "18:22:20 Steve (voice, loud): AAAAH",
            "[1] 18:22:31 Steve (voice): put it there, by the cave",
        ])))

    def test_the_gather_state_marks_the_ask_and_its_lines_in_place_kept_when_nothing_else_fits(self):
        ask = row(20, T0, text="make it bigger " + "x" * 600)
        window = [row(10 + n, T0 - 10 * (4 - n), text="line %d " % n + "x" * 600) for n in (1, 2, 3)]   # oldest first
        others = [row(n, T0 - 5 - 10 * n, text="other %d" % n) for n in range(6)]
        marked = [("ask", ask)] + list(enumerate(window, 1))
        state, kept = build_state(DEFAULT_LOOKOUT, marked, others, 10,   # far too small for anything
                                  rules=GATHER_RULES, heading=GATHER_HEADING)
        self.assertEqual(kept, 0)
        self.assertEqual(state["rules"], GATHER_RULES)
        hist = state["history"].split("\n")
        self.assertEqual(hist.pop(0), GATHER_HEADING)
        self.assertEqual([h.split(" ")[0] for h in hist], ["[1]", "[2]", "[3]", "[ask]"])   # where they were said
        for h in hist:
            self.assertNotIn("…", h)   # a line asked about is never cut
            self.assertTrue(h.endswith("x" * 600))

    def test_budget_with_ten_thousand_lines(self):
        new = row(99999, T0, text="build me a castle")
        others = [row(n, T0 - 1 - n, "Alex", "chat", "line number %d about nothing much" % n) for n in range(10000)]
        state, kept = build_state(DEFAULT_LOOKOUT, [(1, new)], others, 2000)    # newest first
        self.assertLessEqual(estimate(state), 2000)
        self.assertGreater(kept, 10)
        self.assertLess(kept, 10000)
        h = state["history"]
        self.assertIn("line number 0 about", h)                           # the newest in
        self.assertIn("line number %d about" % (kept - 1), h)
        self.assertNotIn("line number %d about" % kept, h)                 # the first that did not fit, out
        self.assertNotIn("line number 9999 about", h)                      # the oldest out
        hist = state["history"].split("\n")[1:]
        self.assertIn("line number %d about" % (kept - 1), hist[0])         # printed oldest first
        self.assertTrue(hist[-1].startswith("[1] "))
        # the estimate is the whole encoded object's, and the next line would not have fit
        nxt = len(json.dumps(render_line(others[kept]), ensure_ascii=False).encode()) - 2 + 2
        self.assertGreater(len(state_json(state).encode()) + nxt, 2000 * 3)

    def test_budget_never_exceeded_across_budgets(self):
        new = row(99999, T0, text="ünïcödé \"quoted\" \\ " * 20)
        others = [row(n, T0 - 1 - n, "Alex", "chat", "ça va? \"🙂\"\t\\ " * (n % 7 + 1)) for n in range(3000)]
        menu = "\n".join("act_%d — does “thing” %d" % (n, n) for n in range(300))
        for budget in (1500, 2000, 4000, 8000, 28000):
            state, _ = build_state(DEFAULT_LOOKOUT, [(1, new)], others, budget)
            self.assertLessEqual(estimate(state), budget, budget)
            with_menu, _ = build_state(DEFAULT_LOOKOUT, [(1, new)], others, budget, menu=menu)
            self.assertLessEqual(estimate(with_menu), budget, budget)
            half, _ = build_state(DEFAULT_LOOKOUT, [(1, new)], others, budget, history_share=0.5)
            self.assertLessEqual(estimate(half), budget)
            self.assertLessEqual(len(half["history"]), len(state["history"]))

    def test_the_menu_after_the_lookout_in_whole_lines_and_at_most_half_the_room(self):
        new = row(99999, T0, text="make it bigger")
        others = [row(n, T0 - 1 - n, "Alex", "chat", "x" * 50) for n in range(1000)]
        small = "give — gives a player items\nweather — sets the weather"
        state, kept = build_state(DEFAULT_LOOKOUT, [(1, new)], others, 4000, menu=small + "\n")
        self.assertEqual(list(state), ["rules", "look_out_for", "examples", "actions", "history"])
        self.assertEqual(state["actions"], small)
        big = "\n".join("act_%d — does a thing" % n for n in range(1000))
        cut, kept_cut = build_state(DEFAULT_LOOKOUT, [(1, new)], others, 4000, menu=big)
        bare, kept_bare = build_state(DEFAULT_LOOKOUT, [(1, new)], others, 4000)
        self.assertNotIn("actions", bare)
        self.assertTrue(big.startswith(cut["actions"] + "\nact_"))           # whole lines, the first ones
        fixed, _ = build_state(DEFAULT_LOOKOUT, [(1, new)], [], 4000)            # the rules, lookout and line
        room = 4000 * 3 - len(state_json(fixed).encode())
        without = {k: v for k, v in cut.items() if k != "actions"}
        cost = len(state_json(cut).encode()) - len(state_json(without).encode())
        self.assertTrue(room // 2 - 30 < cost <= room // 2, (cost, room))   # within a line of half
        self.assertGreater(kept_cut, kept_bare // 3)                          # the history keeps the rest
        self.assertLessEqual(estimate(cut), 4000)

    def test_half_history_gets_half_the_room(self):
        new = row(99999, T0, text="build")
        others = [row(n, T0 - 1 - n, "Alex", "chat", "x" * 50) for n in range(1000)]
        _, full = build_state(DEFAULT_LOOKOUT, [(1, new)], others, 4000)
        _, half = build_state(DEFAULT_LOOKOUT, [(1, new)], others, 4000, history_share=0.5)
        self.assertAlmostEqual(half, full / 2, delta=1)

    def test_ai_lines_loud_and_cut(self):
        self.assertEqual(render_line(row(1, T0, source="ai", text="(to Steve) Where should it go?")),
                         "18:22:31 AI (ai): (to Steve) Where should it go?")
        self.assertEqual(render_line(row(1, T0, text="AAAAH", peak=-6.0)), "18:22:31 Steve (voice, loud): AAAAH")
        self.assertEqual(render_line(row(1, T0, text="hey", peak=-6.1)), "18:22:31 Steve (voice): hey")
        self.assertEqual(render_line(row(1, T0, text="hey", peak=None)), "18:22:31 Steve (voice): hey")
        self.assertEqual(render_line(row(1, T0, "Alex", "chat", "multi\nline\r\ntext")),
                         "18:22:31 Alex (chat): multi line text")
        long = render_line(row(1, T0, "Alex", "chat", "y" * 700))
        self.assertTrue(long.endswith("y" * 500 + "…"))
        self.assertTrue(render_line(row(1, T0, "Alex", "chat", "y" * 700), mark=1).endswith("y" * 700))
        self.assertEqual(render_line(row(1, T0, text="hey"), mark="ask"), "[ask] 18:22:31 Steve (voice): hey")


class TestHistoryFromTheStore(unittest.TestCase):
    """The history is every stored line of the last VFG_JEV_HISTORY_HOURS, AI's included."""

    def test_old_lines_left_out_ai_lines_in(self):
        d = tempdir(self)
        conn = make_db(os.path.join(d, "vfg.db"))
        lines = Lines(conn)
        lines.add(T0 - 4 * 3600, "Steve", "voice", "last night's talk")
        lines.add(T0 - 2 * 3600, "Alex", "chat", "earlier tonight")
        lines.add(T0 - 60, "AI", "ai", "Where should it go?")
        new_id = lines.add(T0, "Steve", "voice", "next to the tower")
        store = Store(os.path.join(d, "vfg.db"))
        self.assertTrue(store.ready())
        new = [r for r in store.unjudged() if r["id"] == new_id]
        others = store.history(iso(T0 - 3 * 3600), exclude=[new_id])
        state, _ = build_state(DEFAULT_LOOKOUT, [(1, r) for r in new], others, 8000)
        self.assertNotIn("last night's talk", state["history"])
        self.assertIn("Alex (chat): earlier tonight", state["history"])
        self.assertIn("AI (ai): Where should it go?", state["history"])
        self.assertEqual(state_json(state).count("next to the tower"), 1)
        store.close()


class TestQuestions(unittest.TestCase):
    def test_ds_two_questions_about_line_1(self):
        qs = questions(row(9, T0, "Steve", "voice", "put it there, by the cave"))
        self.assertEqual(list(qs), ["d_what", "d_feel"])
        self.assertEqual(qs["d_what"], {
            "type": "choice",
            "instructions": "Line [1], Steve at 18:22:31: “put it there, by the cave”. What should happen with this line?",
            "criteria": D2["d_what"][1]})
        self.assertEqual(qs["d_feel"]["instructions"],
                         "Line [1], Steve at 18:22:31: “put it there, by the cave”. If the AI only replied in chat and"
                         " nothing changed in the game, how would the speaker feel?")
        self.assertEqual(list(qs["d_feel"]["criteria"]),
                         ["fine_a_reply_is_all", "let_down_wanted_something_done", "not_talking_to_the_ai"])

    def test_quote_is_the_first_200_characters_on_one_line(self):
        q = questions(row(1, T0, text="a\n" + "a" * 300))["d_what"]["instructions"]
        self.assertIn("“a " + "a" * 198 + "…”", q)

    def test_gather_questions_one_per_line_oldest_first(self):
        ask = row(9, T0, text="make it bigger")
        qs = gather_questions(ask, [row(3, T0 - 30, text="the castle\nby the river"), row(5, T0 - 10, text="the big one")])
        self.assertEqual(list(qs), ["line_1", "line_2"])
        self.assertEqual(qs["line_2"], {
            "type": "choice",
            "instructions": "Steve's line [ask] at 18:22:31, “make it bigger”, goes to the AI. Speech-to-text cuts a"
                            " player at every pause, so one ask can arrive as several lines. Line [2], said by Steve"
                            " before it at 18:22:21: “the big one”. Is line [2] part of that ask?",
            "criteria": GATHER_CRITERIA})
        self.assertIn("Line [1], said by Steve before it at 18:22:01: “the castle by the river”. Is line [1] part",
                      qs["line_1"]["instructions"])
        self.assertEqual(list(GATHER_CRITERIA), ["part_of_the_ask", "not_part_of_it"])


class TestRoute(unittest.TestCase):
    def test_ds_walk_to_each_class_with_d_whats_confidence(self):
        let_down, fine = ("let_down_wanted_something_done", 0.6), ("fine_a_reply_is_all", 0.6)
        for answers, cls in (
                ({"d_what": ("the_ai_must_act", 0.91), "d_feel": let_down}, "action_oriented"),
                ({"d_what": ("the_ai_must_act", 0.91), "d_feel": fine}, "conversational"),
                ({"d_what": ("the_ai_must_act", 0.91), "d_feel": ("not_talking_to_the_ai", 0.6)}, "conversational"),
                ({"d_what": ("the_ai_must_act", 0.91)}, "conversational"),                  # d_feel missing
                ({"d_what": ("the_ai_should_answer", 0.91), "d_feel": let_down}, "conversational"),
                ({"d_what": ("remember_it", 0.91), "d_feel": let_down}, "context"),
                ({"d_what": ("ignore_it", 0.91)}, "not_relevant")):
            self.assertEqual(route(answers), (cls, 0.91), answers)

    def test_both_answers_in_short_for_the_journal(self):
        self.assertEqual(in_short({"d_what": ("the_ai_must_act", 0.91), "d_feel": ("let_down_wanted_something_done", 0.876)}),
                         "act 0.91, let down 0.88")
        self.assertEqual(in_short({"d_what": ("ignore_it", 0.8)}), "ignore 0.80, d_feel -")


class TestLookout(unittest.TestCase):
    def test_default_for_missing_empty_blank(self):
        d = tempdir(self)
        self.assertEqual(read_lookout(os.path.join(d, "missing.md")), (DEFAULT_LOOKOUT, False))
        for content in ("", "  \n\t\n"):
            p = os.path.join(d, "l.md")
            with open(p, "w") as f:
                f.write(content)
            self.assertEqual(read_lookout(p), (DEFAULT_LOOKOUT, False))

    def test_long_lookout_cut_to_8000_and_logged_once(self):
        d = tempdir(self)
        p = os.path.join(d, "lookout.md")
        with open(p, "w") as f:
            f.write("z" * 20000)
        lk = Lookout(p)
        with self.assertLogs("vfg-jev", "WARNING") as logs:
            self.assertEqual(lk.text(), "z" * 8000)
            self.assertEqual(lk.text(), "z" * 8000)
        self.assertEqual(len(logs.output), 1)

    def test_reread_when_the_file_changes(self):
        d = tempdir(self)
        p = os.path.join(d, "lookout.md")
        lk = Lookout(p)
        self.assertEqual(lk.text(), DEFAULT_LOOKOUT)
        with open(p, "w") as f:
            f.write("- the castle at spawn\n")
        self.assertEqual(lk.text(), "- the castle at spawn")
        with open(p, "w") as f:
            f.write("- the iron farm, and who owns it\n")
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1000000))
        self.assertEqual(lk.text(), "- the iron farm, and who owns it")
        os.remove(p)
        self.assertEqual(lk.text(), DEFAULT_LOOKOUT)

    def test_menu_missing_or_blank_is_none_and_is_reread(self):
        d = tempdir(self)
        p = os.path.join(d, "menu.md")
        menu = Menu(p)
        self.assertEqual(menu.text(), "")
        with open(p, "w") as f:
            f.write("  \n")
        self.assertEqual(menu.text(), "")
        with open(p, "w") as f:
            f.write("\ngive — gives a player items  \n\nweather — sets the weather\n")
        self.assertEqual(menu.text(), "give — gives a player items\nweather — sets the weather")

    def test_the_classes(self):
        self.assertEqual(prompt.LABELS, ("action_oriented", "conversational", "context", "not_relevant"))
        self.assertEqual(prompt.KEPT, ("action_oriented", "conversational", "context"))


if __name__ == "__main__":
    unittest.main()
