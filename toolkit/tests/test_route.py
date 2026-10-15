"""vfg route, vfg prefix, vfg action show, remote/jev.py and usage's added columns: no EC2, no network.

    python3 -m unittest discover -s toolkit/tests

harness.py's box, with a small library of engine classes (engine/library/) and one made on this
server (made/main/content/); Jev is fake_jev_route.py on localhost, named by VFG_JEV_URL in the
box's .env beside a test key, the way /opt/vfg/.env holds the real one.
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ISO_RE, REPO, TOOLKIT, Box, BoxCase, Result, vfg_module, wait_for, write  # noqa: E402
import fake_jev_route  # noqa: E402
import standins  # noqa: E402

sys.path.insert(0, os.path.join(TOOLKIT, "lib"))
import content  # noqa: E402
import menu    # noqa: E402
import prefix  # noqa: E402
import route   # noqa: E402

KEY = "ts-test-key-0123456789abcdef"
RETIRED = "T%d" % 7   # a task as it was named before named tasks: refused now

GALICK = '''"""The library's Galick Gun."""
from vfg_engine import Ability, AbilityConfig, Field


class GalickGun(Ability):
    """Galick Gun: hold to charge a beam, then fire it where you look; it leaves a crater."""

    class Config(AbilityConfig):
        summary = "A charged purple beam that leaves a crater."
        crater_radius: float = Field(4, ge=1, le=12)   # how wide the crater is

        def damage(self, ctx):
            return 10
'''
ICE_BEAM = '''from vfg_engine import Ability, AbilityConfig


class IceBeam(Ability):
    """Ice Beam: a beam that freezes what it hits and lays ice along its path."""

    class Config(AbilityConfig):
        summary = "Freezes what it hits."
'''
METEOR = '''from vfg_engine import Args, Field, Scenario


class Meteor(Scenario):
    """Meteor: a meteor falls on a place and leaves a crater."""

    class Args(Args):
        at: str = "caster"          # where it falls
        count: int = Field(1, ge=1, le=20)
'''
GIANT = '''from vfg_engine import Mark, MarkConfig


class Giant(Mark):
    class Config(MarkConfig):
        summary = "Twice as big for a while."
        duration_s = 30
'''
SPIRIT = '''from vfg_engine import Ability, AbilityConfig


class SpiritBomb(Ability):
    """Spirit Bomb: charge a sphere overhead, then throw it where you look."""

    class Config(AbilityConfig):
        summary = "A thrown sphere."
