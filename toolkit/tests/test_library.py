"""vfg script new, check and save, vfg preset save, and the repo's own library: no EC2.

    python3 -m unittest discover -s toolkit/tests

harness.py's box, its repo holding two example scripts with their actions, two presets and a
stand-in kit with values() only; TestRealPresetsTogether copies five of the repo's own presets.
"""
import json
import os
import re
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import REAL_LIB, REPO, TOOLKIT, BoxCase, load_module  # noqa: E402
import standins  # noqa: E402

# A stand-in for scripts/_lib/kit in these tests: values() only, reading what kit.values
# reads — the copy's meta file, <VFG_RUN_DIR>/<VFG_INSTANCE>.json, then for live keys
# <VFG_INSTANCE>.values.json, again on every read.
FAKE_KIT = '''"""A stand-in for the kit: values() only."""
import json
import os


class _Values(dict):
    def __init__(self):
        run = os.environ.get("VFG_RUN_DIR", "/opt/vfg/run")
        name = os.environ.get("VFG_INSTANCE", "")
        with open(os.path.join(run, name + ".json")) as f:
            meta = json.load(f)
        dict.__init__(self, meta["values"])
        self.live = set(meta.get("live") or [])
        self.path = os.path.join(run, name + ".values.json")

    def __getitem__(self, key):
        value = dict.__getitem__(self, key)
        if key in self.live:
            try:
                with open(self.path) as f:
                    value = json.load(f).get(key, value)
            except (OSError, ValueError):
                pass
        return value


def values():
    return _Values()


def result(summary, **facts):
    print("result:", summary)
'''

BEAM = '''"""Beam: prints its values every 0.1 s, so a test can see them change."""
ASKS = ["a beam", "a laser that goes further"]
import json
import time
from kit import values

v = values()
while True:
    print(json.dumps(dict((k, v[k]) for k in v), sort_keys=True), flush=True)
    time.sleep(0.1)
'''

GALICK = '''"""Galick Gun: a beam that hurts what it hits and leaves a crater."""
ASKS = ["galick gun", "a big purple beam"]
from kit import values, aim, area


# --- preset: crater (copied from _presets/crater.py) ---
def crater(at, v):
    _crater_carve(at, v["crater_radius"], v["crater_leave"])


def _crater_carve(at, r, block):
    area.sphere(at, r, block=block, spare_protected=True)


v = values()
crater(aim.ray(v["player"], 60).end, v)
'''

GALICK_ACTION = '''from actions._types import Action, Field, Players, preset


class Galick(Action, preset("crater")):
    """Give a player a galick gun: a beam that leaves a crater."""

    script = "galick"
    runs = "until_stopped"
    takes = ["player", "damage"]
    defaults = {"crater_radius": 4}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    damage: float = Field(20, ge=0, le=100)
    """damage to what the beam hits, in half-hearts"""
'''

CRATER = '''"""Carve a sphere out of the world at a place, leaving air, lava or fire."""
from kit import area
from actions._types import Field, Literal, Live, Values


class Crater(Values):
    crater_radius: Live[int] = Field(8, ge=2, le=64)
    """how wide the crater is, in blocks"""
    crater_leave: Live[Literal["air", "lava", "fire"]] = "air"
    """what is left in it"""


def crater(at, v):
    _crater_carve(at, v["crater_radius"], v["crater_leave"])


def _crater_carve(at, r, block):
    area.sphere(at, r, block=block, spare_protected=True)
'''

EXPLOSION = '''"""An explosion at a place: one blast, a ring or a plus."""
import math
from mc import rcon
from actions._types import Field, Literal, Live, Values


class Explosion(Values):
    explosion_power: Live[float] = Field(4, ge=0, le=20)
    """how strong each blast is"""
    explosion_layout: Literal["one", "ring", "plus"] = "one"
    """one blast, a ring of eight or a plus of four"""


def explosion(at, v):
    for dx, dz in _explosion_spots(v["explosion_layout"]):
        rcon("summon tnt %s %s %s" % (at[0] + dx, at[1], at[2] + dz))


def _explosion_spots(layout):
    n = {"one": 1, "ring": 8, "plus": 4}[layout]
    return [(round(4 * math.cos(i * 6.28 / n)), round(4 * math.sin(i * 6.28 / n))) for i in range(n)]
'''

