"""World settings by priority, and scenarios that leave nothing in the world when they stop (the
game engine spec, "Actors, players and scenarios")."""

from vfg_engine.io import Gamerule, Remove, Summon
from vfg_engine.worldsettings import WorldSettings

HOLDERS = '''
class Siege(Scenario):
    class Config(ScenarioConfig):
        summary = "Creepers may grief."
        duration_s = UNTIL_STOPPED

    def start(self, ctx):
        self.hold("mob_griefing", self.args.get("value", True), priority=self.args.get("priority", 0))
'''

ARENA = '''
class Arena(Scenario):
    every = 1

    class Config(ScenarioConfig):
        summary = "A zone with a horde and a boss; shrinks whoever comes in."
        duration_s = {duration}
        horde: int = Field(3, ge=0, le=20)

    class State(State):
        phase: str = "waiting"

    def start(self, ctx):
        self.watch("ring", center=(0, 64, 0), radius=10)
        self.listen(Enter, "on_enter")
        self.listen(Exit, "on_exit")
        self.hold("mob_griefing", False, priority=5)
        for i in range(self.config.horde):
            self.spawn("minecraft:zombie", at=(i, 64, 5))
        self.spawn("brute", at=(0, 64, 8))
        self.after(5, "phase_two")

    def phase_two(self, ctx):
        self.state.phase = "two"

    def on_enter(self, ctx, ev):
        self.apply(ev.who, "small")

    def on_exit(self, ctx, ev):
        self.command("say %s left the ring" % ev.who)


class Small(Mark):
    class Config(MarkConfig):
        summary = "Small."
        duration_s = 600
        damage = 0

    def start(self, ctx):
        self.modifier("scale", -0.5, "add_multiplied_total")


class Brute(Actor):
    class Config(ActorConfig):
        summary = "A big zombie."
        entity_type = "minecraft:zombie"
        health = 60
        size = 1.5
        damage = 8
        name = "Brute"
'''


def test_priority_wins_then_the_most_recent_then_the_original_comes_back():
    w = WorldSettings()
    reads = []
    w.hold("a", "mob_griefing", False, 1, now=1)
    ops = w.plan(lambda name: reads.append(name) or "true")
    assert reads == ["mob_griefing"] and [(n, v) for n, v, _ in ops] == [("mob_griefing", "false")]
    w.took("mob_griefing", "false", True, None)
    w.hold("b", "mob_griefing", True, 5, now=2)      # higher priority wins
    w.hold("c", "mob_griefing", False, 5, now=3)     # a tie: the most recent wins
    assert w.winner("mob_griefing")["holder"] == "c"
    w.release("c")
    assert w.winner("mob_griefing")["holder"] == "b"
    w.release("b")
    assert w.winner("mob_griefing")["holder"] == "a"
    w.release("a")
    assert [(n, v) for n, v, _ in w.plan(lambda n: "x")] == [("mob_griefing", "true")]
    w.took("mob_griefing", "true", True, None)
    assert w.listing() == []                          # back where it started, nobody holding


def test_world_settings_through_the_engine_and_the_world_op(rig):
    r = rig({"siege": HOLDERS})
    r.start()
    a = r.op("start", scenario="siege", args={"value": False, "priority": 1})["instance"]
    assert r.io.gamerules["mob_griefing"] == "false"
    b = r.op("start", scenario="siege", args={"value": True, "priority": 9})["instance"]
    assert r.io.gamerules["mob_griefing"] == "true"
    (setting,) = r.op("world")["settings"]
    assert setting["name"] == "mob_griefing" and setting["original"] == "true" and setting["value"] == "true"
    assert [h["holder"] for h in setting["holds"]] == [b, a]
    r.op("stop", instance=b)
    assert r.io.gamerules["mob_griefing"] == "false"
    r.op("stop", instance=a)
    assert r.io.gamerules["mob_griefing"] == "true"
    assert r.op("world")["settings"] == []


def test_a_setting_that_failed_is_sent_again(rig):
    r = rig({"siege": HOLDERS})
    r.start()
    r.io.fail_next(Gamerule, "rcon hiccup")
    r.op("start", scenario="siege", args={"value": False})
    assert r.io.gamerules["mob_griefing"] == "true"
    r.tick()
    assert r.io.gamerules["mob_griefing"] == "false"


