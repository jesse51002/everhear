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
# The tests' own action for following one: TestFollow writes it again with a default changed and
# a field gone. Its script reads none of its values.
ZAP = '''from pydantic import Field

from actions._types import Action, Players


class Zap(Action):
    """Zap someone (a test's stand-in)."""

    script = "zap"
    runs = "once"
    takes = ["player", "power"]

    player: Players = "@a"
    """who is zapped"""
    power: int = Field(2, ge=1, le=10)
    """how hard"""
    speed: int = 1
    """how fast"""
    title: str = ""
    """what it is called"""
    extra: bool = False
    """a value the action drops later"""
'''
ZAP_SCRIPT = '''"""Zap: says it zapped, and ends."""
from kit import result, values

values()
result("zapped")
'''


def install(repo):
    """The stand-ins (command, beam, stop, edit ...), the real scripts/_lib, and the three
    incantation actions with their script, as the repo has them."""
    standins.install(repo)
    for n in ("incantation_create", "incantation_edit", "incantation_remove"):
        shutil.copy(os.path.join(REPO, "actions", n + ".py"), os.path.join(repo, "actions", n + ".py"))
    shutil.copytree(os.path.join(REPO, "scripts", "incantations"), os.path.join(repo, "scripts", "incantations"),
                    ignore=shutil.ignore_patterns("__pycache__"))


