"""The incantation actions (incantation_create, _edit and _remove) and vfg incantation list: no
EC2, no network, the alias model off or mocked.

    python3 -m unittest discover -s toolkit/tests

harness.py's box, one for a class, with the stand-in actions, the real scripts/_lib and the
repo's own three incantation actions and their script, deployed and stamped. vfg's own functions
run in this process, as do the remote programs they send; the incantations script runs as a real
copy over the real kit, writing the box's store. Each test starts with an empty store and no
cache. The alias model is off in the box's config (harness.Box.write_config) unless a test
names one, with vfg_llm mocked. TestCli goes through vfg's command line.
"""
import argparse
import contextlib
import copy
import io
import json
import os
import secrets
import shutil
import sys
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import REPO, Box, BoxCase, FakeRcon  # noqa: E402
import standins  # noqa: E402

FIRE = ["say fire"]


def install(repo):
    """The stand-ins (command, beam, stop, edit ...), the real scripts/_lib, and the three
    incantation actions with their script, as the repo has them."""
    standins.install(repo)
    for n in ("incantation_create", "incantation_edit", "incantation_remove"):
        shutil.copy(os.path.join(REPO, "actions", n + ".py"), os.path.join(repo, "actions", n + ".py"))
    shutil.copytree(os.path.join(REPO, "scripts", "incantations"), os.path.join(repo, "scripts", "incantations"),
                    ignore=shutil.ignore_patterns("__pycache__"))


def fake_llm(variants=None, fail=None, after=None):
    """A stand-in for lib/py/vfg_llm: structured() answers `variants`, or raises `fail`, once
    `after` (an Event) is set, if given; the calls it had in .calls."""
    mod = types.ModuleType("vfg_llm")
    mod.calls = []

    async def structured(prompt, output, **kw):
        mod.calls.append(dict(kw, prompt=prompt))
        if after is not None and not after.wait(5):
            raise RuntimeError("answered only after the store was read")
        if fail:
            raise fail
        return types.SimpleNamespace(value=output(variants=variants), cost=0.00012, ms=840)
    mod.structured = structured
    return mod


