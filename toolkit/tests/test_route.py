"""vfg route, vfg prefix, remote/jev.py and usage's cache columns: no EC2, no network.

    python3 -m unittest discover -s toolkit/tests -p 'test_route.py'

The box is test_vfg's Box; Jev is fake_jev_route.py on localhost, named by
VFG_JEV_URL in the box's .env beside a test key, the way /opt/vfg/.env holds
the real one.
"""
import json
import os
import subprocess
import sys
import time
import unittest

from test_vfg import PY, TOOLKIT, Box, Case, wait_for
import fake_jev_route

sys.path.insert(0, os.path.join(TOOLKIT, "lib"))
import prefix  # noqa: E402
import route   # noqa: E402

KEY = "ts-test-key-0123456789abcdef"

CRATER = '''"""Carve a sphere out of the world at a place, leaving air, lava or fire."""
from kit import area

VALUES = {
    "crater_radius": {"type": "int", "min": 2, "max": 64, "default": 8, "live": True},
    "crater_leave":  {"type": "enum", "of": ["air", "lava", "fire"], "default": "air", "live": True},
}


def crater(at, v):
    area.sphere(at, v["crater_radius"], block=v["crater_leave"], spare_protected=True)
'''

FREEZE = '''"""Stop what was hit from moving, and wrap it in ice if asked."""
VALUES = {
    "freeze_seconds": {"type": "number", "min": 0, "max": 60, "default": 5},
}


def freeze(hits, v):
    pass
'''

THEME = '''"""A theme tune for everyone or for one player, cancelled when a newer one starts."""
VALUES = {
    "theme_to": {"type": "player", "default": "@a"},
}


def theme_music(v):
    pass
'''

CHARGE = '''"""A charge before what comes next: orbs at the hands, a sphere overhead or an aura."""
VALUES = {
    "charge_seconds": {"type": "number", "min": 0, "max": 10, "default": 2},
}


def charge_up(p, v):
    pass
'''

GALICK = '''"""Galick Gun: hold to charge a beam, then fire it where you look; it leaves a crater."""
ASKS = ["galick gun", "a purple beam"]
from kit import values, loop

VALUES = {
    "player": {"type": "player", "default": "@a"},
    "cooldown": {"type": "number", "min": 0, "max": 60, "default": 10, "live": True},
    "crater_radius": {"type": "int", "min": 2, "max": 64, "default": 8, "live": True},
    "theme_to": {"type": "player", "default": "@a"},
}

# --- preset: crater (copied from _presets/crater.py) ---
def crater(at, v):
    pass

# --- preset: theme_music (copied from _presets/theme_music.py) ---
def theme_music(v):
    pass

v = values(VALUES)
'''

ICE_BEAM = '''"""Ice Beam: a beam that freezes what it hits and lays ice along its path."""
ASKS = ["ice beam", "freeze ray"]
VALUES = {
    "length": {"type": "int", "min": 1, "max": 300, "default": 60},
    "freeze_seconds": {"type": "number", "min": 0, "max": 60, "default": 5},
}

# --- preset: freeze (copied from _presets/freeze.py) ---
def freeze(hits, v):
    pass
'''

SPIRIT = '''"""Spirit Bomb: charge a sphere overhead, then throw it where you look."""
ASKS = ["spirit bomb", "genki dama"]
VALUES = {
    "charge": {"type": "number", "min": 1, "max": 10, "default": 3},
}
'''


