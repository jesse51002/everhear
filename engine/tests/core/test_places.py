"""Places, the engine's own (DESIGN2 §2): saved per world, read with ctx.places, written with
self.save_place / forget_place, listed by the `places` op, imported once from the old list."""

import json

from vfg_engine.engine import Engine
from vfg_engine.io import Teleport

NAMER = '''
class SavePlace(Scenario):
    class Config(ScenarioConfig):
        summary = "Names the place someone stands at."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        name: str = Field("", description="the place's name")
        at: Place = Field("", description="where")

    def start(self, ctx):
        kept = self.save_place(self.args.name, self.args.at, by="test")
        self.result("saved " + kept, place=ctx.places.get(kept))
        self.end()


class GoTo(Scenario):
    class Config(ScenarioConfig):
        summary = "Sends a player to a place."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        who: Player = Field("", description="who goes")
        to: Place = Field("", description="where")

    def start(self, ctx):
        self.teleport(self.args.who, self.args.to)
        self.end()


class Forget(Scenario):
    class Config(ScenarioConfig):
        summary = "Forgets a place."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        name: str = Field("", description="the place")

    def start(self, ctx):
        self.result("forgot", gone=self.forget_place(self.args.name), left=sorted(ctx.places.all()))
        self.end()
'''


def _rig(rig):
    r = rig({"places": NAMER})
    assert r.start()["errors"] == []
    r.io.join("Steve", pos=(5.0, 70.0, -3.0), yaw=90.0)
    r.io.join("Alex")
    r.tick()
    return r


def test_save_a_place_then_go_there(rig):
    r = _rig(rig)
    reply = r.op("start", scenario="save_place", args={"name": "My Castle", "at": "Steve"}, wait_s=2)
    assert reply["result"]["summary"] == "saved my_castle"
    assert reply["result"]["facts"]["place"]["pos"] == [5.0, 70.0, -3.0]
    listing = r.op("places")
    assert listing["world"] == "fake-world-1" and listing["version"] == 1
    assert listing["places"]["my_castle"]["by"] == "test" and listing["places"]["my_castle"]["yaw"] == 90.0
    assert r.op("start", scenario="go_to", args={"who": "Alex", "to": "my castle"})["ok"]
    (tp,) = r.io.ops_of(Teleport)
    assert (tp.target, tp.pos, tp.yaw) == ("Alex", (5.0, 70.0, -3.0), 90.0)
    gone = r.op("start", scenario="forget", args={"name": "my_castle"}, wait_s=1)["result"]
    assert gone["facts"] == {"gone": True, "left": []}


def test_places_are_kept_per_world_and_over_a_restart(rig):
    r = _rig(rig)
    r.op("start", scenario="save_place", args={"name": "spawn", "at": "0 64 0"})
    r.engine.close()
    r.io.closed = False
    r.engine = Engine(r.io, r.settings, clock=r.clock)
    r.engine.start()
    assert list(r.op("places")["places"]) == ["spawn"]
    r.engine.close()
    r.io.closed = False
    r.io.world = "a-fresh-world"
    r.engine = Engine(r.io, r.settings, clock=r.clock)
    r.engine.start()
    assert r.op("places")["places"] == {}                     # a fresh world starts with none
    assert r.op("start", scenario="go_to", args={"who": "Alex", "to": "spawn"})["ok"] is False


def test_the_old_places_are_imported_once(rig):
    r = _rig(rig)
    old = [{"name": "Base", "dim": "overworld", "pos": [1, 2, 3]}, {"name": "farm", "x": 7, "y": 64, "z": 9, "yaw": 45},
           {"pos": [0, 0, 0]}]
    got = r.op("places_import", places=old)
    assert got["imported"] == ["base", "farm"] and len(got["refused"]) == 1
    assert r.op("places")["places"]["farm"]["pos"] == [7.0, 64.0, 9.0]
    again = r.op("places_import", places=old)
    assert again["ok"] is False and "imported already" in again["error"]
    assert r.op("places_import", places=old, force=True)["skipped"] == ["base", "farm"]
    doc = json.loads(json.dumps(r.engine.state()["places"]))
    assert doc["worlds"]["fake-world-1"]["imported"] is True
