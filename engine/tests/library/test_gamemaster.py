"""The gamemaster group on FakeIo: demon_lord (ported from `actions/demon_lord.py`, `e67505d`),
its four boss Actors (gravekeeper, spider_queen, ravager_warlord, hollow_king) and the six
minigames it starts and owns (sanctuary, blood_moon, lightning_storm, closing_ring, defend_gregor,
parkour). Covers: a boss's own stats and gear; a full round (supply, arm, a wave spawned and
killed, scoring, the break); a boss round (its bar, its body); recall after a kill-less wait; each
minigame's own main effect and its result; args refused in words."""

from vfg_engine.io import AddModifier, Bossbar, Command, Fill, Message, Summon, Teleport

ALL = ["demon_lord", "arena", "gravekeeper", "spider_queen", "ravager_warlord", "hollow_king",
      "sanctuary", "blood_moon", "lightning_storm", "closing_ring", "defend_gregor", "parkour"]


def online(r, *names):
    for i, n in enumerate(names):
        r.io.join(n, pos=(100.0 * i, 64.0, 0.0))        # on FakeIo's stone, whose top is y 63
    r.tick()


def summons(r, entity_type=None):
    made = r.sent(Summon)
    return [s for s in made if entity_type is None or s.entity_type == entity_type]


def titles(r):
    return [m.text for m in r.sent(Message) if m.where == "title"]


def chat(r):
    return [m.text for m in r.sent(Message) if m.where == "chat"]


def commands(r):
    return [c.text for c in r.sent(Command)]


# ---------------------------------------------------------------- loading

def test_it_loads(rig):
    r = rig(*ALL)
    assert set(r.start()["loaded"]) == set(ALL)


# ---------------------------------------------------------------- the four bosses

def test_gravekeeper_stats(rig):
    r = rig("gravekeeper")
    r.start()
    reply = r.op("spawn", actor="gravekeeper", at=[0.0, 64.0, 0.0])
    assert reply["ok"], reply
    g = summons(r, "minecraft:wither_skeleton")[0]
    assert '"minecraft:scale",base:2' in g.nbt and '"minecraft:attack_damage",base:8' in g.nbt
    assert 'equipment:{mainhand:{id:"minecraft:netherite_sword"' in g.nbt and "drop_chances:{mainhand:0" in g.nbt
    assert 'CustomName:"The Gravekeeper"' in g.nbt


def test_spider_queen_glows_and_is_huge(rig):
    r = rig("spider_queen")
    r.start()
    r.op("spawn", actor="spider_queen", at=[0.0, 64.0, 0.0])
    s = summons(r, "minecraft:spider")[0]
    assert '"minecraft:scale",base:3' in s.nbt and "Glowing:1b" in s.nbt


def test_ravager_warlord_carries_an_evoker(rig):
    r = rig("ravager_warlord")
    r.start()
    r.op("spawn", actor="ravager_warlord", at=[0.0, 64.0, 0.0])
    rav = summons(r, "minecraft:ravager")[0]
    assert 'Passengers:[{id:"minecraft:evoker"}]' in rav.nbt


def test_hollow_king_is_faster_than_a_zombie(rig):
    r = rig("hollow_king")
    r.start()
    r.op("spawn", actor="hollow_king", at=[0.0, 64.0, 0.0])
    k = summons(r, "minecraft:zombie")[0]
    assert 'equipment:{head:{id:"minecraft:netherite_helmet"' in k.nbt and "chest:" in k.nbt
    mods = [m for m in r.sent(AddModifier) if m.target == k.uuid and m.attribute == "minecraft:movement_speed"]
    assert mods and abs(mods[0].amount - (0.32 - 0.23)) < 1e-6 and mods[0].operation == "add_value"


# ---------------------------------------------------------------- the round lifecycle

def test_a_round_supplies_arms_and_scores_a_clean_wave(rig):
    r = rig(*ALL)
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("demon_lord", round=1, level=1)
    assert r.result(iid)["summary"] == "the Demon Lord begins at round 1, level 1"
    r.run_for(1)                                         # gather, then supply + "arm up" at the start of round 1
    assert r.io.players["Steve"].gamemode == "survival"
    assert any("Supply chest" in m.text for m in r.sent(Message) if m.where == "actionbar")
    assert r.io.spawnpoints.get("Steve") is not None      # respawn near the event (on_ground by default)
    r.run_for(20)                                         # Config.arm_s: rotation[0] == 0 (webs), round % 4 != 0
    assert any("Round 1" in t for t in titles(r))
    r.run_for(5)                                           # the wave kind's own 5 s pre-wait
    r.tick(3)                                              # the staggered per-player spawn
    made = summons(r)
    assert made and {s.entity_type for s in made} <= {"minecraft:spider", "minecraft:cave_spider"}
    assert len([f for f in r.sent(Fill) if f.block == "minecraft:cobweb"]) == 12
    for s in made:
        r.io.kill_entity(s.uuid)
    r.run_for(4)                                           # the next tick() (every 3 s) notices they are all dead
    assert any("Not a single death" in m.text for m in r.sent(Message))     # clean: the level rises
    r.run_for(60)                                           # Config.break_s
    assert r.running(iid)
    assert r.problems() == []