class RouteBox(Box):
    """test_vfg's box, with a small library, this server's folder and a key on the box."""

    def __init__(self, **kw):
        Box.__init__(self, **kw)
        lib = {
            "scripts/_presets/crater.py": CRATER,
            "scripts/_presets/freeze.py": FREEZE,
            "scripts/_presets/theme_music.py": THEME,
            "scripts/galick_gun/main.py": GALICK,
            "scripts/ice_beam/main.py": ICE_BEAM,
            "scripts/_lib/kit/__init__.py": '"""The kit."""\n',
            "scripts/_lib/kit/aim.py": '"""Aiming: the eyes at +1.62."""\nEYES = 1.62\n',
            "made/main/scripts/spirit_bomb/main.py": SPIRIT,
            "made/main/presets/charge_up.py": CHARGE,
        }
        for rel, text in lib.items():
            self.write(rel, text)
        self.jev = fake_jev_route.serve(hang_seconds=5)
        self.write_env("TYPESAFE_API_KEY=%s\nVFG_JEV_URL=%s\n" % (KEY, self.jev.url))

    def write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def write_env(self, text):
        path = os.path.join(self.rvfg, ".env")
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o600)

    def close(self):
        self.jev.shutdown()
        self.jev.server_close()
        Box.close(self)

    def route(self, brief, *args, **env):
        return self.vfg("route", *args, input=brief, **env)

    def jev_program(self, args):
        """remote/jev.py run as vfg sends it, here: common.py, ARGS, the program."""
        remote = os.path.join(TOOLKIT, "remote")
        with open(os.path.join(remote, "common.py")) as f:
            common = f.read()
        with open(os.path.join(remote, "jev.py")) as f:
            body = f.read()
        args = dict(args)
        args.setdefault("vfg", self.rvfg)
        text = common + "\nARGS = json.loads(" + repr(json.dumps(args)) + ")\n\n" + body
        return subprocess.run([PY, "-"], input=text, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=60)


class RouteCase(Case):
    def setUp(self):
        self.box = RouteBox()

    def jev_rows(self):
        return self.box.query("SELECT component, model, input_tokens, output_tokens, cost_usd, ref"
                              " FROM usage WHERE component = 'jev'")

    def picks(self, r):
        self.assertOk(r)
        got = json.loads(r.stdout)
        return got, [s["name"] for s in got["scripts"]], [p["name"] for p in got["presets"]]


# ================================================================== the cards

class TestCards(RouteCase):
    def test_cards_from_the_library_and_this_servers_folder(self):
        cards = {(c["kind"], c["name"]): c for c in route.load_cards(self.box.repo, "main")}
        self.assertEqual(sorted(cards), [
            ("preset", "charge_up"), ("preset", "crater"), ("preset", "freeze"), ("preset", "theme_music"),
            ("script", "galick_gun"), ("script", "hello"), ("script", "ice_beam"), ("script", "loop"),
            ("script", "spirit_bomb")])
        g = cards[("script", "galick_gun")]
        self.assertEqual((g["source"], g["path"]), ("library", "scripts/galick_gun/main.py"))
        self.assertEqual(g["presets"], ["crater", "theme_music"])
        self.assertEqual(g["asks"], ["galick gun", "a purple beam"])
        self.assertEqual(cards[("script", "spirit_bomb")]["source"], "made")
        self.assertEqual(cards[("preset", "charge_up")]["path"], "made/main/presets/charge_up.py")
        # a seed script's first line is its README's
        self.assertEqual(cards[("script", "hello")]["first"], "Says hello and exits.")
        # another server's folder is not read
        self.assertNotIn(("script", "spirit_bomb"),
                         {(c["kind"], c["name"]) for c in route.load_cards(self.box.repo, "other")})

    def test_one_choice_question_per_script_and_per_preset(self):
        cards = route.load_cards(self.box.repo, "main")
        qs = route.make_questions(cards)
        self.assertEqual(len(qs), len(cards))
        g = qs["script_galick_gun"]
        self.assertEqual(g["type"], "choice")
        self.assertEqual(list(g["criteria"]), ["runs_it", "close", "no"])
        for words in ('Example script "galick_gun" (ours)', "Galick Gun: hold to charge a beam",
                      '"galick gun", "a purple beam"', "cooldown, crater_radius, player, theme_to",
                      "Built from the presets: crater, theme_music", "Does this script do what the job asks?"):
            self.assertIn(words, g["instructions"])
        c = qs["preset_charge_up"]
        self.assertEqual(list(c["criteria"]), ["use", "no"])
        self.assertIn('Preset "charge_up" (made on this server)', c["instructions"])
        self.assertIn("Is this preset part of what the job needs?", c["instructions"])
        self.assertEqual(route.make_state("b", None, None), {"job": "b", "task": "", "context": ""})