def test_a_scenario_owns_what_it_spawns_applies_and_holds(rig):
    r = rig({"arena": ARENA.replace("{duration}", "UNTIL_STOPPED")})
    assert r.start()["errors"] == []
    r.io.join("Steve", pos=(0.0, 64.0, 30.0))
    r.tick()
    iid = r.op("start", scenario="arena")["instance"]
    r.tick()
    assert len(r.io.entities) == 4 and r.io.gamerules["mob_griefing"] == "false"
    (brute,) = r.records("actor", "brute")
    summon = next(op for op in r.io.ops_of(Summon) if op.uuid == brute.user)
    assert "max_health" in summon.nbt and "base:60" in summon.nbt and "Brute" in summon.nbt
    # Steve walks into the ring: enter, a mark; out again: exit
    r.io.move("Steve", (0, 64, 3))
    r.tick(2)
    assert r.records("mark", "small") and ("minecraft:scale", "everhear:mark/small") in r.mods("Steve")
    r.io.move("Steve", (0, 64, 30))
    r.tick()
    assert any("Steve left the ring" in getattr(op, "text", "") for op in r.io.sent)
    r.run_for(5)
    assert r.rt.records[iid].state.phase == "two"
    # stop: nothing is left
    r.op("stop", scenario="arena")
    r.tick()
    assert r.io.entities == {}
    assert not r.records("mark") and ("minecraft:scale", "everhear:mark/small") not in r.mods("Steve")
    assert r.io.gamerules["mob_griefing"] == "true"
    assert not r.records()


def test_a_scenario_ends_at_its_duration(rig):
    r = rig({"arena": ARENA.replace("{duration}", "3")})
    r.start()
    r.op("start", scenario="arena")
    r.run_for(3.5)
    assert not r.records() and r.io.entities == {}


def test_an_actor_that_dies_is_gone_and_its_instance_ends(rig):
    r = rig({"arena": ARENA.replace("{duration}", "UNTIL_STOPPED")})
    r.start()
    uuid = r.op("spawn", actor="brute", at=[0, 64, 0])["uuid"]
    r.tick()
    assert r.records("actor", "brute") and uuid in r.io.entities
    r.io.kill_entity(uuid)
    r.tick()
    assert not r.records("actor")
    assert not [op for op in r.io.ops_of(Remove) if op.uuid == uuid]     # gone: nothing to remove
    assert any("gone" in m for m in r.lines())


def test_a_failed_summon_ends_the_actor(rig):
    r = rig({"arena": ARENA.replace("{duration}", "UNTIL_STOPPED")})
    r.start()
    r.io.fail_next(Summon, "too many entities")
    reply = r.op("spawn", actor="brute", at=[0, 64, 0])
    assert reply == {"ok": False, "error": "too many entities"}
    assert not r.records("actor")


def test_an_actor_taken_as_gone_in_an_unloaded_chunk_is_swept_when_it_comes_back(rig):
    r = rig({"arena": ARENA.replace("{duration}", "UNTIL_STOPPED")}, check_every_s=1.0)
    r.start()
    r.io.join("Steve")
    plain = r.io.add_entity(pos=(1.0, 64.0, 1.0))                  # somebody else's zombie
    uuid = r.op("spawn", actor="brute", at=[0, 64, 0])["uuid"]
    r.tick()
    body = r.io.entities.pop(uuid)                                  # its chunk unloads
    r.tick()
    assert not r.records("actor")                                   # taken as gone
    r.io.entities[uuid] = body                                      # the chunk loads again
    r.run_for(1.5)
    assert uuid not in r.io.entities and plain in r.io.entities
    assert [op.uuid for op in r.io.ops_of(Remove)] == [uuid]
    assert any("nothing owns" in m for m in r.lines())


def test_the_sweep_leaves_the_engines_own_spawns(rig):
    r = rig({"arena": ARENA.replace("{duration}", "UNTIL_STOPPED")}, check_every_s=0.5)
    r.start()
    r.io.join("Steve")
    r.op("start", scenario="arena")
    r.run_for(3)
    assert len(r.io.entities) == 4 and not r.io.ops_of(Remove)
