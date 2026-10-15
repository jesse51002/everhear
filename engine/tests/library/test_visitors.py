"""The visitors group on FakeIo: god_visits (the Radiant/Storm/Thanatos gods and their gifts and
takings), horror (horror_mode and herobrine merged in: the ash, a shadow, a fake creeper, a
whisper, the dark; herobrine's door, fake join, torches, sign and far lightning), and chosen_one
(the private cinematic and its blade). Args refused in words; what each spawns is cleaned up."""

from vfg_engine.io import Command, GiveEffect, Message, Particle, SetBlock, Sound, Summon


def online(r, *names):
    for i, n in enumerate(names):
        r.io.join(n, pos=(100.0 * i, 64.0, 0.0))
    r.tick()


def test_it_loads(rig):
    r = rig("god", "god_visits", "shadow", "fake_creeper", "horror", "chosen_one")
    assert sorted(r.start()["loaded"]) == ["chosen_one", "fake_creeper", "god", "god_visits", "horror", "shadow"]


# ---- god_visits -----------------------------------------------------------------------------

def test_a_god_gives_a_gift_then_leaves(rig):
    r = rig("god", "god_visits")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("god_visits", players="Steve", min_s=10, max_s=10, each=False, gift_weight=1,
                           take_weight=0, gifts=["diamond 4"], stay_s=3)
    r.run_for(12)
    gods = r.sent(Summon)
    assert len(gods) == 1 and gods[0].entity_type == "minecraft:mannequin"
    assert "profile" in gods[0].nbt and "Rotation" in gods[0].nbt and "CustomNameVisible:1b" in gods[0].nbt
    gives = [c for c in r.sent(Command) if c.text.startswith("give Steve diamond")]
    assert len(gives) == 1 and gives[0].text == "give Steve diamond 4"
    whispers = [m for m in r.sent(Message) if m.players == ("Steve",) and "whispers to you" in m.text]
    assert len(whispers) == 1 and "gift" in whispers[0].text
    assert r.running(iid)                                # it keeps visiting, until stopped
    r.run_for(4)
    assert gods[0].uuid not in r.io.by_uuid               # the god left again
    assert r.problems() == []


def test_a_god_takes_something(rig):
    r = rig("god", "god_visits")
    r.start()
    online(r, "Steve")
    r.start_scenario("god_visits", players="Steve", min_s=10, max_s=10, each=False, gift_weight=0, take_weight=1,
                     take_items=["diamond"], take_most=3, stay_s=2)
    r.run_for(12)
    takes = [c for c in r.sent(Command) if c.text.startswith("clear Steve")]
    assert takes and takes[0].text == "clear Steve minecraft:diamond 3"
    assert r.problems() == []


def test_god_visits_args_refused_in_words(rig):
    r = rig("god", "god_visits")
    r.start()
    reply = r.op("start", scenario="god_visits", args={"min_s": 1})
    assert not reply["ok"] and "min_s" in reply["error"]
    reply = r.op("start", scenario="god_visits", args={"take_items": ["dragonfly"]})
    assert not reply["ok"] and "dragonfly" in reply["error"]


# ---- horror ---------------------------------------------------------------------------------

def test_a_shadow_watches_then_a_stop_takes_it(rig):
    r = rig("shadow", "horror")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("horror", players="Steve", events=["shadow"], weights=[1], min_s=1, max_s=1, each=True,
                           haze=False)
    r.run_for(2.5)                                        # one shadow stands behind Steve
    shadows = [s for s in r.sent(Summon) if s.entity_type == "minecraft:armor_stand"]
    assert len(shadows) == 1
    s = shadows[0]
    assert "Herobrine" in s.nbt and "Rotation" in s.nbt and abs(s.pos[2] + 12) <= 1
    assert s.uuid in r.io.by_uuid
    assert r.op("stop", instance=iid)["ok"]               # it lingers 3 to 6 s on its own; a stop takes it now
    r.tick(2)
    assert s.uuid not in r.io.by_uuid
    assert r.problems() == []


def test_a_fake_creeper_hisses_then_pops(rig):
    r = rig("fake_creeper", "horror")
    r.start()
    online(r, "Steve")
    r.start_scenario("horror", players="Steve", events=["mob"], weights=[1], min_s=5, max_s=5, each=True,
                     haze=False)
    r.run_for(6.5)                                        # one fake creeper stands behind Steve
    mobs = [s for s in r.sent(Summon) if s.entity_type == "minecraft:creeper"]
    assert len(mobs) == 1 and "ExplosionRadius:0b" in mobs[0].nbt and "NoAI:1b" in mobs[0].nbt
    hisses = [s for s in r.sent(Sound) if s.name == "minecraft:entity.creeper.primed"]
    assert len(hisses) == 1 and hisses[0].to == ("Steve",)
    r.run_for(1.5)                                        # it hisses 1.5 s, then vanishes on its own
    assert mobs[0].uuid not in r.io.by_uuid
    assert any(op.name == "minecraft:poof" for op in r.sent(Particle))
    assert r.problems() == []


