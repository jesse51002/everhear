"""McIo on a real 26.3 server (realmc), with Carpet's fake players Steve and Alex: every op and every
event, as the build's proof (the game engine spec, its Done list). Skipped unless REALMC_NAME is set
and VFG_MC_PROPERTIES is a realmc server's:

    export REALMC_ROOT=…/integration/realmc/.server VFG_ACCEPT_EULA=true
    integration/realmc/up ge-mc --fresh && eval "$(integration/realmc/env ge-mc)"
    cd engine && uv run --group dev python -m pytest tests/mc -q

The fixture copies engine/datapack/everhear into the server's world and reloads it, then puts back
what the tests assume, whatever the server held before (an engine run leaves its everhear:*
modifiers on players by design; a test that failed may leave anything): every everhear:* modifier
off Steve and Alex, through McIo, before every test (never another modifier); the queue drained;
the arena's floor and air as the template made them, noon and clear weather held, and nothing but
the players within 48 blocks of it. Effects are cleared only for the tests that read them
(`calm`). Carpet's quirks (engine/MINECRAFT.md): `/player X look` swaps yaw and pitch (players.look
uses vanilla /rotate), `use continuous` uses 20 times a second, and a killed fake player leaves
instead of dying.
"""

import os
import shutil
import sys
import time

import pytest

from vfg_engine import io as I
from vfg_engine.mc import McIo, datapack

NAME = os.environ.get("REALMC_NAME", "")
PROPS = os.environ.get("VFG_MC_PROPERTIES", "")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

pytestmark = pytest.mark.skipif(not NAME or "realmc" not in PROPS,
                                reason="no realmc server: set REALMC_NAME (eval \"$(integration/realmc/env NAME)\")")

ARENA = (0.5, 85.0, 0.5)      # where a player stands at the arena's centre (floor y 84)
ALEX = (-4.5, 85.0, 0.5)
TEST_TAG = "ge_test"
PLAYERS = (("Steve", ARENA), ("Alex", ALEX))


def husk_uuid(k: int) -> str:
    return "0000ee00-0000-4000-8000-%012x" % k


