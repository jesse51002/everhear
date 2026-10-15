"""`ctx`: the user, the world from memory (snapshot and mirror), and ctx.query (the game engine
spec, "Reading the world without waiting")."""

from vfg_engine.io import Command, Damage

LOOKER = '''
class Looker(Ability):
    class Config(AbilityConfig):
        summary = "Reports what it can see."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = 1
        damage = 0

    def on_cast(self, ctx, ev):
        u = ctx.user
        cone = ctx.world.cone(u.eye, u.facing, 30, 10)
        near = ctx.world.entities_near(u.pos, 8)
        players = sorted(p.name for p in ctx.world.players_near(u.pos, 8))
        self.command("say power=%g has=%s look=%s" % (u.power(), u.has("looker"), list(u.looking_at())))
        self.command("say cone=%d near=%d players=%s" % (len(cone), len(near), ",".join(players)))
        self.command("say query=" + ctx.query("data get entity Steve Pos"))
'''


def test_ctx_reads_the_world_from_memory(rig):
    r = rig({"looker": LOOKER})
    r.start()
    r.io.join("Steve")                                      # facing +z (yaw 0)
    r.io.join("Alex", pos=(0.0, 64.0, 5.0))                 # in front of Steve
    r.io.join("Sam", pos=(0.0, 64.0, -5.0))                 # behind him
    r.io.add_entity(pos=(1.0, 64.0, 4.0))                   # a zombie in front
    r.io.add_entity(pos=(30.0, 64.0, 0.0))                  # one far away
    r.io.look_at("Steve", (0.0, 64.0, 5.0))
    r.io.query_reply = lambda command: "Steve has the following entity data: [0.0d, 64.0d, 0.0d]"
    r.tick()
    r.op("grant", who="Steve", ability="looker")
    r.op("bind", player="Steve", phrase="look", ability="looker")
    assert r.op("cast", player="Steve", phrase="look")["status"] == "fired"
    said = [op.text for op in r.io.ops_of(Command)]
    assert said[0] == "say power=1 has=True look=[0.0, 64.0, 5.0]"
    assert said[1] == "say cone=2 near=1 players=Alex,Sam,Steve"
    assert said[2] == "say query=Steve has the following entity data: [0.0d, 64.0d, 0.0d]"
    assert r.io.queries == ["data get entity Steve Pos"]


BLAST = '''
class Blast(Ability):
    class Config(AbilityConfig):
        summary = "Explodes where you stand, sparing you."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = 1
        damage = 4
        self_damage = False

    def on_cast(self, ctx, ev):
        u = ctx.user
        cone = [ctx_target(t) for t in ctx.world.cone(u.eye, u.facing, 90, 10)]
        near = [e.type for e in ctx.world.entities_near(u.pos, 10)]
        self.command("say cone=%s near=%s" % (",".join(sorted(cone)), ",".join(near)))
        self.explode(at=u.pos, radius=5, damage=4)


def ctx_target(t):
    return getattr(t, "name", None) or t.uuid
'''


def test_a_player_listed_by_the_mirror_is_ignored(rig):
    """An older datapack's mirror lists players too, by uuid: they come from the snapshot only."""
    from vfg_engine.io import EntitySnap
    from vfg_engine.testing import FakeEntity
    r = rig({"blast": BLAST})
    r.start()
    steve = r.io.join("Steve")
    alex = r.io.join("Alex", pos=(0.0, 64.0, 2.0))
    for p in (steve, alex):
        r.io.by_uuid[p.uuid] = FakeEntity(p.uuid, "minecraft:player", p.dim, p.pos, 20.0, frozenset(), p.name)
    r.tick()
    r.op("grant", who="Steve", ability="blast")
    r.op("bind", player="Steve", phrase="blast", ability="blast")
    r.op("cast", player="Steve", phrase="blast")
    hits = [(d.target, d.amount) for d in r.io.ops_of(Damage)]
    assert hits == [("Alex", 4.0)]                    # once, by name; not Steve (self_damage off)
    said = [op.text for op in r.io.ops_of(Command)]
    assert said == ["say cone=Alex near="]            # the user is not in the cone; no "entities"
    assert r.rt.target_of(EntitySnap(alex.uuid, "minecraft:player", alex.dim, alex.pos, 20.0)) == "Alex"
    assert r.rt.target_of(alex.uuid) == "Alex"