COUNTER = '''"""Count what happens at a place, once each."""
from mc import log
from actions._types import Field, Values


class Counter(Values):
    counter_every: int = Field(1, ge=1, le=10)
    """log the count every this many"""


_counter_n = [0]


def counter(at, v):
    _counter_n[0] += 1
    _counter_seen.add(at)
    if _counter_n[0] % v["counter_every"] == 0:
        log("count", _counter_n[0])


_counter_seen = set()
'''

TALLY = '''"""Tally the players a script touched."""
from actions._types import Field, Values


class Tally(Values):
    tally_max: int = Field(9, ge=1, le=99)
    """the most a player is counted"""


_tally_known = {}


def tally(p, v):
    _tally_known[p] = min(v["tally_max"], _tally_known.get(p, 0) + 1)
'''


def preset_blocks(text):
    """{preset: the code under its marker, up to the next marker or the wiring}."""
    lines = text.split("\n")
    marks = [i for i, l in enumerate(lines) if l.startswith("# --- preset: ")]
    wiring = next(i for i, l in enumerate(lines) if l.startswith("v = values("))
    out = {}
    for k, i in enumerate(marks):
        end = marks[k + 1] if k + 1 < len(marks) else wiring
        out[re.match(r"# --- preset: (\w+)", lines[i]).group(1)] = "\n".join(lines[i + 1:end]).strip()
    return out


ICE_STAFF = '''"""Ice Staff: lays ice where the player looks."""
ASKS = ["an ice staff"]
import random
from kit import values, aim
from mc import rcon

ICE_BLOCKS = ["ice", "packed_ice"]


def ice_path(p, v):
    """Lay ice along where a player looks, some of it packed."""
    for at in aim.ray(p, v["length"]).points:
        _lay(at, v)


def _lay(at, v):
    if random.random() < v["ice_rate"]:
        rcon("setblock %d %d %d %s" % (at[0], at[1], at[2], random.choice(ICE_BLOCKS)))


def shout(p):
    rcon("say %s" % p)


def loud_path(p, v):
    shout(p)
    ice_path(p, v)


v = values()
'''

ICE_STAFF_ACTION = '''from actions._types import Action, Field, Live, Players


class IceStaff(Action):
    """Give a player an ice staff: ice where they look."""

    script = "ice_staff"
    runs = "until_stopped"
    takes = ["player", "length"]

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    length: Live[int] = Field(60, ge=5, le=200)
    """how far the ice reaches, in blocks"""
    ice_rate: float = Field(0.5, ge=0, le=1)
    """how much of it is laid, from 0 to 1"""
'''


_LIBRARY = []


def load_library_module():
    """toolkit/lib/library.py, loaded once."""
    if not _LIBRARY:
        _LIBRARY.append(load_module("vfg_library", os.path.join(TOOLKIT, "lib", "library.py")))
    return _LIBRARY[0]


# A preset whose function calls others: (first line, class, value, function, what it returns).
PRESET_CALLING = '''"""%s"""
from actions._types import Field, Live, Values


class %s(Values):
    %s: Live[float] = Field(4, ge=1, le=40)
    """how much, in blocks"""


def %s(hits, v):
    return %s
'''


