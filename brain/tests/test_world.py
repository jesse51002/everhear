"""The world a call sees: each player, the time, the running copies, the places."""

import asyncio
import json

from brain import running
from brain.vfg import VfgResult
from brain.world import World, players

from conftest import STATE, FakeVfg, catalog

STEVE = ("- Steve at 10 64 -20 in overworld: 14.5/20 health, 18/20 food, armor 12, level 5, survival; "
         "effects: speed II 0:30, night vision ∞")


async def test_the_world_shares_a_young_state_and_falls_back_to_the_last_good_one(tmp_path):
    vfg, now = FakeVfg(), [100.0]
    world = World(vfg, tmp_path / "running.json", catalog(), clock=lambda: now[0])
    (tmp_path / "running.json").write_text(json.dumps({"copies": {
        "railgun_alex": {"action": "railgun", "values": {"player": "Alex", "cooldown": 0}, "task": None},
        "frost_bow_alex": {"action": "frost_bow_alex", "values": {}, "task": "frost_bow_alex"},
        "frost_bow_alex_2": {"action": "frost_bow_alex", "values": {"player": "Steve"}, "task": "frost_bow_alex"}}}))
    seen = await world.seen()
    assert seen.players == f"PLAYERS ONLINE:\n{STEVE}; Steve's items, by slot: hotbar.4 iron_sword (held)"
    assert seen.clock == "TIME: day (6000, day 3). WEATHER: clear."
    assert seen.running.endswith("- frost_bow_alex: frost_bow_alex, its defaults\n"  # a task's copy: its own name
                                 "- frost_bow_alex_2: frost_bow_alex, player=Steve, started by frost_bow_alex\n"
                                 "- railgun_alex: railgun, player=Alex cooldown=0")
    assert seen.places == "SAVED PLACES: home 1 64 2 overworld (home_steve)" and seen.known == {"home"}
    assert seen.incantations == "INCANTATIONS: none"
    now[0] += 1.5
    await world.seen()
    assert len(vfg.of("state")) == 1 and len(vfg.of("incantation")) == 1  # a 1.5 s old state is fresh enough

    async def stuck(argv):
        await asyncio.sleep(1)
        return VfgResult(0, json.dumps({**STATE, "online": []}), "")

    vfg.on("state", answer=stuck)
    now[0] += 5
    assert (await world.seen(wait=0.01)).players.startswith("PLAYERS ONLINE:\n- Steve")  # the last good one
    assert (await world.glance()).players == f"PLAYERS ONLINE:\n{STEVE}"  # Respond's: no items
    await world.close()


def test_each_player_has_their_place_first_then_what_minecraft_answered_and_never_a_guess():
    def one(**p) -> str:
        return players({"online": [{"name": "Alex", "pos": [197, 68, 251], "dimension": "the_nether", **p}]},
                       items=False).removeprefix("PLAYERS ONLINE:\n")

    assert one() == "- Alex at 197 68 251 in the_nether"  # a vfg state from before the state fields
    assert one(health=None, max_health=None, food=None, xp_level=None, armor=None, game_mode=None,
               effects=None) == "- Alex at 197 68 251 in the_nether"  # Minecraft answered nothing
    assert one(health=3.0, max_health=None, food=0, armor=0.0, effects=[]) == \
        "- Alex at 197 68 251 in the_nether: 3 health, 0/20 food, armor 0; no effects"
    assert one(health=13.333334, max_health=40.0, xp_level=0, game_mode="creative", effects=[
        {"id": "minecraft:strength", "amplifier": 254, "duration": 72000},
        {"id": "minecraft:haste", "amplifier": 9, "duration": 1250},
        {"id": "minecraft:glowing", "amplifier": 0, "duration": None}]) == (
        "- Alex at 197 68 251 in the_nether: 13.3/40 health, level 0, creative; "
        "effects: strength 255 1:00:00, haste X 1:02, glowing")


def test_the_brain_sees_only_the_items_of_the_players_it_names():
    alex = {**STATE["online"][0], "name": "Alex"}
    world = {**STATE, "online": [STATE["online"][0], alex]}
    shown = players(world, items=["Steve"])
    assert f"{STEVE}; Steve's items, by slot: hotbar.4 iron_sword (held)\n" in shown
    assert shown.endswith("- Alex at 10 64 -20 in overworld: 14.5/20 health, 18/20 food, armor 12, level 5, "
                          "survival; effects: speed II 0:30, night vision ∞")
    assert "items" not in players(world, items=[]) and players(world, items=False) == players(world, items=[])


