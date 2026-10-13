"""The rules group on FakeIo: lucky_blocks, sneaky_creepers, walking_dead, kill_fx and grave_marks,
five old "runs until stopped" scripts ported straight onto the engine, each its own scenario.

A shared tiny "dealer" ability (hitscan, the kit's hit) stands in for real combat where a test
needs a Kill or Hurt event with a real player attacker, the way 26.3 reports it (self.damage with
no user, as a bare scenario has, never credits a player, so FakeIo never emits one)."""

import pytest

from vfg_engine.io import Command, Fill, GiveEffect, Message, Particle, Remove, SetBlock, Sound, Summon

GROUP = ("lucky_blocks", "sneaky_creepers", "walking_dead", "kill_fx", "grave_marks")

DEALER = '''
class Dealer(Ability):
    class Config(AbilityConfig):
        summary = "A fixed melee hit, for the test."
        damage = 5
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = 0

    def on_cast(self, ctx, ev):
        shot = hitscan(self, ctx, 100, 2, "everything")
        hit(self, ctx, shot.targets, self.config.damage, "minecraft:player_attack", most=1)
'''

# walking_dead's own assumed vanilla bases (see its NEEDS comment).
VANILLA_SPEED = 0.23
VANILLA_FOLLOW = 35.0


def force(monkeypatch, key):
    """Make lucky_blocks' next roll land on the outcome whose key is `key`."""
    import random
    monkeypatch.setattr(random, "choice", lambda seq: next(o for o in seq if o[1] == key))


def own(r, player):
    """Sounds sent only to `player`."""
    return [s for s in r.sent(Sound) if s.to == (player,)]


def test_the_group_loads(rig):
    r = rig(*GROUP)
    loaded = r.start()["loaded"]
    assert sorted(loaded) == sorted(GROUP)


# ---- kill_fx --------------------------------------------------------------------------------

def test_a_death_plays_a_sting_only_to_that_player(rig):
    r = rig("kill_fx")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.io.join("Alex", pos=(50.0, 64.0, 0.0))
    r.start_scenario("kill_fx")
    r.tick()
    r.io.kill("Steve")
    r.tick(2)
    stings = [s for s in own(r, "Steve") if s.name == "minecraft:entity.player.death"]
    assert len(stings) == 1
    assert not [s for s in own(r, "Alex") if s.name == "minecraft:entity.player.death"]
    assert r.problems() == []


def test_kill_streak_chimes_rise_and_twinkle_at_three(rig):
    r = rig("kill_fx", extra={"dealer": DEALER})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.start_scenario("kill_fx", streak_s=8)
    r.tick()
    for _ in range(3):
        r.io.add_entity("minecraft:zombie", (0.0, 64.0, -5.0), health=1)
        r.cast("Steve", "dealer")
        r.tick(2)
    chimes = [s for s in own(r, "Steve") if s.name == "minecraft:entity.experience_orb.pickup"]
    assert [round(c.pitch, 2) for c in chimes] == [1.0, 1.2, 1.4]
    twinkles = [s for s in own(r, "Steve") if s.name == "minecraft:entity.firework_rocket.twinkle"]
    assert len(twinkles) == 1
    assert r.problems() == []


def test_a_gap_past_streak_s_resets_it(rig):
    r = rig("kill_fx", extra={"dealer": DEALER})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.start_scenario("kill_fx", streak_s=1)
    r.tick()
    r.io.add_entity("minecraft:zombie", (0.0, 64.0, -5.0), health=1)
    r.cast("Steve", "dealer")
    r.tick(2)
    r.run_for(2)                                         # past the 1 s streak window
    r.io.add_entity("minecraft:zombie", (0.0, 64.0, -5.0), health=1)
    r.cast("Steve", "dealer")
    r.tick(2)
    chimes = [s for s in own(r, "Steve") if s.name == "minecraft:entity.experience_orb.pickup"]
    assert [round(c.pitch, 2) for c in chimes] == [1.0, 1.0]


