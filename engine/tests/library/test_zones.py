"""The zones group on FakeIo: anti_camp (TNT on a camper, after a warning, then a mock); arena,
with tether merged in as its "near" shape (teleports anyone who leaves a radius, a box, or the
group, back in); launch_pad (a block that throws whoever stands on it, through the kit's push);
walk_replace, with ice_path as its Args and chunk_changer merged in (changes blocks by rules at
each step, or in each chunk entered); frost_walk (made/main's krownjm_ice_fast: a slab that
follows its bearer continuously, only the new edge queued each time)."""

from vfg_engine.io import Fill, GiveEffect, Message, Particle, SetBlock, Sound, Summon, Teleport

GROUP = ("anti_camp", "arena", "launch_pad", "walk_replace", "frost_walk")
OW = "minecraft:overworld"


def block(r, x, y, z):
    return r.io.block_at(OW, x, y, z)


def test_the_group_loads(rig):
    r = rig(*GROUP)
    assert sorted(r.start()["loaded"]) == sorted(GROUP)


# ---- anti_camp -----------------------------------------------------------------------------

def test_camping_warns_then_drops_tnt_and_mocks(rig):
    r = rig("anti_camp")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    iid = r.start_scenario("anti_camp", seconds=6, radius=4, tnt=3, height=5, fuse=1, mock=True,
                           warning="MOVE")
    assert r.result(iid)["summary"] == "3 TNT fall on anyone of @a who stays within 4 blocks for 6 s"
    r.run_for(2.0)
    warned = [m for m in r.sent(Message) if m.where == "actionbar"]
    assert warned and all(m.players == ("Steve",) and m.text.startswith("§cMOVE ") for m in warned)
    r.run_for(5.0)                                       # 7 s total: past the 6 s window
    made = [s for s in r.sent(Summon) if s.entity_type == "minecraft:tnt"]
    assert len(made) == 3
    assert all(s.pos[1] == 69.0 and "fuse:20s" in s.nbt for s in made)   # height 5 over y 64; fuse 1 s = 20 ticks
    assert {(round(s.pos[0]), round(s.pos[2])) for s in made} == {(0, 0), (2, 0), (-2, 0)}
    mocked = [m for m in r.sent(Message) if m.where == "chat"]
    assert mocked and mocked[-1].text == "§4[Demon Lord] Steve thinks standing still is safe."
    assert r.running(iid)                                # until_stopped: it keeps watching
    assert r.problems() == []


def test_moving_away_resets_the_count(rig):
    r = rig("anti_camp")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    r.start_scenario("anti_camp", seconds=6, radius=4, tnt=1, mock=False)
    r.run_for(3.0)
    r.io.move("Steve", (20.0, 64.0, 0.0))                # well past the 4-block radius
    r.tick()
    r.run_for(3.5)
    assert not [s for s in r.sent(Summon) if s.entity_type == "minecraft:tnt"]   # the clock restarted
    r.run_for(3.0)
    assert len([s for s in r.sent(Summon) if s.entity_type == "minecraft:tnt"]) == 1
    assert r.problems() == []


def test_nobody_camping_nothing_falls(rig):
    r = rig("anti_camp")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    r.start_scenario("anti_camp", seconds=6, radius=4)
    for _ in range(10):
        r.io.move("Steve", (10.0 * _, 64.0, 0.0))
        r.run_for(1.0)
    assert not r.sent(Summon)
    assert r.problems() == []


def test_anti_camp_args_refused_in_words(rig):
    r = rig("anti_camp")
    r.start()
    reply = r.op("start", scenario="anti_camp", args={"seconds": 1})
    assert not reply["ok"] and "seconds" in reply["error"]


# ---- arena (and tether, its "near" shape) --------------------------------------------------

def test_radius_sends_back_to_where_it_started(rig):
    r = rig("arena")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    iid = r.start_scenario("arena", radius=10)
    assert r.result(iid)["summary"] == "@a kept within 10 blocks of 0 64 0"
    r.io.move("Steve", (20.0, 64.0, 0.0))                # out past 10 blocks
    r.run_for(1.0)
    tps = r.sent(Teleport)
    assert len(tps) == 1 and tps[0] == Teleport("Steve", OW, (0.0, 64.0, 0.0), None, None)
    warn = [m for m in r.sent(Message) if m.where == "actionbar"]
    assert warn and warn[0].text == "§cBack inside the arena"
    assert r.sent(Sound) and r.sent(Sound)[0].name == "minecraft:entity.enderman.teleport"
    r.run_for(1.0)                                       # back inside: no second teleport
    assert len(r.sent(Teleport)) == 1
    assert r.problems() == []


def test_box_sends_back_to_the_nearest_spot_inside(rig):
    r = rig("arena")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    iid = r.start_scenario("arena", shape="box", box=[-5, 60, -5, 5, 70, 5])
    assert r.result(iid)["summary"] == "@a kept in the box -5 60 -5 5 70 5"
    r.io.move("Steve", (3.0, 64.0, 3.0))                 # still inside
    r.run_for(0.6)
    r.io.move("Steve", (50.0, 64.0, 3.0))                # well outside, east
    r.run_for(1.0)
    tps = r.sent(Teleport)
    assert len(tps) == 1 and tps[0].pos == (5.5, 64.0, 3.0)   # half a block in from its east side
    r.run_for(1.0)
    assert len(r.sent(Teleport)) == 1                    # inside again: no second send
    assert r.problems() == []


