"""The bosses group on FakeIo: boss_fight (horde_boss, dragon_boss/three_headed_dragon and
ice_queen merged in as one scenario with three Actors), horde_king and its call_minions/enrage,
dragon_head and its enrage, ice_queen and its ice_blast/frost_servants/enrage, and the
frost_servant it calls. A boss rises, bars, fights and falls with a reward where it died and
VICTORY shown; args refused in words; mob_griefing held off and put back.

ice_blast applies the shared `frozen` mark by id alone (another group's, ice_staff's and
ice_beam's): a minimal stub mark is loaded alongside it here, the way the library's own tests
stand in for a dependency they do not own (conftest.py's `extra=`)."""

import pytest

from vfg_engine.io import Bossbar, Damage, Gamerule, GiveEffect, Message, Sound, Summon

FROZEN_STUB = """
class Frozen(Mark):
    class Config(MarkConfig):
        summary = "Stub for testing ice_blast in isolation; the real one is ice_staff's and ice_beam's group."
        duration_s = 4
        damage = 0
"""


def online(r, *names):
    for i, n in enumerate(names):
        r.io.join(n, pos=(100.0 * i, 64.0, 0.0))        # on FakeIo's stone, whose top is y 63
    r.tick()


def summons(r, entity_type=None):
    made = r.sent(Summon)
    return [s for s in made if entity_type is None or s.entity_type == entity_type]


# ---------------------------------------------------------------- loading

def test_it_loads(rig):
    r = rig("boss_fight", "horde_king", "dragon_head", "ice_queen", "frost_servant", "call_minions", "enrage",
            "ice_blast", "frost_servants")
    loaded = r.start()["loaded"]
    assert set(loaded) == {"boss_fight", "horde_king", "dragon_head", "ice_queen", "frost_servant", "call_minions",
                          "enrage", "ice_blast", "frost_servants"}


# ---------------------------------------------------------------- boss_fight + horde_king

def test_horde_king_rises_fights_and_falls(rig):
    r = rig("boss_fight", "horde_king", "call_minions", "enrage")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("boss_fight", boss="horde_king", near="Steve")
    r.run_for(0.3)
    made = summons(r, "minecraft:zombie")
    assert len(made) == 1
    king = made[0]
    x, y, z = king.pos
    dist = (x * x + z * z) ** 0.5
    assert y == 64.0 and 1.5 <= dist <= 8.0           # a safe spot near Steve (near_spread), never on him
    assert "CustomName:\"Horde King\"" in king.nbt
    assert "netherite_sword" in king.nbt and "drop_chances" in king.nbt
    where = "at %d %d %d" % (round(x), round(y), round(z))
    got = r.result(iid)
    assert got["summary"] == "Horde King rises %s" % where      # no health claimed: the Actor's own, unread here
    r.run_for(1)
    bars = r.sent(Bossbar)
    assert bars and bars[-1].name == "Horde King" and bars[-1].color == "red" and bars[-1].value == 800
    r.io.kill_entity(king.uuid)
    r.run_for(1)
    assert not r.running(iid)
    got = r.result(iid)
    assert got["summary"] == "Horde King, who rose %s, is dead" % where and got["facts"]["dead"] is True
    reward = summons(r, "minecraft:item")
    assert len(reward) == 1 and reward[0].nbt == '{Item:{id:"minecraft:diamond",count:8}}'
    assert reward[0].tags == ("everhear", "everhear.kept")           # it outlives the scenario
    titles = [m for m in r.sent(Message) if m.where == "title"]
    assert any(m.text == "§6VICTORY" for m in titles)          # gold, speak()'s colour code
    assert r.problems() == []


def test_no_one_rises_with_nobody_online(rig):
    r = rig("boss_fight", "horde_king", "call_minions", "enrage")
    r.start()
    iid = r.start_scenario("boss_fight", boss="horde_king", near="Steve")
    r.tick(2)
    assert not r.running(iid)
    assert r.result(iid)["summary"] == "no horde_king: Steve is not online"