def test_the_dark_spares_no_dark_players(rig):
    r = rig("horror")
    r.start()
    online(r, "Steve", "Alex")
    r.start_scenario("horror", players="Steve,Alex", events=["dark"], weights=[1], min_s=1, max_s=1, each=True,
                     no_dark=["Alex"], haze=False)
    r.run_for(3)
    darks = r.sent(GiveEffect)
    assert {d.target for d in darks} == {"Steve"}
    assert darks[0].effect == "minecraft:darkness" and darks[0].particles is False
    assert r.problems() == []


def test_torches_go_out_near_the_player(rig):
    r = rig("horror")
    r.start()
    online(r, "Steve")
    r.io.set_block("minecraft:overworld", (2, 64, 2), "minecraft:torch")
    r.start_scenario("horror", players="Steve", events=["torches"], weights=[1], min_s=1, max_s=1, each=True,
                     haze=False)
    r.run_for(3)
    assert r.io.block_at("minecraft:overworld", 2, 64, 2) == "minecraft:air"
    assert r.problems() == []


def test_a_sign_appears_behind_the_player(rig):
    r = rig("horror")
    r.start()
    online(r, "Steve")
    r.start_scenario("horror", players="Steve", events=["sign"], weights=[1], min_s=1, max_s=1, each=True,
                     haze=False)
    r.run_for(2.5)
    signs = [op for op in r.sent(SetBlock) if "behind you" in op.block]
    assert len(signs) == 1 and "oak_sign" in signs[0].block
    assert r.problems() == []


def test_herobrine_fake_join_then_leaves(rig):
    r = rig("horror")
    r.start()
    online(r, "Steve")
    r.start_scenario("horror", players="Steve", events=["fake_join"], weights=[1], min_s=1, max_s=1, each=True,
                     haze=False)
    r.run_for(3)
    joined = [m for m in r.sent(Message) if "Herobrine joined the game" in m.text]
    assert joined and joined[0].players == ()             # @a: everyone
    r.run_for(7)                                          # 6 s later, it leaves again
    left = [m for m in r.sent(Message) if "Herobrine left the game" in m.text]
    assert left
    assert r.problems() == []


def test_the_haze_shows_only_to_each_player(rig):
    r = rig("horror")
    r.start()
    online(r, "Steve")
    r.start_scenario("horror", players="Steve", events=["message"], weights=[0], haze=True)
    r.tick(15)
    ash = [p for p in r.sent(Particle) if p.name == "minecraft:ash"]
    assert ash and all(p.to == ("Steve",) for p in ash)
    assert r.problems() == []


def test_horror_args_refused_in_words(rig):
    r = rig("horror")
    r.start()
    reply = r.op("start", scenario="horror", args={"events": ["ghost"]})
    assert not reply["ok"] and "events" in reply["error"]
    reply = r.op("start", scenario="horror", args={"min_s": 0})
    assert not reply["ok"] and "min_s" in reply["error"]


# ---- chosen_one -----------------------------------------------------------------------------

def test_chosen_one_names_steve_and_gives_the_blade(rig):
    r = rig("chosen_one")
    r.start()
    online(r, "Steve")
    iid = r.start_scenario("chosen_one", player="Steve", blade="Test Blade")
    r.run_for(0.5)
    assert r.running(iid)                                 # still mid-cinematic
    r.run_for(20)
    assert not r.running(iid)
    got = r.result(iid)
    assert "Steve is the chosen one" in got["summary"] and got["facts"]["blade"] == "Test Blade"
    gods = [s for s in r.sent(Summon) if s.entity_type == "minecraft:mannequin"]
    assert len(gods) == 1 and "Notch" in gods[0].nbt
    gives = [c for c in r.sent(Command) if c.text.startswith("give Steve netherite_sword")]
    assert gives and "Test Blade" in gives[0].text
    assert gods[0].uuid not in r.io.by_uuid                # the mannequin vanished at the end
    assert r.problems() == []


def test_chosen_one_nobody_online(rig):
    r = rig("chosen_one")
    r.start()
    iid = r.start_scenario("chosen_one", player="Steve")
    r.tick(2)
    assert not r.running(iid)
    assert r.result(iid)["summary"] == "nobody was chosen: Steve is not online"