def test_box_with_nobody_inside_yet_sends_them_in(rig):
    r = rig("arena")
    r.start()
    r.io.join("Steve", pos=(50.0, 64.0, 0.0))            # starts outside the box
    r.tick()
    r.start_scenario("arena", shape="box", box=[-5, 60, -5, 5, 70, 5])
    r.run_for(1.0)
    tps = r.sent(Teleport)
    assert len(tps) == 1 and tps[0].pos == (5.5, 64.0, 0.0)   # the nearest spot inside
    assert r.problems() == []


def test_radius_pulls_in_who_joins_or_respawns_outside(rig):
    r = rig("arena")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    r.start_scenario("arena", radius=10)
    r.run_for(0.5)
    r.io.join("Alex", pos=(40.0, 64.0, 0.0))             # joins outside
    r.run_for(0.5)
    assert [t.target for t in r.sent(Teleport)] == ["Alex"]
    r.io.kill("Steve")                                   # dies inside: no send while dead
    r.run_for(0.5)
    r.io.move("Steve", (60.0, 64.0, 0.0))
    r.io.respawn("Steve")                                # respawns outside
    r.run_for(0.5)
    assert [t.target for t in r.sent(Teleport)] == ["Alex", "Steve"]
    assert r.problems() == []


def test_near_shape_is_tether_sent_to_the_nearest_of_the_group(rig):
    r = rig("arena")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.io.join("Alex", pos=(5.0, 64.0, 0.0))
    r.tick()
    iid = r.start_scenario("arena", shape="near", radius=10, near="Alex", message="Stay with the group")
    assert r.result(iid)["summary"] == "@a kept within 10 blocks of Alex"
    r.io.move("Steve", (50.0, 64.0, 0.0))
    r.run_for(1.0)
    tps = r.sent(Teleport)
    assert len(tps) == 1 and tps[0] == Teleport("Steve", OW, (5.0, 64.0, 0.0), None, None)
    warn = [m for m in r.sent(Message) if m.where == "actionbar"]
    assert warn and warn[0].text == "§cStay with the group"
    assert r.problems() == []


def test_near_shape_with_nobody_to_tether_to_does_nothing(rig):
    r = rig("arena")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))             # Alex (the "near" of) never joins
    r.tick()
    r.start_scenario("arena", shape="near", radius=10, near="Alex")
    r.io.move("Steve", (500.0, 64.0, 0.0))
    r.run_for(1.0)
    assert not r.sent(Teleport)
    assert r.problems() == []


# ---- launch_pad -----------------------------------------------------------------------------

def test_standing_on_the_pad_launches_up_once_per_cooldown(rig):
    r = rig("launch_pad")
    r.start()
    r.io.join("Steve", pos=(0.5, 64.0, 0.5))
    r.io.set_block(OW, (0, 63, 0), "minecraft:slime_block")
    r.tick()
    iid = r.start_scenario("launch_pad", way="up", strength=8, cooldown=2)
    assert r.result(iid)["summary"] == "launch pads on for @a: standing on minecraft:slime_block throws them 8 blocks up"
    r.run_for(0.3)
    charges = [s for s in r.sent(Summon) if s.entity_type == "minecraft:wind_charge"]
    assert charges and all(abs(c.pos[0] - 0.5) <= 2.3 and abs(c.pos[2] - 0.5) <= 2.3 for c in charges)
    cushions = [g for g in r.sent(GiveEffect) if g.effect == "minecraft:resistance"]
    assert cushions and cushions[0].amplifier == 4                  # Resistance V, the safe landing's cushion
    first_count = len(r.sent(Summon))
    r.run_for(0.5)                                       # still within the 2 s cooldown
    assert len(r.sent(Summon)) == first_count
    r.run_for(2.0)                                       # past it: launched again
    assert len(r.sent(Summon)) > first_count
    assert r.problems() == []


def test_off_the_pad_nothing_happens(rig):
    r = rig("launch_pad")
    r.start()
    r.io.join("Steve", pos=(0.5, 64.0, 0.5))             # plain stone, not the pad block
    r.tick()
    r.start_scenario("launch_pad", block="gold_block", way="up", strength=8)
    r.run_for(1.0)
    assert not r.sent(Summon)
    assert r.problems() == []


def test_launch_pad_args_refused_in_words(rig):
    r = rig("launch_pad")
    r.start()
    reply = r.op("start", scenario="launch_pad", args={"way": "sideways"})
    assert not reply["ok"] and "way" in reply["error"]


# ---- walk_replace (ice_walk, ice_path, chunk_changer) ----------------------------------------

