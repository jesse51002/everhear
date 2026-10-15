"""McIo's reads on demand (part 2) against a scripted server: the commands they send and how they
read the replies. The replies are 26.3's, recorded on ge-mc; test_mc_live.py runs them for real."""

import contextlib
import math

import pytest

from vfg_engine.mc import McIo, OpRefused, reading

STEVE_UUID = "8667ba71-b85a-4004-af54-457a9734eed7"


class Scripted:
    """An Rcon-like face: each command answered by the first rule whose prefix it starts with."""

    class Stats:
        connects = 1

    def __init__(self, rules):
        self.rules, self.sent, self.stats = rules, [], self.Stats()

    @contextlib.contextmanager
    def held(self):
        yield self

    def command(self, cmd):
        self.sent.append(cmd)
        for prefix, reply in self.rules:
            if cmd.startswith(prefix):
                return reply(cmd) if callable(reply) else reply
        raise AssertionError("unexpected command %r" % cmd)


def io_with(rules):
    srv = Scripted(rules)
    m = McIo(rcon=srv, mirror=False)
    m.tables = reading.Tables(types=[""] * 200, blocks=[""] * 1300)
    m.tables.blocks[448], m.tables.blocks[1033] = "minecraft:grass_block", "minecraft:sponge"
    m.tables.types[155] = "minecraft:wolf"
    m.names.update("There are 1 of a max of 20 players online: Steve (%s)" % STEVE_UUID)
    return m, srv


def test_probe_sends_points_in_chunks_and_reads_a_byte_each():
    m, srv = io_with([("data modify", "Modified storage everhear:io"),
                      ("function everhear:io/probe", "Running function everhear:io/probe"),
                      ("data get storage everhear:out probe",
                       "Storage everhear:out has the following contents: [" + ", ".join(["1b", "0b"] * 60) + "]")])
    pts = [(x, 84, 0) for x in range(120)]
    assert m.probe("minecraft:overworld", pts, "minecraft:grass_block") == [True, False] * 60
    first = srv.sent[0]
    assert first.startswith('data modify storage everhear:io pr set value {d:"minecraft:overworld",f:"is",'
                            'b:"minecraft:grass_block",p:[{x:0,y:84,z:0},')
    assert all(len(c.encode()) <= 1446 for c in srv.sent)
    assert srv.sent.count("data modify storage everhear:io pr.p append from storage everhear:io pp[]") == 2
    m, srv = io_with([("data modify", "Modified storage everhear:io"),
                      ("function everhear:io/probe", "Running function everhear:io/probe"),
                      ("data get storage everhear:out probe", "Storage everhear:out has the following contents: [1b]")])
    m.probe("minecraft:overworld", [(0, 85, 0)], "open")
    assert 'f:"two",b:"#everhear:open"' in srv.sent[0]
    m.probe("minecraft:overworld", [(0, 85, 0)], "solid")
    assert 'f:"not",b:"#everhear:not_solid"' in srv.sent[3]
    with pytest.raises(OpRefused):
        m.probe("minecraft:overworld", [(0, 85, 0)], 'chest{Items:[]}')
    assert m.probe("minecraft:overworld", [], "solid") == []


def test_ground_y():
    m, srv = io_with([('function everhear:io/ground {d:"minecraft:overworld",x:0,z:0}',
                       "Running function everhear:io/groundFunction everhear:io/ground returned 85"),
                      ("function everhear:io/ground",
                       "Running function everhear:io/groundFunction everhear:io/ground returned -99999")])
    assert m.ground_y("minecraft:overworld", 0.4, 0.9) == 85
    assert m.ground_y("minecraft:overworld", 5000, 0) is None


@pytest.mark.parametrize("direction, yaw, pitch", [((0, 0, 1), 0, 0), ((-1, 0, 0), 90, 0), ((1, 0, 0), -90, 0),
                                                   ((0, -1, 0), 0, 90), ((0, 0.5, math.sqrt(3) / 2), 0, -30)])
def test_a_ray_turns_its_direction_into_minecrafts_rotation(direction, yaw, pitch):
    m, srv = io_with([("function everhear:io/ray", "Running function everhear:io/ray"),
                      ("data get storage everhear:out ray",
                       "Storage everhear:out has the following contents: {p: [0.5d, 84.99d, 3.31d], b: 448, d: 104, k: \"block\"}")])
    look = m.ray("minecraft:overworld", (0.5, 86.62, 0.5), direction, 50)
    sent = srv.sent[0]
    got_yaw = float(sent.split("yaw:")[1].split(",")[0])
    got_pitch = float(sent.split("pitch:")[1].split(",")[0])
    assert (got_yaw, got_pitch) == pytest.approx((yaw, pitch), abs=1e-4) or abs(abs(got_yaw) - 180) < 1e-4 and yaw == 0
    assert ",n:1600}" in sent
    assert (look.kind, look.block, look.block_id, look.distance) == ("block", (0, 84, 3), "minecraft:grass_block", 3.25)
    assert reading.facing(got_yaw, got_pitch) == pytest.approx(tuple(c / math.dist((0, 0, 0), direction) for c in direction),
                                                               abs=1e-6)