def test_hit_marker_and_hurt_thud(rig):
    r = rig("kill_fx", extra={"dealer": DEALER})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.start_scenario("kill_fx")
    r.tick()
    target = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -5.0), health=1000)
    r.io.hit("Steve", target, "minecraft:zombie")        # a real melee hit (engine-dealt damage echoes
    r.tick(2)                                            # its own hit back out, never to content)
    assert [round(s.pitch, 2) for s in own(r, "Steve") if s.name == "minecraft:entity.arrow.hit_player"] == [1.6]
    r.io.kill_entity(target)                             # clear the way: Alex's hitscan must reach Steve
    r.io.join("Alex", pos=(0.0, 64.0, -5.0), yaw=0.0)
    r.tick()
    r.cast("Alex", "dealer")                             # Alex hits Steve: a thud for Steve (5 of his 20)
    r.tick(2)
    assert [round(s.pitch, 2) for s in own(r, "Steve")
           if s.name == "minecraft:entity.player.hurt_sweet_berry_bush"] == [0.6]
    assert r.problems() == []


def test_on_filters_to_death_or_kill_only(rig):
    r = rig("kill_fx")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.start_scenario("kill_fx", on="kill", hit_markers=False, hurt_sound=False)
    r.tick()
    r.io.kill("Steve")                                   # on="kill": no death sting
    r.tick(2)
    assert not [s for s in own(r, "Steve") if s.name == "minecraft:entity.player.death"]


def test_volume_scales_every_sound(rig):
    r = rig("kill_fx")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.start_scenario("kill_fx", volume=2)
    r.tick()
    r.io.kill("Steve")
    r.tick(2)
    sting = [s for s in own(r, "Steve") if s.name == "minecraft:entity.player.death"][0]
    assert sting.volume == 2.0


def test_kill_fx_bad_args_refused(rig):
    r = rig("kill_fx")
    r.start()
    reply = r.op("start", scenario="kill_fx", args={"on": "sometimes"})
    assert not reply["ok"] and "on" in reply["error"]
    reply = r.op("start", scenario="kill_fx", args={"streak_s": 1000})
    assert not reply["ok"] and "streak_s" in reply["error"]


# ---- grave_marks ------------------------------------------------------------------------------

def test_a_death_places_a_skull_and_a_named_marker(rig):
    r = rig("grave_marks")
    r.start()
    r.io.join("Steve", pos=(10.0, 64.0, -20.0))
    r.start_scenario("grave_marks", seconds=100)
    r.tick()
    r.io.kill("Steve")
    r.tick(2)
    skulls = [s for s in r.sent(SetBlock) if s.block == "minecraft:skeleton_skull"]
    assert len(skulls) == 1 and skulls[0].pos == (10, 64, -20)
    marker = [s for s in r.sent(Summon) if s.entity_type == "minecraft:armor_stand"][0]
    assert 'CustomName:"Steve died here (#1)"' in marker.nbt and "Glowing:1b" in marker.nbt
    said = [m for m in r.sent(Message) if "Steve died at 10 64 -20" in m.text]
    assert said and said[0].players == ()
    assert r.problems() == []


def test_it_fades_after_its_seconds(rig):
    r = rig("grave_marks")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.start_scenario("grave_marks", seconds=5)
    r.tick()
    r.io.kill("Steve")
    r.tick(2)
    marker = [s for s in r.sent(Summon) if s.entity_type == "minecraft:armor_stand"][0]
    r.run_for(7)                                         # a little past 5 s: the 1 s tick cadence
    assert any(s.block == "minecraft:air" and s.pos == (0, 64, 0) for s in r.sent(SetBlock))
    assert marker.uuid in {op.uuid for op in r.sent(Remove)}


def test_it_fades_early_once_the_player_comes_back(rig):
    r = rig("grave_marks")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.start_scenario("grave_marks", seconds=3600, found_within=3)
    r.tick()
    r.io.kill("Steve")
    r.tick()
    r.io.move("Steve", (50.0, 64.0, 0.0))                # he leaves it
    r.run_for(1)
    r.io.move("Steve", (0.3, 64.0, 0.3))                 # and comes back, within 3 blocks
    r.run_for(1)
    assert any(s.block == "minecraft:air" and s.pos == (0, 64, 0) for s in r.sent(SetBlock))


