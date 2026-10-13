"""The engine enforces cooldown, charge and duration from config, each cast is a run, a second
cast is the ability's decision, and damage dealt well over damage(ctx) is flagged (the game engine
spec, "Abilities and their config")."""

import pytest

from vfg_engine.io import AddModifier, Command, Damage

BOLT = '''
class Bolt(Ability):
    class Config(AbilityConfig):
        summary = "Charges, then hits what you look at; a speed boost while it lasts."
        hit: float = Field(5, ge=0, le=20)
        scale_per_point: float = 0
        charge_s = {charge}
        cooldown_s = 4
        cooldown_from = "{start}"
        duration_s = 3

        def damage(self, ctx):
            return self.hit

    def on_cast(self, ctx, ev):
        self.command("say bolt %d running beside it" % len(ctx.running))
        self.modifier("movement_speed", 0.1, "add_value")
        self.damage(ctx.user.look, self.config.hit)
'''

AURA = '''
class Aura(Ability):
    every = 1

    class Config(AbilityConfig):
        summary = "Runs until stopped; a second cast ends the first."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = UNTIL_STOPPED
        damage = 2

    def on_cast(self, ctx, ev):
        for other in ctx.running:
            other.end()

    def tick(self, ctx):
        self.command("say aura")
'''

GREEDY = '''
class Greedy(Ability):
    class Config(AbilityConfig):
        summary = "Says 3, deals 30."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = 1
        damage = 3

    def on_cast(self, ctx, ev):
        self.damage(ctx.user.look, 30)
'''


def bolt(charge=0, start="cast"):
    return BOLT.replace("{charge}", str(charge)).replace("{start}", start)


def _caster(rig, files):
    r = rig(files)
    assert r.start()["errors"] == []
    r.io.join("Steve")
    zombie = r.io.add_entity(pos=(0.0, 64.0, 4.0))
    r.io.look_at("Steve", (0.0, 64.5, 4.0), kind="entity")
    r.io.players["Steve"].look = r.io.players["Steve"].look.__class__(
        "entity", (0.0, 64.5, 4.0), 4.0, entity=zombie, entity_type="minecraft:zombie")
    r.tick()
    for cid in files:
        r.op("grant", who="Steve", ability=cid.split(".")[0])
    r.op("bind", player="Steve", phrase="bolt", ability="bolt") if "bolt" in files else None
    return r, zombie


def cast(r, ability="bolt", cast_id=None):
    if ability == "bolt":
        return r.op("cast", player="Steve", phrase="bolt", cast_id=cast_id)
    r.op("bind", player="Steve", phrase=ability, ability=ability)
    return r.op("cast", player="Steve", phrase=ability, cast_id=cast_id)


def test_cooldown_from_the_cast(rig):
    r, zombie = _caster(rig, {"bolt": bolt()})
    assert cast(r)["status"] == "fired"
    second = cast(r)
    assert second["status"] == "cooldown" and 3.5 < second["remaining_s"] <= 4
    r.run_for(4)
    assert cast(r)["status"] == "fired"
    assert [d.target for d in r.io.ops_of(Damage)] == [zombie, zombie]


def test_cooldown_from_the_end(rig):
    r, _ = _caster(rig, {"bolt": bolt(start="end")})
    assert cast(r)["status"] == "fired"
    r.run_for(3.2)                       # the run (3 s) ended: now the 4 s start
    status = cast(r)
    assert status["status"] == "cooldown" and status["remaining_s"] > 3.5


def test_charge_fires_only_once_it_is_done(rig):
    r, _ = _caster(rig, {"bolt": bolt(charge=1.5)})
    assert cast(r)["status"] == "charging"
    r.run_for(1.2)
    assert not r.io.ops_of(Damage)
    r.run_for(0.5)
    assert len(r.io.ops_of(Damage)) == 1


def test_duration_ends_the_run_and_what_it_made(rig):
    r, _ = _caster(rig, {"bolt": bolt()})
    cast(r)
    r.tick()
    assert ("minecraft:movement_speed", "everhear:ability/bolt/1") in r.mods("Steve")
    r.run_for(2.5)
    assert ("minecraft:movement_speed", "everhear:ability/bolt/1") in r.mods("Steve")
    r.run_for(0.7)
    assert ("minecraft:movement_speed", "everhear:ability/bolt/1") not in r.mods("Steve")
    assert r.records("ability", "bolt")                        # the grant stays
    assert any("run 1 ended (duration)" in m for m in r.lines())


