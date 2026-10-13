"""targets / near_line / hitscan / hit (the kit's finders): the spare rules, what each `what`
keeps, the order, the mirror or a live read, a beam stopped by a wall, a headshot; run inside the
engine's loop on FakeIo."""

import pytest

from vfg_engine.io import Damage

FIND = '''
class Find(Scenario):
    class Config(ScenarioConfig):
        summary = "Finds what a hit may touch round a point, or along a line."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        what: str = Field("everything", description="what to find")
        x: float = Field(0, description="the point")
        z: float = Field(0, description="the point")
        radius: float = Field(30, description="how far")
        line: bool = Field(False, description="along a line from the point to 40 blocks east")
        spare: str = Field("", description="names to spare")
        most: int = Field(0, description="at most")

    class State(State):
        keys: list = []
        ts: list = []

    def start(self, ctx):
        a = self.args
        at = (a.x, 64.0, a.z)
        spare = [s for s in a.spare.split(",") if s]
        if a.line:
            found = near_line(self, ctx, at, (a.x + 40, 64.0, a.z), 1.5, a.what, spare, a.most)
        else:
            found = targets(self, ctx, at, a.radius, a.what, spare, a.most)
        self.state.keys = [t.key for t in found]
        self.state.ts = [round(t.t, 2) for t in found]
'''

ZAP = '''
class Zap(Ability):
    class Config(AbilityConfig):
        summary = "A beam along the look: 10 to each, double on the head."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = 1
        damage = 20
        through: bool = False

    def on_cast(self, ctx, ev):
        shot = hitscan(self, ctx, 50, 1.5, through_walls=self.config.through)
        hit(self, ctx, shot.targets, 10, "magic", head=2)
        beam_line(self, ctx, shot.start, shot.end, "minecraft:end_rod", every=5)
        self.message("end %d %s" % (round(shot.end[2]), shot.block))
'''


def world(r):
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0))
    io.join("Carl", pos=(3.0, 64.0, 0.0), gamemode="creative")
    ids = {
        "zombie": io.add_entity("minecraft:zombie", (5.0, 64.0, 0.0)),
        "cow": io.add_entity("minecraft:cow", (8.0, 64.0, 0.0)),
        "villager": io.add_entity("minecraft:villager", (2.0, 64.0, 0.0)),
        "wolf": io.add_entity("minecraft:wolf", (4.0, 64.0, 0.0)),
        "arrow": io.add_entity("minecraft:arrow", (6.0, 65.0, 0.0), health=None),
        "stuck": io.add_entity("minecraft:arrow", (7.0, 64.0, 0.0), health=None),
        "item": io.add_entity("minecraft:item", (1.0, 64.0, 0.0)),
        "display": io.add_entity("minecraft:block_display", (1.5, 64.0, 0.0), health=None),
    }
    io.by_uuid[ids["wolf"]].owner = "Steve"
    io.by_uuid[ids["stuck"]].in_ground = True
    return ids


def found(r, **args):
    iid = r.start_scenario("find", **args)
    keys = list(r.rt.records[iid].state.keys)
    r.op("stop", instance=iid)
    return keys


@pytest.mark.parametrize("what, want", [
    ("everything", ["Steve", "zombie", "cow"]),
    ("mobs", ["zombie", "cow"]),
    ("hostile", ["zombie"]),
    ("players", ["Steve"]),
    ("projectiles", ["arrow"]),
])
def test_targets_keeps_what_the_spare_rules_allow(rig, what, want):
    r = rig({"find": FIND})
    r.start()
    ids = world(r)
    r.tick()
    names = {v: k for k, v in ids.items()}
    assert [names.get(k, k) for k in found(r, what=what)] == want
    assert r.problems() == []


def test_targets_spares_names_and_takes_the_nearest(rig):
    r = rig({"find": FIND})
    r.start()
    ids = world(r)
    r.tick()
    assert found(r, spare="Steve") == [ids["zombie"], ids["cow"]]
    assert found(r, most=1) == ["Steve"]
    iid = r.start_scenario("find", x=5)
    assert r.rt.records[iid].state.ts[0] == pytest.approx(0.2)        # the zombie, at the point