@pytest.fixture(scope="module")
def mc():
    world = os.path.join(os.path.dirname(PROPS), "world")
    os.environ.setdefault("REALMC_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(PROPS))))
    sys.path.insert(0, os.path.join(REPO, "integration", "realmc"))
    import players as players_mod

    dest = os.path.join(world, "datapacks", "everhear")
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(datapack.SOURCE, dest)
    io = McIo(PROPS)
    io.rcon.command("reload")
    if datapack.NAME not in io.rcon.command("datapack list enabled"):
        io.rcon.command('datapack enable "%s"' % datapack.NAME)
    p = players_mod.on(NAME)
    for who, at in PLAYERS:
        p.spawn(who, at=at)
    io.open()
    reset_world(io, p)
    yield io, p
    io.rcon.command("kill @e[tag=%s]" % TEST_TAG)
    io.close()
    p.close()


def clear_everhear_modifiers(io, who):
    """Every everhear:* modifier off a player, through McIo: read_modifiers returns only ours, so
    vanilla's (Speed's, armour's) and anyone else's are never touched."""
    mods = io.read_modifiers(who)
    if mods:
        res = io.flush([I.RemoveModifier(who, attr, mid) for attr, mid in sorted(mods)])
        assert all(r.ok for r in res), res
    assert io.read_modifiers(who) == {}


def drain_queue(io):
    """The queue empty and able to take events again (a test that failed may have left #max low)."""
    io.rcon.command("scoreboard players set #max everhear.q 2048")
    for _ in range(20):
        if not io.events():
            break
    assert io.queue_len == 0


def reset_world(io, p):
    """What every test assumes of the world, put back as the realmc template made it: the arena's
    floor and the air above it, noon and clear weather held, and nothing but the players within 48
    blocks (an engine's actors, a mob spawned at night, drops)."""
    a = p.arena()
    x0, y0, z0, x1, y1, z1 = a["floor"]
    top = y0 + int(a.get("air", 32))
    for cmd in ("fill %d %d %d %d %d %d minecraft:grass_block" % (x0, y0, z0, x1, y0, z1),
                "fill %d %d %d %d %d %d minecraft:air" % (x0, y0 + 1, z0, x1, (y0 + top) // 2, z1),
                "fill %d %d %d %d %d %d minecraft:air" % (x0, (y0 + top) // 2 + 1, z0, x1, top, z1)):
        out = io.rcon.command(cmd)
        assert out.startswith("Successfully filled") or out.startswith("No blocks were filled"), out
    for cmd in ("gamerule advance_time false", "gamerule advance_weather false", "time set noon",
                "weather clear"):
        io.rcon.command(cmd)
    io.rcon.command("execute positioned %g %g %g run kill @e[type=!minecraft:player,distance=..48]" % ARENA)
    drain_queue(io)


@pytest.fixture
def fresh(mc):
    """Both players back on the arena, empty-handed, fed and healed, with none of our modifiers; the
    queue read empty."""
    io, p = mc
    for who, at in PLAYERS:
        if who not in p.online():
            p.spawn(who, at=at)
        p.stop(who)
        p.sneak(who, False)
        p.hotbar(who, 1)                                   # a give lands in the first slot
        clear_everhear_modifiers(io, who)
        io.rcon.command("clear %s" % who)
        io.rcon.command("tp %s %g %g %g 0 0" % ((who,) + at))
        io.rcon.command("gamemode survival %s" % who)
        io.rcon.command("effect give %s minecraft:saturation 1 10 true" % who)      # instant: fed
        io.rcon.command("effect give %s minecraft:instant_health 1 10 true" % who)  # instant: healed
    io.rcon.command("kill @e[tag=%s]" % TEST_TAG)
    io.rcon.command("kill @e[type=item]")
    time.sleep(0.15)
    io.events()
    return io, p


@pytest.fixture
def calm(fresh):
    """`fresh`, and both players' effect stacks empty: for the tests that read effects or count hurts
    (a leftover poison hurts, a resistance or absorption swallows damage). The test's own reset, which
    the engine never does."""
    io, p = fresh
    for who, _ in PLAYERS:
        io.rcon.command("effect clear %s" % who)
    time.sleep(0.1)
    io.events()
    return io, p


def collect(io, until, timeout=3.0):
    """Events from now until `until(events)` is true (or the timeout)."""
    got, end = [], time.monotonic() + timeout
    while time.monotonic() < end:
        got += io.events()
        if until(got):
            return got
        time.sleep(0.05)
    return got


def kinds(evs, kind, player=None):
    return [e for e in evs if e.kind == kind and (player is None or e.player == player)]


def summon_husk(io, k, pos, ai=False, extra=""):
    nbt = "{PersistenceRequired:1b%s%s}" % ("" if ai else ",NoAI:1b", extra)
    r = io.flush([I.Summon("minecraft:husk", "minecraft:overworld", pos, husk_uuid(k), nbt=nbt, tags=(TEST_TAG,))])[0]
    assert r.ok, r
    return husk_uuid(k)


# ---- open ----------------------------------------------------------------------------------------

def test_open_checked_the_datapack(mc):
    io, _ = mc
    assert io.tables.type(157) == "minecraft:zombie" and io.tables.block(448) == "minecraft:grass_block"
    assert "Steve" in io.names.online.values()


def test_the_reset_takes_off_only_everhear_modifiers(mc):
    """The setup's reset (clear_everhear_modifiers): ours go, anyone else's stay."""
    io, _ = mc
    io.rcon.command("attribute Steve minecraft:luck modifier add vfgtest:own 1 add_value")
    try:
        r = io.flush([I.AddModifier("Steve", "minecraft:scale", "everhear:mark/left", 1.0, "add_multiplied_total"),
                      I.AddModifier("Steve", "minecraft:movement_speed", "everhear:item/left", 0.2, "add_multiplied_total")])
        assert all(x.ok for x in r), r
        clear_everhear_modifiers(io, "Steve")
        assert io.read_modifiers("Steve") == {}
        kept = io.rcon.command("attribute Steve minecraft:luck modifier value get vfgtest:own")
        assert "has no modifier" not in kept and kept.endswith("is 1.0"), kept
    finally:
        io.rcon.command("attribute Steve minecraft:luck modifier remove vfgtest:own")


# ---- events --------------------------------------------------------------------------------------

def test_use_with_carrot_on_a_stick(fresh):
    io, p = fresh
    assert io.flush([I.GiveItem("Steve", "minecraft:carrot_on_a_stick", "fire_staff", "Fire Staff")])[0].ok
    time.sleep(0.1)
    p.use("Steve")
    evs = collect(io, lambda e: kinds(e, "use"))
    use = kinds(evs, "use")
    assert len(use) == 1
    assert use[0].player == "Steve" and dict(use[0].data, uuid=None) == {
        "item": "fire_staff", "slot": "mainhand", "count": 1, "uuid": None}


def test_use_with_warped_fungus_in_the_off_hand(fresh):
    io, p = fresh
    io.rcon.command('item replace entity Steve weapon.offhand with warped_fungus_on_a_stick'
                    '[custom_data={everhear:{item:"ice_wand"}}]')
    time.sleep(0.1)
    p.use("Steve")
    use = kinds(collect(io, lambda e: kinds(e, "use")), "use")
    assert [(u.data["item"], u.data["slot"]) for u in use] == [("ice_wand", "offhand")]


def test_a_use_without_an_everhear_id_is_not_an_event(fresh):
    io, p = fresh
    io.rcon.command("give Steve minecraft:carrot_on_a_stick")
    time.sleep(0.1)
    p.use("Steve")
    time.sleep(0.3)
    assert kinds(io.events(), "use") == []


def test_hit_then_kill_a_zombie(fresh):
    io, p = fresh
    io.flush([I.GiveItem("Steve", "minecraft:carrot_on_a_stick", "fire_staff")])
    u = husk_uuid(1)
    # a helmet keeps it from burning at the arena's noon
    r = io.flush([I.Summon("minecraft:zombie", "minecraft:overworld", (0.5, 85, 2.5), u,
                           nbt='{NoAI:1b,PersistenceRequired:1b,equipment:{head:{id:"minecraft:leather_helmet",count:1}}}',
                           tags=(TEST_TAG,))])[0]
    assert r.ok, r
    p.look_at("Steve", 0.5, 86.5, 2.5)
    time.sleep(0.1)
    p.attack("Steve")
    hit = kinds(collect(io, lambda e: kinds(e, "hit")), "hit")
    assert len(hit) == 1
    assert hit[0].data["target"] == u and hit[0].data["target_type"] == "minecraft:zombie"
    assert hit[0].data["item"] == "fire_staff"
    # the killing blow: Minecraft runs the death first, so kill comes before its hit, which has no target
    assert io.flush([I.Damage(u, 100, "minecraft:player_attack", by="Steve")])[0].ok
    evs = collect(io, lambda e: kinds(e, "kill") and kinds(e, "hit"))
    kill = kinds(evs, "kill")
    assert [(k.player, k.data["target_type"], k.data.get("target"), k.data.get("item")) for k in kill] == [
        ("Steve", "minecraft:zombie", None, "fire_staff")]
    assert kinds(evs, "hit")[0].n > kill[0].n and "target" not in kinds(evs, "hit")[0].data


def test_a_hit_on_a_player_names_both(calm):
    """PvP: Steve's hit names Alex, and Alex's hurt names Steve (players by name, not uuid)."""
    io, p = calm
    # west of Steve: south of him a zombie killed by the test before may still be dying (a second)
    io.rcon.command("tp Alex -1.5 85 0.5 -90 0")
    p.look_at("Steve", -1.5, 86.5, 0.5)
    time.sleep(0.1)
    # a player who just joined cannot be hurt for a few seconds: swing until the hit lands
    evs, end = [], time.monotonic() + 5
    while time.monotonic() < end and not (kinds(evs, "hit") and kinds(evs, "hurt", "Alex")):
        p.attack("Steve")
        evs += collect(io, lambda e: kinds(e, "hit") and kinds(e, "hurt", "Alex"), timeout=0.6)
    hit, hurt = kinds(evs, "hit", "Steve"), kinds(evs, "hurt", "Alex")
    assert [(h.data["target"], h.data["target_type"]) for h in hit] == [("Alex", "minecraft:player")]
    assert [(h.data["attacker"], h.data["attacker_type"]) for h in hurt] == [("Steve", "minecraft:player")]


def test_hurt_with_and_without_an_attacker(calm):
    io, p = calm
    u = summon_husk(io, 2, (3.5, 85, 3.5))
    io.flush([I.Damage("Steve", 2, "minecraft:mob_attack", by=u)])
    evs = collect(io, lambda e: kinds(e, "hurt"))
    hurt = kinds(evs, "hurt", "Steve")
    assert len(hurt) == 1
    assert hurt[0].data["attacker"] == u and hurt[0].data["attacker_type"] == "minecraft:husk"
    assert hurt[0].data["amount"] > 0                     # 3.0 on hard: difficulty scales a mob's damage
    time.sleep(0.6)                                       # past the 10 ticks a player cannot be hurt
    io.flush([I.Damage("Steve", 1.5)])
    hurt = kinds(collect(io, lambda e: kinds(e, "hurt")), "hurt", "Steve")
    assert len(hurt) == 1 and hurt[0].data["amount"] == 1.5 and "attacker" not in hurt[0].data


def test_milk(fresh):
    io, p = fresh
    io.flush([I.GiveEffect("Steve", "minecraft:poison", 30)])
    io.rcon.command("item replace entity Steve weapon.mainhand with milk_bucket")
    time.sleep(0.1)
    p.use("Steve", "continuous")
    evs = collect(io, lambda e: kinds(e, "milk"), timeout=4.0)
    p.stop("Steve")
    assert [m.player for m in kinds(evs, "milk")] == ["Steve"]
    assert io.snapshot().players["Steve"].effects == ()


def test_death_of_a_fake_player_is_reported_when_it_comes_back(calm):
    """A Carpet player that is killed leaves the server instead of dying (it never respawns), so
    the datapack sees its deathCount when it comes back: death, then respawn, in one tick. A real
    client stays dead on the death screen, where @a still selects it (26.3's bytecode); not measured."""
    io, p = calm
    io.rcon.command("damage Alex 100 minecraft:generic")
    end = time.monotonic() + 5
    while "Alex" in p.online() and time.monotonic() < end:
        time.sleep(0.1)
    assert "Alex" not in p.online()
    assert kinds(io.events(), "death") == []              # it left before a tick saw it dead
    p.spawn("Alex", at=(-4.5, 85.0, 0.5))
    evs = collect(io, lambda e: kinds(e, "respawn"))
    d, r = kinds(evs, "death", "Alex"), kinds(evs, "respawn", "Alex")
    assert len(d) == 1 and len(r) == 1 and d[0].n < r[0].n and d[0].game_tick == r[0].game_tick
    assert d[0].data["dim"] == "minecraft:overworld" and len(d[0].data["pos"]) == 3     # where it died


def test_the_queue_survives_a_dropped_connection(fresh):
    io, p = fresh
    io.flush([I.GiveItem("Steve", "minecraft:carrot_on_a_stick", "fire_staff")])
    time.sleep(0.1)
    p.use("Steve", 1)                                     # a use every tick
    time.sleep(1.0)
    p.stop("Steve")
    io.rcon._break("dropped by the test")
    evs = collect(io, lambda e: len(e) >= 15, timeout=2)
    io.rcon._break("dropped again")
    evs += collect(io, lambda e: False, timeout=0.5)
    ns = [e.n for e in kinds(evs, "use")]
    assert len(ns) >= 15 and ns == sorted(ns) and len(set(ns)) == len(ns)
    assert ns == list(range(ns[0], ns[0] + len(ns)))      # none lost, none twice


def test_a_full_queue_counts_what_it_turned_away(fresh):
    io, p = fresh
    before = io.overflowed
    io.rcon.command("scoreboard players set #max everhear.q 3")
    try:
        io.flush([I.GiveItem("Steve", "minecraft:carrot_on_a_stick", "fire_staff")])
        time.sleep(0.1)
        p.use("Steve", 1)
        time.sleep(0.5)
        p.stop("Steve")
        evs = io.events()
    finally:
        io.rcon.command("scoreboard players set #max everhear.q 2048")
    assert len(kinds(evs, "use")) == 3
    assert io.overflowed - before >= 5


# ---- the snapshot --------------------------------------------------------------------------------

def test_sneaking(fresh):
    io, p = fresh
    assert io.snapshot().players["Steve"].sneaking is False
    p.sneak("Steve")
    time.sleep(0.15)
    assert io.snapshot().players["Steve"].sneaking is True
    p.sneak("Steve", False)
    time.sleep(0.15)
    assert io.snapshot().players["Steve"].sneaking is False


def test_held_and_carried(fresh):
    io, p = fresh
    io.flush([I.GiveItem("Steve", "minecraft:carrot_on_a_stick", "fire_staff"),
              I.GiveItem("Steve", "minecraft:stick", "rod")])
    io.rcon.command('item replace entity Steve weapon.offhand with warped_fungus_on_a_stick[custom_data={everhear:{item:"ice_wand"}}]')
    io.rcon.command('item replace entity Steve armor.head with leather_helmet[custom_data={everhear:{item:"crown"}}]')
    s = io.snapshot().players["Steve"]
    assert s.held == {"mainhand": "fire_staff", "offhand": "ice_wand", "head": "crown"}
    assert sorted(s.carried) == ["crown", "fire_staff", "ice_wand", "rod"]
    p.hotbar("Steve", 2)                                  # the rod's slot
    time.sleep(0.1)
    assert io.snapshot().players["Steve"].held["mainhand"] == "rod"


def test_a_players_fields(calm):
    io, p = calm
    io.flush([I.GiveEffect("Steve", "minecraft:speed", 30, 1)])
    s = io.snapshot().players["Steve"]
    assert s.pos == ARENA and s.dim == "minecraft:overworld" and s.gamemode == "survival"
    assert s.eye == pytest.approx((0.5, 86.62, 0.5), abs=0.01)
    assert (s.yaw, s.pitch, s.max_health, s.alive) == (0.0, 0.0, 20.0, True)
    assert s.facing == pytest.approx((0, 0, 1))
    assert [(e.id, e.amplifier) for e in s.effects] == [("minecraft:speed", 1)]
    assert 500 < s.effects[0].ticks <= 600


def test_the_raycast_at_a_known_block(fresh):
    io, p = fresh
    p.look("Steve", 0, 30)                                # south, 30 down: the floor 3.25 blocks on
    time.sleep(0.1)
    look = io.snapshot().players["Steve"].looking_at
    assert (look.kind, look.block, look.block_id) == ("block", (0, 84, 3), "minecraft:grass_block")
    assert look.distance == pytest.approx(3.24, abs=1 / 32)


def test_the_raycast_at_a_known_entity(fresh):
    io, p = fresh
    u = summon_husk(io, 3, (0.5, 85, 4.5))
    p.look("Steve", 0, 0)
    time.sleep(0.1)
    look = io.snapshot().players["Steve"].looking_at
    assert (look.kind, look.entity, look.entity_type) == ("entity", u, "minecraft:husk")
    # its hitbox's face is 3.7 blocks on; the ray's point is the first half-block step within 0.25 of it
    assert 3.4 <= look.distance <= 3.75


def test_the_raycast_at_nothing(fresh):
    io, p = fresh
    p.look("Steve", 0, -80)                               # nearly straight up: open sky
    time.sleep(0.1)
    look = io.snapshot().players["Steve"].looking_at
    assert look.kind == "none" and look.distance == pytest.approx(io.ray_range, abs=0.5)


def test_tracked_entities_and_gone(fresh):
    io, p = fresh
    u = summon_husk(io, 4, (5.5, 85, 5.5), extra=',CustomName:"Ice Queen"')
    missing = husk_uuid(999)
    snap = io.snapshot(track=[u, missing])
    assert set(snap.tracked) == {u}
    e = snap.tracked[u]
    assert (e.type, e.dim, e.pos, e.health, e.name, e.tags) == (
        "minecraft:husk", "minecraft:overworld", (5.5, 85.0, 5.5), 20.0, "Ice Queen", frozenset({TEST_TAG}))
    assert io.flush([I.Remove(u)])[0].ok
    assert io.snapshot(track=[u]).tracked == {}


def test_the_mirror(fresh):
    io, p = fresh
    us = [summon_husk(io, 10 + k, (2.5 + k, 85, -3.5)) for k in range(3)]
    time.sleep(0.35)
    m = io.mirror()
    assert m.age_s < 0.3
    near = m.entities_near("minecraft:overworld", (3.5, 85, -3.5), 3)
    assert set(us) <= {e.uuid for e in near}
    assert m.get(us[0]).type == "minecraft:husk"
    # no players: they are the snapshot's
    assert all(e.type != "minecraft:player" for e in m.all())
    assert m.entities_near("minecraft:overworld", ARENA, 1) == []
    assert m.get(io.names.uuid_of("Steve")) is None
    assert "minecraft:player" not in io.rcon.command("data get storage everhear:mirror e")  # not even read


# ---- writes --------------------------------------------------------------------------------------

def test_modifiers(fresh):
    io, p = fresh
    add = I.AddModifier("Steve", "minecraft:scale", "everhear:mark/giant", 1.0, "add_multiplied_total")
    r = io.flush([add, add, I.AddModifier("Steve", "minecraft:scale", "everhear:mark/shrunk", -0.25, "add_multiplied_total")])
    assert [x.ok for x in r] == [True, False, True] and r[1].error == "already present"
    assert io.read_modifiers("Steve") == {("minecraft:scale", "everhear:mark/giant"): 1.0,
                                          ("minecraft:scale", "everhear:mark/shrunk"): -0.25}
    # beside vanilla's own (Speed's), which is never read as ours
    io.flush([I.GiveEffect("Steve", "minecraft:speed", 10)])
    assert all(k[1].startswith("everhear:") for k in io.read_modifiers("Steve"))
    r = io.flush([I.RemoveModifier("Steve", "minecraft:scale", "everhear:mark/shrunk"),
                  I.RemoveModifier("Steve", "minecraft:scale", "everhear:mark/shrunk")])
    assert [x.ok for x in r] == [True, False] and r[1].error == "no modifier with that id (not present)"
    assert io.read_modifiers("Steve") == {("minecraft:scale", "everhear:mark/giant"): 1.0}
    io.flush([I.RemoveModifier("Steve", "minecraft:scale", "everhear:mark/giant")])
    assert io.read_modifiers("Steve") == {}
    with pytest.raises(LookupError):
        io.read_modifiers("Nobody")


def test_effects_stack_and_refuse(calm):
    io, p = calm
    r = io.flush([I.GiveEffect("Steve", "minecraft:poison", 60, 0), I.GiveEffect("Steve", "minecraft:poison", 5, 2),
                  I.GiveEffect("Steve", "minecraft:poison", 3, 1)])
    assert [x.ok for x in r] == [True, True, False] and r[2].error == "has something stronger"
    poison = io.snapshot().players["Steve"].effects[0]
    assert (poison.id, poison.amplifier) == ("minecraft:poison", 2)
    assert poison.hidden is not None and poison.hidden.amplifier == 0 and poison.hidden.ticks > 1000


def test_give_with_components_and_take(fresh):
    io, p = fresh
    r = io.flush([I.GiveItem("Steve", "minecraft:carrot_on_a_stick", "fire_staff", "Fire Staff",
                             {"enchantment_glint_override": "true", "max_damage": "50"})])[0]
    assert r.ok and r.reply == "Gave 1 [Fire Staff] to Steve"
    got = io.rcon.command("data get entity Steve SelectedItem.components")
    assert 'everhear: {item: "fire_staff"}' in got and "enchantment_glint_override" in got
    assert "fire_staff" in io.snapshot().players["Steve"].carried
    r = io.flush([I.TakeItem("Steve", "fire_staff"), I.TakeItem("Steve", "fire_staff"), I.GiveItem("Nobody", "stick", "x")])
    assert [(x.ok, x.error) for x in r] == [(True, None), (True, None), (False, "no player found")]
    assert io.snapshot().players["Steve"].carried == ()


def test_summon_with_a_chosen_uuid_and_remove_without_loot(fresh):
    io, p = fresh
    u = husk_uuid(20)
    nbt = '{NoAI:1b,PersistenceRequired:1b,CustomName:"Loot Husk",equipment:{mainhand:{id:"minecraft:iron_sword",count:1}},drop_chances:{mainhand:2.0f}}'
    op = I.Summon("minecraft:husk", "minecraft:overworld", (6.5, 85, -6.5), u, nbt=nbt, tags=(TEST_TAG,))
    r = io.flush([op, op])
    assert r[0].ok and r[1].error == "an entity with that uuid already exists"
    assert io.snapshot(track=[u]).tracked[u].name == "Loot Husk"
    items = lambda: io.rcon.command("execute positioned 6.5 85 -6.5 if entity @e[type=item,distance=..4]")
    assert items() == "Test failed"
    r = io.flush([I.Remove(u), I.Remove(u)])
    assert r[0].ok and not r[1].ok
    time.sleep(0.3)
    assert items() == "Test failed"                       # its sword went with it
    assert io.flush([I.Remove(io.names.uuid_of("Steve"))])[0].ok is False    # a player never
    assert "Steve" in p.online()


def test_summon_an_actors_body(fresh):
    """The core's Summon for an actor: SNBT with health over the base, attributes, equipment, a name."""
    io, p = fresh
    u = husk_uuid(30)
    nbt = ('{Health:60f,PersistenceRequired:1b,NoAI:1b,attributes:[{id:"minecraft:max_health",base:60},'
           '{id:"minecraft:scale",base:1.5}],CustomName:"Brute",equipment:{mainhand:{id:"minecraft:iron_axe",count:1}}}')
    r = io.flush([I.Summon("minecraft:vindicator", "minecraft:overworld", (-6.5, 85, -6.5), u, nbt=nbt,
                           tags=(TEST_TAG, "everhear.actor"))])[0]
    assert r.ok and r.reply == "Summoned new Brute", r
    e = io.snapshot(track=[u]).tracked[u]
    assert (e.type, e.health, e.name, e.tags) == ("minecraft:vindicator", 60.0, "Brute",
                                                  frozenset({TEST_TAG, "everhear.actor"}))
    assert io.query("attribute %s minecraft:max_health get" % u).endswith("is 60.0")
    assert io.query("attribute %s minecraft:scale get" % u).endswith("is 1.5")
    assert "iron_axe" in io.query("data get entity %s equipment.mainhand.id" % u)


def test_damage_by(fresh):
    io, p = fresh
    u = summon_husk(io, 21, (0.5, 85, 6.5))
    r = io.flush([I.Damage(u, 3, by="Steve"), I.Damage(u, 3, by="Steve")])
    assert r[0].ok and r[0].reply == "Applied 3.0 damage to Husk"
    assert r[1].error == "invulnerable to it (or still unhurtable after a hit)"
    assert io.snapshot(track=[u]).tracked[u].health == 17.0
    # Minecraft counts a damage by a player as that player's own hit
    assert [h.data.get("target") for h in kinds(collect(io, lambda e: kinds(e, "hit")), "hit")] == [u]


def test_gamerule_read_and_write(fresh):
    io, p = fresh
    was = io.read_gamerule("mob_griefing")
    assert was in ("true", "false") and io.read_gamerule("no_such_rule") is None
    other = "false" if was == "true" else "true"
    try:
        assert io.flush([I.Gamerule("mob_griefing", other)])[0].ok
        assert io.read_gamerule("mob_griefing") == other
    finally:
        io.flush([I.Gamerule("mob_griefing", was)])
    assert io.read_gamerule("mob_griefing") == was


def test_particle_sound_message_teleport(fresh):
    io, p = fresh
    r = io.flush([
        I.Particle("minecraft:flame", "minecraft:overworld", (0.5, 86, 0.5), (0.2, 0.2, 0.2), 0.01, 10),
        I.Sound("minecraft:entity.blaze.shoot", "minecraft:overworld", (0.5, 86, 0.5)),
        I.Message((), "engine test: hello"),
        I.Message(("Steve",), "Cooldown 3 s", "actionbar"),
        I.Message(("Steve", "Alex"), "Fire!", "title"),
        I.Teleport("Steve", "minecraft:overworld", (3.5, 85, 3.5), yaw=90, pitch=10),
        I.Teleport("Nobody", "minecraft:overworld", (0, 85, 0)),
    ])
    assert [x.ok for x in r] == [True, True, True, True, True, True, False], r
    assert r[0].reply == "Displaying particle minecraft:flame" and r[1].reply.startswith("Played sound")
    assert r[6].error == "no entity found"
    s = io.snapshot().players["Steve"]
    assert s.pos == (3.5, 85.0, 3.5) and (s.yaw, s.pitch) == (90.0, 10.0)


def test_query(fresh):
    io, p = fresh
    assert io.query("time query gametime").startswith("The game time is")
    from vfg_engine.mc import OpRefused
    with pytest.raises(OpRefused):
        io.query("stop")


def test_flush_holds_the_lock_for_the_batch(fresh):
    io, p = fresh
    ops = [I.Particle("minecraft:flame", "minecraft:overworld", (0.5, 86, 0.5)) for _ in range(50)]
    before = io.rcon.stats.lock_wait_s
    r = io.flush(ops)
    assert all(x.ok for x in r) and len(r) == 50
    assert io.rcon.stats.lock_wait_s - before < 0.05


class _Cut(BaseException):
    pass


def test_a_cut_mid_reply_never_leaks_into_the_next(mc):
    """The core's 50 ms cut is a SIGALRM raised in the main thread. Cut 30 reads of a 35 KB reply at
    different moments: every next reply is its own, on the same connection."""
    import signal
    io, _ = mc

    def cut(*_):
        raise _Cut()

    old = signal.signal(signal.SIGALRM, cut)
    connects, cuts = io.rcon.stats.connects, 0
    try:
        for k in range(30):
            signal.setitimer(signal.ITIMER_REAL, 0.00005 + 0.00004 * k)
            try:
                io.rcon.command("data get storage everhear:meta blocks")
            except _Cut:
                cuts += 1
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
            assert io.rcon.command("time query gametime").startswith("The game time is")
    finally:
        signal.signal(signal.SIGALRM, old)
    assert cuts >= 5
    # recovered on the same socket; a cut in the few bytecodes between a send and its record may
    # cost a reconnect instead (never a wrong reply)
    assert io.rcon.stats.connects - connects <= 1


# ---- part 2: reads on demand --------------------------------------------------------------------

OW = "minecraft:overworld"


def test_world_id_is_kept(mc):
    io, _ = mc
    wid = io.world_id()
    assert len(wid) == 16 and int(wid, 16) >= 0
    io.rcon._break("a reconnect")
    assert io.world_id() == wid


def test_probe(fresh):
    io, p = fresh
    floor = [(x, 84, z) for x in range(-3, 4) for z in range(-3, 4)]
    assert io.probe(OW, floor, "minecraft:grass_block") == [True] * len(floor)
    assert io.probe(OW, floor, "solid") == [True] * len(floor)
    above = [(x, 85, z) for x in range(-3, 4) for z in range(2, 4)]          # clear of the players
    assert io.probe(OW, above, "open") == [True] * len(above)
    assert io.probe(OW, above, "solid") == [False] * len(above)
    io.flush([I.SetBlock(OW, (2, 83, 2), "minecraft:dirt")])
    assert io.probe(OW, [(0, 84, 0), (2, 83, 2)], "#minecraft:dirt") == [False, True]   # grass is not in #dirt
    assert io.probe(OW, [(5000, 84, 0)], "minecraft:grass_block") == [False]         # not loaded


def test_ground_y(fresh):
    io, p = fresh
    assert io.ground_y(OW, 0, 0) == 85 and io.ground_y(OW, 10, -10) == 85
    assert io.ground_y(OW, 5000, 0) is None


def test_a_ray_on_demand(fresh):
    io, p = fresh
    summon_husk(io, 40, (0.5, 86, 2.5))                                    # in the way: entities never stop it
    look = io.ray(OW, (0.5, 86.62, 0.5), (0, -0.5, 3 ** 0.5 / 2), 50)
    assert (look.kind, look.block, look.block_id) == ("block", (0, 84, 3), "minecraft:grass_block")
    assert look.distance == pytest.approx(3.24, abs=1 / 32)
    up = io.ray(OW, (0.5, 86.62, 0.5), (0, 1, 0), 40)
    assert up.kind == "none" and up.distance == pytest.approx(40, abs=0.5), up
    far = io.ray(OW, (0.5, 200, 0.5), (1, 0, 0), 1000)                     # stops where the loaded world ends
    assert far.kind == "none" and 40 < far.distance < 1000
    through = io.ray(OW, (0.5, 86, 0.5), (0, -1, 0), 10, through_blocks=True)
    assert through.kind == "none" and through.pos == pytest.approx((0.5, 76, 0.5))


def test_entities_live_and_far(fresh):
    io, p = fresh
    u = summon_husk(io, 41, (70.5, 100, 0.5), extra=",NoGravity:1b")       # past the mirror's 32 blocks
    found = io.entities(OW, (70.5, 100, 0.5), 3, types=["minecraft:husk"])
    assert [e.uuid for e in found] == [u] and found[0].pos == (70.5, 100.0, 0.5)
    assert u not in {e.uuid for e in io.entities(OW, ARENA, 10)}
    assert io.mirror().get(u) is None
    assert "Steve" not in {e.name for e in io.entities(OW, ARENA, 3)}         # players are the snapshot's
    assert "Steve" in {e.name for e in io.entities(OW, ARENA, 3, types=["minecraft:player"])}


def test_loaded_and_mspt(fresh):
    io, _ = fresh
    assert io.loaded(OW, 0, 0) and not io.loaded(OW, 5000, 0)
    assert 0 < io.mspt() < 50


def test_owner_and_in_ground(fresh):
    io, p = fresh
    steve = [int(x) for x in __import__("vfg_engine.mc.commands", fromlist=["uuid_ints"]).uuid_ints(io.names.uuid_of("Steve"))]
    wolf = "0000ee00-0000-4000-8000-00000000002a"
    io.flush([I.Summon("minecraft:wolf", OW, (3.5, 85, -3.5), wolf, tags=(TEST_TAG,),
                       nbt="{NoAI:1b,Tame:1b,Owner:[I;%d,%d,%d,%d]}" % tuple(steve))])
    e = io.snapshot(track=[wolf]).tracked[wolf]
    assert (e.owner, e.in_ground) == ("Steve", False)
    io.flush([I.Summon("minecraft:arrow", OW, (5.5, 86, 5.5), husk_uuid(43), tags=(TEST_TAG,),
                       nbt="{Owner:[I;%d,%d,%d,%d],Motion:[0.0d,-1.0d,0.0d]}" % tuple(steve))])
    time.sleep(0.4)
    arrow = io.entities(OW, (5.5, 85, 5.5), 2, types=["minecraft:arrow"])[0]
    assert (arrow.owner, arrow.in_ground) == ("Steve", True)


def test_on_ground_and_sprinting(fresh):
    io, p = fresh
    s = io.snapshot().players["Steve"]
    assert (s.on_ground, s.sprinting) == (True, False)
    p.sprint("Steve")
    p.move("Steve", "forward")
    time.sleep(0.3)
    assert io.snapshot().players["Steve"].sprinting is True
    p.move("Steve", None)
    p.sprint("Steve", False)
    io.flush([I.Teleport("Steve", OW, (0.5, 95, 0.5))])
    time.sleep(0.1)                                  # OnGround is the player's own, from its next tick
    assert io.snapshot().players["Steve"].on_ground is False


# ---- part 2: writes ----------------------------------------------------------------------------

def test_fill_and_setblock(fresh):
    io, p = fresh
    a, b = (8, 85, 8), (10, 87, 10)
    try:
        r = io.flush([I.Fill(OW, a, b, "minecraft:stone"), I.Fill(OW, a, b, "minecraft:stone"),
                      I.Fill(OW, a, b, "minecraft:dirt", replace="minecraft:stone"),
                      I.SetBlock(OW, (9, 88, 9), "minecraft:oak_stairs[facing=north]"),
                      I.SetBlock(OW, (9, 88, 9), "minecraft:stone", mode="keep"),
                      I.Fill(OW, (0, 0, 0), (40, 40, 40), "minecraft:stone")])
        assert [(x.ok, x.error) for x in r] == [(True, None), (True, None), (True, None), (True, None),
                                                (False, "something is there already (keep)"),
                                                (False, "refused: the box is 68921 blocks, over the 32768 one fill "
                                                        "changes (the block queue splits it)")]
        assert r[0].reply == "Successfully filled 27 block(s)" and r[1].reply == "No blocks were filled"
        assert io.probe(OW, [(9, 86, 9), (9, 88, 9)], "minecraft:dirt") == [True, False]
        assert io.probe(OW, [(9, 88, 9)], "minecraft:oak_stairs[facing=north]") == [True]
    finally:
        io.flush([I.Fill(OW, a, (10, 88, 10), "minecraft:air")])


def test_forceload(fresh):
    io, _ = fresh
    r = io.flush([I.Forceload(OW, (400, 400), (415, 415)), I.Forceload(OW, (400, 400), (415, 415), add=False)])
    assert [x.ok for x in r] == [True, True]
    assert "Marked chunk [25, 25]" in r[0].reply and "Unmarked chunk [25, 25]" in r[1].reply


def test_motion_and_entity_data(fresh):
    io, p = fresh
    u = summon_husk(io, 44, (-6.5, 85, 6.5), ai=True)
    r = io.flush([I.Motion(u, (0, 1.0, 0)), I.EntityData(u, "{Glowing:1b}"), I.Motion("Steve", (0, 1, 0)),
                  I.EntityData("Steve", "{Glowing:1b}")])
    assert [x.ok for x in r] == [True, True, False, False]
    assert "wind charges" in r[2].error and r[3].error == "a player's data cannot be written"
    time.sleep(0.25)
    e = io.snapshot(track=[u]).tracked[u]
    assert e.pos[1] > 86
    assert io.query("data get entity %s Glowing" % u).endswith("1b")


def test_bossbar(fresh):
    io, _ = fresh
    try:
        r = io.flush([I.Bossbar("ge_test", "Ice Queen", "blue", 150, 300, ("Steve", "Alex")),
                      I.Bossbar("ge_test", "Ice Queen II", value=75), I.Bossbar("ge_test", players=())])
        assert all(x.ok for x in r), r
        assert io.query("bossbar get everhear:ge_test value").endswith("75")
        assert io.query("bossbar get everhear:ge_test max").endswith("300")
        assert "no longer has any players" in r[2].reply
    finally:
        r = io.flush([I.Bossbar("ge_test", remove=True), I.Bossbar("ge_test", value=1)])
    assert r[0].ok and r[1].error == "no such bossbar (give it a name to make it)"


def test_stopsound_spawnpoint_sound_to_particle_force(fresh):
    io, _ = fresh
    r = io.flush([I.StopSound(), I.StopSound(("Steve",), "minecraft:music.game"),
                  I.Spawnpoint("Steve", OW, (2.5, 85, 2.5)),
                  I.Sound("minecraft:entity.blaze.shoot", OW, (0.5, 86, 0.5), to=("Steve",)),
                  I.Sound("minecraft:entity.blaze.shoot", OW, (0.5, 86, 0.5), to=("Steve", "Nobody")),
                  I.Particle("minecraft:flame", OW, (0.5, 86, 0.5), force=True),
                  I.Particle("minecraft:flame", OW, (0.5, 86, 0.5), to=("Alex",))])
    assert [x.ok for x in r] == [True, True, True, True, False, True, True], r
    assert r[2].reply.startswith("Set spawn point to 2, 85, 2") and r[3].reply.endswith("to Steve")
    assert r[4].error == "no player found"


def test_ignite_a_player_and_a_mob(calm):
    io, p = calm
    u = summon_husk(io, 45, (6.5, 85, -6.5), extra=',equipment:{head:{id:"minecraft:leather_helmet",count:1}}')
    r = io.flush([I.Ignite("Steve", 3), I.Ignite(u, 10)])
    assert [x.ok for x in r] == [True, True], r
    time.sleep(0.3)
    fire = int(io.query("data get entity Steve Fire").split(": ")[-1].rstrip("s"))
    assert 40 <= fire <= 60
    assert io.snapshot().players["Steve"].health == 20.0            # the lava's own damage was not taken
    assert io.read_modifiers("Steve") == {}                         # everhear:ignite off again
    assert io.probe(OW, [(0, 85, 0), (0, 86, 0)], "minecraft:lava") == [False, False]
    assert int(io.query("data get entity %s Fire" % u).split(": ")[-1].rstrip("s")) > 150
    # and then it burned: on_fire hurts once the second of Fire Resistance is over (fast
    # regeneration from a full hunger bar heals it back, so the hurts are what is read)
    hurts = kinds(collect(io, lambda e: False, timeout=3.2), "hurt", "Steve")
    assert len(hurts) >= 1 and all(h.data.get("attacker") is None for h in hurts)
    r = io.flush([I.Ignite(husk_uuid(999), 3)])
    assert r[0].error == "no such target, or no air at a player's feet or head to light them from"


# ---- part 2: events ----------------------------------------------------------------------------

def shoot(io, p, item, base="minecraft:bow", hold=1.1):
    io.rcon.command("clear Steve")
    io.rcon.command('item replace entity Steve weapon.mainhand with %s[custom_data={everhear:{item:"%s"}}]' % (base, item))
    if base == "minecraft:bow":
        io.rcon.command("give Steve arrow 16")
    time.sleep(0.1)
    p.use("Steve", "continuous")
    time.sleep(hold)
    p.stop("Steve")


def test_land_an_arrow_in_a_block(fresh):
    io, p = fresh
    p.look("Steve", 0, -10)
    shoot(io, p, "boom_bow")
    land = kinds(collect(io, lambda e: kinds(e, "land")), "land", "Steve")
    assert len(land) == 1
    d = land[0].data
    assert (d["item"], d["hit"], d["projectile_type"], d["dim"]) == ("boom_bow", "block", "minecraft:arrow", OW)
    assert d["block"] and len(d["projectile"]) == 36 and len(d["pos"]) == 3


def test_a_ranged_hit_and_its_land(fresh):
    io, p = fresh
    u = summon_husk(io, 46, (0.5, 85, 8.5), extra=',equipment:{head:{id:"minecraft:leather_helmet",count:1}}')
    p.look_at("Steve", 0.5, 86.3, 8.5)
    time.sleep(0.1)
    shoot(io, p, "boom_bow")
    evs = collect(io, lambda e: kinds(e, "land"))
    hit, land = kinds(evs, "hit", "Steve"), kinds(evs, "land", "Steve")
    assert [(h.data["target"], h.data["ranged"], h.data["item"]) for h in hit] == [(u, True, "boom_bow")]
    assert [(x.data["hit"], x.data["target"], x.data["item"]) for x in land] == [("entity", u, "boom_bow")]
    assert land[0].n > hit[0].n


def test_a_trident_lands_and_the_kill_shot_still_lands(fresh):
    io, p = fresh
    p.look("Steve", 0, -10)
    shoot(io, p, "boom_trident", base="minecraft:trident")
    land = kinds(collect(io, lambda e: kinds(e, "land")), "land", "Steve")
    assert [(x.data["item"], x.data["hit"], x.data["projectile_type"]) for x in land] == [
        ("boom_trident", "block", "minecraft:trident")]
    u = summon_husk(io, 47, (0.5, 85, 8.5))
    io.flush([I.EntityData(u, "{Health:1.0f}")])
    p.look_at("Steve", 0.5, 86.3, 8.5)
    time.sleep(0.1)
    shoot(io, p, "boom_bow")
    evs = collect(io, lambda e: kinds(e, "land"))
    assert kinds(evs, "kill", "Steve") and kinds(evs, "hit", "Steve")[0].data["ranged"] is True
    land = kinds(evs, "land", "Steve")
    assert [(x.data["hit"], x.data["item"]) for x in land] == [("entity", "boom_bow")] and land[0].data["projectile"]


def test_mine_a_watched_block(fresh):
    io, p = fresh
    assert io.watch_mined(["minecraft:sponge"]) == []
    try:
        for block, watched in (("minecraft:sponge", True), ("minecraft:oak_planks", False)):
            io.flush([I.SetBlock(OW, (0, 85, 2), block)])
            p.look_at("Steve", 0.5, 85.5, 2.5)
            time.sleep(0.15)
            io.snapshot()
            p.player("Steve", "attack", "continuous")
            end = time.monotonic() + 8
            got = []
            while io.probe(OW, [(0, 85, 2)], block)[0] and time.monotonic() < end:
                io.snapshot()
                got += io.events()
                time.sleep(0.1)
            p.stop("Steve")
            got += collect(io, lambda e: kinds(e, "mine"), timeout=0.6 if watched else 0.4)
            mine = kinds(got, "mine", "Steve")
            if watched:
                assert [(m.data["block"], m.data["count"], m.data.get("pos")) for m in mine] == [
                    ("minecraft:sponge", 1, (0, 85, 2))]
            else:
                assert mine == []
    finally:
        io.watch_mined([])
        io.flush([I.SetBlock(OW, (0, 85, 2), "minecraft:air")])


def test_block_data(fresh):
    """K's chest(): items into a chest, a sign's text, a barrel's name; and the refusals."""
    io, _ = fresh
    staff = '{Slot:1b,id:"minecraft:carrot_on_a_stick",count:1,components:{"minecraft:custom_data":{everhear:{item:"fire_staff"}}}}'
    try:
        io.flush([I.SetBlock(OW, (6, 85, 6), "minecraft:chest"), I.SetBlock(OW, (7, 85, 6), "minecraft:oak_sign"),
                  I.SetBlock(OW, (8, 85, 6), "minecraft:barrel"), I.SetBlock(OW, (9, 85, 6), "minecraft:stone")])
        r = io.flush([
            I.BlockData(OW, (6, 85, 6), "Items", '{Slot:0b,id:"minecraft:diamond",count:3}'),
            I.BlockData(OW, (6, 85, 6), "Items", staff),
            I.BlockData(OW, (7, 85, 6), "front_text", '{messages:["Hello","","",""]}', "merge"),
            I.BlockData(OW, (8, 85, 6), "CustomName", '"Loot"', "set"),
            I.BlockData(OW, (9, 85, 6), "Items", '{Slot:0b,id:"minecraft:dirt",count:1}'),
            I.BlockData(OW, (6, 85, 6), "Items[0].count", "1"),
            I.BlockData(OW, (6, 85, 6), "Items", '{Slot:2b,id:"minecraft:written_book",count:1,components:{"minecraft:custom_name":"%s"}}' % ("x" * 1400)),
        ])
        assert [(x.ok, x.error) for x in r] == [
            (True, None), (True, None), (True, None), (True, None),
            (False, "no block entity there (a chest, a sign, a barrel… holds data)"),
            (False, "the path holds no list (append and prepend need one)"),
            (False, "the command is over 1446 bytes, RCON's limit")], r
        assert r[0].reply == "Modified block data of 6, 85, 6"
        items = io.query("data get block 6 85 6 Items")
        assert 'id: "minecraft:diamond"' in items and 'everhear: {item: "fire_staff"}' in items
        assert 'messages: ["Hello", "", "", ""]' in io.query("data get block 7 85 6 front_text")
        assert io.query("data get block 8 85 6 CustomName").endswith('"Loot"')
    finally:
        io.flush([I.Fill(OW, (6, 85, 6), (9, 85, 6), "minecraft:air")])
