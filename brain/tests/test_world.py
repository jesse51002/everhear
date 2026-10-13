"""The world a call sees: each player, the time, and the engine's part: what runs, the places, the
incantations and every class."""

import asyncio
import json

from brain.vfg import VfgResult
from brain.world import RUNNING_MAX, World, engine_lines, incantation_lines, places_lines, players, running_lines

from conftest import CLASSES, STATE, FakeVfg, overview

STEVE = ("- Steve at 10 64 -20 in overworld: 14.5/20 health, 18/20 food, armor 12, level 5, survival; "
         "effects: speed II 0:30, night vision ∞")


async def test_the_world_shares_a_young_state_and_falls_back_to_the_last_good_one():
    vfg, now = FakeVfg(), [100.0]
    world = World(vfg, clock=lambda: now[0])
    seen = await world.seen()
    assert seen.players == f"PLAYERS ONLINE:\n{STEVE}; Steve's items, by slot: hotbar.4 iron_sword (held)"
    assert seen.clock == "TIME: day (6000, day 3). WEATHER: clear."
    assert seen.running == "RUNNING NOW: nothing (no scenario runs, and nobody has an ability, an item or a mark)"
    assert seen.places == "SAVED PLACES: home 1 64 2 overworld (home_steve)" and seen.known == {"home"}
    assert seen.incantations == "INCANTATIONS: none"
    assert [c["id"] for c in seen.classes] == [c["id"] for c in CLASSES]
    now[0] += 1.5
    await world.seen()
    assert len(vfg.of("state")) == 1 and len(vfg.of("engine", "overview")) == 1  # 1.5 s old is fresh enough
    world.engine_changed()                  # an engine action ran: the next call reads the engine again
    await world.seen()
    assert len(vfg.of("state")) == 1 and len(vfg.of("engine", "overview")) == 2

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


async def test_the_read_tool_fetches_a_state_of_its_own_and_says_who_is_not_online():
    vfg, now = FakeVfg(), [100.0]
    world = World(vfg, clock=lambda: now[0])
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


INSTANCES = [{"instance": "scenario:horde#3", "kind": "scenario", "id": "horde", "user": None, "ends": None},
             {"instance": "ability:fireball#1", "kind": "ability", "id": "fireball", "user": "Steve"},
             {"instance": "mark:giant#2", "kind": "mark", "id": "giant", "user": "Alex", "ends": 1791880000}]
BINDINGS = [{"player": "Steve", "phrase": "fire ball", "ability": "fireball", "scenario": None, "args": {}},
            {"player": "Alex", "phrase": "meteor", "ability": None, "scenario": "horde", "args": {"count": 3}}]


async def test_the_engine_part_running_now_incantations_and_every_class_with_its_args():
    vfg = FakeVfg().on("engine", "overview", answer=overview(instances=INSTANCES, incantations=BINDINGS))
    world = World(vfg)
    seen = await world.seen()
    assert seen.running.splitlines()[1:] == ["- scenario:horde#3: scenario horde, for the world",
                                             "- ability:fireball#1: ability fireball, granted to Steve",
                                             "- mark:giant#2: mark giant, on Alex, ends 08:26:40 UTC"]
    assert seen.incantations.splitlines()[1:] == ['- "fire ball" for Steve: casts fireball',
                                                  '- "meteor" for Alex: starts horde {"count":3}']
    assert seen.engine.splitlines()[1:] == [
        "SCENARIOS:",
        "- command: Runs Minecraft commands, at once.",
        "    args.commands: list[Command], default []: the commands, each one line",
        "- horde: A horde of mobs round each player until it is stopped.",
        "    args.count: int 1..100, default 10: how many",
        "- teleport: Takes players to a place.",
        "    args.players: Players, default @a: who goes",
        "    args.at: Place: where to",
        "ABILITIES:",
        "- fireball: Burns for burn_s seconds, then explodes in a radius.",
        "ITEMS:",
        "- fire_staff: A staff that shoots fire where you look.",
        "MARKS:",
        "- giant: Twice as big for a while.",
        "    args.factor: float 1..16, default 2.0: how much bigger",
    ]
    assert seen.instances == INSTANCES
    text = seen.show()
    assert text.index("RUNNING NOW") < text.index("SAVED PLACES") < text.index("INCANTATIONS") < text.index("ENGINE")
    await world.close()


async def test_an_engine_that_is_down_or_refuses_a_part_says_so_and_keeps_the_rest():
    vfg = FakeVfg().on("engine", "overview", answer=VfgResult(6, "", "vfg: the game engine is not running"))
    world = World(vfg)
    seen = await world.seen()
    assert seen.running.startswith("RUNNING NOW: unknown, the engine is not running")
    assert seen.places == "SAVED PLACES: unknown (the engine is not running)" and seen.known is None
    assert seen.engine.startswith("ENGINE: not running (vfg-engine is down)")
    assert seen.classes == [] and seen.instances == []
    refused = {"ok": False, "error": "places: no places op", "classes": CLASSES, "instances": [], "incantations": []}
    vfg.rules.clear()
    vfg.on("engine", "overview", answer=VfgResult(1, json.dumps(refused), ""))
    world.engine_changed()
    seen = await world.seen()
    assert seen.places == "SAVED PLACES: unknown (it could not be read: places: no places op)" and seen.known is None
    assert seen.incantations == "INCANTATIONS: none" and seen.engine.startswith("ENGINE (every class")
    await world.close()


def test_the_lines_of_each_part_when_there_is_nothing_and_when_there_is_much():
    assert engine_lines([]) == "ENGINE: no classes yet (nothing to start, grant, give or apply)"
    assert places_lines([]) == ("SAVED PLACES: none", set())
    assert incantation_lines([]) == "INCANTATIONS: none"
    many = [{"instance": "scenario:horde#%d" % n, "kind": "scenario", "id": "horde"} for n in range(RUNNING_MAX + 5)]
    assert running_lines(many).splitlines()[-1] == "- and 5 more"
    nether = places_lines([{"name": "fort", "dim": "minecraft:the_nether", "pos": [1.6, 70, -3.2]}])
    assert nether == ("SAVED PLACES: fort 2 70 -3 the_nether", {"fort"})