# ================================================================== the picks

def card(name, kind="script", presets=()):
    return {"name": name, "kind": kind, "source": "library", "path": "x/%s" % name, "first": "",
            "asks": [], "values": {}, "presets": list(presets)}


def ans(**p):
    choice = max(p, key=p.get)
    return {"choice": choice, "confidence": p[choice], "probabilities": p}


class TestPicks(unittest.TestCase):
    def test_thresholds_and_order(self):
        cards = [card("a"), card("b"), card("c"), card("d"), card("e"),
                 card("p1", "preset"), card("p2", "preset"), card("p3", "preset")]
        answers = {
            "script_a": ans(runs_it=0.15, close=0.15, no=0.70),   # 0.30 together: loaded, close
            "script_b": ans(runs_it=0.10, close=0.19, no=0.71),   # 0.29: not
            "script_c": ans(runs_it=0.80, close=0.15, no=0.05),   # runs it
            "script_d": ans(runs_it=0.60, close=0.30, no=0.10),   # runs it, less sure: after c
            "script_e": ans(runs_it=0.05, close=0.70, no=0.25),   # close, ahead of a
            "preset_p1": ans(use=0.30, no=0.70),
            "preset_p2": ans(use=0.29, no=0.71),
            "preset_p3": ans(use=0.95, no=0.05),
        }
        scripts, presets, unanswered = route.picks(cards, answers, 0.3)
        self.assertEqual([(s["name"], s["verdict"]) for s in scripts],
                         [("c", "runs_it"), ("d", "runs_it"), ("e", "close"), ("a", "close")])
        self.assertEqual([p["name"] for p in presets], ["p3", "p1"])
        self.assertEqual(unanswered, [])
        self.assertEqual(scripts[0]["p_runs_it"], 0.8)
        self.assertEqual(scripts[0]["p_close"], 0.15)

    def test_confidence_alone_and_malformed_answers(self):
        cards = [card("a"), card("b"), card("p", "preset")]
        answers = {"script_a": {"choice": "no", "confidence": 0.6},    # the rest share 0.4: loaded
                   "script_b": {"choice": "maybe", "confidence": 0.9},  # not a label
                   "preset_p": {"choice": "use"}}                      # no confidence
        scripts, presets, unanswered = route.picks(cards, answers, 0.3)
        self.assertEqual([s["name"] for s in scripts], ["a"])
        self.assertEqual([c["name"] for c in unanswered], ["b", "p"])

    def test_a_preset_a_loaded_script_holds_is_not_loaded_again(self):
        cards = [card("galick_gun", presets=["crater"]), card("ice_beam", presets=["freeze"]),
                 card("crater", "preset"), card("freeze", "preset")]
        answers = {"script_galick_gun": ans(runs_it=0.9, close=0.05, no=0.05),
                   "script_ice_beam": ans(runs_it=0.05, close=0.05, no=0.9),
                   "preset_crater": ans(use=0.9, no=0.1), "preset_freeze": ans(use=0.8, no=0.2)}
        r = route.route(cards, "job", "T7", "", "", lambda s, q, b: {"answers": answers, "input_tokens": 9},
                        0.3, 3)
        self.assertEqual([s["name"] for s in r["scripts"]], ["galick_gun"])
        self.assertEqual([p["name"] for p in r["presets"]], ["freeze"])
        self.assertFalse(r["fallback"])
        self.assertEqual(r["tokens"], 9)

    def test_words_when_jev_fails_and_for_what_it_did_not_answer(self):
        cards = [card("galick_gun"), card("ice_beam"), card("crater", "preset")]
        cards[1]["asks"] = ["freeze ray"]

        def down(s, q, b):
            raise route.JevFailed("jev failed: 500 internal error")
        r = route.route(cards, "make the freeze ray longer", "T3", "", "", down, 0.3, 3)
        self.assertTrue(r["fallback"])
        self.assertEqual(r["why"], "jev failed: 500 internal error")
        self.assertEqual([(s["name"], s["verdict"]) for s in r["scripts"]], [("ice_beam", "words")])
        self.assertEqual(r["presets"], [])
        part = {"answers": {"script_galick_gun": ans(runs_it=0.1, close=0.1, no=0.8)}, "input_tokens": 5}
        r = route.route(cards, "a crater under the freeze ray", "T3", "", "", lambda s, q, b: part, 0.3, 3)
        self.assertFalse(r["fallback"])
        self.assertEqual([s["name"] for s in r["scripts"]], ["ice_beam"])
        self.assertEqual([p["name"] for p in r["presets"]], ["crater"])
        self.assertEqual(r["unanswered"], ["script_ice_beam", "preset_crater"])
        r = route.route(cards, "the freeze ray", "T3", "", "", lambda s, q, b: {"answers": []}, 0.3, 3)
        self.assertTrue(r["fallback"])
        self.assertEqual(r["why"], "jev answered none of the questions")
        self.assertEqual([s["name"] for s in r["scripts"]], ["ice_beam"])
        self.assertNotIn("unanswered", r)

    def test_the_log_line(self):
        r = {"task": "T7", "fallback": False, "ms": 312, "tokens": 11840,
             "scripts": [{"name": "ice_beam", "verdict": "runs_it", "p_runs_it": 0.91, "p_close": 0.05},
                         {"name": "galick_gun", "verdict": "close", "p_runs_it": 0.1, "p_close": 0.42}],
             "presets": [{"name": "crater", "p_use": 0.77}]}
        self.assertEqual(route.log_line(r), "route T7 ice_beam runs_it 0.91 · galick_gun close 0.42 · "
                                            "crater use 0.77 · 3 loaded · 312ms 11840tok")
        r = {"task": None, "fallback": True, "why": "jev did not answer within 3 s", "ms": 3004,
             "tokens": 0, "scripts": [], "presets": []}
        self.assertEqual(route.log_line(r), "route - words (jev did not answer within 3 s): · 0 loaded · "
                                            "3004ms 0tok")