class Case(unittest.TestCase):
    """One box and one fake RCON for the class; the box's environment for each test."""

    @classmethod
    def setUpClass(cls):
        cls.rcon = FakeRcon(secrets.token_hex(8)).start()
        cls.box = Box(cls.rcon)
        install(cls.box.repo)
        cls.box.predeploy()

    @classmethod
    def tearDownClass(cls):
        cls.box.close()
        cls.rcon.stop()

    def setUp(self):
        for p in (self.store_path(), os.path.join(self.box.vhome, "incantations.json")):
            if os.path.exists(p):
                os.remove(p)
        self.model("off")
        env = self.box.environ()
        self.vfg = env.__enter__()
        self.addCleanup(env.__exit__, None, None, None)
        self.cfg = self.vfg.Config()
        self.a = argparse.Namespace(timeout=None)

    def model(self, name):
        """The alias model, in the box's config: off, or one a mocked vfg_llm answers for."""
        self.box.write_config("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\nVFG_INCANT_ALIAS_MODEL=%s\n" % (self.box.key, name))
        if hasattr(self, "vfg"):
            self.cfg = self.vfg.Config()

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
        return len(self.box.programs())

    def listed(self, player=None, as_json=False, fresh=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.vfg.cmd_incantation(self.cfg, argparse.Namespace(player=player, json=as_json, fresh=fresh,
                                                                  timeout=None))
        return json.loads(buf.getvalue()) if as_json else buf.getvalue()


class TestCreate(Case):
    def test_one_entry_per_named_player_with_the_codes_aliases(self):
        line = self.create(player="Steve, Alex", phrase="Fire  Ball!")
        self.assertTrue(line["ok"], line)
        self.assertEqual(line["action"], "incantation_create")
        got = self.store()["incantations"]
        self.assertEqual([e["id"] for e in got], ["steve.fire_ball", "alex.fire_ball"])
        e = got[0]
        self.assertEqual({k: e[k] for k in ("player", "phrase", "action", "script", "source", "server", "values",
                                            "live", "runs", "caster_fields", "cooldown_s", "aliases")},
                         {"player": "Steve", "phrase": "fire ball", "action": "command", "script": "command",
                          "source": "library", "server": "", "values": {"commands": FIRE}, "live": [],
                          "runs": "once", "caster_fields": [], "cooldown_s": 3.0, "aliases": ["fireball"]})
        self.assertEqual(line["facts"]["ids"], ["steve.fire_ball", "alex.fire_ball"])
        self.assertNotIn("alias_call", line["facts"])
        self.create(player="@a")
        self.assertEqual([(e["id"], e["player"]) for e in self.store()["incantations"]][2:], [("@a.fire_ball", "@a")])

    def test_refusals_before_anything_runs(self):
        before = self.calls()
        self.assertIn("two words at least", self.refused("incantation_create", player="Steve", phrase="fireball",
                                                         action="command", values='{"commands": ["say hi"]}'))
        self.assertIn("whose incantation", self.refused("incantation_create", player=" ", phrase="fire ball",
                                                        action="command"))
        self.assertIn("whose incantation", self.refused("incantation_create", phrase="fire ball", action="command"))
        self.assertIn("no action no_such", self.refused("incantation_create", player="Steve", phrase="fire ball",
                                                        action="no_such"))
        for bound in ("stop", "edit", "incantation_remove"):
            self.assertIn("action=%s" % bound, self.refused("incantation_create", player="Steve",
                                                            phrase="fire ball", action=bound))
        msg = self.refused("incantation_create", player="Steve", phrase="beam me", action="beam",
                           values={"length": "far"})
        self.assertTrue(msg.startswith("values for beam: length=far"), msg)
        self.assertIn("beam has no value power", self.refused("incantation_create", player="Steve",
                                                              phrase="beam me", action="beam", values={"power": 3}))
        self.assertEqual(self.calls(), before)
        self.assertFalse(os.path.exists(self.store_path()))

    def test_a_clamped_value_is_a_note_and_player_fields_left_out_or_empty_are_the_casters(self):
        line = self.create(phrase="beam me", action="beam", values={"length": 500})
        self.assertIn("length=500 is over its max, clamped to 200", line["notes"])
        e = self.entry("steve.beam_me")
        self.assertEqual((e["caster_fields"], e["values"]["player"], e["values"]["length"]), (["player"], "", 200))
        self.create(phrase="beam you", action="beam", values={"player": ""})
        self.assertEqual(self.entry("steve.beam_you")["caster_fields"], ["player"])
        self.create(phrase="beam alex", action="beam", values={"player": "Alex"})
        e = self.entry("steve.beam_alex")
        self.assertEqual((e["caster_fields"], e["values"]["player"]), ([], "Alex"))

    def test_the_models_variants_come_while_the_store_is_read_and_are_merged_after_the_codes(self):
        reading = threading.Event()
        fetch = self.vfg.fetch_incantations

        def fetch_and_say(cfg, a, stamps=None):
            reading.set()
            return fetch(cfg, a, stamps)
        # It answers only once the store is being read: the two at once.
        llm = fake_llm(["fire bowl", "Fire-Ball", "fireball"] + ["variant number %s" % w for w in
                                                                  "one two three four five six seven eight nine ten eleven".split()],
                       after=reading)
        self.model("test/m")
        with mock.patch.dict(sys.modules, {"vfg_llm": llm}), mock.patch.object(self.vfg, "fetch_incantations", fetch_and_say):
            line = self.create()
        aliases = self.entry("steve.fire_ball")["aliases"]
        self.assertEqual(aliases[:3], ["fireball", "fire bowl", "variant number one"])
        self.assertEqual(len(aliases), 12)
        self.assertEqual(line["facts"]["alias_call"], {"model": "test/m", "cost": 0.00012, "ms": 840})
        self.assertFalse(any("alias call" in n for n in line["notes"]), line["notes"])
        call = llm.calls[0]
        self.assertEqual((call["model"], call["reasoning"], call["timeout"]), ("test/m", False, 8.0))
        self.assertIn('The phrase: "fire ball"', call["prompt"])

    def test_a_failed_alias_call_is_a_note(self):
        llm = fake_llm(fail=RuntimeError("no answer from test/m within 8 s"))
        self.model("test/m")
        with mock.patch.dict(sys.modules, {"vfg_llm": llm}):
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

    def test_the_bound_script_is_deployed_by_its_stamp_and_creating_it_again_replaces_it(self):
        shutil.rmtree(os.path.join(self.box.rvfg, "scripts"), ignore_errors=True)
        before = self.calls()
        self.create()
        self.assertEqual(self.calls() - before, 3)  # the store and the stamps; the kit and both scripts; the run
        self.assertTrue(os.path.isfile(os.path.join(self.box.rvfg, "scripts", "command", "main.py")))
        before = self.calls()
        line = self.create(action="beam", values={})
        self.assertEqual(self.calls() - before, 3)  # beam's script was not there yet
        before = self.calls()
        line = self.create(action="command")
        self.assertEqual(self.calls() - before, 2)  # nothing to deploy
        self.assertIn('replaces Steve\'s "fire ball", which fired beam', line["notes"])
        self.assertIn("it replaced the one Steve had", line["summary"])
        self.assertEqual([e["action"] for e in self.store()["incantations"]], ["command"])


class TestEdit(Case):
    def test_values_are_checked_over_the_stored_ones_and_the_cooldown(self):
        self.assertIn('no incantation "beam me" for Steve', self.refused("incantation_edit", code=1, player="Steve",
                                                                        phrase="beam me", cooldown_s="5"))
        self.create(phrase="beam me", action="beam", values={"length": 50})
        self.assertIn("nothing to change", self.refused("incantation_edit", player="Steve", phrase="beam me"))
        msg = self.refused("incantation_edit", player="Steve", phrase="beam me", values={"length": "far"})
        self.assertTrue(msg.startswith("values for beam: length=far"), msg)
        self.run_("incantation_edit", player="Steve", phrase="beam me", values={"cooldown": 1})
        e = self.entry("steve.beam_me")
        self.assertEqual((e["values"]["length"], e["values"]["cooldown"], e["values"]["player"], e["caster_fields"]),
                         (50, 1.0, "", ["player"]))
        line = self.run_("incantation_edit", player="steve", phrase="Beam me", values={"player": "Alex"},
                         cooldown_s="700")
        self.assertTrue(line["ok"], line)
        self.assertIn("cooldown_s=700 is over its max, clamped to 600", line["notes"])
        self.assertIn('changed "beam me" for Steve: cooldown 600 s', line["summary"])
        e = self.entry("steve.beam_me")
        self.assertEqual((e["values"]["player"], e["caster_fields"], e["cooldown_s"]), ("Alex", [], 600.0))

    def test_two_edits_of_different_values_both_land(self):
        self.create(phrase="beam me", action="beam", values={"length": 50})
        read_before = copy.deepcopy(self.vfg.fetch_incantations(self.cfg, self.a))
        self.run_("incantation_edit", player="Steve", phrase="beam me", values={"length": 80})
        # The second edit read the entry before the first landed: length still 50 to it.
        with mock.patch.object(self.vfg, "fetch_incantations", lambda cfg, a, stamps=None: read_before):
            self.run_("incantation_edit", player="Steve", phrase="beam me", values={"cooldown": 1})
        e = self.entry("steve.beam_me")
        self.assertEqual((e["values"]["length"], e["values"]["cooldown"], e["values"]["player"]), (80, 1.0, ""))

    def test_aliases_are_normalised_and_bind_checked(self):
        self.create(phrase="ice wall")
        self.create()
        line = self.run_("incantation_edit", player="Steve", phrase="fire ball", aliases="Fire-Bowl,fire ball,ice walls")
        self.assertEqual(self.entry("steve.fire_ball")["aliases"], ["fire bowl", "ice walls"])
        self.assertIn("left out of the aliases (the phrase itself, too short to tell apart, or past 12): fire ball",
                      line["notes"])
        self.assertIn('Its alias "ice walls" sounds like "ice wall", so one may fire for the other.', line["notes"])


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
    def test_the_lines_the_json_and_a_player(self):
        self.assertEqual(self.listed(), "none\n")
        self.create(phrase="beam me", action="beam", values={"length": 50}, cooldown_s="2.5")
        self.create(player="Alex", phrase="ice wall")
        self.create(player="@a")
        self.assertEqual(self.listed(fresh=True).splitlines(),
                         ['Alex: "ice wall" → command {"commands":["say fire"]} cooldown 3s; aliases icewall',
                          'Steve: "beam me" → beam {"length":50} player=<caster> cooldown 2.5s; no aliases',
                          '@a: "fire ball" → command {"commands":["say fire"]} cooldown 3s; aliases fireball'])
        got = self.listed(player="steve", as_json=True)
        self.assertEqual(got, [{"player": "Steve", "phrase": "beam me", "action": "beam", "values": {"length": 50},
                                "cooldown_s": 2.5, "aliases": []},
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


class TestCli(BoxCase):
    """vfg's command line: the repo's own three actions pass its check; a run and a list."""

    def test_the_three_actions_pass_check_and_a_run_and_a_list(self):
        install(self.box.repo)              # the repo's own three actions and their script, copied
        self.box.predeploy()
        for n in ("incantation_create", "incantation_edit", "incantation_remove"):
            r = self.vfg("action", "check", n)
            self.assertEqual((r.returncode, r.stdout), (0, "ok: actions/%s.py\n" % n), r.stderr)
        r = self.vfg("action", "run", "incantation_create", "player=Steve", "phrase=fire ball", "action=command",
                     'values={"commands": ["say fire"]}', "--task", "fire_ball_steve")
        self.assertEqual(r.returncode, 0, r.stderr)
        line = json.loads(r.stdout)
        self.assertEqual((line["ok"], line["summary"]),
                         (True, 'stored "fire ball" for Steve: command, aliases fireball; version 1'))
        r = self.vfg("incantation", "list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [{"player": "Steve", "phrase": "fire ball", "action": "command",
                                                 "values": {"commands": FIRE}, "cooldown_s": 3.0,
                                                 "aliases": ["fireball"]}])

if __name__ == "__main__":
    unittest.main()