def test_on_kill_marks_where_the_killer_stands(rig):
    r = rig("grave_marks", extra={"dealer": DEALER})
    r.start()
    r.io.join("Steve", pos=(5.0, 64.0, 5.0), yaw=180.0)
    r.start_scenario("grave_marks", on="kill", seconds=3600)
    r.tick()
    r.io.add_entity("minecraft:zombie", (5.0, 64.0, 0.0), health=1)
    r.cast("Steve", "dealer")
    r.tick(2)
    skulls = [s for s in r.sent(SetBlock) if s.block == "minecraft:skeleton_skull"]
    assert len(skulls) == 1 and skulls[0].pos == (5, 64, 5)   # at the killer's own spot, not the mob's


def test_the_default_only_marks_deaths(rig):
    r = rig("grave_marks", extra={"dealer": DEALER})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.start_scenario("grave_marks")                       # on="death" by default
    r.tick()
    r.io.add_entity("minecraft:zombie", (0.0, 64.0, -5.0), health=1)
    r.cast("Steve", "dealer")
    r.tick(2)
    assert not r.sent(SetBlock)
    assert r.problems() == []


def test_grave_marks_bad_args_refused(rig):
    r = rig("grave_marks")
    r.start()
    reply = r.op("start", scenario="grave_marks", args={"seconds": 1})
    assert not reply["ok"] and "seconds" in reply["error"]


# ---- lucky_blocks -------------------------------------------------------------------------

def test_give_at_start(rig):
    r = rig("lucky_blocks")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.io.join("Alex", pos=(50.0, 64.0, 0.0))
    r.tick()
    iid = r.start_scenario("lucky_blocks", give=3)
    gives = sorted(c.text for c in r.sent(Command) if "sponge" in c.text)
    assert gives == sorted(['give Steve sponge[item_name="Lucky Block"] 3',
                            'give Alex sponge[item_name="Lucky Block"] 3'])
    assert r.result(iid)["summary"] == "lucky blocks on for @a; gave 3 each to Steve, Alex"
    assert r.problems() == []


def test_give_zero_gives_nothing(rig):
    r = rig("lucky_blocks")
    r.start()
    r.io.join("Steve")
    iid = r.start_scenario("lucky_blocks", give=0)
    assert not r.sent(Command)
    assert r.result(iid)["summary"] == "lucky blocks on for @a"


def test_any_sponge_counts_not_only_lucky_block(rig, monkeypatch):
    r = rig("lucky_blocks")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.start_scenario("lucky_blocks", give=0, players="Steve")
    r.tick()
    force(monkeypatch, "totem")
    assert r.io.mine("Steve", "minecraft:sponge", (1, 64, 0))
    r.tick(2)
    assert any(c.text == "give Steve totem_of_undying 1" for c in r.sent(Command))
    msgs = [m for m in r.sent(Message) if "You got a totem!" in m.text]
    assert msgs and msgs[0].players == ("Steve",)
    assert any(p.name == "minecraft:totem_of_undying" for p in r.sent(Particle))
    assert r.problems() == []


def test_cake_block(rig, monkeypatch):
    r = rig("lucky_blocks")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.start_scenario("lucky_blocks", give=0)
    r.tick()
    force(monkeypatch, "cake")
    r.io.mine("Steve", "minecraft:sponge", (1, 64, 0))
    r.tick(2)
    assert any(s.block == "minecraft:cake" for s in r.sent(SetBlock))


def test_wolf_spawns_named_owner_by_the_players_name(rig, monkeypatch):
    r = rig("lucky_blocks")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.start_scenario("lucky_blocks", give=0)
    r.tick()
    force(monkeypatch, "wolf")
    r.io.mine("Steve", "minecraft:sponge", (1, 64, 0))
    r.tick(2)
    wolves = [s for s in r.sent(Summon) if s.entity_type == "minecraft:wolf"]
    assert len(wolves) == 1 and 'Owner:"Steve"' in wolves[0].nbt


def test_zombies_trap_summons_three(rig, monkeypatch):
    r = rig("lucky_blocks")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.start_scenario("lucky_blocks", give=0)
    r.tick()
    force(monkeypatch, "zombies")
    r.io.mine("Steve", "minecraft:sponge", (1, 64, 0))
    r.tick(2)
    assert len([s for s in r.sent(Summon) if s.entity_type == "minecraft:zombie"]) == 3