class LibraryCase(BoxCase):
    """A box whose repo has two example scripts with their actions, two presets and a fake kit."""

    def setUp(self):
        BoxCase.setUp(self)
        self.write("scripts/beam/main.py", BEAM)
        self.write("scripts/galick/main.py", GALICK)
        self.write("scripts/_presets/crater.py", CRATER)
        self.write("scripts/_presets/explosion.py", EXPLOSION)
        self.write("scripts/_lib/kit/__init__.py", FAKE_KIT)
        standins.install_types(self.box.repo)
        self.write("actions/beam.py", standins.ACTIONS["beam"])
        self.write("actions/galick.py", GALICK_ACTION)

    def write(self, rel, text):
        path = os.path.join(self.box.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def read(self, rel):
        with open(os.path.join(self.box.repo, rel)) as f:
            return f.read()

    def made(self, *p):
        return os.path.join(self.box.repo, "made", "main", *p)


def finish_todos(path, line="It does a thing players asked for."):
    """What an implementer does after vfg script new: the action's line and descriptions."""
    with open(path) as f:
        text = f.read()
    text = text.replace("TODO: what it does, in players' words: its line on the menu.", line)
    text = text.replace("TODO: what it is, in players' words", "a value players change")
    with open(path, "w") as f:
        f.write(text)


class TestScriptNew(LibraryCase):
    def test_new_writes_a_script_and_its_action_and_check_says_what_is_left(self):
        r = self.vfg("script", "new", "beam_boom", "--from", "beam", "--preset", "crater",
                         "--preset", "explosion")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines()[:4], [
            "made made/main/scripts/beam_boom/ from scripts/beam/ (library)",
            "  + crater (library): crater_radius, crater_leave",
            "  + explosion (library): explosion_power, explosion_layout",
            'made made/main/actions/beam_boom.py: class BeamBoom(Action, preset("crater"), preset("explosion"))'])
        path = self.made("scripts", "beam_boom", "main.py")
        text = self.read("made/main/scripts/beam_boom/main.py")
        self.assertIn("from kit import values, area\n", text)
        self.assertIn("import math\n", text)
        self.assertIn("from mc import rcon\n", text)
        self.assertNotIn("actions", text)                # a values class stays on the PC
        crater = text.index("# --- preset: crater (copied from _presets/crater.py) ---\ndef crater(at, v):")
        boom = text.index("# --- preset: explosion (copied from _presets/explosion.py) ---\ndef explosion(at, v):")
        wiring = text.index("v = values()\n")
        self.assertLess(crater, boom)
        self.assertLess(boom, wiring)
        self.assertTrue(text.startswith(BEAM.split("\n")[0]))
        compile(text, path, "exec")
        action = self.read("made/main/actions/beam_boom.py")
        self.assertIn('class BeamBoom(Action, preset("crater"), preset("explosion")):\n', action)
        self.assertIn('    script = "beam_boom"\n', action)
        self.assertIn('    takes = ["player", "cooldown"]\n', action)
        self.assertIn('    fixed = {"colour": "red"}\n', action)
        self.assertIn("    cooldown: Live[float] = Field(10, ge=0, le=60)\n    \"\"\"seconds between shots\"\"\"\n",
                      action)
        self.assertNotIn("crater_leave", action)        # inherited, with its description
        # What is left to do: the action's line.
        self.assertIn("check: 1 to fix before it is done:", r.stdout)
        self.assertIn("made/main/actions/beam_boom.py: its line on the menu is still a TODO", r.stdout)
        finish_todos(self.made("actions", "beam_boom.py"))
        r = self.vfg("script", "check", "beam_boom")
        self.assertOk(r)
        self.assertEqual(r.stdout, "ok: made/main/scripts/beam_boom/main.py\n")
        r = self.vfg("action", "check", "beam_boom")
        self.assertEqual((r.returncode, r.stdout), (0, "ok: made/main/actions/beam_boom.py\n"), r.stderr)
        # A made script and its action are copied the same way.
        r = self.vfg("script", "new", "boom2", "--from", "beam_boom")
        self.assertOk(r)
        self.assertIn("from made/main/scripts/beam_boom/ (made)", r.stdout)
        self.assertIn('class Boom2(Action, preset("crater"), preset("explosion")):\n',
                      self.read("made/main/actions/boom2.py"))

    def test_a_preset_brings_the_presets_it_calls_each_once(self):
        """kick calls sling, which calls toss (as push calls launch): asked for kick, a script gets all
        three, each once, and its action inherits each one's values; one given or held is not added again."""
        self.write("scripts/_presets/toss.py", PRESET_CALLING % ("Toss what was hit up.", "Toss", "toss_height",
                                                               "toss", "len(hits)"))
        self.write("scripts/_presets/sling.py", PRESET_CALLING % ("Sling what was hit away.", "Sling", "sling_far",
                                                                "sling", "toss(hits, v)"))
        self.write("scripts/_presets/kick.py", PRESET_CALLING % ("Kick what was hit, then sling it.", "Kick",
                                                               "kick_power", "kick", "sling(hits, v) + toss(hits, v)"))
        r = self.vfg("script", "new", "kicker", "--from", "beam", "--preset", "kick")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines()[1:4], [
            "  + kick (library): kick_power", "  + sling (library, which kick needs): sling_far",
            "  + toss (library, which kick needs): toss_height"])
        text = self.read("made/main/scripts/kicker/main.py")
        for name in ("kick", "sling", "toss"):
            self.assertEqual(text.count("# --- preset: %s (" % name), 1, name)
        compile(text, "kicker", "exec")
        self.assertIn('class Kicker(Action, preset("kick"), preset("sling"), preset("toss")):\n',
                      self.read("made/main/actions/kicker.py"))
        # given too: not twice
        r = self.vfg("script", "new", "slinger", "--preset", "toss", "--preset", "sling")
        self.assertOk(r)
        self.assertEqual(self.read("made/main/scripts/slinger/main.py").count("# --- preset: toss ("), 1)
        self.assertNotIn("which", r.stdout)
        # a script copied --from that holds sling but not toss (made before sling called it) gets toss
        old = self.read("made/main/scripts/slinger/main.py")
        start = old.index("# --- preset: toss (")
        end = old.index("# --- preset: sling (")
        self.write("made/main/scripts/slinger/main.py", old[:start] + old[end:])
        r = self.vfg("script", "new", "slinger_2", "--from", "slinger")
        self.assertOk(r)
        self.assertIn("  + toss (library, which sling needs): toss_height", r.stdout)
        self.assertEqual(self.read("made/main/scripts/slinger_2/main.py").count("# --- preset: toss ("), 1)
        # a name it takes or sets itself is no need
        lob = PRESET_CALLING % ("Lob it.", "Lob", "lob_far", "lob", "[toss for toss in hits] or kick")
        self.write("scripts/_presets/lob.py", lob.replace("def lob(hits, v):", "def lob(hits, v, kick=None):"))
        r = self.vfg("script", "new", "lobber", "--preset", "lob")
        self.assertOk(r)
        self.assertNotIn("which", r.stdout)

    def test_new_from_a_script_ends_with_its_outline(self):
        r = self.vfg("script", "new", "beam_boom", "--from", "beam", "--preset", "crater")
        self.assertOk(r)
        text = self.read("made/main/scripts/beam_boom/main.py").split("\n")
        tail = r.stdout.rstrip("\n").split("\n")
        at = tail.index("outline of made/main/scripts/beam_boom/main.py (line, then what is there):")
        self.assertTrue(tail[at - 1].startswith("next: "))
        outline = tail[at + 1:]
        self.assertTrue(outline and all(re.match(r"  \d+ (ASKS = |def |class )", x) for x in outline), outline)
        self.assertIn("ASKS = ", outline[0])
        self.assertIn("  %d def crater(at, v)" % (text.index("def crater(at, v):") + 1), outline)
        for x in outline:  # each line number points at what it names
            n, what = x.split(" ", 3)[2], x.split(" ", 3)[3]
            self.assertTrue(text[int(n) - 1].startswith(what.split("(")[0].split(" = ")[0]), x)
        r = self.vfg("script", "new", "blank_one")  # from nothing: no outline
        self.assertNotIn("outline of", r.stdout)

    def test_a_scripts_action_is_carried_with_its_presets_inherited(self):
        r = self.vfg("script", "new", "galick_pit", "--from", "galick", "--preset", "explosion")
        self.assertOk(r)
        self.assertIn('made made/main/actions/galick_pit.py: class GalickPit(Action, preset("crater"), '
                      'preset("explosion"))', r.stdout)
        action = self.read("made/main/actions/galick_pit.py")
        self.assertNotIn("crater_radius:", action)      # inherited, with its description
        self.assertIn('    takes = ["player", "damage"]\n', action)
        self.assertIn('    defaults = {"crater_radius": 4}\n', action)
        self.assertIn('    damage: float = Field(20, ge=0, le=100)\n', action)
        self.assertEqual([l for l in r.stdout.splitlines() if "TODO" in l],
                         ["  made/main/actions/galick_pit.py: its line on the menu is still a TODO: say what it "
                          "does, in players' words"])

    def test_the_actions_own_words_for_what_it_inherits_are_carried_too(self):
        self.write("actions/galick.py", GALICK_ACTION.replace(
            '    defaults = {"crater_radius": 4}\n',
            '    defaults = {"crater_radius": 4}\n    descriptions = {"crater_radius": "how wide the galick pit is"}\n'))
        r = self.vfg("script", "new", "galick_pit", "--from", "galick", "--preset", "explosion")
        self.assertOk(r)
        action = self.read("made/main/actions/galick_pit.py")
        self.assertIn('    defaults = {"crater_radius": 4}\n    descriptions = {"crater_radius": "how wide the galick pit '
                      'is"}\n', action)
        finish_todos(self.made("actions", "galick_pit.py"))
        r = self.vfg("action", "check", "galick_pit")
        self.assertEqual((r.returncode, r.stdout), (0, "ok: made/main/actions/galick_pit.py\n"), r.stderr)
        got = json.loads(self.vfg("action", "list", "--json").stdout)["actions"]["galick_pit"]
        self.assertEqual(got["fields"]["crater_radius"]["description"], "how wide the galick pit is")
        # From a script whose action gives no words of its own, none.
        r = self.vfg("script", "new", "beam_pit", "--from", "beam", "--preset", "crater")
        self.assertOk(r)
        self.assertNotIn("descriptions", self.read("made/main/actions/beam_pit.py"))

    def test_a_presets_own_variables_stay_in_its_block(self):
        self.write("scripts/_presets/counter.py", COUNTER)
        self.write("scripts/_presets/tally.py", TALLY)
        r = self.vfg("script", "new", "counted", "--from", "beam", "--preset", "counter",
                         "--preset", "tally", "--preset", "crater")
        self.assertOk(r)
        text = self.read("made/main/scripts/counted/main.py")
        L = load_library_module()
        lib = L.Library(self.box.repo, "main")
        blocks = preset_blocks(text)
        self.assertEqual(list(blocks), ["counter", "tally", "crater"])
        for name in blocks:
            self.assertEqual(blocks[name], L.preset_body(lib.preset(name)).strip(), name)
        self.assertLess(text.index("_counter_seen = set()"), text.index("# --- preset: tally"))

    def test_a_preset_the_script_already_holds_and_one_it_does_not(self):
        r = self.vfg("script", "new", "galick2", "--from", "galick", "--preset", "crater")
        self.assertOneLine(r, 1, "^vfg: cannot copy crater in: it already holds crater$")
        self.assertFalse(os.path.exists(self.made("scripts", "galick2")))
        self.assertFalse(os.path.exists(self.made("actions", "galick2.py")))
        r = self.vfg("script", "new", "galick2", "--from", "galick", "--preset", "explosion")
        self.assertOk(r)
        text = self.read("made/main/scripts/galick2/main.py")
        self.assertEqual(text.count("# --- preset: "), 2)
        self.assertLess(text.index("# --- preset: crater"), text.index("# --- preset: explosion"))
        self.assertLess(text.index("# --- preset: explosion"), text.index("v = values()"))
        finish_todos(self.made("actions", "galick2.py"))
        self.assertOk(self.vfg("script", "check", "galick2"))

    def test_names_are_never_taken_twice(self):
        self.assertOneLine(self.vfg("script", "new", "beam"), 1, r"^vfg: beam is taken: scripts/beam — ")
        self.assertOneLine(self.vfg("script", "new", "galick"), 1, "galick is taken")
        self.assertOk(self.vfg("script", "new", "mine", "--from", "beam"))
        self.assertOneLine(self.vfg("script", "new", "mine"), 1, r"mine is taken: made/main/scripts/mine")
        self.assertOneLine(self.vfg("script", "new", "mine", "--library"), 1, "mine is taken")
        self.write("actions/zap.py", "# an action's name\n")
        self.assertOneLine(self.vfg("script", "new", "zap"), 1, r"^vfg: zap is taken: actions/zap\.py")
        for bad in ("Bad.Name", "with-dash", "9lives"):
            self.assertOneLine(self.vfg("script", "new", bad), 2)
        self.assertOneLine(self.vfg("script", "new", "x", "--preset", "nosuch"), 1, "no preset nosuch")
        self.assertOneLine(self.vfg("script", "new", "x", "--from", "nosuch"), 1, "no script nosuch")
        self.assertOneLine(self.vfg("script", "new", "x", "--preset", "crater", "--preset", "crater"), 2)
        self.assertFalse(os.path.exists(self.made("scripts", "x")))

    def test_new_from_nothing_must_be_filled_in(self):
        r = self.vfg("script", "new", "fresh", "--preset", "crater")
        self.assertOk(r)
        self.assertIn("made made/main/scripts/fresh/ from nothing", r.stdout)
        self.assertIn("first line is still a TODO", r.stdout)
        self.assertIn("ASKS is empty", r.stdout)
        action = self.read("made/main/actions/fresh.py")
        self.assertIn('class Fresh(Action, preset("crater")):\n', action)
        self.assertIn('    player: Players = "@a"\n    """who it is for: a name, several separated by commas, or @a"""', action)
        r = self.vfg("script", "check", "fresh")
        self.assertOneLine(r, 1, r"^vfg: 3 problems in 1 of 1 file$")
        self.assertEqual(len(r.stdout.splitlines()), 3)

    def test_library_flag_writes_into_scripts_and_actions(self):
        r = self.vfg("script", "new", "beam_too", "--from", "beam", "--library")
        self.assertOk(r)
        self.assertTrue(os.path.isfile(os.path.join(self.box.repo, "scripts", "beam_too", "main.py")))
        self.assertTrue(os.path.isfile(os.path.join(self.box.repo, "actions", "beam_too.py")))
        self.assertFalse(os.path.exists(self.made("scripts", "beam_too")))


