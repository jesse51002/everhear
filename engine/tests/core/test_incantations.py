"""Incantations: per-player bindings phrase -> ability, in the engine's state, exported atomically
with a version bump; the `cast` op's answers (the game engine spec, "Incantations")."""

import importlib.util
import json
import os

import pytest

from vfg_engine import incantations

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
KIT = os.path.join(REPO, "scripts", "_lib", "incantations.py")

TEXTS = ["Hot fireball.", "  Fire-Ball!! ", "don't  cast", "Café au lait", "ice_beam", "ÉLAN vital",
         "‘quoted’ word", "fire\tball\nnow", "", None, 42, "Ｆｕｌｌ ｗｉｄｔｈ", "ゆき", "it's... OK?"]

ZAP = '''
class Zap(Ability):
    class Config(AbilityConfig):
        summary = "Zap."
        damage = 1
        duration_s = 1
        charge_s = 0
        cooldown_s = 2.5
        scale_per_point = 0

    def on_cast(self, ctx, ev):
        self.command("say zap " + str(ev.phrase))
'''


@pytest.mark.skipif(not os.path.exists(KIT), reason="no scripts/_lib/incantations.py here")
def test_normalize_is_the_kits():
    spec = importlib.util.spec_from_file_location("kit_incantations", KIT)
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    for text in TEXTS:
        assert incantations.normalize(text) == kit.normalize(text), repr(text)
    assert incantations.MAX_PHRASE == kit.MAX_PHRASE
    for player, phrase in [("Steve", "Fire Ball!"), ("ALEX", "ice-beam")]:
        assert incantations.entry_id(player, phrase) == kit.entry_id(player, phrase)


def _zapper(rig):
    r = rig({"zap": ZAP})
    r.start()
    r.io.join("Steve")
    r.io.join("Alex")
    r.tick()
    return r


def test_bind_cast_unbind(rig):
    r = _zapper(rig)
    r.op("grant", who="Steve", ability="zap")
    reply = r.op("bind", player="Steve", phrase="Zap it!", ability="zap", aliases=["zapp it", "zap"],
                 everyday_aliases=["zap"], by="test")
    entry = {"id": "steve.zap_it", "player": "Steve", "phrase": "zap it", "aliases": ["zapp it", "zap"],
             "everyday_aliases": ["zap"], "ability": "zap", "cooldown_s": 2.5}
    assert reply == {"ok": True, "id": "steve.zap_it", "version": 2, "entry": entry}
    assert r.op("cast", player="steve", phrase="ZAP IT", cast_id="1")["status"] == "fired"
    assert r.op("cast", player="Steve", phrase="zapp it", cast_id="2")["status"] == "cooldown"
    # per player: Alex has no such incantation
    assert r.op("cast", player="Alex", phrase="zap it", cast_id="3")["status"] == "unknown"
    # bound but not granted
    r.op("bind", player="Alex", phrase="zap it", ability="zap")
    assert r.op("cast", player="Alex", phrase="zap it", cast_id="4")["status"] == "not_granted"
    assert r.op("incantations", player="Steve")["incantations"] == [entry]   # the same shape
    assert r.op("unbind", player="Steve", phrase="zap it")["ok"]
    assert r.op("cast", player="Steve", phrase="zap it", cast_id="5")["status"] == "unknown"


def test_the_export_is_written_atomically_with_a_version_bump_on_each_change(rig):
    r = _zapper(rig)
    path = r.tmp / "incantations.json"
    v0 = json.loads(path.read_text())["version"]
    r.op("bind", player="Steve", phrase="zap", ability="zap")
    doc = json.loads(path.read_text())
    assert doc["version"] == v0 + 1 and [e["phrase"] for e in doc["incantations"]] == ["zap"]
    r.op("bind", player="Alex", phrase="zap", ability="zap")
    assert json.loads(path.read_text())["version"] == v0 + 2
    # a cooldown changed by a reload is a change too
    r.write("zap", ZAP.replace("cooldown_s = 2.5", "cooldown_s = 4"))
    r.op("reload")
    doc = json.loads(path.read_text())
    assert doc["version"] == v0 + 3 and {e["cooldown_s"] for e in doc["incantations"]} == {4.0}
    assert not [p for p in r.tmp.iterdir() if p.name.endswith(".tmp")]


def test_no_incantation_for_everyone(rig):
    r = _zapper(rig)
    reply = r.op("bind", player="@a", phrase="zap", ability="zap")
    assert reply["ok"] is False and "per player" in reply["error"]


def test_bind_refuses_what_it_should(rig):
    r = _zapper(rig)
    assert "not a loaded ability" in r.op("bind", player="Steve", phrase="x", ability="nope")["error"]
    assert "a word at least" in r.op("bind", player="Steve", phrase="!!", ability="zap")["error"]
    assert "not one of the aliases" in r.op("bind", player="Steve", phrase="zap", ability="zap",
                                            everyday_aliases=["zip"])["error"]
    assert "not a player name" in r.op("bind", player="Steve Jobs", phrase="zap", ability="zap")["error"]


def test_bindings_survive_a_restart(rig):
    from vfg_engine.engine import Engine
    r = _zapper(rig)
    r.op("bind", player="Steve", phrase="zap", ability="zap")
    r.engine.close()
    r.io.closed = False
    r.engine = Engine(r.io, r.settings, clock=r.clock)
    r.engine.start()
    assert [e["id"] for e in r.op("incantations")["incantations"]] == ["steve.zap"]


def test_a_restart_with_nothing_changed_keeps_the_export_version(rig):
    from vfg_engine.engine import Engine
    r = _zapper(rig)
    r.op("bind", player="Steve", phrase="zap", ability="zap")
    path = r.tmp / "incantations.json"
    before = json.loads(path.read_text())
    r.engine.close()
    r.io.closed = False
    r.engine = Engine(r.io, r.settings, clock=r.clock)
    r.engine.start()
    assert json.loads(path.read_text()) == before
    assert r.op("incantations")["version"] == before["version"]
    r.op("bind", player="Alex", phrase="zap", ability="zap")   # a change still bumps it
    assert json.loads(path.read_text())["version"] == before["version"] + 1