def fake_llm(variants=None, fail=None, after=None, everyday=False, everyday_variants=()):
    """A stand-in for lib/py/vfg_llm: structured() answers `variants`, those in
    `everyday_variants` marked common_phrase, and `everyday`, or raises `fail`, once `after` (an
    Event) is set, if given; the calls it had in .calls."""
    mod = types.ModuleType("vfg_llm")
    mod.calls = []

    async def structured(prompt, output, **kw):
        mod.calls.append(dict(kw, prompt=prompt, output=output))
        if after is not None and not after.wait(5):
            raise RuntimeError("answered only after the store was read")
        if fail:
            raise fail
        said = [{"text": v, "sounds": "", "common_phrase": v in everyday_variants} for v in variants or []]
        return types.SimpleNamespace(value=output(variants=said, everyday=everyday), cost=0.00012, ms=840)
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
        with mock.patch.object(self.vfg, "flush_usage_later") as flush:
            line = self.create(player="Steve, Alex", phrase="Fire  Ball!")
        flush.assert_not_called()  # the alias model is off: no call, no row to send
        self.assertTrue(line["ok"], line)
        self.assertEqual(line["action"], "incantation_create")
        got = self.store()["incantations"]
        self.assertEqual([e["id"] for e in got], ["steve.fire_ball", "alex.fire_ball"])
        e = got[0]
        self.assertEqual({k: e[k] for k in ("player", "phrase", "action", "script", "source", "server", "values",
                                            "live", "runs", "caster_fields", "cooldown_s", "aliases",
                                            "everyday_aliases")},
                         {"player": "Steve", "phrase": "fire ball", "action": "command", "script": "command",
                          "source": "library", "server": "", "values": {"commands": FIRE}, "live": [],
                          "runs": "once", "caster_fields": [], "cooldown_s": 3.0, "aliases": ["fireball"],
                          "everyday_aliases": []})
        self.assertEqual(line["facts"]["ids"], ["steve.fire_ball", "alex.fire_ball"])
        self.assertNotIn("alias_call", line["facts"])
        self.create(player="@a")
        self.assertEqual([(e["id"], e["player"]) for e in self.store()["incantations"]][2:], [("@a.fire_ball", "@a")])

    def test_refusals_before_anything_runs(self):
        before = self.calls()
        self.assertIn("a word at least", self.refused("incantation_create", player="Steve", phrase=" ! ",
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
        # It answers only once the store is being read: the two at once. Thirty that sound like it
        # (fyre bawl, fier boll ...), among them the phrase itself, a repeat, one far longer and one
        # that sounds unlike it.
        alike = [a + " " + b for a in ("fyre", "fier", "fire") for b in ("bawl", "bal", "boll", "bol", "pall",
                                                                          "paul", "bull", "bell", "baul", "balle")]
        llm = fake_llm(["fire bowl", "Fire-Ball", "fireball", "fire bowl", "fire ball now please", "ice cream"] + alike,
                       after=reading)
        self.model("test/m")
        with mock.patch.dict(sys.modules, {"vfg_llm": llm}), mock.patch.object(self.vfg, "fetch_incantations", fetch_and_say), \
                mock.patch.object(self.vfg, "flush_usage_later") as flush:
            line = self.create()
        flush.assert_called_once_with(self.cfg)  # the call's own usage row, sent nobody waiting
        aliases = self.entry("steve.fire_ball")["aliases"]
        self.assertEqual(aliases[:3], ["fireball", "fire bowl", "fyre bawl"])     # the code's first
        self.assertEqual(len(aliases), 24)
        # What the code's checks dropped is in the facts, never a note.
        self.assertEqual(line["facts"]["alias_call"], {
            "model": "test/m", "cost": 0.00012, "ms": 840,
            "dropped": ["fire ball: the phrase itself", "fire bowl: a repeat",
                        "fire ball now please: 6 speech sounds longer", "ice cream: sounds unlike it (0.82)"]
            + ["%s: past the 24" % x for x in alike[22:]]})
        self.assertEqual([n for n in line["notes"] if "alias" in n or "dropped" in n], [])
        call = llm.calls[0]
        self.assertEqual((call["model"], call["reasoning"], call["timeout"], call.get("providers")),
                         ("test/m", "low", 20.0, None))
        self.assertEqual((call["component"], call["call_id"]), ("alias", "fire ball"))
        prompt = call["prompt"]
        self.assertIn('The incantation: "fire ball"\nHow it sounds (the CMU pronouncing dictionary, ARPAbet, stress '
                      'left out):\n  fire = F AY ER\n  ball = B AO L\n', prompt)
        self.assertIn("Always exactly 20, the likeliest first.", prompt)
        schema = call["output"].model_json_schema()
        self.assertEqual(schema["$defs"]["Variant"]["required"], ["text", "sounds", "common_phrase"])
        self.assertIn("exactly 20", schema["properties"]["variants"]["description"])

    def test_a_made_up_word_is_said_from_its_letters_in_the_prompt(self):
        self.model("test/m")
        llm = fake_llm(["garlic gun", "galic gun"])
        with mock.patch.dict(sys.modules, {"vfg_llm": llm}), mock.patch.object(self.vfg, "flush_usage_later"):
            self.create(phrase="galick gun")
        self.assertIn('  galick = not in the dictionary (a made-up word); a guess from its letters: G AE L IH K. '
                      'Think how a player would say it.\n  gun = G AH N\n', llm.calls[0]["prompt"])
        self.assertEqual(self.entry("steve.galick_gun")["aliases"], ["galickgun", "garlic gun", "galic gun"])

    def test_a_failed_alias_call_is_a_note(self):
        llm = fake_llm(fail=RuntimeError("no answer from test/m within 8 s"))
        self.model("test/m")
        with mock.patch.dict(sys.modules, {"vfg_llm": llm}), mock.patch.object(self.vfg, "flush_usage_later") as flush:
            line = self.create()
        flush.assert_called_once_with(self.cfg)  # a failed call's row is queued too
        self.assertTrue(line["ok"], line)
        self.assertIn("aliases from code only: the alias call failed (no answer from test/m within 8 s)", line["notes"])
        self.assertEqual(self.entry("steve.fire_ball")["aliases"], ["fireball"])

    def test_a_one_word_phrase_is_created_with_its_split_form_and_a_short_one_with_a_note(self):
        line = self.create(phrase="Fireball!")
        self.assertTrue(line["ok"], line)
        self.assertEqual(line["notes"], [])     # 6 speech sounds, as "fire ball"
        e = self.entry("steve.fireball")
        self.assertEqual((e["phrase"], e["aliases"]), ("fireball", ["fire ball"]))
        line = self.create(phrase="heal")
        self.assertTrue(line["ok"], line)
        self.assertEqual(line["notes"],
                         ['"heal" is short (3 speech sounds), so it may fire by accident: a second word helps.'])
        self.assertEqual(self.entry("steve.heal")["aliases"], [])
        self.assertEqual(self.listed(player="Steve", as_json=True)[1]["phrase"], "heal")

    def test_everyday_talk_is_a_note_when_the_alias_call_says_so(self):
        self.model("test/m")
        for everyday in (True, False):
            llm = fake_llm(["heel", "heals"], everyday=everyday)
            with mock.patch.dict(sys.modules, {"vfg_llm": llm}), mock.patch.object(self.vfg, "flush_usage_later"):
                line = self.create(phrase="heal")
            self.assertTrue(line["ok"], line)
            note = '"heal" is everyday talk, so it will fire whenever it is said in conversation.'
            self.assertEqual(note in line["notes"], everyday, line["notes"])
            self.assertEqual(self.entry("steve.heal")["aliases"], ["heals"])   # heel: 3 speech sounds
            self.assertIn("everyday talk", llm.calls[0]["prompt"])

    def test_the_variants_marked_everyday_talk_are_kept_as_everyday_aliases(self):
        self.model("test/m")
        llm = fake_llm(["ice been", "Ice cream", "I Scream!", "nice beam", "eyes beam", "icebeam"],
                       everyday_variants=["Ice cream", "I Scream!", "nice beam", "eyes beam", "icebeam"])
        with mock.patch.dict(sys.modules, {"vfg_llm": llm}), mock.patch.object(self.vfg, "flush_usage_later"):
            line = self.create(phrase="ice beam")
        self.assertTrue(line["ok"], line)
        e = self.entry("steve.ice_beam")
        self.assertEqual(e["aliases"], ["icebeam", "ice been", "ice cream", "i scream", "nice beam", "eyes beam"])
        # Among the aliases only, and never the phrase's own words run together (the code's).
        self.assertEqual(e["everyday_aliases"], ["ice cream", "i scream", "nice beam", "eyes beam"])
        self.assertIn("(everyday talk, not spotted: ice cream, i scream, nice beam, eyes beam)", line["summary"])
        self.assertEqual(self.cache()["incantations"], [e])
        self.assertTrue(self.listed().rstrip().endswith(
            "; everyday, not spotted: ice cream, i scream, nice beam, eyes beam"), self.listed())
        self.assertEqual(self.listed(as_json=True)[0]["everyday_aliases"], e["everyday_aliases"])
        prompt = llm.calls[0]["prompt"]
        for words in ('"ice cream", "i scream", "nice beam" and "eyes beam" are', '("ice been", "juicy stake"',
                      '"destroyed everything"'):
            self.assertIn(words, prompt)
        # The model off: none.
        self.model("off")
        self.create(phrase="ice beam")
        self.assertEqual(self.entry("steve.ice_beam")["everyday_aliases"], [])

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
        self.assertIn("cooldown 600 s", line["facts"]["changed"])     # what it changed, as edit's facts say it
        line = self.run_("incantation_edit", player="steve", phrase="Beam me", cooldown_s="600")
        self.assertTrue(line["ok"], line)                              # the same again: it ran, and changed nothing
        self.assertEqual((line["facts"]["changed"], line["summary"].split(";")[0]),
                         ([], 'changed "beam me" for Steve: nothing new'))
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
        self.assertIn("left out of the aliases (the phrase itself, too short to tell apart, or past 24): fire ball",
                      line["notes"])
        self.assertIn('Its alias "ice walls" sounds like "ice wall", so one may fire for the other.', line["notes"])


    def test_new_aliases_keep_the_everyday_marks_still_among_them(self):
        self.model("test/m")
        llm = fake_llm(["ice been", "ice cream", "i scream"], everyday_variants=["ice cream", "i scream"])
        with mock.patch.dict(sys.modules, {"vfg_llm": llm}), mock.patch.object(self.vfg, "flush_usage_later"):
            self.create(phrase="ice beam")
        self.assertEqual(self.entry("steve.ice_beam")["everyday_aliases"], ["ice cream", "i scream"])
        line = self.run_("incantation_edit", player="Steve", phrase="ice beam", aliases="ice been,i scream,ice queen")
        self.assertTrue(line["ok"], line)
        e = self.entry("steve.ice_beam")
        self.assertEqual((e["aliases"], e["everyday_aliases"]), (["ice been", "i scream", "ice queen"], ["i scream"]))
        self.assertIn("everyday aliases i scream", line["facts"]["changed"])
        self.assertEqual(self.cache()["incantations"], [e])
        line = self.run_("incantation_edit", player="Steve", phrase="ice beam", cooldown_s="4")
        self.assertEqual((line["facts"]["changed"], self.entry("steve.ice_beam")["everyday_aliases"]),
                         (["cooldown 4 s"], ["i scream"]))

    def test_everyday_aliases_set_and_cleared_by_hand(self):
        self.create(phrase="ice beam")
        self.run_("incantation_edit", player="Steve", phrase="ice beam", aliases="ice been,ice cream,i scream")
        self.assertNotIn("everyday_aliases", self.run_("incantation_edit", player="Steve", phrase="ice beam",
                                                       cooldown_s="4")["facts"]["changed"])
        line = self.run_("incantation_edit", player="Steve", phrase="ice beam",
                         everyday_aliases="Ice-Cream,i scream,eyes beam")
        self.assertTrue(line["ok"], line)
        self.assertEqual(self.entry("steve.ice_beam")["everyday_aliases"], ["ice cream", "i scream"])
        self.assertIn("left out of the everyday aliases (not among its aliases): eyes beam", line["notes"])
        self.assertIn('changed "ice beam" for Steve: everyday aliases ice cream, i scream', line["summary"])
        # The aliases and the marks at once: the marks among the new aliases.
        line = self.run_("incantation_edit", player="Steve", phrase="ice beam", aliases="ice been,eyes beam",
                         everyday_aliases="eyes beam")
        e = self.entry("steve.ice_beam")
        self.assertEqual((e["aliases"], e["everyday_aliases"]), (["ice been", "eyes beam"], ["eyes beam"]))
        line = self.run_("incantation_edit", player="Steve", phrase="ice beam", everyday_aliases="")
        self.assertTrue(line["ok"], line)
        self.assertEqual(self.entry("steve.ice_beam")["everyday_aliases"], [])
        self.assertEqual(line["facts"]["changed"], ["everyday aliases none"])
        self.assertIn("everyday_aliases=", self.refused("incantation_edit", player="Steve", phrase="ice beam"))

    def test_an_entry_from_before_the_marks_gets_none_from_new_aliases(self):
        self.create(phrase="ice beam")
        for path in (self.store_path(), os.path.join(self.box.vhome, "incantations.json")):
            with open(path) as f:
                data = json.load(f)
            data["incantations"][0].pop("everyday_aliases")
            with open(path, "w") as f:
                json.dump(data, f)
        line = self.run_("incantation_edit", player="Steve", phrase="ice beam", aliases="ice been,ice cream")
        self.assertTrue(line["ok"], line)
        self.assertNotIn("everyday_aliases", self.entry("steve.ice_beam"))
        self.assertNotIn("everyday", self.listed())
        self.assertEqual(self.listed(as_json=True)[0]["everyday_aliases"], [])

    def test_a_one_word_phrase(self):
        self.create(phrase="dash")
        self.create(phrase="speed dash")
        line = self.run_("incantation_edit", player="Steve", phrase="Dash!", cooldown_s="1", aliases="dashes,dosh")
        self.assertTrue(line["ok"], line)
        e = self.entry("steve.dash")
        self.assertEqual((e["cooldown_s"], e["aliases"]), (1.0, ["dashes"]))
        self.assertEqual(line["notes"], [
            "left out of the aliases (the phrase itself, too short to tell apart, or past 24): dosh",
            '"dash" is short (3 speech sounds), so it may fire by accident: a second word helps.',
            '"dash" sounds like "speed dash", so one may fire for the other.'])


class TestRemove(Case):
    def test_remove_a_one_word_phrase(self):
        self.create(phrase="shield")
        line = self.run_("incantation_remove", player="Steve", phrase="Shield")
        self.assertTrue(line["ok"], line)
        self.assertEqual(line["summary"], 'removed "shield" for Steve; version 2')
        self.assertEqual(self.store()["incantations"], [])

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
                                "cooldown_s": 2.5, "aliases": [], "everyday_aliases": []},
                               {"player": "@a", "phrase": "fire ball", "action": "command",
                                "values": {"commands": FIRE}, "cooldown_s": 3.0, "aliases": ["fireball"],
                                "everyday_aliases": []}])

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