BAD = {
    # (kind, text, what the problem line says)
    "undeclared": ("script", '"""Bad: x."""\nASKS = ["x"]\nv = {}\nprint(v["b"])\n',
                   r':4: v\["b"\] is read but its action has no field of that name'),
    "no_action": ("script", '"""Bad: x."""\nASKS = ["x"]\n', r":1: no action runs it"),
    "no_doc": ("script", 'ASKS = ["x"]\n', r":1: line 1 must be a docstring"),
    "no_asks": ("script", '"""Bad: x."""\n', r":1: no ASKS"),
    "imports_presets": ("script", '"""Bad: x."""\nASKS = ["x"]\nfrom _presets import crater\n',
                        r":3: imports from _presets"),
    "py310": ("script", '"""Bad: x."""\nASKS = ["x"]\nmatch 1:\n    case 1:\n        pass\n',
              r":\d+: not valid Python 3\.9"),
    "blast": ("preset", '"""A preset whose value lacks its name."""\nfrom actions._types import Values\n\n\n'
                        'class Blast(Values):\n    power: float = 1\n    """how strong"""\n\n\n'
                        'def blast(at, v):\n    return v["power"]\n',
              r":6: power does not start with the preset's name: blast_"),
    "no_class": ("preset", '"""A preset that reads a value it never declares."""\n\n\n'
                           'def nothing(at, v):\n    return v["nothing_much"]\n',
                 r':5: v\["nothing_much"\] is read but it has no values class to declare it'),
    "no_description": ("preset", '"""A preset with a value it never describes."""\nfrom actions._types import Values'
                                 '\n\n\nclass NoDescription(Values):\n    no_description_n: int = 1\n\n\n'
                                 'def no_description(at, v):\n    return v["no_description_n"]\n',
                       r":6: no_description_n has no description"),
    "bad_type": ("preset", '"""A preset whose value has a type no action takes."""\nfrom typing import Optional\n'
                           'from actions._types import Values\n\n\nclass BadType(Values):\n'
                           '    bad_type_n: Optional[int] = None\n    """how many"""\n\n\n'
                           'def bad_type(at, v):\n    return v["bad_type_n"]\n',
                 r": bad_type_n: Optional\[int\] is not one of actions/_types\.py's types"),
    "not_loading": ("preset", '"""A preset whose values class needs what the PC does not read."""\nimport datetime\n'
                              'from actions._types import Values\n\n\nclass NotLoading(Values):\n'
                              '    not_loading_when: str = str(datetime.date(2026, 1, 1))\n    """when"""\n\n\n'
                              'def not_loading(at, v):\n    return v["not_loading_when"]\n',
                    r": its values class does not load: NameError"),
}


