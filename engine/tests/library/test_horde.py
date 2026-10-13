"""The horde group on FakeIo: horde, with the zoo and summon_around merged in as its args. A wave
of mixed mobs at safe spots round each player, reported once out, ending when they are dead; the
cap; defused creepers; a zoo's waves until stopped, which a stop takes away; summon_around's mobs
that stay; args refused in words."""

from vfg_engine.io import Message, Remove, Summon


def online(r, *names):
    for i, n in enumerate(names):
        r.io.join(n, pos=(100.0 * i, 64.0, 0.0))        # on FakeIo's stone, whose top is y 63
    r.tick()


def summons(r):
    return r.sent(Summon)


def test_it_loads(rig):
    r = rig("horde")
    assert r.start()["loaded"] == ["horde"]


def test_a_wave_round_each_player_reported_then_dead(rig):
    r = rig("horde")
    r.start()
    online(r, "Steve", "Alex")
    iid = r.start_scenario("horde", mobs=["zombie", "skeleton"], per_player=10, spread=8)
    r.tick(3)
    made = summons(r)
    assert len(made) == 20
    assert {s.entity_type for s in made} <= {"minecraft:zombie", "minecraft:skeleton"}
    for s in made:                                       # at a safe spot near one of them, on the ground
        near = min(abs(s.pos[0] - 0), abs(s.pos[0] - 100))
        assert near <= 9 and abs(s.pos[2]) <= 9 and s.pos[1] == 64 and "PersistenceRequired:1b" in s.nbt
    got = r.result(iid)
    assert got["summary"].startswith("summoned 20 mobs (zombie, skeleton) round Steve at 0 64 0, Alex at 100 64 0")
    assert got["facts"]["count"] == 20
    waves = [m for m in r.sent(Message) if m.where == "actionbar"]
    assert {m.players for m in waves} == {("Steve",), ("Alex",)} and all(m.text == "§cWave 1" for m in waves)
    for s in made[:-1]:
        r.io.kill_entity(s.uuid)
    r.run_for(2)
    assert r.running(iid)                                # one still alive
    r.io.kill_entity(made[-1].uuid)
    r.run_for(2)
    assert not r.running(iid)
    assert r.result(iid)["summary"] == "the horde round @a is dead, after 1 wave"
    assert r.problems() == []


def test_the_cap_keeps_it_under(rig):
    r = rig("horde")
    r.start()
    online(r, "Steve", "Alex")
    r.start_scenario("horde", per_player=50, cap=30, spread=20)
    r.tick(3)
    assert len(summons(r)) == 30                         # Steve's 30; Alex's round found no room under it


def test_creepers_are_defused_unless_asked(rig):
    r = rig("horde")
    r.start()
    online(r, "Steve")
    r.start_scenario("horde", mobs=["creeper"], per_player=3, spread=8)
    r.start_scenario("horde", mobs=["creeper"], per_player=3, spread=8, explode=True)
    r.tick(3)
    nbts = [s.nbt for s in summons(r)]
    assert sum("ExplosionRadius:0b" in n for n in nbts) == 3 and len(nbts) == 6


def test_a_zoo_sends_waves_until_stopped_and_a_stop_takes_them(rig):
    r = rig("horde")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("horde", mobs=["pig", "cow", "sheep"], waves=0, every_s=1, per_player=5, cap=12,
                           spread=8, announce=False)
    r.run_for(0.5)
    assert len(summons(r)) == 5
    r.run_for(1)
    assert len(summons(r)) == 10
    r.run_for(3)
    assert len(summons(r)) == 12                         # at the cap
    assert not [m for m in r.sent(Message) if m.where == "actionbar"]
    assert r.op("stop", instance=iid)["ok"]
    r.tick(2)
    assert not r.running(iid)
    removed = {op.uuid for op in r.sent(Remove)}
    assert {s.uuid for s in summons(r)} <= removed       # every animal it brought is gone


def test_mobs_that_stay_are_left_in_the_world(rig):
    r = rig("horde")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("horde", mobs=["cow"], per_player=4, spread=8, stay=True, scale=2, name="Bob",
                           glowing=True, announce=False)
    r.tick(3)
    assert not r.running(iid)                            # it ended once they were out
    got = r.result(iid)
    assert got["summary"] == ("summoned 4 mobs (cow) round Steve at 0 64 0 (named Bob, glowing, 2x size); "
                              "they stay in the world")
    made = summons(r)
    assert len(made) == 4 and all("everhear.kept" in s.tags for s in made)
    assert all('CustomName:"Bob"' in s.nbt and "Glowing:1b" in s.nbt and "minecraft:scale" in s.nbt for s in made)
    assert not r.sent(Remove)                            # left there
    assert all(s.uuid in r.io.by_uuid for s in made)


def test_nobody_online(rig):
    r = rig("horde")
    r.start()
    iid = r.start_scenario("horde", players="Steve")
    r.tick(2)
    assert not r.running(iid) and r.result(iid)["summary"] == "no horde: Steve is not online"


def test_args_are_refused_in_words(rig):
    r = rig("horde")
    r.start()
    reply = r.op("start", scenario="horde", args={"mobs": ["dragonfly"]})
    assert not reply["ok"] and "dragonfly is not an entity type in Minecraft 26.3" in reply["error"]
    reply = r.op("start", scenario="horde", args={"per_player": 500})
    assert not reply["ok"] and "per_player" in reply["error"]
    reply = r.op("start", scenario="horde", args={"zoo": True})
    assert not reply["ok"] and "not an arg of this" in reply["error"]


def test_no_safe_ground_says_so(rig):
    r = rig("horde")
    r.start()
    r.io.join("Steve", pos=(0.0, 20.0, 0.0))             # deep in FakeIo's stone: nowhere to stand
    r.tick()
    one = r.start_scenario("horde", players="Steve", spread=8)
    kept = r.start_scenario("horde", players="Steve", spread=8, stay=True)
    r.run_for(2)
    assert not summons(r)
    for iid in (one, kept):
        assert not r.running(iid)
        assert r.result(iid)["summary"] == "no horde: no safe ground within 8 blocks of Steve"