def test_many_deaths_hold_the_level_back_and_resupply(rig):
    r = rig(*ALL)
    r.start()
    online(r, "Steve")
    r.start_scenario("demon_lord", round=1, level=4)
    r.run_for(1 + 20 + 5)
    r.tick(3)
    for _ in range(3):                                     # 3 deaths, 1 player: ratio 3.0 >= 1.5
        r.io.kill("Steve")
        r.io.respawn("Steve")
    r.run_for(4)
    assert any("too fragile" in m.text for m in r.sent(Message))
    resupplies = [m for m in r.sent(Message) if m.where == "actionbar" and "Supply chest" in m.text]
    assert len(resupplies) >= 2            # the round's own chest, and the hard-held-back round's fresh one


def test_a_boss_round_rises_bars_and_falls(rig):
    r = rig(*ALL)
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("demon_lord", round=12, level=5)        # rotation[11] == 5: a plain kind, so a boss
    r.run_for(1 + 20)                                              # gather + supply + arm
    assert any("awakens in 15 seconds" in m.text for m in r.sent(Message))
    r.run_for(15)                                                  # the boss itself rises
    boss = summons(r, "minecraft:ravager")                         # round // 4 - 1 == 2: ravager_warlord
    assert boss and "Health:100f" in boss[0].nbt                   # 40 + 12 x 5 x 1 player
    r.run_for(3)
    bars = r.sent(Bossbar)
    assert bars and bars[-1].name == "Ravager Warlord"
    r.io.kill_entity(boss[0].uuid)
    r.run_for(4)
    assert any("has fallen" in m.text for m in r.sent(Message))
    assert r.running(iid)


def test_a_wave_recalled_after_no_kill(rig):
    r = rig(*ALL)
    r.start()
    online(r, "Steve")
    r.start_scenario("demon_lord", round=2, level=1)       # rotation[1] == 1: zombies (not a boss: round % 4 != 0)
    r.run_for(1 + 20 + 5)
    r.tick(3)
    assert summons(r, "minecraft:zombie") or summons(r, "minecraft:husk")
    r.run_for(31)                                          # Config.recall_after_s, no kill
    assert any("I recall them" in m.text for m in r.sent(Message))


def test_args_refused_in_words(rig):
    r = rig(*ALL)
    r.start()
    reply = r.op("start", scenario="demon_lord", args={"round": 0})
    assert not reply["ok"] and "round" in reply["error"]


# ---------------------------------------------------------------- the minigames, standing alone

def test_blood_moon_darkens_and_spawns_then_ends(rig):
    r = rig("blood_moon")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("blood_moon", level=3, players="Steve")
    r.tick(3)
    assert r.io.players["Steve"].effects.get("darkness") is not None
    assert summons(r)
    r.run_for(6 * 7 + 1)
    assert not r.running(iid)
    assert r.result(iid)["facts"]["count"] > 0


def test_lightning_storm_strikes_and_clears(rig):
    r = rig("lightning_storm")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("lightning_storm", level=1, players="Steve")
    r.run_for((10 + 1) * 2 + 1)
    assert not r.running(iid)
    assert len(summons(r, "minecraft:lightning_bolt")) == 11
    assert any("weather clear" in c for c in commands(r))


def test_closing_ring_shuts_and_reopens_the_border(rig):
    r = rig("closing_ring")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("closing_ring", level=4, players="Steve")
    r.tick(2)
    assert any(c.startswith("worldborder center") for c in commands(r))
    assert r.io.spawnpoints.get("Steve") is not None
    r.run_for(85)
    assert not r.running(iid)
    assert r.result(iid)["facts"]["final"] == max(6, 16 - 4 // 2)
    assert any("worldborder set 60000000" in c for c in commands(r))


def test_sanctuary_spares_or_punishes(rig):
    r = rig("sanctuary")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("sanctuary", level=1, players="Steve")
    r.tick(2)
    assert r.sent(Fill)                                     # the platform is built
    r.run_for(45)                                            # max(min_time_s, max_time_s - level) == 44
    assert not r.running(iid)
    facts = r.result(iid)["facts"]
    assert bool(facts["spared"]) != bool(facts["punished"]) or (facts["spared"] or facts["punished"])


def test_defend_gregor_rewards_survival(rig):
    r = rig("defend_gregor")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("defend_gregor", level=1, players="Steve")
    r.tick(2)
    assert summons(r, "minecraft:villager")
    r.run_for(12 + 60 + 5 + 1)
    assert not r.running(iid)
    assert r.result(iid)["facts"]["survived"] is True
    assert any("give Steve emerald 8" in c for c in commands(r))


def test_parkour_builds_a_course_and_ends(rig):
    r = rig("parkour")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("parkour", level=1, players="Steve")
    r.tick(2)
    assert any(t.target == "Steve" for t in r.sent(Teleport))
    assert [f for f in r.sent(Fill) if "gold_block" in f.block]
    r.run_for(95)
    assert not r.running(iid)
    assert r.problems() == []
