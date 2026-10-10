"""The 16 seed scripts and the run's mountains and mountain_trees, as library scripts: each
started the way run_tests.py starts an example (as on EC2, its defaults, VFG_INSTANCE set,
against fake_world with Steve and Alex online, Steve trying every item it gives him, then
SIGTERM), whether or not a README.md sits beside it; and each seed's README Usage line
naming only values its VALUES declares, in key=value form.

    uv run --python 3.9 python scripts/_test/test_examples_seeds.py

What each does on a real 26.3 server is live_examples_seeds.py.
"""
import ast
import os
import re
import sys
import threading
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run_tests  # noqa: E402  (its fake RCON, fake world and example runner)

SEEDS = ["anti_camp", "attribute_guard", "boom_bow", "chosen_one", "demon_lord", "god_visits", "grave_marks",
         "gun_chests", "herobrine", "kill_fx", "lucky_blocks", "sneaky_creepers", "tnt_rain", "walking_dead",
         "zeus", "zoo"]
KEPT = ["mountains", "mountain_trees"]
RESULTS = {}


def values_of(name):
    with open(os.path.join(run_tests.SCRIPTS, name, "main.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "VALUES" for t in node.targets):
            return ast.literal_eval(node.value)
    return None


class SeedExampleTests(unittest.TestCase):
    check_example = run_tests.ScriptTests.check_example

    @classmethod
    def setUpClass(cls):
        threads = []
        for name in SEEDS + KEPT:
            def go(n=name):
                try:
                    RESULTS[n] = run_tests.run_example(n)
                except Exception as e:  # reported by the test itself
                    RESULTS[n] = {"name": n, "error": repr(e)}
            t = threading.Thread(target=go)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    def check(self, name):
        res = RESULTS[name]
        self.assertNotIn("error", res, res.get("error"))
        self.assertIsNone(res.get("compiles"), "main.py does not compile")
        self.check_example(name, res)
        self.assertIsNotNone(values_of(name), "no VALUES")

    def check_readme(self, name):
        values = values_of(name)
        with open(os.path.join(run_tests.SCRIPTS, name, "README.md"), encoding="utf-8") as f:
            usage = f.read().splitlines()[1]
        self.assertTrue(usage.startswith("Usage: %s" % name), usage)
        self.assertNotIn("<", usage, "a seed's values all have defaults: [key=value], no <positional>")
        keys = re.findall(r"\[([a-z][a-z0-9_]*)=", usage)
        self.assertTrue(keys or not values, "Usage names none of its values: " + usage)
        for k in keys:
            self.assertIn(k, values, "Usage names %s, which VALUES does not declare" % k)


def _add():
    for name in SEEDS + KEPT:
        setattr(SeedExampleTests, "test_example_" + name, lambda self, n=name: self.check(n))
    for name in SEEDS:
        setattr(SeedExampleTests, "test_readme_" + name, lambda self, n=name: self.check_readme(n))


_add()

if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        run_tests.FAKE.stop()