class TestScriptCheck(LibraryCase):
    def test_check_refuses_each_bad_case(self):
        for name, (kind, text, want) in sorted(BAD.items()):
            rel = ("made/main/scripts/%s/main.py" % name) if kind == "script" else "made/main/presets/%s.py" % name
            self.write(rel, text)
            if kind == "script" and name != "no_action":
                self.write("made/main/actions/%s.py" % name, 'from actions._types import Action\n\n\n'
                           'class %s(Action):\n    """A test\'s."""\n\n    script = "%s"\n    runs = "once"\n'
                           % ("".join(w.title() for w in name.split("_")), name))
            r = self.vfg("script", "check", name)
            self.assertOneLine(r, 1, r"^vfg: \d+ problems? in 1 of 1 file$")
            self.assertRegex(r.stdout, "^" + re.escape(rel) + want, name)
            # Also by path.
            r = self.vfg("script", "check", os.path.join(self.box.repo, rel))
            self.assertEqual(r.returncode, 1, name)

    def test_check_everything_passes_and_counts(self):
        r = self.vfg("script", "check")
        self.assertOk(r)
        self.assertEqual(r.stdout, "ok: 2 scripts and 2 presets\n")
        self.write("made/main/presets/blast.py", BAD["blast"][1])
        r = self.vfg("script", "check")
        self.assertOneLine(r, 1, r"^vfg: 1 problem in 1 of 5 files$")
        self.assertOneLine(self.vfg("script", "check", "nosuch"), 1, "no script or preset nosuch")

    def test_a_preset_value_never_starts_with_another_presets_name(self):
        self.write("made/main/presets/crater_rim.py", CRATER.replace("def crater(", "def crater_rim(")
                   .replace("class Crater(", "class CraterRim(")
                   .replace("def _crater_carve(", "def _crater_rim_carve(").replace("_crater_carve(at,", "_crater_rim_carve(at,"))
        r = self.vfg("script", "check", "crater_rim")
        self.assertOneLine(r, 1, r"^vfg: 2 problems in 1 of 1 file$")
        self.assertIn("crater_radius starts with crater_, another preset's name", r.stdout)

    def test_a_preset_helper_carries_its_name_and_a_script_defines_nothing_twice(self):
        self.write("made/main/presets/twin.py", CRATER.replace("def crater(", "def twin(").replace("class Crater(", "class Twin(")
                   .replace("    crater_", "    twin_").replace('v["crater_', 'v["twin_'))
        r = self.vfg("script", "check", "twin")
        self.assertOneLine(r, 1)
        self.assertIn("_crater_carve: a preset's helpers are private and carry its name: _twin_", r.stdout)
        self.write("made/main/scripts/double/main.py", GALICK.replace(
            "\n\nv = values()", "\n\ndef _crater_carve(at, r, block):\n    pass\n\n\nv = values()"))
        r = self.vfg("script", "check", "double")
        self.assertOneLine(r, 1)
        self.assertRegex(r.stdout, r"main\.py:\d+: _crater_carve is defined twice")