def test_until_stopped_and_a_second_cast_is_the_abilitys_decision(rig):
    r, _ = _caster(rig, {"aura": AURA})
    assert cast(r, "aura")["status"] == "fired"
    r.run_for(2)
    (rec,) = r.records("ability", "aura")
    assert list(rec.runs) == [1]
    assert cast(r, "aura")["status"] == "fired"
    r.tick()
    assert list(rec.runs) == [2]                               # the new one ended the old one
    r.run_for(30)
    assert list(rec.runs) == [2]                               # until stopped


def test_each_cast_is_a_run_and_sees_the_others(rig):
    r, _ = _caster(rig, {"bolt": bolt().replace("cooldown_s = 4", "cooldown_s = 0")})
    cast(r)
    cast(r)
    said = [op.text for op in r.io.ops_of(Command)]
    assert said == ["say bolt 0 running beside it", "say bolt 1 running beside it"]
    r.tick()
    ids = sorted(k[1] for k in r.mods("Steve"))
    assert ids == ["everhear:ability/bolt/1", "everhear:ability/bolt/2"]


def test_damage_well_over_the_declared_is_flagged(rig):
    r, _ = _caster(rig, {"greedy": GREEDY})
    cast(r, "greedy")
    r.run_for(1.5)
    assert any("damage drift" in m and "dealt 30.0" in m and "declared 3.0" in m for m in r.lines())


def test_damage_as_declared_is_not_flagged(rig):
    r, _ = _caster(rig, {"bolt": bolt()})
    cast(r)
    r.run_for(4)
    assert not any("damage drift" in m for m in r.lines())


def test_cast_statuses(rig):
    r, _ = _caster(rig, {"bolt": bolt()})
    assert r.op("cast", player="Steve", phrase="nothing bound", cast_id="a")["status"] == "unknown"
    r.op("revoke", who="Steve", ability="bolt")
    assert r.op("cast", player="Steve", phrase="bolt", cast_id="b")["status"] == "not_granted"
    r.op("grant", who="Steve", ability="bolt")
    assert r.op("cast", player="Steve", phrase="Bolt!", cast_id="c")["status"] == "fired"
    # the same cast_id answers as the first did, and fires nothing more
    n = len(r.io.ops_of(Damage))
    again = r.op("cast", player="Steve", phrase="bolt", cast_id="c")
    assert again["status"] == "fired" and again["repeated"] and len(r.io.ops_of(Damage)) == n


def test_a_cast_whose_on_cast_fails_answers_failed(rig):
    broken = bolt().replace('self.command("say', 'raise RuntimeError("no aim"); self.command("say')
    r, _ = _caster(rig, {"bolt": broken})
    reply = cast(r, cast_id="x")
    assert reply["status"] == "failed" and "no aim" in reply["error"]
    assert r.records("ability", "bolt")                        # only that run ended
    assert not [op for op in r.io.ops_of(AddModifier) if "bolt" in op.id]


def test_an_actor_casts_the_same_abilities(rig):
    actor = '''
class Golem(Actor):
    every = 0.5

    class Config(ActorConfig):
        summary = "Bolts whoever is nearest."
        entity_type = "minecraft:iron_golem"
        health = 50
        size = 1.2
        damage = 7
        abilities = ["bolt"]

    def tick(self, ctx):
        near = ctx.world.players_near(ctx.user.pos, 16)
        if near:
            ctx.user.aim(near[0].name)
            ctx.user.cast("bolt")
'''
    r = rig({"bolt": bolt(), "golem": actor})
    assert r.start()["errors"] == []
    r.io.join("Steve", pos=(5.0, 64.0, 0.0))
    r.tick()
    uuid = r.op("spawn", actor="golem", at=[0, 64, 0])["uuid"]
    r.run_for(1)
    hits = r.io.ops_of(Damage)
    assert hits and hits[0].target == "Steve" and hits[0].by == uuid and hits[0].amount == 5


@pytest.mark.parametrize("ability", ["bolt"])
def test_passive_abilities_cannot_be_cast(rig, ability):
    passive = '''
class Calm(Ability):
    class Config(AbilityConfig):
        summary = "Passive."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = UNTIL_STOPPED
        damage = 0

    def start(self, ctx):
        self.modifier("armor", 1, "add_value")
'''
    r = rig({"calm": passive})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="calm")
    r.tick()
    assert ("minecraft:armor", "everhear:ability/calm") in r.mods("Steve")
    assert r.op("bind", player="Steve", phrase="calm", ability="calm")["ok"] is False
