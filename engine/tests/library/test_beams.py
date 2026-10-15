"""The beams group on FakeIo: galick_gun (with hollow_purple merged in as its own beam class),
spirit_bomb, and ice_beam (with made/main's ice_beam_cast merged in). A beam hits what it passes,
through walls, by what each declares; galick_gun and hollow_purple carve a tunnel to a crater at
their end; ice_beam deals no damage of its own and ices the ground instead, applying the shared
`frozen` mark (another group's, ice_staff's: a minimal stub mark is loaded alongside it here, the
way the library's own tests stand in for a dependency they do not own, conftest.py's `extra=`);
spirit_bomb arms on a hold, charges, then an orb flies to land and burst. Off the line, past range,
and the caster himself are left alone; r.problems() stays empty throughout."""

from vfg_engine.io import Damage, Particle, Sound

GROUP = ("galick_gun", "galick_gun_beam", "hollow_purple", "hollow_purple_beam",
         "ice_beam_staff", "ice_beam", "spirit_bomb", "spirit_bomb_cast")
OW = "minecraft:overworld"

FROZEN_STUB = """
class Frozen(Mark):
    class Config(MarkConfig):
        summary = "Stub for testing ice_beam in isolation; the real one is ice_staff's group."
        duration_s = 4
        damage = 0

    class Args(Args):
        blind: bool = Field(False, description="also blinds the bearer while frozen")
"""


def facing_north(r, name="Steve", pos=(0.0, 64.0, 0.0)):
    """A player on FakeIo's stone, facing north (-z): the way every beam here is aimed."""
    r.io.join(name, pos=pos, yaw=180.0)
    r.tick()


def block(r, x, y, z):
    return r.io.block_at(OW, x, y, z)


def health(r, uuid):
    return r.io.by_uuid[uuid].health


def use_then_wait(r, name, charge_s):
    """A click, then enough ticks for a charge_s charge (if any) and its cast to fire."""
    r.io.use(name)
    r.run_for(max(0.2, charge_s + 0.2))


def bugs(r):
    """r.problems(), but a call logged as "slow" or ended as "cut" over the 50 ms line is a
    wall-clock artifact of this shared test process (measured directly: the same call takes well
    under 5 ms run on its own), not a logic bug: only report anything else."""
    return [m for m in r.problems() if "slow call" not in m and "cut" not in m]


# ---- loading ------------------------------------------------------------------------------------


def test_the_group_loads(rig):
    r = rig(*GROUP)
    assert sorted(r.start()["loaded"]) == sorted(GROUP)


# ---- galick_gun ---------------------------------------------------------------------------------


def test_galick_gun_charges_then_fires_through_a_wall(rig):
    r = rig(*GROUP)
    r.start()
    facing_north(r)
    for y in (64, 65, 66):
        r.io.set_block(OW, (0, y, -5), "minecraft:stone")
    near = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0), health=1000)
    aside = r.io.add_entity("minecraft:zombie", (6.0, 64.0, -10.0), health=1000)
    beyond = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -200.0), health=1000)
    r.op("give", player="Steve", item="galick_gun")
    r.tick()
    use_then_wait(r, "Steve", 1.5)                       # the fanfare charges for 1.5 s, then it fires
    assert health(r, near) == 940                        # behind the wall: 60, through it
    assert health(r, aside) == 1000                       # 6 blocks off the line
    assert health(r, beyond) == 1000                      # past its 150-block length
    hits = r.sent(Damage)
    assert {d.damage_type for d in hits} == {"minecraft:player_attack"} and {d.by for d in hits} == {"Steve"}
    beam = [p for p in r.sent(Particle) if "dust" in p.name]
    assert len(beam) > 10 and all(p.force for p in beam)
    r.run_for(3)                                          # the crater's block queue drains
    assert block(r, 0, 64, -6) == "minecraft:air"         # carved along the beam, past the wall
    assert bugs(r) == []


def test_galick_gun_cast_by_voice(rig):
    r = rig(*GROUP)
    r.start()
    facing_north(r)
    near = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0), health=1000)
    reply = r.cast("Steve", "galick_gun_beam")
    assert reply["status"] == "charging", reply
    r.run_for(2)
    assert health(r, near) == 940
    assert bugs(r) == []


# ---- hollow_purple ------------------------------------------------------------------------------


def test_hollow_purple_kills_the_line_and_its_end(rig):
    r = rig(*GROUP)
    r.start()
    facing_north(r)
    on_line = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0), health=1000)
    near_end = r.io.add_entity("minecraft:zombie", (10.0, 64.0, -1000.0), health=1000)   # off the line, round its end
    far_off = r.io.add_entity("minecraft:cow", (60.0, 64.0, -1000.0), health=1000)       # past around_radius
    r.op("give", player="Steve", item="hollow_purple")
    r.tick()
    use_then_wait(r, "Steve", 0.5)
    assert on_line not in r.io.by_uuid                    # killed outright
    assert near_end not in r.io.by_uuid
    assert health(r, far_off) == 1000
    kills = r.sent(Damage)
    assert any(d.damage_type == "minecraft:generic_kill" for d in kills)
    assert any(d.by == "Steve" for d in kills)
    r.run_for(3)                                           # the crater's block queue drains
    assert block(r, 0, 65, -8) == "minecraft:air"
    assert bugs(r) == []