def test_unknown_boss_refused_in_words(rig):
    r = rig("boss_fight")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("boss_fight", boss="not_a_boss", near="Steve")
    r.tick(2)
    assert not r.running(iid)
    assert r.result(iid)["summary"].startswith("no not_a_boss:")
    assert not summons(r)


def test_overrides_add_extra_nbt(rig):
    r = rig("boss_fight", "horde_king", "call_minions", "enrage")
    r.start()
    online(r, "Steve")
    r.start_scenario("boss_fight", boss="horde_king", near="Steve", name="Big Steve", health=1000, scale=6)
    r.tick(2)
    nbt = summons(r, "minecraft:zombie")[0].nbt
    # the Actor's own values, then the overrides after (NEEDS, boss_fight.py: relies on Minecraft
    # keeping the later of two same-named keys; not proven live in this stage)
    assert 'CustomName:"Horde King"' in nbt and 'CustomName:"Big Steve"' in nbt
    assert "Health:1000f" in nbt and '"minecraft:max_health",base:1000' in nbt
    assert '"minecraft:scale",base:6' in nbt


def test_griefing_held_off_and_restored(rig):
    r = rig("boss_fight", "horde_king", "call_minions", "enrage")
    r.start()
    r.io.gamerules["mob_griefing"] = "true"
    online(r, "Steve")
    r.start_scenario("boss_fight", boss="horde_king", near="Steve", griefing=False)
    r.run_for(0.3)
    rules = r.sent(Gamerule)
    assert rules and rules[-1].name == "mob_griefing" and rules[-1].value == "false"
    r.io.kill_entity(summons(r, "minecraft:zombie")[0].uuid)
    r.run_for(1)
    assert r.sent(Gamerule)[-1].value == "true"                   # put back once the fight ends


def test_args_refused_in_words(rig):
    r = rig("boss_fight")
    r.start()
    reply = r.op("start", scenario="boss_fight", args={"bodies": 9})
    assert not reply["ok"] and "bodies" in reply["error"]
    reply = r.op("start", scenario="boss_fight", args={"music": "pigstep"})
    assert not reply["ok"] and "music" in reply["error"]
    reply = r.op("start", scenario="boss_fight", args={"boss_mob": "zombie"})
    assert not reply["ok"] and "not an arg of this" in reply["error"]


def test_at_a_place_not_near_a_player(rig):
    r = rig("boss_fight", "horde_king", "call_minions", "enrage")
    r.start()
    iid = r.start_scenario("boss_fight", boss="horde_king", at="10 64 20")
    r.tick(2)
    made = summons(r, "minecraft:zombie")
    assert len(made) == 1 and made[0].pos == (10.0, 64.0, 20.0)
    assert r.result(iid)["summary"] == "Horde King rises at 10 64 20"


def test_call_minions_calls_round_the_body(rig):
    r = rig("boss_fight", "horde_king", "call_minions", "enrage")
    r.start()
    online(r, "Steve")
    r.start_scenario("boss_fight", boss="horde_king", near="Steve")
    r.run_for(10.3)
    minions = [s for s in summons(r, "minecraft:zombie") if "Horde King" not in (s.nbt or "")]
    assert len(minions) == 3
    assert r.problems() == []


def test_enrage_buffs_and_warns_as_it_weakens(rig):
    r = rig("boss_fight", "horde_king", "call_minions", "enrage")
    r.start()
    online(r, "Steve")
    r.start_scenario("boss_fight", boss="horde_king", near="Steve")
    r.run_for(1.1)
    uuid = summons(r, "minecraft:zombie")[0].uuid
    r.io.by_uuid[uuid].health = 350.0                              # under 0.5 of 800
    r.run_for(1.1)
    roars = [s for s in r.sent(Sound) if "ravager.roar" in s.name]
    assert len(roars) == 1
    # ctx.user.name here is the body's live CustomName (beings.py: ActorBeing.name), which FakeIo's
    # Summon does not parse back out of the nbt it was spawned with (it falls back to the uuid);
    # on a real server this reads "Horde King is enraged" (WRITING.md: ctx.user.name).
    warn = [m for m in r.sent(Message) if m.where == "actionbar" and m.text.startswith("§c") and
            m.text.endswith("is enraged")]
    assert len(warn) == 1
    r.run_for(2.5)
    effects = r.sent(GiveEffect)
    assert r.io.attribute_value(uuid, "minecraft:movement_speed") == pytest.approx(1.25)   # faster: a modifier
    assert any(e.effect == "minecraft:strength" and e.amplifier == 0 for e in effects)
    r.io.by_uuid[uuid].health = 150.0                              # under 0.25 of 800
    r.run_for(1.1)
    assert len([s for s in r.sent(Sound) if "ravager.roar" in s.name]) == 2
    assert r.io.attribute_value(uuid, "minecraft:movement_speed") == pytest.approx(1.5)    # faster again
    assert r.problems() == []


