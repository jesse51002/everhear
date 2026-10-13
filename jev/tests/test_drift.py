"""Jev's wording against the wording that was benched, so it never drifts from it: the routing call's
rules, examples, lookout and two questions are the bench's SHIPPED (`b4`) of
integration/jev_addressing_bench.py, word for word (Prototype_Addressing_The_AI's bench on the night
of 2026-10-13), and the lookout the brain sets, brain/prompts/lookout.md, is the default lookout.
Each is skipped where its file is not there: integration/ and brain/ are not deployed to the box."""
import importlib.util
import os
import unittest

from support import ROOT

import prompt

REPO = os.path.dirname(ROOT)
BENCH = os.path.join(REPO, "integration", "jev_addressing_bench.py")
LOOKOUT_MD = os.path.join(REPO, "brain", "prompts", "lookout.md")


@unittest.skipUnless(os.path.exists(BENCH), "no integration/jev_addressing_bench.py here")
class TestBenched(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("jev_addressing_bench", BENCH)
        cls.bench = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.bench)   # asserts it too, as it loads

    def test_the_routing_call_is_the_shipped_candidate_word_for_word(self):
        self.assertEqual(self.bench.SHIPPED, "b4")
        self.assertTrue(self.bench._shipped_is_production())

    def test_the_bench_asks_production_as_prod(self):
        c = self.bench.candidate("prod")
        self.assertIs(c["questions"], prompt.questions)
        self.assertIs(c["route"], prompt.route)
        self.assertEqual((c["rules"], c["examples"], c["lookout"], c["required"]),
                         (prompt.RULES, prompt.EXAMPLES, prompt.DEFAULT_LOOKOUT, prompt.QUESTION))

    def test_the_shipped_walk_is_productions_classes(self):
        shipped = self.bench.candidate(self.bench.SHIPPED)
        for who in prompt.ROUTING[prompt.QUESTION][1]:
            for keep in list(prompt.ROUTING[prompt.KEEP][1]) + [None]:
                answers = {prompt.QUESTION: (who, 0.5)}
                if keep:
                    answers[prompt.KEEP] = (keep, 0.4)
                self.assertEqual(shipped["route"](answers)[0], prompt.route(answers)[0], answers)


@unittest.skipUnless(os.path.exists(LOOKOUT_MD), "no brain/prompts/lookout.md here")
class TestBrainsLookout(unittest.TestCase):
    def test_the_lookout_the_brain_sets_is_the_default(self):
        with open(LOOKOUT_MD, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), prompt.DEFAULT_LOOKOUT)


if __name__ == "__main__":
    unittest.main()
