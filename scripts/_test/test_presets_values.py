"""Every preset's values class, in one table, with pydantic:

    uv run --no-project --python 3.12 --with pydantic python scripts/_test/test_presets_values.py

For each preset in scripts/_presets/ (vfg script new copies its function; its values class is
read on the PC only): its defaults pass as vfg action run checks a run's values, a number past
its limits is clamped and a word of the wrong kind refused, an option is taken in any case, a
particle that is none is refused, every value is named for the preset, and the file passes
`vfg script check`. Then the values a player could give that a preset must refuse.
"""
import glob
import importlib.util
import os
import sys
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
PRESETS = os.path.join(SCRIPTS, "_presets")
sys.path.append(os.path.dirname(SCRIPTS))  # the repo: a preset's values class imports actions._types
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPTS), "toolkit", "lib"))

import test_kit  # noqa: E402
import library  # noqa: E402


def _load(path):
    """A preset as a module from its file, left out of sys.modules (as the other preset tests
    load it, so a class an action's preset() reads keeps its descriptions)."""
    spec = importlib.util.spec_from_file_location("preset_" + os.path.basename(path)[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PATHS = sorted(glob.glob(os.path.join(PRESETS, "*.py")))
P = {os.path.basename(p)[:-3]: _load(p) for p in PATHS}
# The hits presets read every value at each call, so all their values are live.
ALL_LIVE = ("damage_hits", "freeze", "push", "around", "lock_on", "lightning", "explosion", "crater")
# What a player could ask for that a preset must refuse, not clamp.
REFUSED = (("summon", "summon_mob", "potion"), ("summon", "summon_weapon", "#swords"),
           ("projectile", "projectile_block", "#minecraft:tall_flowers"), ("shape_fill", "shape_offset", "1,up,2"),
           ("shape_fill", "shape_blocks", "#logs"), ("meteor_fall", "meteor_blocks", "0% magma_block"),
           ("replace_blocks", "replace_rules", "stone"), ("crater", "crater_leave", "water"))


class PresetValuesTests(unittest.TestCase):
    def test_every_presets_values_as_vfg_checks_them(self):
        va = test_kit._vfg_actions()
        T = va._types()
        lib = library.Library(os.path.dirname(SCRIPTS))
        self.assertGreaterEqual(len(P), 30)
        for name, mod in sorted(P.items()):
            with self.subTest(preset=name):
                test_kit.assert_checked_on_the_pc(self, mod)
                cls = test_kit.values_class(mod)
                for key, f in cls.model_fields.items():
                    self.assertTrue(library.prefixed(name, key), "%s is not named for %s" % (key, name))
                    kind = T.kind(f.annotation, f.metadata)
                    if kind == "enum":                                  # an option in any case
                        for o in T.choices(f.annotation, f.metadata) or []:
                            self.assertEqual(va.check_values(cls, {key: o.upper()}, allow_fixed=True).values[key], o)
                    if kind == "particle":
                        self.assertRaises(ValueError, va.check_values, cls, {key: "Not A Particle"}, allow_fixed=True)
                    if name in ALL_LIVE:
                        self.assertTrue(T.is_live(f.metadata), "%s: a hits preset reads its values each call" % key)
                item = library.parse(os.path.join(PRESETS, name + ".py"), "preset", source="library",
                                     rel="scripts/_presets/%s.py" % name)
                self.assertEqual(library.check(item, lib), [])

    def test_what_a_preset_refuses(self):
        for name, key, bad in REFUSED:
            with self.subTest(preset=name, value=key):
                with self.assertRaises(ValueError):
                    test_kit.values_meta(P[name], given={key: bad})
        self.assertNotIn("bedrock", str(test_kit.values_meta(P["freeze"])["values"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