class TestFollow(Case):
    """What was asked ("asked"), kept beside the values, and a binding made again from it over its
    action's defaults after a run of the action: decided from the cache, with no SSH while
    nothing changed."""

    def setUp(self):
        Case.setUp(self)
        self.zap(ZAP)

    def zap(self, text):
        self.box.write("actions/zap.py", text)
        self.box.write("scripts/zap/main.py", ZAP_SCRIPT)
        self.vfg.catalog(self.cfg)[1].refresh()

    def ops(self, since):
        """The remote programs run since call `since`, by op."""
        return [c["args"].get("op") for c in self.box.calls[since:] if "args" in c]

    def test_what_was_asked_is_kept_without_the_caster_fields(self):
        self.create(phrase="zap it", action="zap", values={"power": 4, "title": "bolt", "player": ""})
        e = self.entry("steve.zap_it")
        self.assertEqual((e["asked"], e["caster_fields"]), ({"power": 4, "title": "bolt"}, ["player"]))
        self.assertEqual(e["values"], {"player": "", "power": 4, "speed": 1, "title": "bolt", "extra": False})
        self.assertEqual(self.cache()["incantations"], [e])

    def test_values_words_are_read_as_the_bound_actions_fields_take_them(self):
        line = self.run_("incantation_create", player="Steve", phrase="zap it", action="zap",
                         **{"values.title": "1", "values.power": "4", "values.extra": "true"})
        self.assertTrue(line["ok"], line)
        e = self.entry("steve.zap_it")
        self.assertEqual(e["asked"], {"title": "1", "power": 4, "extra": True})  # text stays text
        self.run_("incantation_edit", player="Steve", phrase="zap it", **{"values.title": "2.5", "values.speed": "2"})
        e = self.entry("steve.zap_it")
        self.assertEqual((e["values"]["title"], e["values"]["speed"], e["asked"]["title"]), ("2.5", 2, "2.5"))
        # A list: JSON, else the field's own reading of the words.
        self.run_("incantation_create", player="Steve", phrase="say it", action="command",
                  **{"values.commands": '["say a, b"]'})
        self.run_("incantation_create", player="Steve", phrase="say more", action="command",
                  **{"values.commands": "say a, say b"})
        self.assertEqual((self.entry("steve.say_it")["values"]["commands"],
                          self.entry("steve.say_more")["values"]["commands"]), (["say a, b"], ["say a", "say b"]))

    def test_edits_merge_what_was_asked_and_a_field_made_the_casters_leaves_it(self):
        self.create(phrase="zap it", action="zap", values={"power": 4})
        read_before = copy.deepcopy(self.vfg.fetch_incantations(self.cfg, self.a))
        self.run_("incantation_edit", player="Steve", phrase="zap it", **{"values.title": "bolt"})
        # The second edit read the entry before the first landed: no title asked, to it.
        with mock.patch.object(self.vfg, "fetch_incantations", lambda cfg, a, stamps=None: read_before):
            self.run_("incantation_edit", player="Steve", phrase="zap it", **{"values.speed": "3",
                                                                              "values.player": "Alex"})
        e = self.entry("steve.zap_it")
        self.assertEqual(e["asked"], {"power": 4, "title": "bolt", "speed": 3, "player": "Alex"})
        self.assertEqual((e["values"]["title"], e["values"]["player"], e["caster_fields"]), ("bolt", "Alex", []))
        line = self.run_("incantation_edit", player="Steve", phrase="zap it", **{"values.player": ""})
        self.assertTrue(line["ok"], line)
        e = self.entry("steve.zap_it")
        self.assertEqual(e["asked"], {"power": 4, "title": "bolt", "speed": 3})
        self.assertEqual((e["values"]["player"], e["caster_fields"]), ("", ["player"]))
        self.assertEqual(self.cache()["incantations"], [e])  # the cache's, as the box made it

    def test_a_run_sends_nothing_more_while_its_bindings_hold_what_they_would_be_made(self):
        self.create(phrase="zap it", action="zap", values={"power": 4})
        before = len(self.box.calls)
        line = self.run_("zap")
        self.assertTrue(line["ok"], line)
        self.assertEqual((line["notes"], self.ops(before)), ([], ["run"]))
        self.assertEqual(self.vfg.refresh_incantations(self.cfg, self.a, "zap"), [])
        self.assertEqual(self.ops(before), ["run"])
        # No cache: nothing to decide from, and a note says so, with no SSH either.
        os.remove(os.path.join(self.box.vhome, "incantations.json"))
        self.assertEqual(self.vfg.refresh_incantations(self.cfg, self.a, "zap"),
                         ["incantations of zap not checked: no cache of them on this PC (vfg incantation list --fresh)"])
        self.assertEqual(self.ops(before), ["run"])

    def test_after_the_action_changed_a_run_makes_its_bindings_again_replacing_their_values(self):
        self.create(phrase="zap it", action="zap", values={"power": 4})
        self.create(phrase="zap old", action="zap", values={"power": 5})
        # A binding made before "asked" was kept stays as it is.
        for path in (self.store_path(), os.path.join(self.box.vhome, "incantations.json")):
            with open(path) as f:
                data = json.load(f)
            data["incantations"][1].pop("asked")
            with open(path, "w") as f:
                json.dump(data, f)
        old = self.entry("steve.zap_old")
        self.zap(ZAP.replace("speed: int = 1", "speed: int = 3").replace(
            '    extra: bool = False\n    """a value the action drops later"""\n', ""))
        before = len(self.box.calls)
        r = self.box.vfg("action", "check", "zap")
        self.assertEqual((r.returncode, r.stdout, self.ops(before)), (0, "ok: actions/zap.py\n", []))  # a check sends nothing
        line = self.run_("zap")
        self.assertTrue(line["ok"], line)
        self.assertEqual(line["notes"], ['"zap it" for Steve now follows zap as it is'])
        self.assertEqual(self.ops(before), ["run", "list", "run"])  # the run, the store read, the update
        e = self.entry("steve.zap_it")
        self.assertEqual((e["values"], e["asked"]), ({"player": "", "power": 4, "speed": 3, "title": ""}, {"power": 4}))
        self.assertEqual(self.entry("steve.zap_old"), old)
        self.assertEqual(sorted(self.cache()["incantations"], key=lambda x: x["id"]),
                         sorted(self.store()["incantations"], key=lambda x: x["id"]))
        before = len(self.box.calls)
        line = self.run_("zap")
        self.assertEqual((line["notes"], self.ops(before)), ([], ["run"]))  # the cache holds them as the box does


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
                                                 "aliases": ["fireball"], "everyday_aliases": []}])

if __name__ == "__main__":
    unittest.main()
