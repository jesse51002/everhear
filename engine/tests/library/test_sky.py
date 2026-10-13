"""The sky group on FakeIo: lava_rain and tnt_rain falling round each player until stopped; meteor
(with meteor_shower as its count/radius) falling and blasting a crater, breaking or not; nuke (with
made/main's destroy_everything merged in as its fire_s) going off, sparing and protecting players
only above fire_s, carving a crater, killing what is in it; args refused in words; nobody online."""

from vfg_engine.io import Message, Particle, Remove, Summon

OW = "minecraft:overworld"


def online(r, *names):
    for i, n in enumerate(names):
        r.io.join(n, pos=(100.0 * i, 64.0, 0.0))        # on FakeIo's stone, whose top is y 63
    r.tick()


def steve(r, pos=(0.0, 64.0, 0.0)):
    r.io.join("Steve", pos=pos, yaw=180.0)
    r.tick()


def block(r, x, y, z):
    return r.io.block_at(OW, x, y, z)


def finish(r, iid, seconds=60):
    """Tick until the instance ends (a block-queue build, spread over ticks), then one tick more:
    a done() the block queue calls (the crater, then the pad) queues its last ops (a teleport)
    only as it returns, too late for that same tick's write; the next tick's flushes them."""
    for _ in range(int(seconds / 0.1)):
        if not r.running(iid):
            break
        r.tick()
    assert not r.running(iid), "still running after %ds" % seconds
    r.tick()
    return iid


# ---------------------------------------------------------------- lava_rain

def test_lava_rain_loads(rig):
    r = rig("lava_rain")
    assert r.start()["loaded"] == ["lava_rain"]


def test_lava_rain_round_each_player_until_stopped(rig):
    r = rig("lava_rain")
    r.start()
    online(r, "Steve", "Alex")
    iid = r.start_scenario("lava_rain", drops=10, count=3, radius=5, every_s=1)
    r.tick(2)                                      # the first puff, already sent at start()
    first = r.sent(Particle)
    assert len(first) == 6                         # 3 puffs round each of 2 players
    assert all(p.count == 10 for p in first)
    assert {p.name for p in first} <= {"minecraft:falling_lava", "minecraft:dripping_lava",
                                       "minecraft:lava", "minecraft:flame"}
    for p in first:
        near = min(abs(p.pos[0] - 0), abs(p.pos[0] - 100))
        assert near <= 5.01
    r.run_for(1.1)
    assert len(r.sent(Particle)) > len(first)       # a second puff landed
    assert r.running(iid)
    assert r.op("stop", instance=iid)["ok"]
    r.tick()
    assert not r.running(iid)
    assert r.problems() == []


def test_lava_rain_args_refused_in_words(rig):
    r = rig("lava_rain")
    r.start()
    reply = r.op("start", scenario="lava_rain", args={"drops": 500})
    assert not reply["ok"] and "drops" in reply["error"]


# ---------------------------------------------------------------- tnt_rain

def test_tnt_rain_loads(rig):
    r = rig("tnt_rain")
    assert r.start()["loaded"] == ["tnt_rain"]


def test_tnt_rain_round_each_player_until_stopped(rig):
    r = rig("tnt_rain")
    r.start()
    online(r, "Steve", "Alex")
    iid = r.start_scenario("tnt_rain", count=4, radius=6, every_s=2, fuse_s=3)
    r.tick(2)
    made = [s for s in r.sent(Summon) if s.entity_type == "minecraft:tnt"]
    assert len(made) == 8                           # 4 round each of 2 players, the first volley
    assert all(s.nbt == "{fuse:60}" for s in made)  # 3 s * 20 ticks a second
    for s in made:
        near = min(abs(s.pos[0] - 0), abs(s.pos[0] - 100))
        assert near <= 6.01
    r.run_for(2.1)
    assert len(r.sent(Summon)) > len(made)          # a second volley fell
    assert r.running(iid)
    assert r.op("stop", instance=iid)["ok"]
    r.tick(2)
    assert not r.running(iid)
    removed = {op.uuid for op in r.sent(Remove)}
    assert {s.uuid for s in made} <= removed        # a stop takes away what is still falling
    assert r.problems() == []


def test_tnt_rain_args_refused_in_words(rig):
    r = rig("tnt_rain")
    r.start()
    reply = r.op("start", scenario="tnt_rain", args={"fuse_s": 20})
    assert not reply["ok"] and "fuse_s" in reply["error"]


# ---------------------------------------------------------------- meteor

def test_meteor_loads(rig):
    r = rig("meteor")
    assert r.start()["loaded"] == ["meteor"]


def test_a_meteor_falls_and_blasts_a_crater(rig):
    r = rig("meteor")
    r.start()
    steve(r)
    iid = r.start_scenario("meteor", players="Steve", radius=0, size=3, power=4, breaks=True)
    r.run_for(6)                                    # the 4.8 s fall, then the blast
    assert not r.running(iid)
    got = r.result(iid)
    assert got["summary"] == "1 meteor fell on Steve, size 3: 0 64 0"
    assert got["facts"]["meteors"] == 1 and got["facts"]["at"] == ["0 64 0"]
    displays = [s for s in r.sent(Summon) if s.entity_type == "minecraft:block_display"]
    assert len(displays) == 19                      # a 3x3x3 cluster without its corners
    # "rings": Steve stands exactly where it lands, so the middle blast and its inner ring (4 out)
    # are within keep (7.5) of him and never go off; only the outer ring (8 out) survives
    tnts = [s for s in r.sent(Summon) if s.entity_type == "minecraft:tnt"]
    assert len(tnts) == 10
    assert r.problems() == []


