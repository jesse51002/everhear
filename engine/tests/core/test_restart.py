"""A restart is the reload path: saved state, rebuild everything, then remove every
everhear:item/* modifier nobody is holding the item for (the game engine spec, "Live updates",
"Items" and "When something goes wrong")."""

import os
import json

from vfg_engine.engine import Engine
from vfg_engine.io import RemoveModifier

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "integration", "engine_e2e_content")


PULSE = '''
class Pulse(Ability):
    class Config(AbilityConfig):
        summary = "Counts, passively."
        damage = 0
        duration_s = UNTIL_STOPPED
        charge_s = 0
        cooldown_s = 0
        scale_per_point = 0

    class State(State):
        n: int = 0

    def start(self, ctx):
        self.every(1, "count")

    def count(self, ctx):
        self.state.n += 1
'''

HOLD = '''
class Calm(Scenario):
    class Config(ScenarioConfig):
        summary = "No griefing for a while."
        duration_s = 600

    def start(self, ctx):
        self.hold("mob_griefing", False, priority=1)
'''


def _first_life(rig):
    r = rig({"pulse": PULSE, "calm": HOLD}, examples=True)
    r.start()
    r.io.join("Steve")
    r.io.join("Alex", pos=(0.0, 64.0, 6.0))
    r.io.hold("Steve", "fire_staff")
    r.tick()
    assert r.op("grant", who="Steve", ability="pulse")["ok"]
    assert r.op("apply", who="Steve", mark="giant")["ok"]
    assert r.op("start", scenario="calm")["ok"]
    assert r.op("bind", player="Steve", phrase="Fire Ball!", ability="e2e_fireball")["ok"]
    r.io.look_at("Steve", (0.0, 64.0, 6.0))
    r.io.use("Steve")
    r.run_for(3.0)
    return r


def test_a_restart_rebuilds_every_instance_from_saved_state(rig):
    r = _first_life(rig)
    r.engine.close()
    saved = json.loads((r.tmp / "state.json").read_text())
    assert {d["cid"] for d in saved["records"]} >= {"pulse", "giant", "calm", "fire_staff", "e2e_fireball"}
    giant_end = next(d["ends"] for d in saved["records"] if d["cid"] == "giant")
    count = next(d["state"]["n"] for d in saved["records"] if d["cid"] == "pulse")
    cooldown = saved["players"]["Steve"]["cooldowns"]["e2e_fireball"]

    r.io.closed = False
    e2 = Engine(r.io, r.settings, clock=r.clock)
    r.engine = e2
    e2.start()
    rt = e2.rt
    assert {rec.cid for rec in rt.records.values()} >= {"pulse", "giant", "calm", "fire_staff", "e2e_fireball"}
    assert next(rec.ends for rec in rt.records.values() if rec.cid == "giant") == giant_end
    assert rt.players["Steve"].cooldowns["e2e_fireball"] == cooldown
    assert rt.players["Steve"].grants.keys() >= {"pulse", "e2e_fireball"}
    r.run_for(2.0)
    assert next(rec.state.n for rec in rt.records.values() if rec.cid == "pulse") >= count + 2
    # the world setting is still held, the incantation still bound
    assert r.io.gamerules["mob_griefing"] == "false"
    assert r.op("cast", player="Steve", phrase="fire ball", cast_id="c1")["status"] in ("fired", "cooldown")
    assert e2.rt.world_settings.listing()[0]["holds"][0]["holder"].startswith("scenario:calm#")


def test_start_up_removes_item_modifiers_nobody_is_holding_the_item_for(rig):
    r = _first_life(rig)
    r.engine.close()
    # while the engine was down, Steve put the staff away; Alex has a leftover from long ago
    r.io.unhold("Steve")
    r.io.players["Alex"].modifiers[("minecraft:movement_speed", "everhear:item/fire_staff")] = (0.2, "add_multiplied_total")
    r.io.players["Alex"].modifiers[("minecraft:scale", "someone_else:thing")] = (0.5, "add_value")
    r.io.players["Alex"].modifiers[("minecraft:scale", "everhear:not_ours")] = (0.5, "add_value")
    r.io.closed = False
    r.engine = Engine(r.io, r.settings, clock=r.clock)
    r.engine.start()
    r.tick(2)
    assert ("minecraft:movement_speed", "everhear:item/fire_staff") not in r.mods("Steve")
    assert ("minecraft:movement_speed", "everhear:item/fire_staff") not in r.mods("Alex")
    assert ("minecraft:scale", "someone_else:thing") in r.mods("Alex")          # never touched
    assert ("minecraft:scale", "everhear:not_ours") in r.mods("Alex")          # not an engine family
    assert ("minecraft:scale", "everhear:mark/giant") in r.mods("Steve")        # still wanted
    removed = {(op.target, op.id) for op in r.io.ops_of(RemoveModifier)}
    assert ("Alex", "everhear:item/fire_staff") in removed


def test_a_record_whose_class_did_not_load_waits_for_it(rig):
    r = _first_life(rig)
    r.engine.close()
    (r.dir / "pulse.py").rename(r.tmp / "pulse.py")
    r.io.closed = False
    r.engine = Engine(r.io, r.settings, clock=r.clock)
    r.engine.start()
    assert not r.records(cid="pulse") and len(r.rt.orphans) == 1
    r.tick()
    r.engine.save(force=True)
    assert any(d["cid"] == "pulse" for d in json.loads((r.tmp / "state.json").read_text())["records"])
    (r.tmp / "pulse.py").rename(r.dir / "pulse.py")
    r.op("reload")
    assert len(r.records(cid="pulse")) == 1 and not r.rt.orphans
    assert "pulse" in r.rt.players["Steve"].grants


def test_state_is_saved_about_once_a_second_and_atomically(rig):
    r = rig({"pulse": PULSE})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="pulse")
    r.run_for(1.2)
    doc = json.loads((r.tmp / "state.json").read_text())
    assert any(d["cid"] == "pulse" for d in doc["records"])
    assert not [p for p in r.tmp.iterdir() if p.name.endswith(".tmp")]


def test_examples_folder_exists():
    import os
    assert os.path.exists(EXAMPLES)


def test_saving_a_record_does_not_copy_its_state_deeply():
    from vfg_engine.content import State
    from vfg_engine.records import Record, Run

    class Fussy(State):
        n: int = 1

        def __deepcopy__(self, memo):
            raise AssertionError("deep-copied")
    rec = Record("ability:x#1", "ability", "x", "Steve", 0.0, state=Fussy())
    rec.runs[1] = Run(1, 0.0, 0.0, state=Fussy(n=2))
    doc = rec.to_dict()
    assert doc["state"] == {"n": 1, "kit": {}} and doc["runs"]["1"]["state"] == {"n": 2, "kit": {}}
    assert "ending" not in doc
    back = Record.from_dict(json.loads(json.dumps(doc)))
    assert back.state["n"] == 1 and back.runs[1].state["n"] == 2 and back.iid == rec.iid