def test_buffs_gives_speed_and_haste(rig, monkeypatch):
    r = rig("lucky_blocks")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.start_scenario("lucky_blocks", give=0)
    r.tick()
    force(monkeypatch, "buffs")
    r.io.mine("Steve", "minecraft:sponge", (1, 64, 0))
    r.tick(2)
    effects = {e.effect: e for e in r.sent(GiveEffect)}
    assert effects["minecraft:speed"].amplifier == 2 and effects["minecraft:haste"].amplifier == 2


def test_cobweb_trap_fills_round_the_player(rig, monkeypatch):
    r = rig("lucky_blocks")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.start_scenario("lucky_blocks", give=0)
    r.tick()
    force(monkeypatch, "cobweb")
    r.io.mine("Steve", "minecraft:sponge", (1, 64, 0))
    r.tick(2)
    assert [f for f in r.sent(Fill) if f.block == "minecraft:cobweb"]
    for x, y, z in ((-1, 64, -1), (0, 64, 0), (1, 65, 1)):           # corners and middle of the 3x2x3
        assert r.io.block_at("minecraft:overworld", x, y, z) == "minecraft:cobweb"


def test_lightning_strikes_near_the_player(rig, monkeypatch):
    r = rig("lucky_blocks")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.start_scenario("lucky_blocks", give=0)
    r.tick()
    force(monkeypatch, "lightning")
    r.io.mine("Steve", "minecraft:sponge", (1, 64, 0))
    r.tick(2)
    assert any(s.entity_type == "minecraft:lightning_bolt" for s in r.sent(Summon))


def test_only_the_given_players_breaking_counts(rig, monkeypatch):
    r = rig("lucky_blocks")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.start_scenario("lucky_blocks", give=0, players="Alex")
    r.tick()
    force(monkeypatch, "totem")
    assert r.io.mine("Steve", "minecraft:sponge", (1, 64, 0))    # the watch covers sponge regardless of who
    r.tick(2)
    assert not r.sent(Command)                                   # but Steve is not in players="Alex"


def test_lucky_blocks_bad_args_refused(rig):
    r = rig("lucky_blocks")
    r.start()
    reply = r.op("start", scenario="lucky_blocks", args={"give": 100})
    assert not reply["ok"] and "give" in reply["error"]


# ---- sneaky_creepers ----------------------------------------------------------------------

def test_new_creepers_get_a_longer_fuse_once(rig):
    r = rig("sneaky_creepers")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    c1 = r.io.add_entity("minecraft:creeper", (2.0, 64.0, 0.0))
    r.start_scenario("sneaky_creepers", fuse=5, invisible_within=10, hiss_within=0)
    r.run_for(1.2)
    assert any("Fuse:100s" in d for d in r.io.by_uuid[c1].data)
    c2 = r.io.add_entity("minecraft:creeper", (3.0, 64.0, 0.0))
    r.run_for(1.2)
    assert any("Fuse:100s" in d for d in r.io.by_uuid[c2].data)       # a later one too
    assert len([d for d in r.io.by_uuid[c1].data if "Fuse" in d]) == 1   # but not a second time
    assert r.problems() == []


def test_near_creepers_turn_invisible(rig):
    r = rig("sneaky_creepers")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    near = r.io.add_entity("minecraft:creeper", (5.0, 64.0, 0.0))
    far = r.io.add_entity("minecraft:creeper", (40.0, 64.0, 0.0))
    r.start_scenario("sneaky_creepers", invisible_within=10, hiss_within=0)
    r.run_for(1.2)
    effects = {e.target for e in r.sent(GiveEffect) if e.effect == "minecraft:invisibility"}
    assert near in effects and far not in effects


def test_hiss_plays_near_the_player_not_far(rig):
    r = rig("sneaky_creepers")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.io.add_entity("minecraft:creeper", (2.0, 64.0, 0.0))
    r.io.add_entity("minecraft:creeper", (8.0, 64.0, 0.0))
    r.start_scenario("sneaky_creepers", invisible_within=10, hiss_within=4)
    r.run_for(1.2)
    hisses = [s for s in r.sent(Sound) if s.name == "minecraft:entity.creeper.primed"]
    assert any(abs(s.pos[0] - 2.0) < 0.01 for s in hisses)
    assert not any(abs(s.pos[0] - 8.0) < 0.01 for s in hisses)


