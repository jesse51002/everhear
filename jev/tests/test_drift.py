"""Jev's wording against the wording that was benched, so it never drifts from it: D2's two
questions and their walk (integration/replay/trees_questions.py), the rules, the lookout and the
examples (state_v2.py, with the edits decided since), and the brain's lookout.md. Each is skipped
where its file is not there: integration/ and brain/ are not deployed to the box."""
import ast
import importlib.util
import os
import unittest

from support import ROOT

import prompt

REPO = os.path.dirname(ROOT)
TREES = os.path.join(REPO, "integration", "replay", "trees_questions.py")
STATE_V2 = os.path.join(REPO, "integration", "replay", "state_v2.py")
LOOKOUT_MD = os.path.join(REPO, "brain", "prompts", "lookout.md")


@unittest.skipUnless(os.path.exists(TREES), "no integration/replay/trees_questions.py here")
class TestD2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("trees_questions", TREES)
        cls.trees = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.trees)

    def test_the_questions_word_for_word_in_order(self):
        def flat(d2):
            return [(name, question, list(options.items())) for name, (question, options) in d2.items()]
        self.assertEqual(flat(prompt.D2), flat(self.trees.D2))

    def test_the_walk_is_route_ds(self):
        cls = {"brain": "action_oriented", "respond": "conversational", "context": "context", "dropped": "not_relevant"}
        what, feel = (list(self.trees.D2[name][1]) for name in ("d_what", "d_feel"))
        for w in what:
            for f in feel + [None]:
                answers = {"d_what": (w, 0.5)} if f is None else {"d_what": (w, 0.5), "d_feel": (f, 0.5)}
                self.assertEqual(prompt.route(answers)[0], cls[self.trees.route_d(answers, "D2")["out"]], answers)


@unittest.skipUnless(os.path.exists(STATE_V2), "no integration/replay/state_v2.py here")
class TestState(unittest.TestCase):
    def test_state_v2s_texts_with_the_edits_decided(self):
        with open(STATE_V2, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        v2 = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
              if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "").endswith("_V2")}
        # in production the line routed is always one line said, never pieces joined
        joined = ' It can hold several pieces of what one player said in a row, joined with " … ", in the order said.'
        self.assertIn(joined, v2["RULES_V2"])
        self.assertEqual(prompt.RULES, v2["RULES_V2"].replace(joined, ""))
        self.assertEqual(prompt.DEFAULT_LOOKOUT, v2["LOOKOUT_V2"])
        # a complaint said to the AI is an act; the context example is said to another player
        self.assertEqual(prompt.EXAMPLES, v2["EXAMPLES_V2"]
                         .replace('"stop the rain".', '"stop the rain", "AI, the sword you made me is way too weak".')
                         .replace('"this sword the AI made', '"Alex, this sword the AI made'))


@unittest.skipUnless(os.path.exists(LOOKOUT_MD), "no brain/prompts/lookout.md here")
class TestBrainsLookout(unittest.TestCase):
    def test_the_lookout_the_brain_sets_is_the_default(self):
        with open(LOOKOUT_MD, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), prompt.DEFAULT_LOOKOUT)


if __name__ == "__main__":
    unittest.main()
