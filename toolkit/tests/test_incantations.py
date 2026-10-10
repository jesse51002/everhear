"""The incantation actions (incantation_create, _edit, _remove) and vfg incantation list: no EC2,
no network, the alias model off or mocked.

    uv run --no-project --python 3.12 --with pytest --with pydantic --with httpx pytest toolkit/tests/test_incantations.py

Most tests call vfg's own functions in this process (toolkit/vfg loaded as a module) against
test_vfg's fake box: fake_ssh runs the remote side here, the fake RCON answers, and the
incantations script runs as a real copy over the real scripts/_lib, writing the box's store.
They need pydantic here, as vfg has it under uv. One box serves a class; each test starts with
an empty store and no cache. TestCli runs vfg itself.
"""
import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS)

import standins  # noqa: E402
from test_vfg import REPO, Box, vfg_module  # noqa: E402

try:
    import pydantic  # noqa: F401
    HAVE_PYDANTIC = True
except ImportError:
    HAVE_PYDANTIC = False

FIRE = ["say fire"]


def install(repo):
    """The stand-ins (command, beam, stop, edit ...), the real scripts/_lib, and the three
    incantation actions with their script, as the repo has them."""
    standins.install(repo)
    for n in ("incantation_create", "incantation_edit", "incantation_remove"):
        shutil.copy(os.path.join(REPO, "actions", n + ".py"), os.path.join(repo, "actions", n + ".py"))
    shutil.copytree(os.path.join(REPO, "scripts", "incantations"), os.path.join(repo, "scripts", "incantations"),
                    ignore=shutil.ignore_patterns("__pycache__"))


def fake_llm(variants=None, fail=None):
    """A stand-in for lib/py/vfg_llm: structured() answers `variants`, or raises `fail`; the
    calls it had in .calls."""
    mod = types.ModuleType("vfg_llm")
    mod.calls = []

    async def structured(prompt, output, **kw):
        mod.calls.append(dict(kw, prompt=prompt))
        if fail:
            raise fail
        return types.SimpleNamespace(value=output(variants=variants), cost=0.00012, ms=840)
    mod.structured = structured
    return mod