# ================================================================== vfg route

class TestRoute(RouteCase):
    def test_one_call_with_the_brief_the_task_and_its_context(self):
        orch = os.path.join(self.box.vhome, "orch")
        os.makedirs(orch)
        with open(os.path.join(orch, "state.json"), "w") as f:
            json.dump({"tasks": {"T7": {"task": "fire the galick gun at the mountain",
                                        "context": "Steve asked in #12"}}}, f)
        with open(os.path.join(orch, "inbox.jsonl"), "w") as f:
            f.write(json.dumps({"id": "T8", "at": "x", "task": "an ice beam for Alex", "context": "#13"}) + "\n")
        t0 = time.time()
        r = self.box.route("Give Steve the galick gun with no cooldown and a bigger crater.", "--task", "T7")
        got, scripts, presets = self.picks(r)
        self.assertEqual(scripts, ["galick_gun"])
        self.assertEqual(got["scripts"][0]["verdict"], "runs_it")
        self.assertEqual(got["scripts"][0]["path"], "scripts/galick_gun/main.py")
        self.assertEqual(presets, [])      # crater is in galick_gun
        self.assertFalse(got["fallback"])
        self.assertEqual(got["task"], "T7")
        self.assertGreater(got["tokens"], 0)
        self.assertEqual(sorted(got), ["fallback", "ms", "presets", "scripts", "task", "tokens"])
        calls = self.box.jev.calls
        self.assertEqual(len(calls), 1)
        body = calls[0]["body"]
        self.assertEqual(body["model"], "jev-latest")
        self.assertEqual(body["state"], {"job": "Give Steve the galick gun with no cooldown and a bigger crater.",
                                         "task": "fire the galick gun at the mountain",
                                         "context": "Steve asked in #12"})
        self.assertEqual(len(body["questions"]), 9)
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer " + KEY)
        self.assertRegex(r.stderr, r"^route T7 galick_gun runs_it 0\.90 · 1 loaded · \d+ms \d+tok\n$")
        self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertTrue(wait_for(lambda: len(self.jev_rows()) == 1))
        row = self.jev_rows()[0]
        self.assertEqual(row[:2], ("jev", "jev-latest"))
        self.assertEqual((row[2], row[3], row[5]), (got["tokens"], 0, "T7"))
        self.assertAlmostEqual(row[4], got["tokens"] * 42e-9, places=12)
        self.assertLess(time.time() - t0, 10)
        # the inbox, when state.json does not have the task
        r = self.box.route("Do the job.", "--task", "T8")
        self.assertOk(r)
        self.assertEqual(self.box.jev.calls[-1]["body"]["state"]["task"], "an ice beam for Alex")
        got, scripts, presets = self.picks(r)
        self.assertEqual(scripts, ["ice_beam"])

    def test_scripts_and_presets_made_on_this_server_are_cards_too(self):
        got, scripts, presets = self.picks(self.box.route("A spirit bomb with a longer charge_up for Alex."))
        self.assertEqual(scripts, ["spirit_bomb"])
        self.assertEqual(got["scripts"][0]["source"], "made")
        self.assertEqual(got["scripts"][0]["path"], "made/main/scripts/spirit_bomb/main.py")
        self.assertEqual(presets, ["charge_up"])
        self.assertEqual(got["presets"][0]["source"], "made")
        self.assertIsNone(got["task"])
        self.assertTrue(wait_for(lambda: len(self.jev_rows()) == 1))
        self.assertIsNone(self.jev_rows()[0][5])

    def test_close_and_held_presets(self):
        # "ice" is a word of ice_beam: close at 0.6; freeze is held by ice_beam
        got, scripts, presets = self.picks(self.box.route("Lay ice under Alex and freeze the zombies, a crater too."))
        self.assertEqual(scripts, ["ice_beam"])
        self.assertEqual(got["scripts"][0]["verdict"], "close")
        self.assertEqual(presets, ["crater"])

    def test_words_when_jev_hangs_within_the_timeout(self):
        self.box.jev.mode = "hang"
        t0 = time.time()
        r = self.box.route("Make the galick gun bigger.", "--task", "T3", VFG_ROUTE_TIMEOUT_S="1")
        took = time.time() - t0
        got, scripts, presets = self.picks(r)
        self.assertLess(took, 2.5)
        self.assertTrue(got["fallback"])
        self.assertEqual(got["why"], "jev did not answer within 1 s")
        self.assertEqual(scripts, ["galick_gun"])
        self.assertEqual(got["scripts"][0]["verdict"], "words")
        self.assertIsNone(got["scripts"][0]["p_runs_it"])
        self.assertRegex(r.stderr, r"T3 is in neither state.json nor inbox.jsonl")
        self.assertRegex(r.stderr, r"route T3 words \(jev did not answer within 1 s\): galick_gun · 1 loaded")
        # it may still have reached Jev, so its estimated size is the row
        self.assertTrue(wait_for(lambda: len(self.jev_rows()) == 1))
        self.assertGreater(self.jev_rows()[0][2], 0)

    def test_words_on_any_error_at_once(self):
        cases = [({}, "fail", r"^jev failed: 500 internal error$"),
                 ({}, "refuse", r"^jev refused the request: 400 bad request for key \*\*\*$"),
                 ({"FAKE_SSH_DOWN": "1"}, "ok", r"^cannot reach EC2"),
                 ({"VFG_SSH_HOST": None, "VFG_SSH_KEY": None}, "ok", r"^not configured")]
        for env, mode, why in cases:
            self.box.jev.mode = mode
            if "VFG_SSH_HOST" in env:
                self.box.write_config("")
            t0 = time.time()
            r = self.box.route("an ice beam that freezes everything", **env)
            got, scripts, presets = self.picks(r)
            self.assertLess(time.time() - t0, 3, mode)
            self.assertTrue(got["fallback"])
            self.assertRegex(got["why"], why)
            self.assertEqual(scripts, ["ice_beam", "galick_gun"])   # galick_gun's first line has "beam"
            self.assertEqual(presets, [])           # freeze is in ice_beam, crater in galick_gun
            self.assertNotIn(KEY, r.stdout + r.stderr)

    def test_no_key_on_the_box(self):
        self.box.write_env("VFG_JEV_URL=%s\n" % self.box.jev.url)
        got, scripts, presets = self.picks(self.box.route("the ice beam"))
        self.assertTrue(got["fallback"])
        self.assertRegex(got["why"], r"^not configured: no TYPESAFE_API_KEY in .*/\.env$")
        self.assertEqual(self.box.jev.calls, [])

    def test_bad_arguments(self):
        for args, inp in ((["--task", "7"], "x"), (["--ref", "x" * 65], "x"), ([], "  \n")):
            self.assertOneLine(self.box.route(inp, *args), 2)
        self.assertOneLine(self.box.route("x", VFG_ROUTE_LOAD="2"), 3, r"VFG_ROUTE_LOAD")
        self.assertEqual(self.box.jev.calls, [])

    def test_the_ref_and_an_empty_library(self):
        r = self.box.route("anything", "--ref", "route-eval")
        self.assertOk(r)
        self.assertTrue(wait_for(lambda: len(self.jev_rows()) == 1))
        self.assertEqual(self.jev_rows()[0][5], "route-eval")
        empty = os.path.join(self.box.root, "empty")
        os.makedirs(empty)
        calls = len(self.box.jev.calls)
        got, scripts, presets = self.picks(self.box.route("anything", VFG_REPO=empty))
        self.assertEqual((scripts, presets, got["fallback"]), ([], [], False))
        self.assertEqual(len(self.box.jev.calls), calls)


