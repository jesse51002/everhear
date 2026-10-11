"""The world a call sees: each player, the time, the running copies, the places."""

import asyncio
import json

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