def test_far_from_players_it_reads_live(rig):
    r = rig({"find": FIND})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    far = r.io.add_entity("minecraft:zombie", (200.0, 64.0, 0.0))
    calls = []
    real = r.io.entities
    r.io.entities = lambda *a, **k: calls.append(a) or real(*a, **k)
    r.tick()
    assert found(r, x=200, radius=5) == [far] and len(calls) == 1
    assert found(r, x=0, radius=5) == ["Steve"] and len(calls) == 1      # near a player: the mirror


def test_near_line_orders_along_it(rig):
    r = rig({"find": FIND})
    r.start()
    io = r.io
    io.join("Steve", pos=(-50.0, 64.0, 0.0))
    a = io.add_entity("minecraft:zombie", (30.0, 64.0, 1.0))
    b = io.add_entity("minecraft:husk", (10.0, 64.0, -1.0))
    io.add_entity("minecraft:zombie", (20.0, 64.0, 4.0))            # 4 off the line: missed
    io.add_entity("minecraft:zombie", (45.0, 64.0, 0.0))            # past its end: missed
    r.tick()
    assert found(r, line=True, what="mobs") == [b, a]


def beam_world(r):
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0))                          # looking south (+z), eyes at 65.62
    io.join("Alex", pos=(0.0, 64.0, 16.0))
    io.join("Carl", pos=(0.0, 64.0, 18.0), gamemode="creative")
    zombie = io.add_entity("minecraft:zombie", (0.0, 64.0, 10.0))
    io.add_entity("minecraft:villager", (0.5, 64.0, 12.0))
    behind = io.add_entity("minecraft:zombie", (0.0, 64.0, 30.0))
    for y in (64, 65, 66):
        io.set_block("minecraft:overworld", (0, y, 25), "minecraft:stone")
    return zombie, behind


def test_hitscan_stops_at_a_wall_and_hits_once_each_with_headshots(rig):
    r = rig({"zap": ZAP})
    r.start()
    zombie, behind = beam_world(r)
    r.tick()
    r.cast("Steve", "zap")
    r.tick()
    hits = {d.target: (d.amount, d.damage_type, d.by) for d in r.sent(Damage)}
    assert hits == {zombie: (20.0, "minecraft:magic", "Steve"), "Alex": (20.0, "minecraft:magic", "Steve")}
    assert any("end 25 (0, 65, 25)" in m.text for m in r.io.sent if hasattr(m, "text"))
    assert r.problems() == []


def test_hitscan_through_walls_reaches_past_them(rig):
    r = rig({"zap": ZAP.replace("through: bool = False", "through: bool = True")})
    r.start()
    zombie, behind = beam_world(r)
    r.tick()
    r.cast("Steve", "zap")
    r.tick()
    assert {d.target for d in r.sent(Damage)} == {zombie, "Alex", behind}


def test_hit_dedupes_kills_and_stops_at_most():
    from vfg_engine.kit import Target, hit

    class Fake:
        def __init__(self):
            self.dealt = []

        def damage(self, key, amount, kind):
            self.dealt.append((key, amount, kind))

    me = Fake()
    ts = [Target("b", "minecraft:zombie", "minecraft:overworld", (0, 0, 0), t=2.0),
          Target("a", "minecraft:zombie", "minecraft:overworld", (0, 0, 0), t=1.0, on_head=True),
          Target("b", "minecraft:zombie", "minecraft:overworld", (0, 0, 0), t=3.0)]
    assert [t.key for t in hit(me, None, ts, 5, head=3)] == ["a", "b"]
    assert me.dealt == [("a", 15.0, "minecraft:generic"), ("b", 5.0, "minecraft:generic")]
    me.dealt.clear()
    hit(me, None, ts, 5, kill=True, most=1)
    assert me.dealt == [("a", 1_000_000.0, "minecraft:generic_kill")]