class TestPresetSave(LibraryCase):
    def setUp(self):
        LibraryCase.setUp(self)
        self.write("made/main/scripts/ice_staff/main.py", ICE_STAFF)
        self.write("made/main/actions/ice_staff.py", ICE_STAFF_ACTION)

    def test_lift_a_function_then_copy_it_into_a_script(self):
        r = self.vfg("preset", "save", "ice_staff", "ice_path", "ice_trail")
        self.assertOk(r)
        self.assertEqual(r.stdout.splitlines()[0],
                         "saved made/main/presets/ice_trail.py from ice_staff.ice_path: values "
                         "ice_trail_length (was length), ice_rate; helpers _ice_trail_lay")
        self.assertIn("check: ok", r.stdout)
        self.assertIn("next: read its values class", r.stdout)
        text = self.read("made/main/presets/ice_trail.py")
        self.assertTrue(text.startswith('"""Lay ice along where a player looks, some of it packed."""\n'))
        L = load_library_module()
        self.assertEqual(L.values_class_fields(L.parse(self.made("presets", "ice_trail.py"), "preset")),
                         ["ice_trail_length", "ice_rate"])
        self.assertIn("from actions._types import Field, Live, Values  # the values class: read on the PC only\n",
                      text)
        self.assertIn('class IceTrail(Values):\n    ice_trail_length: Live[int] = Field(60, ge=5, le=200)\n'
                      '    """how far the ice reaches, in blocks"""\n    ice_rate: float = Field(0.5, ge=0, le=1)\n'
                      '    """how much of it is laid, from 0 to 1"""\n', text)
        self.assertIn("def ice_trail(p, v):", text)
        self.assertIn('aim.ray(p, v["ice_trail_length"])', text)
        self.assertIn("def _ice_trail_lay(at, v):", text)
        self.assertIn("ICE_BLOCKS = ", text)
        for imp in ("import random\n", "from kit import aim\n", "from mc import rcon\n"):
            self.assertIn(imp, text)
        self.assertNotIn("def shout", text)
        # The script it came from is not touched.
        self.assertEqual(self.read("made/main/scripts/ice_staff/main.py"), ICE_STAFF)
        r = self.vfg("script", "new", "beam_ice", "--from", "beam", "--preset", "ice_trail")
        self.assertOk(r)
        self.assertIn("  + ice_trail (made): ice_trail_length, ice_rate", r.stdout)
        self.assertIn('class BeamIce(Action, preset("ice_trail")):\n', self.read("made/main/actions/beam_ice.py"))
        self.assertIn("# --- preset: ice_trail (copied from made/main/presets/ice_trail.py) ---",
                      self.read("made/main/scripts/beam_ice/main.py"))
        finish_todos(self.made("actions", "beam_ice.py"))
        self.assertOk(self.vfg("action", "check", "beam_ice"))

    def test_a_value_the_action_does_not_describe_is_a_todo_to_fill(self):
        self.write("made/main/actions/ice_staff.py", ICE_STAFF_ACTION.replace(
            '    """how much of it is laid, from 0 to 1"""\n', '    """TODO"""\n'))
        r = self.vfg("preset", "save", "ice_staff", "ice_path", "ice_trail")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ice_rate's description is still a TODO", r.stdout)

    def test_refusals(self):
        self.assertOk(self.vfg("preset", "save", "ice_staff", "ice_path", "ice_trail"))
        self.assertOneLine(self.vfg("preset", "save", "ice_staff", "ice_path", "ice_trail"), 1,
                           "ice_trail is taken: made/main/presets/ice_trail.py")
        self.assertOneLine(self.vfg("preset", "save", "ice_staff", "ice_path", "crater"), 1,
                           "crater is taken: scripts/_presets/crater.py")
        self.assertOneLine(self.vfg("preset", "save", "ice_staff", "nosuch", "x"), 1,
                           "ice_staff has no top-level function nosuch")
        self.assertOneLine(self.vfg("preset", "save", "ice_staff", "loud_path", "loud"), 1,
                           r"loud_path calls ice_path, shout, which is public in ice_staff")
        self.assertOneLine(self.vfg("preset", "save", "nosuch", "f", "x"), 1, "no script nosuch")
        self.assertOneLine(self.vfg("preset", "save", "ice_staff", "ice_path", "Bad"), 2)
        os.remove(self.made("actions", "ice_staff.py"))
        self.assertOneLine(self.vfg("preset", "save", "ice_staff", "ice_path", "ice_lane"), 1,
                           r"ice_path reads length, ice_rate, which ice_staff's action has no field for "
                           r"\(no action runs ice_staff\)")
        r = self.vfg("preset", "save", "ice_staff", "shout", "shout_out")
        self.assertOk(r)  # saved, but its first line is a TODO to fill in
        self.assertIn("first line is still a TODO", r.stdout)
        self.assertIn("def shout_out(p, v):", self.read("made/main/presets/shout_out.py"))