@unittest.skipUnless(HAVE_PYDANTIC, "needs pydantic in this Python: run it under uv, as the docstring says")
class Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vfg = vfg_module("vfgmod_incant_%s" % cls.__name__)
        cls.box = Box()
        install(cls.box.repo)

    @classmethod
    def tearDownClass(cls):
        cls.box.close()

    def setUp(self):
        for p in (self.store_path(), os.path.join(self.box.vhome, "incantations.json")):
            if os.path.exists(p):
                os.remove(p)
        env = mock.patch.dict(os.environ, self.box.env(VFG_INCANT_ALIAS_MODEL="off"), clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.cfg = self.vfg.Config()
        self.a = argparse.Namespace(timeout=None)

    def store_path(self):
        return os.path.join(self.box.rvfg, "incantations.json")

    def store(self):
        with open(self.store_path()) as f:
            return json.load(f)

    def entry(self, id_):
        return next(e for e in self.store()["incantations"] if e["id"] == id_)

    def cache(self):
        with open(os.path.join(self.box.vhome, "incantations.json")) as f:
            return json.load(f)

    def run_(self, name, **given):
        """vfg action run <name> key=value …, as the command line gives them: a dict as JSON."""
        words = dict((k, json.dumps(v) if isinstance(v, dict) else v) for k, v in given.items())
        return self.vfg.run_named(self.cfg, self.a, name, words)

    def create(self, player="Steve", phrase="fire ball", action="command", values=None, **more):
        return self.run_("incantation_create", player=player, phrase=phrase, action=action,
                         values={"commands": FIRE} if values is None else values, **more)

    def refused(self, name, code=2, **given):
        with self.assertRaises(self.vfg.Fail) as cm:
            self.run_(name, **given)
        self.assertEqual(cm.exception.code, code, cm.exception.msg)
        return cm.exception.msg

    def calls(self):
        """The remote programs run so far (each an SSH call)."""
        return len([c for c in self.box.ssh_calls() if c[-1].endswith("python3 -u -")])

    def listed(self, player=None, as_json=False, fresh=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.vfg.cmd_incantation(self.cfg, argparse.Namespace(player=player, json=as_json, fresh=fresh,
                                                                  timeout=None))
        return json.loads(buf.getvalue()) if as_json else buf.getvalue()


class TestCreate(Case):
    def test_one_entry_per_named_player(self):
        line = self.create(player="Steve, Alex", phrase="Fire  Ball!")
        self.assertTrue(line["ok"], line)
        self.assertEqual(line["action"], "incantation_create")
        got = self.store()["incantations"]
        self.assertEqual([e["id"] for e in got], ["steve.fire_ball", "alex.fire_ball"])
        e = got[0]
        self.assertEqual({k: e[k] for k in ("player", "phrase", "action", "script", "source", "server", "values",
                                            "live", "runs", "caster_fields", "cooldown_s")},
                         {"player": "Steve", "phrase": "fire ball", "action": "command", "script": "command",
                          "source": "library", "server": "", "values": {"commands": FIRE}, "live": [],
                          "runs": "once", "caster_fields": [], "cooldown_s": 3.0})
        self.assertEqual(line["facts"]["ids"], ["steve.fire_ball", "alex.fire_ball"])

    def test_at_a_is_one_entry(self):
        self.create(player="@a")
        self.assertEqual([(e["id"], e["player"]) for e in self.store()["incantations"]], [("@a.fire_ball", "@a")])

    def test_a_one_word_phrase_is_refused(self):
        self.assertIn("two words at least", self.refused("incantation_create", player="Steve", phrase="fireball",
                                                         action="command", values='{"commands": ["say hi"]}'))
        self.assertFalse(os.path.exists(self.store_path()))

    def test_an_empty_player_is_refused(self):
        self.assertIn("whose incantation", self.refused("incantation_create", player=" ", phrase="fire ball",
                                                        action="command"))
        self.assertIn("whose incantation", self.refused("incantation_create", phrase="fire ball", action="command"))

    def test_an_unknown_action_is_refused(self):
        self.assertIn("no action no_such", self.refused("incantation_create", player="Steve", phrase="fire ball",
                                                        action="no_such"))

    def test_binding_stop_or_an_incantation_action_is_refused(self):
        for bound in ("stop", "edit", "incantation_remove"):
            self.assertIn("action=%s" % bound, self.refused("incantation_create", player="Steve",
                                                            phrase="fire ball", action=bound))

    def test_a_bad_value_for_the_bound_action_is_refused(self):
        msg = self.refused("incantation_create", player="Steve", phrase="beam me", action="beam",
                           values={"length": "far"})
        self.assertTrue(msg.startswith("values for beam: length=far"), msg)
        self.assertIn("beam has no value power", self.refused("incantation_create", player="Steve",
                                                              phrase="beam me", action="beam", values={"power": 3}))

    def test_a_clamped_value_is_a_note(self):
        line = self.create(phrase="beam me", action="beam", values={"length": 500})
        self.assertIn("length=500 is over its max, clamped to 200", line["notes"])
        self.assertEqual(self.entry("steve.beam_me")["values"]["length"], 200)

    def test_player_fields_left_out_or_empty_are_the_casters(self):
        self.create(phrase="beam me", action="beam", values={"length": 50})
        e = self.entry("steve.beam_me")
        self.assertEqual((e["caster_fields"], e["values"]["player"], e["values"]["length"]), (["player"], "", 50))
        self.create(phrase="beam you", action="beam", values={"player": ""})
        self.assertEqual(self.entry("steve.beam_you")["caster_fields"], ["player"])
        self.create(phrase="beam alex", action="beam", values={"player": "Alex"})
        e = self.entry("steve.beam_alex")
        self.assertEqual((e["caster_fields"], e["values"]["player"]), ([], "Alex"))

    def test_the_code_aliases_are_made(self):
        line = self.create()
        self.assertEqual(self.entry("steve.fire_ball")["aliases"], ["fireball"])
        self.assertNotIn("alias_call", line["facts"])

    def test_the_models_variants_are_merged_after_the_codes_and_capped(self):
        llm = fake_llm(["fire bowl", "Fire-Ball", "fireball"] + ["variant number %s" % w for w in
                                                                  "one two three four five six seven eight nine ten eleven".split()])
        with mock.patch.dict(sys.modules, {"vfg_llm": llm}), mock.patch.dict(os.environ, VFG_INCANT_ALIAS_MODEL="test/m"):
            self.cfg = self.vfg.Config()
            line = self.create()
        aliases = self.entry("steve.fire_ball")["aliases"]
        self.assertEqual(aliases[:3], ["fireball", "fire bowl", "variant number one"])
        self.assertEqual(len(aliases), 12)
        self.assertEqual(line["facts"]["alias_call"], {"model": "test/m", "cost": 0.00012, "ms": 840})
        call = llm.calls[0]
        self.assertEqual((call["model"], call["reasoning"], call["timeout"]), ("test/m", False, 8.0))
        self.assertIn('The phrase: "fire ball"', call["prompt"])

    def test_a_failed_alias_call_is_a_note(self):
        llm = fake_llm(fail=RuntimeError("no answer from test/m within 8 s"))
        with mock.patch.dict(sys.modules, {"vfg_llm": llm}), mock.patch.dict(os.environ, VFG_INCANT_ALIAS_MODEL="test/m"):
            self.cfg = self.vfg.Config()
            line = self.create()
        self.assertTrue(line["ok"], line)
        self.assertIn("aliases from code only: the alias call failed (no answer from test/m within 8 s)", line["notes"])
        self.assertEqual(self.entry("steve.fire_ball")["aliases"], ["fireball"])

    def test_bind_warnings_are_notes(self):
        self.create()
        line = self.create(phrase="fire wall")
        self.assertIn('"fire wall" sounds like "fire ball", so one may fire for the other.', line["notes"])
        line = self.create(player="Alex", phrase="go go", values={"commands": FIRE})
        self.assertTrue(any(n.startswith('"go go" is short') for n in line["notes"]), line["notes"])

    def test_creating_it_again_replaces_it_with_a_note(self):
        self.create()
        line = self.create(action="beam", values={})
        self.assertIn('replaces Steve\'s "fire ball", which fired command', line["notes"])
        self.assertIn("it replaced the one Steve had", line["summary"])
        self.assertEqual([e["action"] for e in self.store()["incantations"]], ["beam"])

    def test_the_bound_script_is_deployed_by_its_stamp(self):
        shutil.rmtree(os.path.join(self.box.rvfg, "scripts"), ignore_errors=True)
        before = self.calls()
        self.create()
        self.assertEqual(self.calls() - before, 3)  # the store and the stamps; the kit and both scripts; the run
        self.assertTrue(os.path.isfile(os.path.join(self.box.rvfg, "scripts", "command", "main.py")))
        before = self.calls()
        self.create(phrase="fire wall")
        self.assertEqual(self.calls() - before, 2)  # nothing to deploy


class TestEdit(Case):
    def test_the_cooldown(self):
        self.create()
        line = self.run_("incantation_edit", player="steve", phrase="Fire ball", cooldown_s="700")
        self.assertTrue(line["ok"], line)
        self.assertIn("cooldown_s=700 is over its max, clamped to 600", line["notes"])
        self.assertIn('changed "fire ball" for Steve: cooldown 600 s', line["summary"])
        self.assertEqual(self.entry("steve.fire_ball")["cooldown_s"], 600.0)

    def test_values_are_checked_over_the_stored_ones(self):
        self.create(phrase="beam me", action="beam", values={"length": 50})
        msg = self.refused("incantation_edit", player="Steve", phrase="beam me", values={"length": "far"})
        self.assertTrue(msg.startswith("values for beam: length=far"), msg)
        self.run_("incantation_edit", player="Steve", phrase="beam me", values={"cooldown": 1})
        e = self.entry("steve.beam_me")
        self.assertEqual((e["values"]["length"], e["values"]["cooldown"], e["values"]["player"], e["caster_fields"]),
                         (50, 1.0, "", ["player"]))
        self.run_("incantation_edit", player="Steve", phrase="beam me", values={"player": "Alex"})
        e = self.entry("steve.beam_me")
        self.assertEqual((e["values"]["player"], e["caster_fields"]), ("Alex", []))

    def test_aliases_are_normalised_and_bind_checked(self):
        self.create()
        self.create(phrase="ice wall")
        line = self.run_("incantation_edit", player="Steve", phrase="fire ball", aliases="Fire-Bowl,fire ball,ice walls")
        self.assertEqual(self.entry("steve.fire_ball")["aliases"], ["fire bowl", "ice walls"])
        self.assertIn("left out of the aliases (the phrase itself, too short to tell apart, or past 12): fire ball",
                      line["notes"])
        self.assertIn('Its alias "ice walls" sounds like "ice wall", so one may fire for the other.', line["notes"])

    def test_a_missing_one_or_nothing_to_change_is_refused(self):
        self.assertIn('no incantation "fire ball" for Steve', self.refused("incantation_edit", code=1, player="Steve",
                                                                          phrase="fire ball", cooldown_s="5"))
        self.create()
        self.assertIn("nothing to change", self.refused("incantation_edit", player="Steve", phrase="fire ball"))


class TestRemove(Case):
    def test_remove(self):
        self.create(player="Steve,Alex")
        self.listed()  # the cache
        line = self.run_("incantation_remove", player="Steve", phrase="fire ball")
        self.assertTrue(line["ok"], line)
        self.assertEqual(line["summary"], 'removed "fire ball" for Steve; version 3')
        self.assertEqual([e["id"] for e in self.store()["incantations"]], ["alex.fire_ball"])
        self.assertEqual([e["id"] for e in self.cache()["incantations"]], ["alex.fire_ball"])
        line = self.run_("incantation_remove", player="Steve", phrase="fire ball")
        self.assertFalse(line["ok"])
        self.assertIn('Steve has no incantation "fire ball"', line["error"])


class TestList(Case):
    def test_the_lines(self):
        self.assertEqual(self.listed(), "none\n")
        self.create(phrase="beam me", action="beam", values={"length": 50}, cooldown_s="2.5")
        self.create(player="@a")
        self.assertEqual(self.listed().splitlines(),
                         ['Steve: "beam me" → beam {"length":50} player=<caster> cooldown 2.5s; no aliases',
                          '@a: "fire ball" → command {"commands":["say fire"]} cooldown 3s; aliases fireball'])

    def test_the_json_and_a_player(self):
        self.create(phrase="beam me", action="beam", values={"length": 50})
        self.create(player="Alex", phrase="ice wall")
        self.create(player="@a")
        got = self.listed(player="steve", as_json=True)
        self.assertEqual(got, [{"player": "Steve", "phrase": "beam me", "action": "beam", "values": {"length": 50},
                                "cooldown_s": 3.0, "aliases": []},
                               {"player": "@a", "phrase": "fire ball", "action": "command",
                                "values": {"commands": FIRE}, "cooldown_s": 3.0, "aliases": ["fireball"]}])

    def test_runs_write_the_cache_through(self):
        self.create()
        self.assertEqual([e["id"] for e in self.cache()["incantations"]], ["steve.fire_ball"])
        self.run_("incantation_edit", player="Steve", phrase="fire ball", cooldown_s="9")
        self.assertEqual(self.cache()["incantations"][0]["cooldown_s"], 9.0)
        self.assertEqual(self.cache()["version"], self.store()["version"])
        before = self.calls()
        self.assertIn('Steve: "fire ball"', self.listed())
        self.assertEqual(self.calls(), before)  # answered from the cache
        self.listed(fresh=True)
        self.assertEqual(self.calls(), before + 1)


class TestCli(unittest.TestCase):
    """vfg itself, under uv: the repo's own three actions pass its check; a run and a list."""

    def setUp(self):
        self.box = Box()
        self.addCleanup(self.box.close)

    def test_the_three_actions_pass_check(self):
        for n in ("incantation_create", "incantation_edit", "incantation_remove"):
            r = self.box.vfg("action", "check", n, VFG_REPO=REPO)
            self.assertEqual((r.returncode, r.stdout), (0, "ok: actions/%s.py\n" % n), r.stderr)

    def test_vfg_action_run_and_incantation_list(self):
        box = self.box
        install(box.repo)
        r = box.vfg("action", "run", "incantation_create", "player=Steve", "phrase=fire ball", "action=command",
                    'values={"commands": ["say fire"]}', "--task", "T4", VFG_INCANT_ALIAS_MODEL="off")
        self.assertEqual(r.returncode, 0, r.stderr)
        line = json.loads(r.stdout)
        self.assertEqual((line["ok"], line["summary"]),
                         (True, 'stored "fire ball" for Steve: command, aliases fireball; version 1'))
        r = box.vfg("incantation", "list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [{"player": "Steve", "phrase": "fire ball", "action": "command",
                                                 "values": {"commands": FIRE}, "cooldown_s": 3.0,
                                                 "aliases": ["fireball"]}])


if __name__ == "__main__":
    unittest.main()
