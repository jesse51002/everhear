"""casting.py's copy of the bridge's reading held to the bridge's own code: `normalize`
(vfg_bridge/sounds.py), and a player's incantations from the game engine's export (vfg_bridge/
engine.py's entries, then casts.py's `Casts._entries` and `Casts._one_per_phrase`), each looked up
by its phrase as the bridge looks one up (`Casts._lookup`). Skipped where the bridge is not there:
it is not deployed with jev/."""
import json
import logging
import os
import sys
import tempfile
import unittest

from support import ROOT

import casting

BRIDGE = os.path.join(os.path.dirname(ROOT), "bridge")

TEXTS = ["Hot fireball.", "  Fire-Ball!! ", "don't  cast", "Café au lait", "ice_beam", "ÉLAN vital",
         "‘quoted’ word", "fire\tball\nnow", "", None, 42, "Ｆｕｌｌ ｗｉｄｔｈ", "ゆき", "it's... OK?"]


def bound(player, phrase, ability="fireball", **more):
    e = {"id": "%s.%s" % (player.lower(), "_".join(phrase.lower().split())), "player": player, "phrase": phrase,
         "aliases": [], "everyday_aliases": [], "ability": ability, "cooldown_s": 5.0}
    e.update(more)
    return e


EXPORTS = [
    [bound("Steve", "fire ball"), bound("STEVE", "Fire-Ball", ability="other"), bound("steve", "ice beam"),
     bound("Alex", "rock slide"), bound("Steve", "light up", ability=None, scenario="glow", args={"n": 1})],
    [bound("steve", "Light Up", ability="glow", cooldown_s=None), bound("Alex", "ice beam", aliases=["ice been"])],
    [bound("STEVE", "dash", aliases=["dsh", "Dash it"], everyday_aliases=["dsh"]), {"player": "Steve", "phrase": 7},
     "junk", bound("Steve", "heal", aliases="hl"), bound("Steve", "zap", cooldown_s=-1), bound("Steve", "", ability="x"),
     dict(bound("Steve", "bolt"), id=None), bound("Steve", "nova", ability=""), bound("Steve", "both", scenario="m"),
     bound("Steve", "argh", ability=None, scenario="m", args=[1]), dict(bound("x", "nobody"), player=None)],
    [],
]


@unittest.skipUnless(os.path.isdir(os.path.join(BRIDGE, "vfg_bridge")), "no bridge/vfg_bridge here")
class TestTheBridgesAnswers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if BRIDGE not in sys.path:
            sys.path.insert(0, BRIDGE)
        from vfg_bridge import casts, engine, sounds
        cls.casts, cls.engine, cls.sounds = casts, engine, sounds

    def test_normalize(self):
        for text in TEXTS:
            self.assertEqual(casting.normalize(text), self.sounds.normalize(text), repr(text))

    def bridge(self, export_path):
        quiet = logging.getLogger("test_bridge_pin.bridge")   # its warning on the bad entries, unprinted
        quiet.addHandler(logging.NullHandler())
        quiet.propagate = False
        engine = self.engine.Engine(os.path.join(os.path.dirname(export_path), "engine.sock"), export_path,
                                    logger=quiet)
        return self.casts.Casts(None, None, engine)

    def offered(self, casts, name):
        """What the bridge offers `name`, as Layer 2 and the list take it: one per phrase (an empty
        phrase, which the engine never binds, left out as casting.py does)."""
        merged = casts._one_per_phrase(casts._entries(name))
        return [e for e in merged if self.sounds.normalize(e.get("phrase"))]

    def test_a_players_incantations_and_the_lookup_of_each(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = os.path.join(tmp, "incantations.json")
            for m, items in enumerate(EXPORTS):
                with open(export, "w") as f:
                    json.dump({"version": m + 1, "incantations": items}, f)
                casts = self.bridge(export)
                for name in ("Steve", "steve", "Alex", "Notch", ""):
                    ours = casting.Incantations(export).for_player(name)
                    self.assertEqual(ours, self.offered(casts, name), (m, name))
                    entries = casts._entries(name)
                    for e in ours:   # the phrase Jev answers, looked up by the bridge, is that entry
                        self.assertEqual(casts._lookup(entries, e["phrase"]), e, (m, name, e))

    def test_an_export_the_bridge_cannot_read_has_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = os.path.join(tmp, "incantations.json")
            for text in ("{not json", json.dumps({"incantations": EXPORTS[0]}),
                         json.dumps({"version": True, "incantations": EXPORTS[0]}), json.dumps([EXPORTS[0]])):
                with open(export, "w") as f:
                    f.write(text)
                for name in ("Steve", "Notch"):
                    self.assertEqual(casting.Incantations(export).for_player(name),
                                     self.offered(self.bridge(export), name), (text, name))
            os.remove(export)
            self.assertEqual(casting.Incantations(export).for_player("Steve"), [])
            self.assertEqual(self.offered(self.bridge(export), "Steve"), [])


if __name__ == "__main__":
    unittest.main()