# ---------------------------------------------------------------- dragon_head

def test_dragon_head_three_bodies_one_bar_in_the_sky(rig):
    r = rig("boss_fight", "dragon_head", "enrage")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("boss_fight", boss="dragon_head", near="Steve", bodies=3, height=30, music="boss",
                           griefing=False, name="Three-Headed Dragon")
    r.run_for(0.3)
    made = summons(r, "minecraft:ender_dragon")
    assert len(made) == 3 and all(s.pos[1] == 94.0 for s in made)         # 64 + 30
    xs = sorted(s.pos[0] for s in made)
    assert [round(xs[1] - xs[0], 1), round(xs[2] - xs[1], 1)] == [6.0, 6.0]   # spaced, not stacked
    assert len({s.pos[2] for s in made}) == 1                             # one row, same z
    r.run_for(1)
    bars = r.sent(Bossbar)
    assert bars[-1].name == "Three-Headed Dragon" and bars[-1].max == 1800 and bars[-1].value == 1800
    for s in made:
        r.io.kill_entity(s.uuid)
    r.run_for(1)
    assert not r.running(iid)
    assert r.result(iid)["facts"]["bodies"] == 3
    assert r.problems() == []


# ---------------------------------------------------------------- ice_queen

def test_ice_queen_rises_in_her_gear_and_calls_servants(rig):
    r = rig("boss_fight", "ice_queen", "ice_blast", "frost_servants", "enrage", "frost_servant",
            extra={"frozen": FROZEN_STUB})
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("boss_fight", boss="ice_queen", near="Steve", reward="blue_ice", reward_count=16)
    r.run_for(0.3)
    made = summons(r, "minecraft:stray")
    queen = made[0]
    assert "dyed_color" in queen.nbt and "diamond_helmet" in queen.nbt and "diamond_boots" in queen.nbt
    assert "leather_chestplate" in queen.nbt and "iron_sword" in queen.nbt
    r.run_for(1)
    bars = r.sent(Bossbar)
    assert bars and bars[-1].color == "blue"
    r.run_for(8.2)
    servants = [s for s in summons(r, "minecraft:stray") if s.uuid != queen.uuid]
    assert len(servants) == 3 and all('CustomName:"Frost Servant"' in s.nbt for s in servants)
    dmg = [d for d in r.sent(Damage) if d.damage_type == "minecraft:freeze"]
    assert dmg and all(d.amount == 6 and d.target == "Steve" for d in dmg)
    r.io.kill_entity(queen.uuid)
    r.run_for(1)
    assert not r.running(iid)
    reward = summons(r, "minecraft:item")
    assert reward[0].nbt == '{Item:{id:"minecraft:blue_ice",count:16}}'
    assert r.problems() == []


def test_frost_servants_never_pass_the_cap(rig):
    r = rig("boss_fight", "ice_queen", "ice_blast", "frost_servants", "enrage", "frost_servant",
            extra={"frozen": FROZEN_STUB})
    r.start()
    online(r, "Steve")
    r.start_scenario("boss_fight", boss="ice_queen", near="Steve")
    r.run_for(24.3)                                       # three calls' worth (every 8 s): 9 asked, cap is 6
    servants = [s for s in summons(r, "minecraft:stray") if "Frost Servant" in s.nbt]
    assert len(servants) == 6
    assert r.problems() == []