'''

LIBRARY = {
    "engine/WRITING.md": "# Writing content\nOne class a file.\n",
    "engine/KIT.md": "# The kit\nhitscan, targets, blast.\n",
    "engine/library/galick_gun.py": GALICK,
    "engine/library/ice_beam.py": ICE_BEAM,
    "engine/library/meteor.py": METEOR,
    "engine/library/giant.py": GIANT,
    "engine/library/_helpers.py": "# not a class\n",
    "engine/library/README.md": "The library.\n",
    "made/main/content/spirit_bomb.py": SPIRIT,
}


class RouteCase(BoxCase):
    """A box with a small library, this server's folder and Jev's key on the box; one fake Jev
    for the class. vfg route's usage row is written by `vfg usage add` in a process of its own;
    here it is noted instead (self.usage), but in the one test that waits for the row."""

    @classmethod
    def setUpClass(cls):
        BoxCase.setUpClass()
        cls.jev = fake_jev_route.serve(hang_seconds=5)

    @classmethod
    def tearDownClass(cls):
        cls.jev.shutdown()
        cls.jev.server_close()
        BoxCase.tearDownClass()

    def setUp(self):
        BoxCase.setUp(self)
        self.jev.mode, self.jev.canned = "ok", []
        del self.jev.calls[:]
        for rel, text in LIBRARY.items():
            self.box.write(rel, text)
        self.write_env("TYPESAFE_API_KEY=%s\nVFG_JEV_URL=%s\n" % (KEY, self.jev.url))
        mod = vfg_module()
        self.usage = []
        self.real_usage_later = mod.usage_later
        mod.usage_later = lambda cfg, ref, tokens: self.usage.append((ref, tokens))
        self.addCleanup(setattr, mod, "usage_later", self.real_usage_later)

    def write_env(self, text):
        path = os.path.join(self.box.rvfg, ".env")
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o600)

    def route(self, brief, *args, **env):
        return self.vfg("route", *args, input=brief, **env)

    def jev_program(self, args):
        """remote/jev.py run as vfg sends it, on the box here: (code, stdout, stderr) as text."""
        code, out, err = self.box.run_program(vfg_module().program("jev.py", dict(args, vfg=self.box.rvfg)))
        return Result(code, out.decode(), err.decode())

    def jev_rows(self):
        return self.box.query("SELECT component, model, input_tokens, output_tokens, cost_usd, ref"
                              " FROM usage WHERE component = 'jev'")

    def picks(self, r):
        self.assertOk(r)
        got = json.loads(r.stdout)
        return got, [s["name"] for s in got["classes"]]


# ================================================================== the cards

class TestCards(RouteCase):
    def test_cards_from_the_library_and_this_servers_content(self):
        cards = {c["name"]: c for c in route.load_cards(self.box.repo, "main")}
        self.assertEqual(sorted(cards), ["galick_gun", "giant", "ice_beam", "meteor", "spirit_bomb"])
        g = cards["galick_gun"]
        self.assertEqual(g, {"name": "galick_gun", "kind": "ability", "source": "library",
                             "path": "engine/library/galick_gun.py",
                             "first": "Galick Gun: hold to charge a beam, then fire it where you look; it leaves a "
                                      "crater."})
        self.assertEqual((cards["meteor"]["kind"], cards["giant"]["first"]), ("scenario", "Twice as big for a while."))
        self.assertEqual((cards["spirit_bomb"]["source"], cards["spirit_bomb"]["path"]),
                         ("made", "made/main/content/spirit_bomb.py"))
        # another server's folder is not read; a made class the library has is the library's
        self.assertNotIn("spirit_bomb", [c["name"] for c in route.load_cards(self.box.repo, "other")])
        self.box.write("made/main/content/ice_beam.py", ICE_BEAM.replace("Ice Beam:", "Our Ice Beam:"))
        self.assertEqual([c["source"] for c in route.load_cards(self.box.repo, "main") if c["name"] == "ice_beam"],
                         ["library"])
        # a file that is not one content class is no card
        self.box.write("engine/library/broken.py", "class Broken(:\n")
        self.box.write("engine/library/two.py", ICE_BEAM + SPIRIT)
        self.assertEqual(len(route.load_cards(self.box.repo, "main")), 5)
        c = content.Content(self.box.repo, "main").get("two")
        self.assertEqual(c.problem, "2 content classes in it, not one")

    def test_one_choice_question_per_class(self):
        cards = route.load_cards(self.box.repo, "main")
        qs = route.make_questions(cards)
        self.assertEqual(sorted(qs), ["class_galick_gun", "class_giant", "class_ice_beam", "class_meteor",
                                      "class_spirit_bomb"])
        g = qs["class_galick_gun"]
        self.assertEqual((g["type"], list(g["criteria"])), ("choice", ["runs_it", "close", "no"]))
        for words in ('Ability "galick_gun" (the library\'s): Galick Gun: hold to charge a beam',
                      "Used as it is, would this class give the players that?"):
            self.assertIn(words, g["instructions"])
        self.assertIn('Ability "spirit_bomb" (made on this server)', qs["class_spirit_bomb"]["instructions"])
        self.assertIn('Scenario "meteor"', qs["class_meteor"]["instructions"])
        self.assertIn("started with its args set for this job, granted, given", g["criteria"]["runs_it"])
        self.assertEqual(route.make_state("b", None, None), {"job": "b", "task": "", "context": ""})


# ================================================================== the picks

def card(name, kind="ability"):
    return {"name": name, "kind": kind, "source": "library", "path": "engine/library/%s.py" % name, "first": ""}


def ans(**p):
    choice = max(p, key=p.get)
    return {"choice": choice, "confidence": p[choice], "probabilities": p}


class TestPicks(unittest.TestCase):
    def test_thresholds_and_order(self):
        cards = [card("a"), card("b"), card("c"), card("d"), card("e", "scenario")]
        answers = {
            "class_a": ans(runs_it=0.15, close=0.15, no=0.70),   # 0.30 together: loaded, close
            "class_b": ans(runs_it=0.10, close=0.19, no=0.71),   # 0.29: not
            "class_c": ans(runs_it=0.80, close=0.15, no=0.05),   # runs it
            "class_d": ans(runs_it=0.60, close=0.30, no=0.10),   # runs it, less sure: after c
            "class_e": ans(runs_it=0.05, close=0.70, no=0.25),   # close, ahead of a
        }
        classes, unanswered = route.picks(cards, answers, 0.3)
        self.assertEqual([(s["name"], s["verdict"]) for s in classes],
                         [("c", "runs_it"), ("d", "runs_it"), ("e", "close"), ("a", "close")])
        self.assertEqual(unanswered, [])
        self.assertEqual((classes[0]["p_runs_it"], classes[0]["p_close"], classes[2]["kind"]), (0.8, 0.15, "scenario"))
        r = route.route(cards, "job", "x_steve", "", "", lambda s, q, b: {"answers": answers, "input_tokens": 9},
                        0.3, 3)
        self.assertEqual([s["name"] for s in r["classes"]], ["c", "d", "e"])   # at most 3, best first
        self.assertEqual((r["cut"], r["tokens"], r["fallback"]), (["a"], 9, False))
        self.assertEqual(route.log_line(dict(r, ms=5)), "route x_steve c runs_it 0.80 · d runs_it 0.60 · e close 0.70 · "
                                                        "3 loaded, 1 cut · 5ms 9tok")

    def test_confidence_alone_and_malformed_answers(self):
        cards = [card("a"), card("b"), card("p")]
        answers = {"class_a": {"choice": "no", "confidence": 0.6},    # the rest share 0.4: loaded
                   "class_b": {"choice": "maybe", "confidence": 0.9},  # not a label
                   "class_p": {"choice": "runs_it"}}                   # no confidence
        classes, unanswered = route.picks(cards, answers, 0.3)
        self.assertEqual([s["name"] for s in classes], ["a"])
        self.assertEqual([c["name"] for c in unanswered], ["b", "p"])

    def test_words_when_jev_fails_and_for_what_it_did_not_answer(self):
        cards = [card("galick_gun"), card("ice_beam"), card("meteor", "scenario")]
        cards[1]["first"] = "Ice Beam: a freeze ray"

        def down(s, q, b):
            raise route.JevFailed("jev failed: 500 internal error")
        r = route.route(cards, "make the ice beam longer", "ice_beam_alex", "", "", down, 0.3, 3)
        self.assertTrue(r["fallback"])
        self.assertEqual(r["why"], "jev failed: 500 internal error")
        self.assertEqual([(s["name"], s["verdict"]) for s in r["classes"]], [("ice_beam", "words")])
        part = {"answers": {"class_galick_gun": ans(runs_it=0.1, close=0.1, no=0.8)}, "input_tokens": 5}
        r = route.route(cards, "a meteor on the ice beam's path", "ice_beam_alex", "", "", lambda s, q, b: part, 0.3, 3)
        self.assertFalse(r["fallback"])
        self.assertEqual([s["name"] for s in r["classes"]], ["ice_beam", "meteor"])
        self.assertEqual(r["unanswered"], ["class_ice_beam", "class_meteor"])
        r = route.route(cards, "the ice beam", "ice_beam_alex", "", "", lambda s, q, b: {"answers": []}, 0.3, 3)
        self.assertTrue(r["fallback"])
        self.assertEqual(r["why"], "jev answered none of the questions")
        self.assertEqual([s["name"] for s in r["classes"]], ["ice_beam"])
        self.assertNotIn("unanswered", r)

    def test_the_log_line(self):
        r = {"task": "ice_beam_alex", "fallback": False, "ms": 312, "tokens": 11840,
             "classes": [{"name": "ice_beam", "verdict": "runs_it", "p_runs_it": 0.91, "p_close": 0.05},
                         {"name": "galick_gun", "verdict": "close", "p_runs_it": 0.1, "p_close": 0.42}],
             "cut": ["meteor"]}
        self.assertEqual(route.log_line(r), "route ice_beam_alex ice_beam runs_it 0.91 · galick_gun close 0.42 · "
                                            "2 loaded, 1 cut · 312ms 11840tok")
        r = {"task": None, "fallback": True, "why": "jev did not answer within 3 s", "ms": 3004, "tokens": 0,
             "classes": [{"name": "meteor", "verdict": "words", "p_runs_it": None, "p_close": None}]}
        self.assertEqual(route.log_line(r), "route - words (jev did not answer within 3 s): meteor · 1 loaded · "
                                            "3004ms 0tok")


# ================================================================== vfg route

class TestRoute(RouteCase):
    def test_one_call_with_the_brief_the_task_and_the_players_words(self):
        # The task's row is on the box: Jev gets its description (else its brief) and the player's words.
        self.box.add_task("galick_gun_steve", "Fire the galick gun at the mountain for Steve.", player="Steve",
                          line="fire the galick gun at the mountain", brief="a galick gun, fired at the mountain")
        self.box.add_task("ice_beam_alex", "", status="done", player="Alex", brief="an ice beam for Alex")
        vfg_module().usage_later = self.real_usage_later
        t0 = time.time()
        r = self.route("Give Steve the galick gun with no cooldown and a bigger crater.", "--task", "galick_gun_steve")
        got, classes = self.picks(r)
        self.assertEqual(classes, ["galick_gun"])
        self.assertEqual(got["classes"][0], {"name": "galick_gun", "kind": "ability", "source": "library",
                                             "verdict": "runs_it", "p_runs_it": 0.9, "p_close": 0.05,
                                             "path": "engine/library/galick_gun.py"})
        self.assertEqual((got["fallback"], got["task"]), (False, "galick_gun_steve"))
        reads = self.box.programs("list")
        self.assertEqual([(a["name"], a["open"], a["json"]) for a in reads], [("galick_gun_steve", False, True)])
        self.assertGreater(got["tokens"], 0)
        self.assertEqual(sorted(got), ["classes", "fallback", "ms", "task", "tokens"])
        self.assertEqual(len(self.jev.calls), 1)
        body = self.jev.calls[0]["body"]
        self.assertEqual(body["model"], "jev-latest")
        self.assertEqual(body["state"], {"job": "Give Steve the galick gun with no cooldown and a bigger crater.",
                                         "task": "Fire the galick gun at the mountain for Steve.",
                                         "context": "Steve said: fire the galick gun at the mountain"})
        self.assertEqual(len(body["questions"]), 5)
        self.assertEqual(self.jev.calls[0]["headers"]["Authorization"], "Bearer " + KEY)
        self.assertRegex(r.stderr, r"^route galick_gun_steve galick_gun runs_it 0\.90 · 1 loaded · \d+ms \d+tok\n$")
        self.assertNotIn(KEY, r.stdout + r.stderr)
        # The usage row, by vfg usage add in a process of its own: the route never waits for it.
        self.assertTrue(wait_for(lambda: len(self.jev_rows()) == 1, 10))
        row = self.jev_rows()[0]
        self.assertEqual((row[0], row[1], row[2], row[3], row[5]),
                         ("jev", "jev-latest", got["tokens"], 0, "galick_gun_steve"))
        self.assertAlmostEqual(row[4], got["tokens"] * 42e-9, places=12)
        self.assertLess(time.time() - t0, 10)
        # A task with no description and no player's words: its brief alone.
        self.picks(self.route("Do the job.", "--task", "ice_beam_alex"))
        self.assertEqual(self.jev.calls[-1]["body"]["state"]["task"], "an ice beam for Alex")
        self.assertEqual(self.jev.calls[-1]["body"]["state"]["context"], "")

    def test_a_task_the_box_lacks_or_cannot_read_routes_on_the_brief_alone(self):
        got, classes = self.picks(self.route("an ice beam that freezes everything", "--task", "no_such_task"))
        self.assertEqual(self.jev.calls[-1]["body"]["state"], {"job": "an ice beam that freezes everything",
                                                               "task": "", "context": ""})
        self.assertEqual(got["task"], "no_such_task")
        r = self.route("an ice beam", "--task", "no_such_task")
        self.assertRegex(r.stderr, r"^vfg: no_such_task is not a task on the box — routing on the brief alone\n")
        # A box whose bridge has not added the table, or one out of reach: a warning, then the route.
        self.box.close()
        self.box = Box(self.rcon, tasks=False)
        self.addCleanup(self.box.close)
        for rel, text in LIBRARY.items():
            self.box.write(rel, text)
        self.write_env("TYPESAFE_API_KEY=%s\nVFG_JEV_URL=%s\n" % (KEY, self.jev.url))
        r = self.route("an ice beam", "--task", "ice_beam_alex")
        got, classes = self.picks(r)
        self.assertEqual((classes, got["fallback"], got["task"]), (["ice_beam"], False, "ice_beam_alex"))
        self.assertRegex(r.stderr, r"^vfg: cannot read ice_beam_alex from the box \(vfg.db has no tasks table yet — "
                                   r"restart vfg-bridge, which adds it\) — routing on the brief alone\n")
        self.box.slow = True                # the read out of time (Jev's call, which vfg starts itself, answers)
        r = self.route("an ice beam", "--task", "ice_beam_alex")
        got, classes = self.picks(r)
        self.assertEqual(classes, ["ice_beam"])
        self.assertRegex(r.stderr, r"^vfg: cannot read ice_beam_alex from the box \(EC2 did not answer within 1\.5 s\) "
                                   r"— routing on the brief alone\n")

    def test_jev_gets_what_is_left_of_the_budget_after_the_tasks_read(self):
        self.box.add_task("galick_gun_steve", "A galick gun for Steve.", player="Steve")
        mod = vfg_module()
        budgets = []
        real = mod.jev_ask
        mod.jev_ask = lambda cfg, st, qs, b, said: budgets.append((b, said)) or real(cfg, st, qs, b, said)
        self.addCleanup(setattr, mod, "jev_ask", real)
        got, classes = self.picks(self.route("the galick gun", "--task", "galick_gun_steve", VFG_ROUTE_TIMEOUT_S="2"))
        self.assertFalse(got["fallback"])
        self.assertEqual(len(budgets), 1)
        self.assertTrue(1.0 < budgets[0][0] < 2.0, budgets)   # 2 s less the read and the cards
        self.assertEqual(budgets[0][1], 2.0)                    # what a late answer says

    def test_classes_made_on_this_server_are_cards_too_and_close_ones_follow(self):
        got, classes = self.picks(self.route("A spirit bomb that leaves ice where it lands, for Alex."))
        self.assertEqual(classes, ["spirit_bomb", "ice_beam"])     # "ice" is a word of ice_beam: close at 0.6
        self.assertEqual([(c["source"], c["verdict"]) for c in got["classes"]], [("made", "runs_it"),
                                                                                 ("library", "close")])
        self.assertEqual(got["classes"][0]["path"], "made/main/content/spirit_bomb.py")
        self.assertIsNone(got["task"])
        self.assertEqual(self.usage, [(None, got["tokens"])])

    def test_words_when_jev_hangs_within_the_timeout(self):
        self.jev.mode = "hang"
        t0 = time.time()
        r = self.route("Make the galick gun bigger.", "--task", "galick_gun_alex", VFG_ROUTE_TIMEOUT_S="0.15")
        got, classes = self.picks(r)
        self.assertLess(time.time() - t0, 1.5)
        self.assertTrue(got["fallback"])
        self.assertEqual(got["why"], "jev did not answer within 0.15 s")
        self.assertEqual(classes, ["galick_gun"])
        self.assertEqual(got["classes"][0]["verdict"], "words")
        self.assertIsNone(got["classes"][0]["p_runs_it"])
        self.assertRegex(r.stderr, r"galick_gun_alex is not a task on the box — routing on the brief alone")
        self.assertRegex(r.stderr, r"route galick_gun_alex words \(jev did not answer within 0.15 s\): galick_gun · "
                                   r"1 loaded")
        # It may still have reached Jev, so its estimated size is the row.
        self.assertEqual(self.usage[0][0], "galick_gun_alex")
        self.assertGreater(self.usage[0][1], 0)

    def test_words_on_any_error_at_once(self):
        cases = [({}, "fail", r"^jev failed: 500 internal error$"),
                 ({}, "refuse", r"^jev refused the request: 400 bad request for key \*\*\*$"),
                 ({"down": True}, "ok", r"^cannot reach EC2"),
                 ({"config": ""}, "ok", r"^not configured")]
        for how, mode, why in cases:
            self.jev.mode = mode
            self.box.down = how.get("down", False)
            if "config" in how:
                self.box.write_config(how["config"])
            t0 = time.time()
            got, classes = self.picks(self.route("an ice beam that freezes everything"))
            self.assertLess(time.time() - t0, 3, mode)
            self.assertTrue(got["fallback"])
            self.assertRegex(got["why"], why)
            self.assertEqual(classes, ["ice_beam"])  # named; galick_gun shares only "beam", in its first line

    def test_no_key_on_the_box_and_bad_arguments(self):
        self.write_env("VFG_JEV_URL=%s\n" % self.jev.url)
        got, classes = self.picks(self.route("the ice beam"))
        self.assertTrue(got["fallback"])
        self.assertRegex(got["why"], r"^not configured: no TYPESAFE_API_KEY in .*/\.env$")
        before = len(self.box.calls)
        for args, inp in ((["--task", "7"], "x"), (["--task", RETIRED], "x"), (["--task", "cast"], "x"),
                          (["--task", "Galick"], "x"), (["--ref", "x" * 65], "x"), ([], "  \n")):
            self.assertOneLine(self.route(inp, *args), 2)
        self.assertEqual(len(self.box.calls), before)
        self.assertOneLine(self.route("x", VFG_ROUTE_LOAD="2"), 3, r"VFG_ROUTE_LOAD")
        self.assertEqual(self.jev.calls, [])

    def test_the_ref_and_an_empty_library(self):
        self.assertOk(self.route("anything", "--ref", "route-eval"))
        self.assertEqual(self.usage[0][0], "route-eval")
        empty = os.path.join(self.box.root, "empty")
        os.makedirs(empty)
        calls = len(self.jev.calls)
        got, classes = self.picks(self.route("anything", VFG_REPO=empty))
        self.assertEqual((classes, got["fallback"]), ([], False))
        self.assertEqual(len(self.jev.calls), calls)


# ================================================================== jev.py

class TestJevProgram(RouteCase):
    def q(self, name, size=10):
        return {"type": "choice", "instructions": 'Example script "%s": %s' % (name, "x" * size),
                "criteria": {"runs_it": "a", "close": "b", "no": "c"}}

    def test_answers_tokens_and_time(self):
        r = self.jev_program({"state": {"job": "the ice_beam"}, "timeout": 3,
                                  "questions": {"script_ice_beam": self.q("ice_beam"), "script_x": self.q("x")}})
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got["answers"]["script_ice_beam"]["choice"], "runs_it")
        self.assertEqual(got["answers"]["script_x"]["choice"], "no")
        self.assertGreater(got["input_tokens"], 0)
        self.assertGreater(got["estimated_tokens"], 0)
        self.assertEqual(len(got["calls"]), 1)
        self.assertEqual(got["calls"][0]["questions"], 2)
        self.assertIsInstance(got["ms"], int)

    def test_questions_past_64k_go_in_several_calls_at_once_each_with_the_whole_state(self):
        state = {"job": "the ice_beam " + "y" * 3000}
        qs = {"script_s%02d" % i: self.q("s%02d" % i, 6000) for i in range(40)}   # ~80k tokens
        r = self.jev_program({"state": state, "questions": qs, "timeout": 5})
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        calls = self.jev.calls
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(got["calls"]), 2)
        for c in calls:
            self.assertEqual(c["body"]["state"], state)
        sent = [n for c in calls for n in c["body"]["questions"]]
        self.assertEqual(sorted(sent), sorted(qs))
        self.assertEqual(sorted(got["answers"]), sorted(qs))
        self.assertEqual(got["input_tokens"], sum(c["input_tokens"] for c in got["calls"]))
        for c in calls:
            body = json.dumps(c["body"], ensure_ascii=False).encode("utf-8")
            self.assertLessEqual(len(body) / 3.0, 56000)

    def test_a_state_and_question_surely_past_32k_are_refused_before_anything_is_sent(self):
        r = self.jev_program({"state": {"job": "z" * 190000}, "timeout": 3,
                                  "questions": {"script_a": self.q("a", 10000)}})
        self.assertEqual(r.returncode, 1)
        self.assertRegex(r.stderr.strip(), r"^vfg: the state and its longest question are at least \d+ tokens; "
                                           r"Jev takes at most 32000 — nothing was cut and nothing was sent$")
        self.assertEqual(self.jev.calls, [])

    def test_one_that_may_be_past_32k_is_sent_and_a_400_says_why_it_may_be(self):
        self.jev.mode = "refuse"
        r = self.jev_program({"state": {"job": "z" * 100000}, "timeout": 3,
                                  "questions": {"script_a": self.q("a")}})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(self.jev.calls), 1)
        self.assertRegex(r.stderr.strip(), r"400 .* — the state and its longest question may be over its "
                                           r"32000 tokens \(about \d+\)$")

    def test_the_key_is_never_printed(self):
        self.jev.mode = "refuse"
        r = self.jev_program({"state": {"job": "x"}, "questions": {"script_a": self.q("a")}})
        self.assertEqual(r.returncode, 1)
        self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertIn("***", r.stderr)

    def test_bad_args(self):
        for args in ({"state": "text", "questions": {"a": self.q("a")}}, {"state": {}, "questions": {}},
                     {"state": {}, "questions": {"a": "not a question"}}):
            r = self.jev_program(args)
            self.assertEqual(r.returncode, 2, r.stderr)


# ================================================================== vfg prefix

class TestPrefix(RouteCase):
    def test_the_same_bytes_every_time_and_made_classes_change_nothing(self):
        r1, r2 = self.vfg("prefix"), self.vfg("prefix")
        self.assertOk(r1)
        self.assertEqual(r1.stdout, r2.stdout)
        self.assertEqual(r1.stdout, prefix.build(self.box.repo))
        text = r1.stdout
        self.box.write("made/main/content/brand_new.py", SPIRIT.replace("SpiritBomb", "BrandNew"))
        self.assertEqual(self.vfg("prefix").stdout, text)
        self.assertNotIn("spirit_bomb", text)
        self.assertNotIn(self.box.root, text)
        # how a class is written, then the kit, whole; then the library by id, its Config and Args as written
        self.assertTrue(text.startswith(prefix.HEAD_HOW + "\n=== engine/WRITING.md ===\n# Writing content\n"))
        self.assertLess(text.index("=== engine/WRITING.md ==="), text.index("=== engine/KIT.md ===\n# The kit\n"))
        lib = text[text.index(prefix.HEAD_LIBRARY):]
        self.assertIn("=== galick_gun (ability) — Galick Gun: hold to charge a beam, then fire it where you look; it "
                      "leaves a crater. ===\n    class Config(AbilityConfig):\n", lib)
        self.assertIn("        crater_radius: float = Field(4, ge=1, le=12)   # how wide the crater is\n", lib)
        self.assertIn("=== meteor (scenario) — Meteor: a meteor falls on a place and leaves a crater. ===\n"
                      "    class Args(Args):\n        at: str = \"caster\"          # where it falls\n", lib)
        names = [l.split(" ")[1] for l in lib.splitlines() if l.startswith("=== ")]
        self.assertEqual(names, ["galick_gun", "giant", "ice_beam", "meteor"])
        # a library change is a new prefix
        self.box.write("engine/library/galick_gun.py", GALICK.replace("Field(4,", "Field(5,"))
        self.assertNotEqual(self.vfg("prefix").stdout, text)

    def test_with_no_library_yet_it_says_so(self):
        empty = os.path.join(self.box.root, "repo2")
        write(os.path.join(empty, "engine", "WRITING.md"), "# Writing\n")
        r = self.vfg("prefix", VFG_REPO=empty)
        self.assertOk(r)
        self.assertTrue(r.stdout.endswith("\n" + prefix.NO_LIBRARY + "\n"), r.stdout)
        self.assertNotIn(prefix.HEAD_KIT, r.stdout)        # no KIT.md: no kit part


class TestActionShow(RouteCase):
    def setUp(self):
        RouteCase.setUp(self)
        standins.install(self.box.repo)

    def cache(self):
        with open(os.path.join(self.box.vhome, "actions.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_an_action_in_full_exactly_as_the_brain_sees_it(self):
        r = self.vfg("action", "show", "engine_apply")
        self.assertOk(r)
        self.assertEqual(r.stderr, "")
        entry = self.cache()["actions"]["engine_apply"]
        self.assertEqual(r.stdout, entry["describe"] + "\n")
        self.assertEqual(r.stdout, menu.describe("engine_apply", entry) + "\n")
        lines = r.stdout.splitlines()
        self.assertTrue(lines[0].startswith("engine_apply — Put one of the engine's marks on a player"))
        self.assertEqual(lines[1:3], ["engine apply", "values (* = the brain fills it):"])
        self.assertIn("* args (the class's args, each by its name as ENGINE lists them; default {}): its args, each "
                      "by its name as ENGINE lists them", lines)
        self.assertIn("* seconds (a number 0..86400; default 0): how long it lasts, in seconds; 0 for the mark's own "
                      "duration", lines)
        self.assertEqual(self.vfg("action", "show", "engine_bind").stdout.splitlines()[1], "engine bind · timeout 40 s")

    def test_one_saved_a_moment_ago_is_there(self):
        self.assertOk(self.vfg("action", "show", "engine_grant"))
        src = os.path.join(self.box.repo, "actions", "engine_grant.py")
        with open(src) as f:
            text = f.read()
        path = self.box.write("actions/engine_grant_twice.py", text.replace("class EngineGrant", "class EngineGrantTwice"))
        os.utime(path, (time.time() + 5, time.time() + 5))
        r = self.vfg("action", "show", "engine_grant_twice")
        self.assertOk(r)
        self.assertTrue(r.stdout.startswith("engine_grant_twice — Give a player one of the engine's abilities"))

    def test_an_unknown_name_says_the_names_near_it_and_a_broken_one_why(self):
        self.assertOneLine(self.vfg("action", "show", "engine_grnt"), 1,
                           r"^vfg: no action engine_grnt in actions/; near it: engine_grant, ")   # the nearest first
        self.assertOneLine(self.vfg("action", "show", "zzzz"), 1,
                           r"^vfg: no action zzzz in actions/ — vfg action list names them$")
        self.box.write("actions/broken.py", "def broken(:\n")
        self.assertOneLine(self.vfg("action", "show", "broken"), 1,
                           r"^vfg: action broken did not load: .*SyntaxError.* — vfg action check broken$")
        self.assertEqual(self.vfg("action", "show").returncode, 2)


# ================================================================== usage

class TestUsageCache(BoxCase):
    def cache_columns(self):
        con = __import__("sqlite3").connect(self.box.db)
        con.execute("ALTER TABLE usage ADD COLUMN cache_read_tokens INTEGER")
        con.execute("ALTER TABLE usage ADD COLUMN cache_write_tokens INTEGER")
        con.commit()
        con.close()

    def test_cache_counts_are_stored(self):
        self.cache_columns()
        r = self.vfg("usage", "add", "--component", "implementer", "--model", "haiku", "--in", "10",
                         "--out", "5", "--cache-read", "9000", "--cache-write", "400", "--ref", "galick_gun_steve")
        self.assertOk(r)
        self.assertEqual(r.stderr, "")
        r = self.vfg("usage", "add", "--component", "jev", "--model", "jev-latest", "--in", "1", "--out", "0")
        self.assertOk(r)
        self.assertEqual(self.box.query("SELECT input_tokens, cache_read_tokens, cache_write_tokens, ref FROM usage"),
                         [(10, 9000, 400, "galick_gun_steve"), (1, None, None, None)])

    def test_before_the_bridge_adds_the_columns_the_row_still_lands(self):
        r = self.vfg("usage", "add", "--component", "implementer", "--model", "haiku", "--in", "10",
                         "--out", "5", "--cache-read", "9000")
        self.assertOk(r)
        self.assertEqual(r.stdout.strip(), "1")
        self.assertRegex(r.stderr, r"^vfg: cache counts of 1 row\(s\) not stored: usage has no cache columns yet")
        self.assertEqual(self.box.query("SELECT input_tokens FROM usage"), [(10,)])

    def test_the_largest_request_is_stored(self):
        self.cache_columns()
        con = __import__("sqlite3").connect(self.box.db)
        con.execute("ALTER TABLE usage ADD COLUMN largest_tokens INTEGER")
        con.commit()
        con.close()
        r = self.vfg("usage", "add", "--component", "implementer", "--model", "deepseek-flash", "--in", "4000",
                     "--out", "900", "--cache-read", "310000", "--largest", "104322", "--ref", "hot_fireball_steve")
        self.assertOk(r)
        self.assertEqual(r.stderr, "")
        self.assertOk(self.vfg("usage", "add", "--component", "jev", "--model", "jev-latest", "--in", "1", "--out", "0"))
        self.assertEqual(self.box.query("SELECT component, cache_read_tokens, largest_tokens FROM usage ORDER BY id"),
                         [("implementer", 310000, 104322), ("jev", None, None)])

    def test_before_the_bridge_adds_largest_tokens_the_row_still_lands(self):
        self.cache_columns()
        r = self.vfg("usage", "add", "--component", "implementer", "--model", "deepseek-flash", "--in", "10",
                     "--out", "5", "--cache-read", "9000", "--largest", "9010")
        self.assertOk(r)
        self.assertEqual(r.stderr, "vfg: largest requests of 1 row(s) not stored: usage has no largest_tokens column "
                                   "yet — restart vfg-bridge, which adds them\n")
        self.assertEqual(self.box.query("SELECT input_tokens, cache_read_tokens FROM usage"), [(10, 9000)])

    def test_bad_cache_counts(self):
        self.assertOneLine(self.vfg("usage", "add", "--component", "implementer", "--model", "m", "--in", "1",
                                    "--out", "1", "--largest", "-1"), 2, r"--largest is a whole number, 0 or more")
        for flag in ("--cache-read", "--cache-write"):
            r = self.vfg("usage", "add", "--component", "jev", "--model", "m", "--in", "1", "--out", "1",
                             flag, "-1")
            self.assertOneLine(r, 2)


class TestCallRows(BoxCase):
    """The row lib/py/vfg_llm.py queues for every structured() call: the row `vfg usage add`
    queues, with ok, error and ms after it, sent by `vfg usage flush`."""

    def llm(self):
        lib = os.path.join(REPO, "lib", "py")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import vfg_llm
        return vfg_llm

    def columns(self, *names):
        con = __import__("sqlite3").connect(self.box.db)
        for name, kind in (("cache_read_tokens", "INTEGER"), ("cache_write_tokens", "INTEGER"), ("ok", "INTEGER"),
                           ("error", "TEXT"), ("ms", "INTEGER")):
            if name in names:
                con.execute("ALTER TABLE usage ADD COLUMN %s %s" % (name, kind))
        con.commit()
        con.close()

    def queue(self):
        """Two calls' rows, as structured() writes them: a failed planner call and an alias call."""
        llm = self.llm()
        with self.box.environ() as vfg:
            a = __import__("argparse").Namespace(component="brain", model="m", in_=1, out=2, cost=0.5,
                                                 ref="castle_steve", cache_read=None, cache_write=None)
            added = vfg.usage_row(a, time.time())
            failed = llm.usage_row("planner", "castle_steve #2", "~deepseek/x", 0.0031, 2400, 900, 0, 40,
                                   error="~deepseek/x twice failed to match Planned_command: - action: …")
            answered = llm.usage_row(llm.Component.ALIAS, "fire ball", "~deepseek/x", 0.00005, 800, 300, 256, 20,
                                     error=None)
            self.assertEqual(list(failed), list(added) + ["ok", "error", "ms"])
            self.assertRegex(failed["at"], ISO_RE)
            llm.record(failed)
            llm.record(answered)
        self.assertEqual(self.box.calls, [])  # queued: nothing reached the box

    def test_flushed_with_every_column(self):
        self.columns("cache_read_tokens", "cache_write_tokens", "ok", "error", "ms")
        self.queue()
        r = self.vfg("usage", "flush")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "2\n", ""))
        self.assertEqual(self.box.query("SELECT component, model, input_tokens, output_tokens, cost_usd, ref, "
                                        "cache_read_tokens, cache_write_tokens, ok, error, ms FROM usage ORDER BY id"), [
            ("planner", "~deepseek/x", 900, 40, 0.0031, "castle_steve #2", 0, None, 0,
             "~deepseek/x twice failed to match Planned_command: - action: …", 2400),
            ("alias", "~deepseek/x", 300, 20, 0.00005, "fire ball", 256, None, 1, None, 800)])

    def test_before_the_bridge_adds_the_columns_the_rows_still_land(self):
        self.queue()
        r = self.vfg("usage", "flush")
        self.assertEqual((r.returncode, r.stdout), (0, "2\n"))
        self.assertEqual(r.stderr.splitlines(), [
            "vfg: cache counts of 2 row(s) not stored: usage has no cache columns yet "
            "— restart vfg-bridge, which adds them",
            "vfg: call outcomes of 2 row(s) not stored: usage has no ok, error and ms columns yet "
            "— restart vfg-bridge, which adds them"])
        self.assertEqual(self.box.query("SELECT component, cost_usd FROM usage ORDER BY id"),
                         [("planner", 0.0031), ("alias", 0.00005)])

    def before_the_router(self):
        """usage as a box has it before its bridge is redeployed with the router: no 'router' in its CHECK."""
        con = __import__("sqlite3").connect(self.box.db)
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'usage'").fetchone()[0]
        self.assertIn(",'router'", sql)
        con.execute("DROP TABLE usage")
        con.execute(sql.replace(",'router'", ""))
        con.commit()
        con.close()

    def test_a_row_the_box_refuses_is_skipped_named_and_dropped_and_the_rest_are_stored(self):
        self.before_the_router()
        self.columns("cache_read_tokens", "cache_write_tokens", "ok", "error", "ms")
        llm = self.llm()
        with self.box.environ():
            for component, ref in (("respond", "#58"), ("router", "#58"), ("brain", "#58 s1"), ("router", "#60")):
                llm.record(llm.usage_row(component, ref, "deepseek/deepseek-v4.1-flash", 0.0005, 800, 27000, 26000,
                                         60, error=None))
        r = self.vfg("usage", "flush")
        self.assertEqual((r.returncode, r.stdout), (0, ""))   # the last row, the call's own, was refused
        self.assertEqual(r.stderr, "vfg: 2 usage row(s) of component router refused by the box's usage table, not "
                                   "stored — redeploy vfg-bridge, whose migration allows it\n")
        self.assertEqual(self.box.query("SELECT component, ref FROM usage ORDER BY id"),
                         [("respond", "#58"), ("brain", "#58 s1")])
        self.assertFalse(os.path.exists(os.path.join(self.box.vhome, "usage.pending.jsonl")))  # never requeued
        r = self.vfg("usage", "add", "--component", "jev", "--model", "jev-latest", "--in", "1", "--out", "0")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "3\n", ""))  # the queue no longer blocks
        r = self.vfg("usage", "add", "--component", "router", "--model", "m", "--in", "1", "--out", "0")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIn("1 usage row(s) of component router refused", r.stderr)
        self.assertEqual(self.box.query("SELECT COUNT(*) FROM usage"), [(3,)])

    def test_vfg_usage_queue_is_the_queue_both_sides_use_and_a_cost_may_be_unknown(self):
        """A replay's rows queue in its own folder: `vfg usage flush` sends them only with the same
        VFG_USAGE_QUEUE, and never from $VFG_HOME. A call cut off with an attempt in flight has no
        cost: NULL in the table, and what came back said in its error."""
        self.columns("cache_read_tokens", "cache_write_tokens", "ok", "error", "ms")
        llm = self.llm()
        out = os.path.join(self.box.home, "replay", "out")
        cut = llm.usage_row("brain", "replay #812", "openai/gpt-6.1-sol", None, 30004, 9000, 8800, 0,
                            error="no answer from openai/gpt-6.1-sol within 30 s; $0.002100 spent before it, "
                                  "the attempt in flight not counted")
        with self.box.environ(VFG_USAGE_QUEUE=out):
            llm.record(cut)
        self.assertEqual([n for n in os.listdir(self.box.vhome) if n.startswith("usage.")], [])
        self.assertTrue(os.path.exists(os.path.join(out, "usage.pending.jsonl")))
        r = self.vfg("usage", "flush")  # the live queue: nothing there, nothing sent
        self.assertEqual((r.returncode, r.stdout, r.stderr, self.box.calls), (0, "", "", []))
        self.box.down = True
        self.assertOneLine(self.vfg("usage", "flush", VFG_USAGE_QUEUE=out), 4, "cannot reach EC2")
        with open(os.path.join(out, "usage.pending.jsonl")) as f:
            self.assertEqual(len(f.read().splitlines()), 1)  # kept there
        self.box.down = False
        r = self.vfg("usage", "flush", VFG_USAGE_QUEUE=out)
        self.assertEqual((r.returncode, r.stdout), (0, "1\n"), r.stderr)
        self.assertEqual(self.box.query("SELECT component, cost_usd, ref, ok, error, ms FROM usage"), [
            ("brain", None, "replay #812", 0, "no answer from openai/gpt-6.1-sol within 30 s; $0.002100 spent "
                                               "before it, the attempt in flight not counted", 30004)])
        self.assertEqual([n for n in os.listdir(out) if n.startswith("usage.") and n != "usage.pending.lock"], [])


if __name__ == "__main__":
    unittest.main()