async def test_the_read_tool_fetches_a_state_of_its_own_and_says_who_is_not_online(tmp_path):
    vfg, now = FakeVfg(), [100.0]
    world = World(vfg, tmp_path / "running.json", catalog(), clock=lambda: now[0])
    await world.fresh_state()
    assert await world.items("steve") == (True, "Steve's items, by slot: hotbar.4 iron_sword (held)")
    assert await world.items("Bob") == (False, "Bob is not online")
    assert len(vfg.of("state")) == 3  # a read never takes a state another call had fetched
    vfg.on("state", answer=VfgResult(4, "", "vfg: cannot reach EC2"))
    now[0] += 60                        # the fetch fails: the last good state, a minute old, never passes as fresh
    assert await world.items("Steve") == (False, "the server's state could not be read just now: nothing read, "
                                                 "try again")
    gate = asyncio.Event()

    async def late(argv):                    # an older fetch that ends after this read asked, while the read's own
        await gate.wait()                    # fails: the read never takes that one's state for its own
        return VfgResult(0, json.dumps(STATE), "")

    vfg.rules.clear()
    calls = iter([late, VfgResult(4, "", "vfg: cannot reach EC2")])

    async def next_answer(argv):
        got = next(calls)
        return await got(argv) if callable(got) else got

    vfg.on("state", answer=next_answer)
    older = asyncio.create_task(world.fresh_state(wait=0))   # an older fetch, in flight
    await asyncio.sleep(0)
    reading = asyncio.create_task(world.items("Steve"))
    await asyncio.sleep(0)
    gate.set()                               # the older one lands after the read asked
    await older
    assert await reading == (False, "the server's state could not be read just now: nothing read, try again")
    vfg.rules.clear()
    vfg.on("state", answer=VfgResult(5, json.dumps({**STATE, "rcon": "connection refused"}), ""))
    assert await world.items("Steve") == (False, "Minecraft did not answer: connection refused")
    await world.close()


async def test_the_brain_sees_running_now_in_full_respond_one_line_a_copy(tmp_path):
    vfg = FakeVfg()
    (tmp_path / "running.json").write_text(json.dumps({"copies": {
        "railgun_alex": {"action": "railgun", "values": {"player": "Alex", "cooldown": 0}, "task": None}}}))
    world = World(vfg, tmp_path / "running.json", catalog())
    full = (await world.seen(full=True)).running
    assert full.startswith("RUNNING NOW (each copy: its action, who started it; then every value")
    assert "- railgun_alex: railgun, the brain started it\n  live: " in full and "cooldown=0 (0..60)" in full
    assert (await world.seen()).running.endswith("- railgun_alex: railgun, player=Alex cooldown=0")
    await world.close()


COPIES = {
    "railgun_steve": {"action": "railgun", "script": "railgun", "values": {"player": "Steve", "cooldown": 5,
                                                                          "range": 100, "through_walls": True},
                      "live": ["cooldown", "range"], "task": None},
    "frost_bow_alex": {"action": "frost_bow_alex", "script": "frost_bow_alex",
                       "values": {"power": 3, "name": "Frost Bow"}, "live": ["power"], "task": "frost_bow_alex"},
    "summon_around": {"action": "summon_around", "values": {"mob": "zombie", "count": 20}, "task": "cast"},
}


def test_running_in_full_shows_each_value_now_its_limits_or_choices_live_or_restart_and_who_started_it():
    text = running.full(COPIES, catalog())
    lines = text.splitlines()
    assert lines[0].startswith("RUNNING NOW (each copy: its action, who started it; then every value")
    i = lines.index("- railgun_steve: railgun, the brain started it")
    live, restart = lines[i + 1], lines[i + 2]
    assert live.startswith("  live: ") and "cooldown=5 (0..60)" in live and "range=100 (4..300)" in live
    assert "damage_what=all (all|mobs|hostile|players)" in live and "through_walls=true" in live
    assert ("damage_type=magic (player_attack|magic|lightning_bolt|explosion|freeze|on_fire|sonic_boom|arrow|…+"
            in live)  # choices capped at 8
    assert restart == "  restart: player=Steve item=Railgun"  # not live: a restart; the default when not set
    assert "fire=" not in text  # a fixed value is left out
    # An action not loaded: running.json's values and live list.
    assert "- frost_bow_alex: frost_bow_alex, started by task frost_bow_alex\n  live: power=3\n" \
           "  restart: name=\"Frost Bow\"" in text
    assert "- summon_around: summon_around, an incantation started it" in text
    assert "count=20 (1..100)" in text and "mob=zombie" in text
    assert running.full({}, catalog()) == "RUNNING NOW: none"
    long = {"c": {"action": "command", "values": {"commands": ["x" * 80]}, "task": None}}
    shown = running.full(long, catalog()).split("commands=", 1)[1].split()[0]
    assert len(shown) == 40 and shown.endswith("…")