def test_stop_puts_the_fuse_back(rig):
    r = rig("sneaky_creepers")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    c = r.io.add_entity("minecraft:creeper", (2.0, 64.0, 0.0))
    iid = r.start_scenario("sneaky_creepers", fuse=5)
    r.run_for(1.2)
    assert r.op("stop", instance=iid)["ok"]
    r.tick()
    assert r.io.by_uuid[c].data[-1] == "{Fuse:30s}"
    assert r.problems() == []


def test_sneaky_creepers_bad_args_refused(rig):
    r = rig("sneaky_creepers")
    r.start()
    reply = r.op("start", scenario="sneaky_creepers", args={"fuse": 20})
    assert not reply["ok"] and "fuse" in reply["error"]


# ---- walking_dead -------------------------------------------------------------------------

def test_new_zombies_get_can_break_doors(rig):
    r = rig("walking_dead")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    z = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -20.0))
    r.start_scenario("walking_dead", friends=0)
    r.run_for(1.2)
    assert any("CanBreakDoors:1b" in d for d in r.io.by_uuid[z].data)


def test_the_whole_zombie_family_gets_doors(rig):
    r = rig("walking_dead")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    kinds = ("minecraft:zombie", "minecraft:husk", "minecraft:drowned", "minecraft:zombie_villager")
    made = {k: r.io.add_entity(k, (0.0, 64.0, -15.0)) for k in kinds}
    r.start_scenario("walking_dead", friends=0)
    r.run_for(1.2)
    for k, u in made.items():
        assert any("CanBreakDoors:1b" in d for d in r.io.by_uuid[u].data), k


def test_speed_lunges_close_and_is_calm_further_out(rig):
    r = rig("walking_dead")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    close = r.io.add_entity("minecraft:zombie", (4.0, 64.0, 0.0))    # within 6: lunges
    mid = r.io.add_entity("minecraft:zombie", (10.0, 64.0, 0.0))     # within 12: back to normal
    far = r.io.add_entity("minecraft:zombie", (30.0, 64.0, 0.0))     # further: the slow walker speed
    r.start_scenario("walking_dead", walk_speed=0.17, friends=0)
    r.run_for(1.2)

    def speed(u):
        return r.io.attribute_value(u, "minecraft:movement_speed", base=VANILLA_SPEED)

    assert speed(close) == pytest.approx(0.3, abs=0.01)
    assert speed(mid) == pytest.approx(0.23, abs=0.01)
    assert speed(far) == pytest.approx(0.17, abs=0.01)


def test_follow_range_matches_the_players_noise(rig):
    r = rig("walking_dead")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    z = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0))
    r.start_scenario("walking_dead", sneak_range=5, walk_range=16, sprint_range=40, friends=0)
    r.run_for(1.2)

    def follow(u):
        return r.io.attribute_value(u, "minecraft:follow_range", base=VANILLA_FOLLOW)

    assert follow(z) == pytest.approx(16, abs=0.01)               # walking: walk_range
    r.io.sneak("Steve", True)
    r.run_for(1.2)
    assert follow(z) == pytest.approx(5, abs=0.01)                # sneaking: sneak_range
    r.io.sneak("Steve", False)
    r.io.players["Steve"].sprinting = True
    r.run_for(1.2)
    assert follow(z) == pytest.approx(40, abs=0.01)               # sprinting: sprint_range


def test_friends_bring_a_companion(rig):
    r = rig("walking_dead")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0))
    r.start_scenario("walking_dead", friends=1)                   # always, this tick
    r.run_for(1.2)
    assert len([s for s in r.sent(Summon) if s.entity_type == "minecraft:zombie"]) == 1


def test_no_friends_when_the_chance_is_zero(rig):
    r = rig("walking_dead")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0))
    r.start_scenario("walking_dead", friends=0)
    r.run_for(1.2)
    assert not r.sent(Summon)
    assert r.problems() == []


def test_walking_dead_bad_args_refused(rig):
    r = rig("walking_dead")
    r.start()
    reply = r.op("start", scenario="walking_dead", args={"walk_speed": 10})
    assert not reply["ok"] and "walk_speed" in reply["error"]
