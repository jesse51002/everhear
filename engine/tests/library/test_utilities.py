"""The utilities group on FakeIo: command (up to five vanilla commands, stopping at the first
Minecraft refuses), teleport (every destination kind, landing on safe ground through the kit's
land_safely), save_place and forget_place (the engine's own places store), and the potion_effect
mark (keep_effects: one potion effect kept on its bearer, through self.keep_potion)."""

from vfg_engine.io import GiveEffect, Spawnpoint, Teleport

GROUP = ("command", "teleport", "save_place", "forget_place", "potion_effect")
OW = "minecraft:overworld"


def test_the_group_loads(rig):
    r = rig(*GROUP)
    assert sorted(r.start()["loaded"]) == sorted(GROUP)


# ---- command ---------------------------------------------------------------------------------

def test_command_runs_each_in_order_and_reports_replies(rig):
    r = rig("command")
    r.start()
    replies = {"time set day": "Set the time to 1000", "weather clear": "Changed the weather to clear"}
    r.io.query_reply = lambda cmd: replies.get(cmd, "")
    iid = r.start_scenario("command", commands=["time set day", "weather clear"])
    result = r.result(iid)
    assert result["facts"]["ran"] == ["time set day", "weather clear"]
    assert result["facts"]["replies"] == list(replies.values())
    assert result["facts"]["failed"] is None
    assert r.io.queries == ["time set day", "weather clear"]
    assert r.problems() == []


def test_command_stops_at_the_first_refused_reply(rig):
    r = rig("command")
    r.start()
    replies = {"time set day": "Set the time to 1000", "give Steve minecraft:bogus 1": "Unknown item 'minecraft:bogus'"}
    r.io.query_reply = lambda cmd: replies.get(cmd, "")
    iid = r.start_scenario("command", commands=["time set day", "give Steve minecraft:bogus 1", "weather clear"])
    result = r.result(iid)
    assert result["facts"]["ran"] == ["time set day"]
    assert result["facts"]["failed"] == "give Steve minecraft:bogus 1"
    assert r.io.queries == ["time set day", "give Steve minecraft:bogus 1"]     # the third never sent
    assert r.problems() == []


def test_command_refuses_block_edits_without_sending_them(rig):
    r = rig("command")
    r.start()
    iid = r.start_scenario("command", commands=["setblock 0 64 0 minecraft:stone"])
    result = r.result(iid)
    assert result["facts"]["failed"] == "setblock 0 64 0 minecraft:stone"
    assert "setblock" in result["summary"]
    assert r.io.queries == []
    assert r.problems() == []


def test_command_refuses_block_edits_behind_execute_run(rig):
    r = rig("command")
    r.start()
    iid = r.start_scenario("command", commands=["execute as @a run fill 0 64 0 1 65 1 minecraft:air"])
    result = r.result(iid)
    assert result["facts"]["failed"] is not None
    assert r.io.queries == []


def test_command_with_no_commands_says_so(rig):
    r = rig("command")
    r.start()
    iid = r.start_scenario("command")
    assert r.result(iid)["summary"] == "no commands given"
    assert r.problems() == []


def test_command_refuses_the_owners_commands_in_words(rig):
    r = rig("command")
    r.start()
    reply = r.op("start", scenario="command", args={"commands": ["stop"]})
    assert not reply["ok"] and "stop" in reply["error"]


# ---- teleport ----------------------------------------------------------------------------------

def test_teleport_to_a_saved_place(rig):
    r = rig("teleport", "save_place")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    r.start_scenario("save_place", name="home", player="Steve")
    r.io.move("Steve", (500.0, 64.0, 500.0))
    r.tick()
    iid = r.start_scenario("teleport", to="place", target="home")
    tps = r.sent(Teleport)
    assert len(tps) == 1 and tps[0].target == "Steve" and tps[0].pos == (0.5, 64.0, 0.5)
    assert "home" in r.result(iid)["summary"]
    assert {g.effect for g in r.sent(GiveEffect)} == {"minecraft:resistance", "minecraft:slow_falling"}
    assert r.problems() == []


