"""The railgun group on FakeIo: railgun (item) fires railgun_shot (ability); gatling_gun (item)
fires gatling_shot (ability) while held. A beam through walls hurts what it passes, once a shot,
by what the ability declares; off the line and past its range nothing is hurt; a click within
the cooldown only says it is recharging."""

from vfg_engine.io import Damage, Message, Particle, Sound

GROUP = ("railgun", "railgun_shot", "gatling_gun", "gatling_shot")


def stage(r):
    """Steve at 0 64 0 facing north (-z); a wall 5 ahead; mobs on his line and off it."""
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    for y in (64, 65, 66):
        io.set_block("minecraft:overworld", (0, y, -5), "minecraft:stone")
    return {
        "near": io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0), health=1000),
        "far": io.add_entity("minecraft:cow", (0.0, 64.0, -60.0), health=1000),
        "beyond": io.add_entity("minecraft:zombie", (0.0, 64.0, -120.0), health=1000),
        "aside": io.add_entity("minecraft:zombie", (6.0, 64.0, -10.0), health=1000),
    }


def health(r, uuid):
    return r.io.by_uuid[uuid].health


def test_the_group_loads(rig):
    r = rig(*GROUP)
    loaded = r.start()["loaded"]
    assert sorted(loaded) == sorted(GROUP)


def test_a_click_fires_a_beam_through_the_wall(rig):
    r = rig(*GROUP)
    r.start()
    mobs = stage(r)
    assert r.op("give", player="Steve", item="railgun")["ok"]
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert health(r, mobs["near"]) == 970               # behind the wall: 30 magic
    assert health(r, mobs["far"]) == 970                # a cow too: it hurts everything
    assert health(r, mobs["beyond"]) == 1000            # past 100 blocks
    assert health(r, mobs["aside"]) == 1000             # 6 blocks off the line
    hits = r.sent(Damage)
    assert {d.damage_type for d in hits} == {"minecraft:magic"} and {d.by for d in hits} == {"Steve"}
    assert any(s.name == "minecraft:entity.firework_rocket.blast" for s in r.sent(Sound))
    beam = [p for p in r.sent(Particle) if p.name == "minecraft:end_rod"]
    assert len(beam) > 50 and all(p.force for p in beam)
    assert r.problems() == []


def test_within_the_cooldown_a_click_only_says_so(rig):
    r = rig(*GROUP)
    r.start()
    mobs = stage(r)
    r.op("give", player="Steve", item="railgun")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    r.io.use("Steve")                                   # 0.2 s later: recharging
    r.tick(2)
    assert health(r, mobs["near"]) == 970
    assert any(m.where == "actionbar" and "Railgun: recharging" in m.text for m in r.sent(Message))
    r.run_for(2)
    r.io.use("Steve")                                   # 2 s on: it fires again
    r.tick(2)
    assert health(r, mobs["near"]) == 940


def test_the_shot_cast_by_voice(rig):
    r = rig(*GROUP)
    r.start()
    mobs = stage(r)
    reply = r.cast("Steve", "railgun_shot")
    assert reply["status"] == "fired", reply
    r.tick(2)
    assert health(r, mobs["near"]) == 970 and health(r, mobs["aside"]) == 1000


def test_holding_the_gatling_gun_fires_until_let_go(rig):
    r = rig(*GROUP)
    r.start()
    mobs = stage(r)
    r.op("give", player="Steve", item="gatling_gun")
    r.tick()
    for _ in range(10):                                 # held 2 s: a Use every 0.2 s, as Minecraft sends
        r.io.use("Steve")
        r.tick(2)
    r.run_for(1)                                        # let go
    shots = 1000 - health(r, mobs["near"])
    assert shots % 30 == 0 and 15 * 30 <= shots <= 25 * 30, shots    # ten a second, 2 s and the let-go's 0.4
    assert health(r, mobs["far"]) == 1000                # 60 blocks: past its 24
    after = health(r, mobs["near"])
    r.run_for(1)
    assert health(r, mobs["near"]) == after             # let go: it stopped
    assert any(p.name == "minecraft:crit" for p in r.sent(Particle))
    assert r.problems() == []


def test_the_gatling_shot_cast_by_voice_is_one_shot(rig):
    r = rig(*GROUP)
    r.start()
    mobs = stage(r)
    assert r.cast("Steve", "gatling_shot")["status"] == "fired"
    r.run_for(1)
    assert health(r, mobs["near"]) == 970
