"""casting.py's copy of the kit's reading (scripts/_lib/incantations.py) held to the kit's answers:
`normalize`, and the incantations a player has, one per phrase, as the bridge looks a phrase up
(the kit's `Store.for_player`, then `Casts._one_per_phrase`, then `Store.lookup` from the phrase).
With the game engine's export beside the store, held to the bridge's own code: its export entries
(vfg_bridge/engine.py), `Casts._entries` (the engine's first, hiding the store's with their phrase),
`Casts._one_per_phrase` and `Casts._lookup`. Skipped where the kit or the bridge is not there:
neither is deployed with jev/."""
import importlib.util
import json
import logging
import os
import sys
import tempfile
import unittest

from support import ROOT

import casting

KIT = os.path.join(os.path.dirname(ROOT), "scripts", "_lib", "incantations.py")
BRIDGE = os.path.join(os.path.dirname(ROOT), "bridge")

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



def bound(player, phrase, ability="fireball", **more):
    e = {"id": "%s.%s" % (player.lower(), "_".join(phrase.lower().split())), "player": player, "phrase": phrase,
         "aliases": [], "everyday_aliases": [], "ability": ability, "cooldown_s": 5.0}
    e.update(more)
    return e


ENGINES = [
    [bound("Steve", "fire ball")],                                     # hides Steve's and @a's fire ball
    [bound("steve", "Light Up", ability="glow", cooldown_s=None), bound("Alex", "ice beam", aliases=["ice been"])],
    [bound("STEVE", "dash", aliases=["dsh", "Dash it"], everyday_aliases=["dsh"]), {"player": "Steve", "phrase": 7},
     "junk", bound("Steve", "heal", aliases="hl"), bound("Steve", "zap", cooldown_s=-1), bound("Steve", "", ability="x"),
     dict(bound("Steve", "bolt"), id=None), bound("Steve", "nova", ability="")],
    [],
]


@unittest.skipUnless(os.path.exists(KIT) and os.path.isdir(os.path.join(BRIDGE, "vfg_bridge")),
                     "no scripts/_lib/incantations.py or bridge/vfg_bridge here")
class TestTheBridgesAnswersWithTheEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("kit_incantations_engine", KIT)
        cls.kit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.kit)
        if BRIDGE not in sys.path:
            sys.path.insert(0, BRIDGE)
        from vfg_bridge import casts, engine
        cls.casts, cls.engine = casts, engine

    def bridge(self, store_path, export_path):
        casts = self.casts.Casts(None, None, None, store_path, os.path.dirname(store_path))
        quiet = logging.getLogger("test_kit_pin.bridge")   # the bridge's warning on the bad entries, unprinted
        quiet.addHandler(logging.NullHandler())
        quiet.propagate = False
        casts.engine = self.engine.Engine(os.path.join(os.path.dirname(store_path), "engine.sock"), export_path,
                                          logger=quiet)
        return casts

    def offered(self, casts, name):
        """What the bridge offers `name`, as Layer 2 and the list take it: one per phrase of the
        merged entries (an empty phrase, which neither store keeps, left out as casting.py does)."""
        merged = casts._one_per_phrase(self.kit, casts._entries(self.kit, name))
        return [e for e in merged if self.kit.normalize(e.get("phrase"))]

    def test_the_engines_first_hiding_the_stores_with_their_phrase(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, export = os.path.join(tmp, "incantations.json"), os.path.join(tmp, "engine.json")
            for n, items in enumerate(STORES):
                with open(store, "w") as f:
                    json.dump({"version": n + 1, "incantations": items}, f)
                for m, bindings in enumerate(ENGINES):
                    with open(export, "w") as f:
                        json.dump({"version": m + 1, "incantations": bindings}, f)
                    casts = self.bridge(store, export)
                    for name in ("Steve", "steve", "Alex", "Notch", ""):
                        ours = casting.Incantations(store, export).for_player(name)
                        self.assertEqual(ours, self.offered(casts, name), (n, m, name))
                        merged = casts._entries(self.kit, name)
                        for e in ours:   # the phrase Jev answers, looked up by the bridge, is that entry
                            self.assertEqual(casts._lookup(self.kit, merged, e["phrase"]), e, (n, m, name, e))

    def test_an_export_the_bridge_cannot_read_has_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, export = os.path.join(tmp, "incantations.json"), os.path.join(tmp, "engine.json")
            with open(store, "w") as f:
                json.dump({"version": 1, "incantations": STORES[0]}, f)
            for text in ("{not json", json.dumps({"incantations": ENGINES[0]}),
                         json.dumps({"version": True, "incantations": ENGINES[0]}), json.dumps([ENGINES[0]])):
                with open(export, "w") as f:
                    f.write(text)
                casts = self.bridge(store, export)
                for name in ("Steve", "Notch"):
                    self.assertEqual(casting.Incantations(store, export).for_player(name),
                                     self.offered(casts, name), (text, name))
            os.remove(export)
            self.assertEqual(casting.Incantations(store, export).for_player("Steve"),
                             self.offered(self.bridge(store, export), "Steve"))


if __name__ == "__main__":
    unittest.main()