def test_teleport_to_a_place_that_does_not_exist(rig):
    r = rig("teleport")
    r.start()
    r.io.join("Steve")
    r.tick()
    iid = r.start_scenario("teleport", to="place", target="nowhere")
    assert "nowhere" in r.result(iid)["summary"]
    assert not r.sent(Teleport)


def test_teleport_to_coords_lands_on_the_exact_spot_when_safe(rig):
    r = rig("teleport")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    iid = r.start_scenario("teleport", to="coords", target="10 64 20")
    tps = r.sent(Teleport)
    assert len(tps) == 1 and tps[0].pos == (10.5, 64.0, 20.5) and tps[0].dim == OW
    assert r.result(iid)["facts"]["pos"] == [10, 64, 20]


def test_teleport_to_coords_without_y_finds_the_ground(rig):
    r = rig("teleport")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    r.start_scenario("teleport", to="coords", target="30 40")
    (tp,) = r.sent(Teleport)
    assert tp.pos[1] == 64.0                 # FakeIo's ground is y 63: feet stand at 64 everywhere


def test_teleport_far_lands_roughly_that_far_away(rig):
    import math
    r = rig("teleport")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    r.start_scenario("teleport", to="far", target="500")
    (tp,) = r.sent(Teleport)
    # flat ground: the first safe ring (8 blocks) round the random spot lands it close to 500 away
    assert 490 <= math.hypot(tp.pos[0], tp.pos[2]) <= 510


def test_teleport_a_long_move_sets_the_spawn_point(rig):
    r = rig("teleport")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    r.start_scenario("teleport", to="coords", target="2000 64 0")
    assert r.sent(Spawnpoint) and r.sent(Spawnpoint)[0].player == "Steve"


def test_teleport_a_short_move_does_not_set_the_spawn_point(rig):
    r = rig("teleport")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    r.start_scenario("teleport", to="coords", target="10 64 20")
    assert not r.sent(Spawnpoint)


def test_teleport_everyone_else_follows_the_first_online(rig):
    r = rig("teleport")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.io.join("Alex", pos=(50.0, 64.0, 50.0))
    r.tick()
    r.start_scenario("teleport", players="@a", to="coords", target="10 64 20")
    targets = {tp.target for tp in r.sent(Teleport)}
    assert targets == {"Steve", "Alex"}


def test_teleport_nobody_online_is_refused_in_words(rig):
    r = rig("teleport")
    r.start()
    iid = r.start_scenario("teleport", players="Steve", to="coords", target="0 64 0")
    assert "not online" in r.result(iid)["summary"]
    assert not r.sent(Teleport)


def test_teleport_to_a_biome_locates_it_off_the_tick(rig):
    r = rig("teleport")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    r.io.query_reply = lambda cmd: "The nearest plains is at [112, ~, -64]" if "locate biome" in cmd else ""
    iid = r.start_scenario("teleport", to="biome", target="plains")
    assert not r.sent(Teleport)                 # the locate is still out; it lands next tick
    r.tick()
    tps = r.sent(Teleport)
    assert len(tps) == 1
    # the ring search lands within 8 blocks of the biome's located edge, 112, -64
    assert abs(tps[0].pos[0] - 112.5) <= 8 and abs(tps[0].pos[2] - (-63.5)) <= 8
    assert "a plains" in r.result(iid)["summary"]
    assert r.problems() == []


def test_teleport_to_a_structure_retries_as_a_tag(rig):
    r = rig("teleport")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()

    def reply(cmd):
        if "locate structure #" in cmd:
            return "The nearest village (minecraft:village_plains) is at [200, ~, 300]"
        if "locate structure" in cmd:
            return "There is no structure called minecraft:village"
        return ""
    r.io.query_reply = reply
    iid = r.start_scenario("teleport", to="structure", target="village")
    r.tick()           # the first locate's reply: "no structure", so it asks again as a tag
    r.tick()           # the tag's reply: found; it lands
    tps = r.sent(Teleport)
    assert len(tps) == 1
    assert abs(tps[0].pos[0] - 200.5) <= 8 and abs(tps[0].pos[2] - 300.5) <= 8
    assert "plains village" in r.result(iid)["summary"]