def test_a_ray_through_blocks_asks_nothing():
    m, srv = io_with([])
    look = m.ray("minecraft:overworld", (0, 0, 0), (0, 0, 2), 30, through_blocks=True)
    assert (look.kind, look.pos, look.distance) == ("none", (0, 0, 30), 30)
    assert srv.sent == []
    with pytest.raises(OpRefused):
        m.ray("minecraft:overworld", (0, 0, 0), (0, 0, 0), 30)


def test_entities_and_loaded_and_mspt_and_world_id():
    m, srv = io_with([
        ("data modify storage everhear:io en", "Modified storage everhear:io"),
        ("function everhear:io/entities", "Running function everhear:io/entities"),
        ("data get storage everhear:out ents", 'Storage everhear:out has the following contents: [{p: [-8.1d, 85.0d, 3.1d], '
         'd: "minecraft:overworld", u: [I; 1, 2, 3, 4], h: 8.0f, y: 155, o: [I; -2040022415, -1202044924, -1353431686, '
         '-1758138665]}, {p: [0.0d, 85.0d, 0.0d], u: [I; 1, 2, 3, 5], h: 0.0f, y: 155}]'),
        ("execute in minecraft:overworld if loaded 0 0 0", "Test passed"),
        ("execute in minecraft:overworld if loaded", "Test failed"),
        ("tick query", "The game is running normallyTarget tick rate: 20.0 per second.\nAverage time per tick: 8.4ms "
                       "(Target: 50.0ms)Percentiles: P50: 8.6ms P95: 12.8ms P99: 15.9ms. Sample: 100"),
        ("data get storage everhear:meta world", "Storage everhear:meta has the following contents: "
                                                 "{a: 266125805, b: -1951559074, t: 100784}")])
    ents = m.entities("minecraft:overworld", (0.5, 85, 0.5), 20, types=["wolf"])
    assert srv.sent[0] == 'data modify storage everhear:io en set value {d:"minecraft:overworld",x:0.5,y:85,z:0.5,r:20,t:[{t:"minecraft:wolf"}]}'
    assert [(e.type, e.owner) for e in ents] == [("minecraft:wolf", "Steve")]      # the dying one left out
    assert m.loaded("minecraft:overworld", 0.5, 0.5) and not m.loaded("minecraft:overworld", 5000, 0)
    assert m.mspt() == 8.4
    m.mspt()
    assert srv.sent.count("tick query") == 1                                       # once a second at most
    assert m.world_id() == "%08x%08x" % (266125805, -1951559074 & 0xFFFFFFFF)
    m.world_id()
    assert srv.sent.count("data get storage everhear:meta world") == 1


def test_watch_mined_and_the_place_of_a_mine():
    world = {"a": 1}
    m, srv = io_with([("scoreboard objectives add", "Created new objective [everhear.mn.1033]"),
                      ("scoreboard players reset", "Reset [everhear.mn.1033] for 0 entities"),
                      ("data modify", "Modified storage everhear:config"),
                      ("data get storage everhear:meta world",
                       lambda cmd: "Storage everhear:meta has the following contents: {a: %d, b: 2, t: 3}" % world["a"])])
    assert m.watch_mined(["sponge", "minecraft:not_a_block"]) == ["minecraft:not_a_block"]
    adds = [c for c in srv.sent if c.startswith("scoreboard objectives add")]
    assert adds == ["scoreboard objectives add everhear.mn.1033 minecraft.mined:minecraft.sponge"]
    assert "scoreboard players reset * everhear.mn.1033" in srv.sent                # zeroed when made
    assert 'data modify storage everhear:io mw set value [{o:"everhear.mn.1033",b:"minecraft:sponge"}]' in srv.sent
    srv.sent.clear()
    m.watch_mined(["minecraft:sponge"])
    assert not any(c.startswith("scoreboard objectives add") for c in srv.sent)   # made once
    # a new connection (a restarted server): made again; one that is there already is not zeroed
    srv.stats.connects = 2
    srv.rules.insert(0, ("scoreboard objectives add", "An objective already exists by that name"))
    srv.sent.clear()
    assert m.watch_mined(["minecraft:sponge"]) == []
    assert "scoreboard objectives add everhear.mn.1033 minecraft.mined:minecraft.sponge" in srv.sent
    assert not any(c.startswith("scoreboard players reset") for c in srv.sent)
    srv.sent.clear()
    m.watch_mined(["minecraft:sponge"])
    assert not any(c.startswith("scoreboard objectives add") for c in srv.sent)
    # a new world on the same connection's count (read again after a reconnect): made again too
    srv.rules.pop(0)
    world["a"] = 9
    srv.sent.clear()
    m.watch_mined(["minecraft:sponge"])
    assert "scoreboard objectives add everhear.mn.1033 minecraft.mined:minecraft.sponge" in srv.sent
    # a mine's place is the block the player looked at in the last snapshot, of the kind mined
    from vfg_engine.io import RawEvent
    m._looks["Steve"] = [("minecraft:sponge", (0, 85, 2)), ("minecraft:dirt", (0, 84, 3))]   # glanced away last
    ev = m._placed(RawEvent(1, "mine", "Steve", {"block": "minecraft:sponge", "count": 1}, 5))
    assert ev.data["pos"] == (0, 85, 2)
    ev = m._placed(RawEvent(2, "mine", "Steve", {"block": "minecraft:stone", "count": 1}, 5))
    assert "pos" not in ev.data