def test_each_step_turns_the_box_round_the_feet_and_flashes_on_landing(rig):
    r = rig("walk_replace")
    r.start()
    r.io.join("Steve", pos=(0.5, 64.0, 0.5))
    r.tick()
    iid = r.start_scenario("walk_replace", rules="stone > ice", radius=1, below=1, above=0, every_s=2)
    assert r.result(iid)["summary"] == ("ice walk on for @a: everything 1 blocks round each step turns to "
                                        "ice, 1 down and 0 up")
    r.tick(2)
    assert block(r, 0, 63, 0) == "minecraft:ice" and block(r, 1, 63, 1) == "minecraft:ice"
    assert block(r, 3, 63, 3) == "minecraft:stone"                  # outside the radius
    r.io.players["Steve"].on_ground = False                         # a fall of 3+ blocks, then land
    r.io.move("Steve", (0.5, 70.0, 0.5))
    r.tick()
    r.io.move("Steve", (0.5, 64.0, 0.5))
    r.io.players["Steve"].on_ground = True
    r.tick(2)
    assert r.sent(Particle) and r.sent(Sound)
    assert any(op.name.startswith("minecraft:entity.lightning_bolt") for op in r.sent(Sound))
    assert r.problems() == []


def test_a_path_under_the_feet_alone_is_ice_path(rig):
    r = rig("walk_replace")
    r.start()
    r.io.join("Steve", pos=(0.5, 64.0, 0.5))
    r.tick()
    r.start_scenario("walk_replace", rules="stone > ice", radius=2, below=1, above=0, every_s=2)
    r.tick(2)
    assert block(r, 0, 63, 0) == "minecraft:ice"
    assert block(r, 0, 62, 0) == "minecraft:stone"                  # below: 1, so no deeper than the block stood on
    assert r.problems() == []


def test_per_chunk_changes_each_chunk_entered_and_the_ones_round_it(rig):
    r = rig("walk_replace")
    r.start()
    r.io.join("Steve", pos=(0.5, 64.0, 0.5))
    r.tick()
    iid = r.start_scenario("walk_replace", per="chunk", rules="stone > ice", around_chunks=1, below=2, above=2)
    assert r.result(iid)["summary"] == ("chunk changer on for @a: 1 rule in each chunk they walk into and the "
                                        "8 round it")
    r.tick(2)
    assert block(r, 0, 63, 0) == "minecraft:ice"                    # his own chunk
    assert block(r, 20, 63, 20) == "minecraft:ice"                  # a neighbouring chunk, round it
    assert block(r, 0, 61, 0) == "minecraft:stone"                  # below the "below" reach (feet y 64, below 2)
    assert r.problems() == []


def test_walk_replace_args_refused_in_words(rig):
    r = rig("walk_replace")
    r.start()
    reply = r.op("start", scenario="walk_replace", args={"per": "sideways"})
    assert not reply["ok"] and "per" in reply["error"]
    reply = r.op("start", scenario="walk_replace", args={"rules": "not a rule"})
    assert not reply["ok"]


# ---- frost_walk (made/main's krownjm_ice_fast) -----------------------------------------------

def test_follows_the_bearer_queuing_only_the_new_edge(rig):
    r = rig("frost_walk")
    r.start()
    r.io.join("Steve", pos=(0.5, 64.0, 0.5))
    r.tick()
    reply = r.op("apply", who="Steve", mark="frost_walk",
                args={"radius": 2, "below": 1, "above": 0, "rules": "stone > ice"})
    assert reply["ok"]
    iid = reply["instance"]
    r.tick(2)
    assert r.running(iid)
    assert block(r, 0, 63, 0) == "minecraft:ice" and block(r, 2, 63, 2) == "minecraft:ice"
    before = len(r.sent(Fill)) + len(r.sent(SetBlock))
    r.io.move("Steve", (1.5, 64.0, 0.5))                 # one block over: only the new edge should queue
    r.tick(2)
    after = len(r.sent(Fill)) + len(r.sent(SetBlock))
    assert 0 < after - before < 6                        # far fewer pieces than refilling the whole slab
    assert block(r, 1, 63, 1) == "minecraft:ice"          # the new edge
    assert block(r, 1, 63, 0) == "minecraft:ice"          # still covered from before, untouched either way
    assert r.problems() == []


def test_clearing_it_stops_the_slab_from_following(rig):
    r = rig("frost_walk")
    r.start()
    r.io.join("Steve", pos=(0.5, 64.0, 0.5))
    r.tick()
    reply = r.op("apply", who="Steve", mark="frost_walk", args={"radius": 1, "rules": "stone > ice"})
    r.tick(2)
    assert r.op("clear", who="Steve", mark="frost_walk")["ok"]
    r.tick()
    assert not r.running(reply["instance"])
    before = len(r.sent(Fill)) + len(r.sent(SetBlock))
    r.io.move("Steve", (20.5, 64.0, 20.5))
    r.tick(2)
    assert len(r.sent(Fill)) + len(r.sent(SetBlock)) == before    # nothing more is queued once cleared
    assert r.problems() == []


def test_frost_walk_args_refused_in_words(rig):
    r = rig("frost_walk")
    r.start()
    r.io.join("Steve")
    r.tick()
    reply = r.op("apply", who="Steve", mark="frost_walk", args={"radius": 100})
    assert not reply["ok"] and "radius" in reply["error"]