# ================================================================== jev.py

class TestJevProgram(RouteCase):
    def q(self, name, size=10):
        return {"type": "choice", "instructions": 'Example script "%s": %s' % (name, "x" * size),
                "criteria": {"runs_it": "a", "close": "b", "no": "c"}}

    def test_answers_tokens_and_time(self):
        r = self.box.jev_program({"state": {"job": "the ice_beam"}, "timeout": 3,
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
        r = self.box.jev_program({"state": state, "questions": qs, "timeout": 5})
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        calls = self.box.jev.calls
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
            self.assertLessEqual(len(body) / 3.0, 64000)

    def test_a_state_and_question_past_32k_are_refused_before_anything_is_sent(self):
        r = self.box.jev_program({"state": {"job": "z" * 90000}, "timeout": 3,
                                  "questions": {"script_a": self.q("a", 10000)}})
        self.assertEqual(r.returncode, 1)
        self.assertRegex(r.stderr.strip(), r"^vfg: the state and its longest question are about \d+ tokens; "
                                           r"Jev takes at most 32000 — nothing was cut and nothing was sent$")
        self.assertEqual(self.box.jev.calls, [])

    def test_the_key_is_never_printed(self):
        self.box.jev.mode = "refuse"
        r = self.box.jev_program({"state": {"job": "x"}, "questions": {"script_a": self.q("a")}})
        self.assertEqual(r.returncode, 1)
        self.assertNotIn(KEY, r.stdout + r.stderr)
        self.assertIn("***", r.stderr)

    def test_bad_args(self):
        for args in ({"state": "text", "questions": {"a": self.q("a")}}, {"state": {}, "questions": {}},
                     {"state": {}, "questions": {"a": "not a question"}}):
            r = self.box.jev_program(args)
            self.assertEqual(r.returncode, 2, r.stderr)


# ================================================================== vfg prefix

class TestPrefix(RouteCase):
    def test_the_same_bytes_every_time_and_made_scripts_change_nothing(self):
        r1, r2 = self.box.vfg("prefix"), self.box.vfg("prefix")
        self.assertOk(r1)
        self.assertEqual(r1.stdout, r2.stdout)
        self.assertEqual(r1.stdout, prefix.build(self.box.repo))
        text = r1.stdout
        self.box.write("made/main/scripts/brand_new/main.py", '"""Brand new: made a minute ago."""\nVALUES = {}\n')
        self.box.write("made/main/presets/brand_preset.py", '"""A made preset."""\nVALUES = {}\n')
        self.assertEqual(self.box.vfg("prefix").stdout, text)
        self.assertNotIn("spirit_bomb", text)
        self.assertNotIn("charge_up", text)
        self.assertNotIn(self.box.root, text)
        # the kit in full, mc.py first; then the library by name, first line and values
        self.assertLess(text.index("=== scripts/_lib/mc.py ===\nNAME = 'fake mc'\n"),
                        text.index("=== scripts/_lib/kit/__init__.py ===\n"))
        self.assertLess(text.index("=== scripts/_lib/kit/__init__.py ==="),
                        text.index("=== scripts/_lib/kit/aim.py ===\n\"\"\"Aiming: the eyes at +1.62.\"\"\"\nEYES = 1.62\n"))
        self.assertIn("=== scripts/README.md ===\nThe scripts folder.\n", text)
        lib = text[text.index("THE LIBRARY"):]
        self.assertIn("crater (preset)\n  Carve a sphere out of the world at a place, leaving air, lava or fire.\n"
                      "  crater_radius  int 2..64 = 8, live\n  crater_leave  enum air|lava|fire = \"air\", live\n",
                      lib)
        names = [l.split(" (")[0] for l in lib.splitlines()[2:] if l and not l.startswith(" ")]
        self.assertEqual(names, ["crater", "freeze", "galick_gun", "hello", "ice_beam", "loop", "theme_music"])
        # a library change is a new prefix
        self.box.write("scripts/_presets/crater.py", CRATER.replace("default\": 8", "default\": 9"))
        self.assertNotEqual(self.box.vfg("prefix").stdout, text)


# ================================================================== usage

class TestUsageCache(Case):
    def cache_columns(self):
        con = __import__("sqlite3").connect(self.box.db)
        con.execute("ALTER TABLE usage ADD COLUMN cache_read_tokens INTEGER")
        con.execute("ALTER TABLE usage ADD COLUMN cache_write_tokens INTEGER")
        con.commit()
        con.close()

    def test_cache_counts_are_stored(self):
        self.cache_columns()
        r = self.box.vfg("usage", "add", "--component", "implementer", "--model", "haiku", "--in", "10",
                         "--out", "5", "--cache-read", "9000", "--cache-write", "400", "--ref", "T7")
        self.assertOk(r)
        self.assertEqual(r.stderr, "")
        r = self.box.vfg("usage", "add", "--component", "jev", "--model", "jev-latest", "--in", "1", "--out", "0")
        self.assertOk(r)
        self.assertEqual(self.box.query("SELECT input_tokens, cache_read_tokens, cache_write_tokens, ref FROM usage"),
                         [(10, 9000, 400, "T7"), (1, None, None, None)])

    def test_before_the_bridge_adds_the_columns_the_row_still_lands(self):
        r = self.box.vfg("usage", "add", "--component", "implementer", "--model", "haiku", "--in", "10",
                         "--out", "5", "--cache-read", "9000")
        self.assertOk(r)
        self.assertEqual(r.stdout.strip(), "1")
        self.assertRegex(r.stderr, r"^vfg: cache counts of 1 row\(s\) not stored: usage has no cache columns yet")
        self.assertEqual(self.box.query("SELECT input_tokens FROM usage"), [(10,)])

    def test_bad_cache_counts(self):
        for flag in ("--cache-read", "--cache-write"):
            r = self.box.vfg("usage", "add", "--component", "jev", "--model", "m", "--in", "1", "--out", "1",
                             flag, "-1")
            self.assertOneLine(r, 2)


if __name__ == "__main__":
    unittest.main()
