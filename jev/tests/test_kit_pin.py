"""casting.py's copy of the kit's reading (scripts/_lib/incantations.py) held to the kit's answers:
`normalize`, and the incantations a player has, one per phrase, as the bridge looks a phrase up
(the kit's `Store.for_player`, then `Casts._one_per_phrase`, then `Store.lookup` from the phrase).
Skipped where the kit is not there: scripts/ is not deployed with jev/."""
import importlib.util
import json
import os
import tempfile
import unittest

from support import ROOT

import casting

KIT = os.path.join(os.path.dirname(ROOT), "scripts", "_lib", "incantations.py")

TEXTS = ["Hot fireball.", "  Fire-Ball!! ", "don't  cast", "Café au lait", "ice_beam", "ÉLAN vital",
         "‘quoted’ word", "fire\tball\nnow", "", None, 42, "Ｆｕｌｌ ｗｉｄｔｈ", "ゆき", "it's... OK?"]

STORES = [
    [{"player": "Steve", "phrase": "fire ball"}, {"player": "@a", "phrase": "Fire-Ball"},
     {"player": "STEVE", "phrase": "ice beam"}, {"player": "Alex", "phrase": "rock slide"},
     {"player": "@a", "phrase": "light up"}, {"player": "steve", "phrase": "fire  ball"}],
    [{"player": "@a", "phrase": "dash"}, {"player": "@A", "phrase": "heal"}, {"player": "Steve", "phrase": "Dash"}],
    [{"player": "Steve", "phrase": ""}, {"player": "Steve", "phrase": "ok go"}, {"player": None, "phrase": "x"}],
]


@unittest.skipUnless(os.path.exists(KIT), "no scripts/_lib/incantations.py here")
class TestTheKitsAnswers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("kit_incantations", KIT)
        cls.kit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.kit)

    def test_normalize(self):
        for text in TEXTS:
            self.assertEqual(casting.normalize(text), self.kit.normalize(text), repr(text))

    def bridges(self, store, name):
        """What the bridge offers for `name`: the kit's for_player, one per normalised phrase."""
        out, seen = [], set()
        for e in store.for_player(name):
            phrase = self.kit.normalize(e.get("phrase"))
            if phrase not in seen:
                seen.add(phrase)
                out.append(e)
        return out

    def test_a_players_incantations_and_the_lookup_of_each(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "incantations.json")
            for n, items in enumerate(STORES + [STORES[0] + ["junk"]]):
                with open(path, "w") as f:
                    json.dump({"version": n + 1, "incantations": items}, f)
                kit = self.kit.Store(path)
                for name in ("Steve", "steve", "Alex", "Notch", ""):
                    ours = casting.Incantations(path).for_player(name)
                    # an empty phrase cannot be stored (the kit's put refuses it); casting.py skips one
                    theirs = [e for e in self.bridges(kit, name) if self.kit.normalize(e.get("phrase"))]
                    self.assertEqual(ours, theirs, (n, name))
                    for e in ours:   # the phrase Jev answers, looked up by the bridge, is that entry
                        self.assertEqual(kit.lookup(name, e["phrase"]), e, (n, name, e))
            with open(path, "w") as f:
                json.dump({"incantations": STORES[0]}, f)   # no version: the kit reads none
            self.assertEqual(casting.Incantations(path).for_player("Steve"), self.kit.Store(path).for_player("Steve"))


if __name__ == "__main__":
    unittest.main()