def test_meteor_breaks_false_only_hurts_and_throws_back(rig):
    r = rig("meteor")
    r.start()
    steve(r)
    z = r.io.add_entity("minecraft:zombie", (8.0, 64.0, 0.0), health=200.0)
    r.start_scenario("meteor", players="Steve", radius=0, power=4, breaks=False)
    r.run_for(6)
    assert not [s for s in r.sent(Summon) if s.entity_type == "minecraft:tnt"]
    assert r.io.by_uuid[z].health < 200.0            # the outer ring's blast hurt it, nothing broken
    assert r.problems() == []


def test_a_meteor_shower_is_more_of_them_further_out(rig):
    r = rig("meteor")
    r.start()
    steve(r)
    iid = r.start_scenario("meteor", players="Steve", count=8, radius=40, power=0)
    r.run_for(8)
    assert not r.running(iid)
    got = r.result(iid)
    assert got["facts"]["meteors"] == 8
    assert not [s for s in r.sent(Summon) if s.entity_type == "minecraft:tnt"]   # power 0: no blast at all


def test_meteor_nobody_online(rig):
    r = rig("meteor")
    r.start()
    iid = r.start_scenario("meteor", players="Steve")
    r.tick(2)
    assert not r.running(iid)
    assert r.result(iid)["summary"] == "no meteor fell: Steve is not online"


def test_meteor_args_refused_in_words(rig):
    r = rig("meteor")
    r.start()
    reply = r.op("start", scenario="meteor", args={"count": 200})
    assert not reply["ok"] and "count" in reply["error"]
    reply = r.op("start", scenario="meteor", args={"power": 20})
    assert not reply["ok"] and "power" in reply["error"]


# ---------------------------------------------------------------- nuke

def test_nuke_loads(rig):
    r = rig("nuke")
    assert r.start()["loaded"] == ["nuke"]


def test_nuke_goes_off_at_once_kills_and_carves(rig):
    r = rig("nuke")
    r.start()
    steve(r)
    z = r.io.add_entity("minecraft:zombie", (2.0, 64.0, 0.0), health=20.0)
    iid = r.start_scenario("nuke", players="Steve", countdown_s=0, radius=10, kill=True)
    finish(r, iid, 60)
    got = r.result(iid)
    assert got["summary"] == "nuke went off at 0 64 0: radius 10, 2 killed"
    assert got["facts"]["killed"] == 2
    assert z not in r.io.by_uuid                    # the zombie died
    assert not r.io.players["Steve"].alive          # nobody is spared by default: Steve too
    assert block(r, 0, 60, 0) == "minecraft:air"    # carved, where it was stone
    assert block(r, 0, 60, 30) == "minecraft:stone"  # well outside radius 10: untouched
    assert r.problems() == []


def test_nuke_a_stop_before_zero_calls_it_off(rig):
    r = rig("nuke")
    r.start()
    steve(r)
    iid = r.start_scenario("nuke", players="Steve", countdown_s=600, radius=10)
    r.tick()
    assert r.running(iid)
    assert r.op("stop", instance=iid)["ok"]
    r.tick()
    assert not r.running(iid)
    assert r.io.players["Steve"].alive               # it never went off
    assert block(r, 0, 60, 0) == "minecraft:stone"
    assert r.problems() == []


def test_nuke_fire_s_spares_and_protects_the_players(rig):
    r = rig("nuke")
    r.start()
    steve(r)
    z = r.io.add_entity("minecraft:zombie", (2.0, 64.0, 0.0), health=20.0)
    iid = r.start_scenario("nuke", players="Steve", countdown_s=0, radius=10, fire_s=1, leave="fire")
    finish(r, iid, 60)
    assert r.result(iid)["summary"] == "nuke went off at 0 64 0: radius 10, 1 killed"
    assert z not in r.io.by_uuid                     # the zombie still died
    assert r.io.players["Steve"].alive               # Steve was spared and protected
    assert block(r, 0, 63, 0) == "minecraft:obsidian"   # the pad, put back under him after the carve
    assert r.io.players["Steve"].pos == (0.5, 64.0, 0.5)   # and he was put back on it
    assert r.problems() == []


def test_nuke_no_countdown_text_is_said_at_once(rig):
    r = rig("nuke")
    r.start()
    steve(r)
    r.start_scenario("nuke", players="Steve", countdown_s=0, radius=0, kill=False, countdown_text="Destroy "
                     "everything")
    r.tick()
    titles = [m for m in r.sent(Message) if m.where == "title"]
    assert titles and titles[0].text == "█" * 30


def test_nuke_nobody_online(rig):
    r = rig("nuke")
    r.start()
    iid = r.start_scenario("nuke", players="Steve")
    r.tick(2)
    assert not r.running(iid)
    assert r.result(iid)["summary"] == "no nuke: Steve is not online"


def test_nuke_args_refused_in_words(rig):
    r = rig("nuke")
    r.start()
    reply = r.op("start", scenario="nuke", args={"radius": 2000})
    assert not reply["ok"] and "radius" in reply["error"]
    reply = r.op("start", scenario="nuke", args={"leave": "lightning"})
    assert not reply["ok"] and "leave" in reply["error"]