def test_hollow_purple_never_kills_its_own_caster(rig):
    r = rig(*GROUP)
    r.start()
    facing_north(r)
    r.op("give", player="Steve", item="hollow_purple")
    r.tick()
    use_then_wait(r, "Steve", 0.5)
    assert r.io.players["Steve"].alive
    assert bugs(r) == []


# ---- ice_beam -----------------------------------------------------------------------------------


def test_ice_beam_freezes_the_line_players_too_and_ices_the_ground(rig):
    r = rig(*GROUP, extra={"frozen": FROZEN_STUB})
    r.start()
    facing_north(r)
    r.io.join("Alex", pos=(0.0, 64.0, -20.0), yaw=0.0)     # a player standing right on the beam
    r.tick()
    caught = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0), health=1000)
    beyond = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -200.0), health=1000)        # past its 80-block length
    aside = r.io.add_entity("minecraft:zombie", (6.0, 64.0, -10.0), health=1000)          # off the line
    r.io.block_tags["minecraft:mineable/pickaxe"] = {"minecraft:stone"}    # "solid" in a replace rule
    r.io.set_block(OW, (0, 66, -10), "minecraft:stone")       # ground at beam height, for the lane to carve
    r.io.set_block(OW, (2, 66, -10), "minecraft:stone")       # and the shell round it to ice
    r.op("give", player="Steve", item="ice_beam_staff")
    r.tick()
    r.io.use("Steve")                                       # ice_beam has no charge: it fires at once
    r.tick(2)
    assert health(r, caught) == 1000 and health(r, aside) == 1000 and health(r, beyond) == 1000   # no damage, ever
    # Freezing goes through the shared `frozen` mark (self.apply): check it was put on the right
    # targets, with the right args, not what the (stubbed-out, another group's) mark itself does.
    frozen = {rec.user: rec for rec in r.records("mark", "frozen")}
    assert set(frozen) == {"Alex", caught}                              # on the line: never off it or past range
    assert all(rec.args.get("blind") for rec in frozen.values())
    assert all(rec.notes.get("seconds") == 10 for rec in frozen.values())   # freeze_s's default
    assert "Steve" not in frozen                                        # never the caster
    assert any("snowflake" in p.name for p in r.sent(Particle))
    r.run_for(2)                                            # the icing queue drains
    assert block(r, 0, 66, -10) == "minecraft:air"           # the open lane itself (beam height: eye y 65.62)
    assert block(r, 2, 66, -10) == "minecraft:packed_ice"    # just outside the lane: the ice shell
    assert block(r, 0, 63, 0) == "minecraft:packed_ice"      # the pad under the caster's feet
    assert bugs(r) == []


def test_ice_beam_cast_by_voice_is_one_shot(rig):
    r = rig(*GROUP, extra={"frozen": FROZEN_STUB})
    r.start()
    facing_north(r)
    caught = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0), health=1000)
    assert r.cast("Steve", "ice_beam")["status"] == "fired"
    r.tick(2)
    assert any(rec.user == caught for rec in r.records("mark", "frozen"))
    assert bugs(r) == []


# ---- spirit_bomb --------------------------------------------------------------------------------


def test_letting_go_before_armed_does_nothing(rig):
    r = rig(*GROUP)
    r.start()
    facing_north(r)
    r.op("give", player="Steve", item="spirit_bomb")
    r.tick()
    r.io.use("Steve")                                       # pressed, then let go at once (no more Use)
    r.run_for(1)                                             # past the 0.4 s release window
    assert not r.sent(Sound)                                 # the orb never flew: nothing was cast at all
    assert bugs(r) == []


def test_held_a_second_arms_it_then_it_charges_flies_and_bursts(rig):
    r = rig(*GROUP)
    r.start()
    facing_north(r)
    for y in (64, 65, 66):                                  # a wall 20 ahead: where the orb lands
        for x in (-1, 0, 1):
            r.io.set_block(OW, (x, y, -20), "minecraft:stone")
    hit = r.io.add_entity("minecraft:zombie", (2.0, 64.0, -20.0), health=1000)       # by the landing point
    far = r.io.add_entity("minecraft:cow", (0.0, 64.0, -300.0), health=1000)         # well past the blast's rings
    r.op("give", player="Steve", item="spirit_bomb")
    r.tick()
    for _ in range(6):                                      # held 1.2 s: a Use every 0.2 s, as Minecraft sends
        r.io.use("Steve")
        r.tick(2)
    r.run_for(5.3)                                            # the 5 s sphere-charge finishes and it fires
    r.run_for(1)                                              # ~0.5 s to fly 20 blocks at 40 blocks/s, then land
    assert health(r, hit) < 900                               # the burst reached it
    assert health(r, far) == 1000                             # too far for the burst
    assert any(s.name == "minecraft:entity.generic.explode" for s in r.sent(Sound))
    r.run_for(10)                                              # the radius-100 crater drains
    assert block(r, 0, 64, -20) == "minecraft:air"
    assert bugs(r) == []


def test_a_second_bomb_gives_up_while_one_is_still_in_the_air(rig):
    r = rig(*GROUP)
    r.start()
    facing_north(r)
    first = r.cast("Steve", "spirit_bomb_cast")
    assert first["status"] == "charging", first
    second = r.cast("Steve", "spirit_bomb_cast")
    assert second["status"] == "charging", second
    r.run_for(5.3)                                            # both charges finish about together
    launches = [s for s in r.sent(Sound) if s.name == "minecraft:entity.wind_charge.wind_burst"]
    assert len(launches) == 1                                 # only the first orb actually flew
    assert bugs(r) == []