# ========================================================== the real kit

class TestRealPresetsTogether(BoxCase):
    """The repo's own presets with module-level variables of their own (_projectile_n,
    _meteor_fall_n, _replace_blocks_known), and two more, copied into one script: each
    preset's code stays whole under its marker, check passes, and it runs on the real kit."""
    PRESETS = ["projectile", "meteor_fall", "replace_blocks", "crater", "explosion"]

    def setUp(self):
        BoxCase.setUp(self)
        have = [os.path.isfile(os.path.join(REPO, "scripts", "_presets", p + ".py")) for p in self.PRESETS]
        if not all(have) or not os.path.isfile(os.path.join(REAL_LIB, "kit", "values.py")):
            self.skipTest("needs scripts/_presets/{%s}.py and scripts/_lib/kit" % ",".join(self.PRESETS))
        lib = os.path.join(self.box.repo, "scripts", "_lib")
        shutil.copytree(REAL_LIB, lib, ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(self.box.repo, "scripts", "_presets"))
        standins.install_types(self.box.repo)
        for p in self.PRESETS:
            shutil.copy(os.path.join(REPO, "scripts", "_presets", p + ".py"),
                        os.path.join(self.box.repo, "scripts", "_presets", p + ".py"))

    def test_five_presets_in_one_script_each_whole_under_its_marker(self):
        args = ["script", "new", "together"]
        for p in self.PRESETS:
            args += ["--preset", p]
        self.assertOk(self.vfg(*args))
        path = os.path.join(self.box.repo, "made", "main", "scripts", "together", "main.py")
        with open(path) as f:
            text = f.read()
        L = load_library_module()
        lib = L.Library(self.box.repo, "main")
        blocks = preset_blocks(text)
        self.assertEqual(list(blocks), self.PRESETS)
        for name in self.PRESETS:
            self.assertEqual(blocks[name], L.preset_body(lib.preset(name)).strip(), name)
        text = text.replace("TODO <Title>: say what it does in one line.", "Together: five presets in one.") \
            .replace("ASKS = []", 'ASKS = ["all of them at once"]')
        with open(path, "w") as f:
            f.write(text)
        finish_todos(os.path.join(self.box.repo, "made", "main", "actions", "together.py"))
        r = self.vfg("script", "check", "together")
        self.assertOk(r)


# ======================================================= the real library


if __name__ == "__main__":
    unittest.main()