def test_teleport_to_last_death(rig):
    r = rig("teleport")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    r.io.query_reply = lambda cmd: (
        'Steve has the following entity data: {pos: [I; 5, 70, 9], dimension: "minecraft:overworld"}'
        if "LastDeathLocation" in cmd else "")
    iid = r.start_scenario("teleport", to="last_death")
    assert r.sent(Teleport)
    assert "last died" in r.result(iid)["summary"]


def test_teleport_to_last_death_with_none_on_record(rig):
    r = rig("teleport")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    r.io.query_reply = lambda cmd: "Found no elements matching argument type"
    iid = r.start_scenario("teleport", to="last_death")
    assert "no death on record" in r.result(iid)["summary"]
    assert not r.sent(Teleport)


def test_teleport_args_refused_in_words(rig):
    r = rig("teleport")
    r.start()
    r.io.join("Steve")
    r.tick()
    reply = r.op("start", scenario="teleport", args={"to": "coords", "target": "not numbers"})
    assert reply["ok"]               # target's format is checked inside start, not by Args
    assert "coordinates" in r.result(reply["instance"])["summary"]


# ---- save_place / forget_place -----------------------------------------------------------------

def test_save_place_by_player(rig):
    r = rig("save_place")
    r.start()
    r.io.join("Steve", pos=(12.0, 70.0, -5.0))
    r.tick()
    iid = r.start_scenario("save_place", name="home", player="Steve")
    result = r.result(iid)
    assert result["summary"] == "saved home at 12 70 -5 in overworld"
    assert r.rt.places.get("home")["pos"] == [12.0, 70.0, -5.0]


def test_save_place_by_coords(rig):
    r = rig("save_place")
    r.start()
    iid = r.start_scenario("save_place", name="base", at="100 64 200")
    assert r.result(iid)["summary"] == "saved base at 100 64 200 in overworld"


def test_save_place_needs_exactly_one_of_player_and_at(rig):
    r = rig("save_place")
    r.start()
    r.io.join("Steve")
    r.tick()
    iid = r.start_scenario("save_place", name="home", player="Steve", at="0 64 0")
    assert "not both" in r.result(iid)["summary"]
    iid2 = r.start_scenario("save_place", name="home2")
    assert "give the player or the coordinates" in r.result(iid2)["summary"]


def test_save_place_needs_a_name(rig):
    r = rig("save_place")
    r.start()
    iid = r.start_scenario("save_place", at="0 64 0")
    assert "which name" in r.result(iid)["summary"]


def test_save_place_then_forget_it(rig):
    r = rig("save_place", "forget_place")
    r.start()
    r.start_scenario("save_place", name="camp", at="1 64 1")
    assert r.rt.places.get("camp") is not None
    iid = r.start_scenario("forget_place", name="camp")
    result = r.result(iid)
    assert result["summary"] == "forgot camp"
    assert result["facts"] == {"name": "camp", "forgotten": True}
    assert r.rt.places.get("camp") is None


def test_forget_place_that_was_never_saved(rig):
    r = rig("forget_place")
    r.start()
    iid = r.start_scenario("forget_place", name="ghost")
    assert "there is no saved place called ghost" in r.result(iid)["summary"]
    assert r.result(iid)["facts"]["forgotten"] is False


# ---- potion_effect ---------------------------------------------------------------------------

def test_potion_effect_keeps_an_effect_at_a_level(rig):
    r = rig("potion_effect")
    r.start()
    r.io.join("Steve")
    r.op("apply", who="Steve", mark="potion_effect", args={"effect": "speed", "amplifier": 3})
    r.run_for(0.5)
    effects = r.io.players["Steve"].effects
    assert "minecraft:speed" in effects and effects["minecraft:speed"].amplifier == 3
    assert r.problems() == []


def test_potion_effect_bad_effect_refused_in_words(rig):
    r = rig("potion_effect")
    r.start()
    r.io.join("Steve")
    reply = r.op("apply", who="Steve", mark="potion_effect", args={"effect": "made_up"})
    assert not reply["ok"]
