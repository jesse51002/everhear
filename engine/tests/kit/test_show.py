"""beam_line / charge_frames / melody / countdown / speak / fly: what each sends and when, on the
kit's timers; a newer tune cuts the older off (a record too); a countdown calls the class back at
zero and is called off by a stop; a flight moves its displays each tick, takes them away and calls
back where it landed."""

import pytest

from vfg_engine.io import Message, Particle, Remove, Sound, StopSound, Summon, Teleport
from vfg_engine.kit.show import note_pitch, note_sound, span

SHOW = '''
class Show(Scenario):

    class Config(ScenarioConfig):
        summary = "Shows things round Steve."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        what: str = Field("beam", description="what to show")

    class State(State):
        landed: list = []
        boom: bool = False
        lasts: float = 0

    def start(self, ctx):
        w = self.args.what
        if w == "beam":
            beam_line(self, ctx, (0, 65, 0), (0, 65, 1000), "minecraft:end_rod", every=1)
        elif w == "tune":
            self.state.lasts = melody(self, ctx, "galick_gun", at="Steve")
        elif w == "everyone":
            melody(self, ctx, [(0, "bell", 12, 1), (0.5, "bell", 24, 1)], at=(0, 64, 0), to="everyone")
        elif w == "alex":
            melody(self, ctx, [(0, "bell", 12, 1)], at=(0, 64, 0), to="@r")
            melody(self, ctx, [(0, "bell", 12, 1)], at=(0, 64, 0), to="Alex", key="other")
        elif w == "boss":
            self.state.lasts = melody(self, ctx, "boss", at=(0, 64, 0), to="everyone", loop=True)
        elif w == "count":
            countdown(self, ctx, 12, "Nuke", then="zero")
        elif w == "speak":
            speak(self, ctx, "You will not leave.")
            speak(self, ctx, "Kneel.", voice="<The Radiant One>", color="gold", to="Steve", where="title")
        elif w in ("orb", "block", "meteor"):
            self.state.lasts = fly(self, ctx, (0, 70, 0), (0, 70, 30), speed=30, look=w, size=3,
                                   blocks="70% magma_block 30% obsidian", then="landed")

    def cut(self, ctx):
        melody(self, ctx, None)

    def zero(self, ctx):
        self.state.boom = True

    def landed(self, ctx, at):
        self.state.landed = at
'''

CHARGE = '''
class Bomb(Ability):
    class Config(AbilityConfig):
        summary = "Charges a sphere for a second, then fires."
        scale_per_point = 0
        charge_s = 1
        cooldown_s = 0
        duration_s = 1
        damage = 0

    def on_charge(self, ctx, ev):
        at = charge_frames(self, ctx, ev.progress, "sphere", size=4)
        self.message("charge %.1f %d" % (ev.progress, round(at[1])))

    def on_cast(self, ctx, ev):
        self.message("fired")
'''


def show(rig, what, **join):
    r = rig({"show": SHOW})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.io.join("Alex", pos=(500.0, 64.0, 0.0))
    r.tick()
    iid = r.start_scenario("show", what=what)
    return r, iid


def test_a_long_beam_is_spaced_out_and_forced(rig):
    r, _ = show(rig, "beam")
    parts = r.sent(Particle)
    assert len(parts) == 201 and all(p.force for p in parts)
    assert parts[0].pos == (0.0, 65.0, 0.0) and parts[-1].pos == (0.0, 65.0, 1000.0)


def test_charge_frames_draw_each_tick_of_the_charge(rig):
    r = rig({"bomb": CHARGE})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    r.cast("Steve", "bomb")
    r.run_for(1.5)
    texts = [m.text for m in r.sent(Message)]
    charges = [t for t in texts if t.startswith("charge")]
    assert len(charges) >= 8 and all(t.endswith(" 70") for t in charges)    # over the head: 64 + 4 + 2.5
    assert texts[-1] == "fired" and r.sent(Particle) and r.problems() == []


def test_a_tune_plays_note_by_note_and_follows_the_player(rig):
    r, iid = show(rig, "tune")
    assert r.rt.records[iid].state.lasts == pytest.approx(2.0)
    first = len(r.sent(Sound))
    assert first == 2                                   # the two notes at 0 s
    r.io.move("Steve", (10.0, 64.0, 0.0))
    r.run_for(2)
    sounds = r.sent(Sound)
    assert len(sounds) == 15 and sounds[-1].pos == (10.0, 64.0, 0.0)
    assert sounds[0].name == "minecraft:block.note_block.guitar" and sounds[0].pitch == note_pitch(0)
    assert r.problems() == []


def test_a_newer_tune_cuts_the_older_off(rig):
    r, iid = show(rig, "tune")
    r.run_for(0.3)
    played = len(r.sent(Sound))
    r.rt.call(r.rt.records[iid], "cut")
    r.run_for(2)
    assert len(r.sent(Sound)) == played


def test_everyone_hears_it_where_they_stand(rig):
    r, _ = show(rig, "everyone")
    heard = {(s.to, s.pos) for s in r.sent(Sound)}
    assert heard == {(("Steve",), (0.0, 64.0, 0.0)), (("Alex",), (500.0, 64.0, 0.0))}


def test_a_tune_for_some_players_only(rig):
    r, _ = show(rig, "alex")
    heard = [s.to for s in r.sent(Sound)]
    assert len(heard) == 2 and heard[0] in (("Steve",), ("Alex",)) and heard[1] == ("Alex",)


def test_a_record_is_stopped_when_cut_and_a_loop_plays_again(rig):
    r, iid = show(rig, "boss")
    assert r.rt.records[iid].state.lasts == 148.0
    assert any(s.name == "minecraft:music_disc.pigstep" for s in r.sent(Sound))
    r.run_for(150)
    assert len([s for s in r.sent(Sound) if s.name == "minecraft:music_disc.pigstep"]) == 4   # again, for both
    before = len(r.sent(StopSound))                  # each new round stops the last one's record first
    r.rt.call(r.rt.records[iid], "cut")
    r.tick()
    stops = r.sent(StopSound)
    assert len(stops) == before + 1 and stops[-1].name == "minecraft:music_disc.pigstep"


def test_a_countdown_says_when_and_calls_back_at_zero(rig):
    r, iid = show(rig, "count")
    texts = [m.text for m in r.sent(Message)]
    assert texts == ["§c§lNuke in 12s."]
    r.run_for(2.05)
    assert "§c§lNuke in 10s." in [m.text for m in r.sent(Message)]
    r.run_for(10)
    titles = [m.text for m in r.sent(Message) if m.where == "title" and m.players == ("Steve",)]
    assert len(titles) == 10 and titles[0] == "§c§l10" and titles[-1] == "§c§l1"
    assert r.rt.records[iid].state.boom is True and r.problems() == []


def test_a_countdown_is_called_off_by_a_stop(rig):
    r, iid = show(rig, "count")
    r.run_for(1)
    r.op("stop", instance=iid)
    r.run_for(15)
    assert [m.text for m in r.sent(Message)] == ["§c§lNuke in 12s."]


def test_speak(rig):
    r, _ = show(rig, "speak")
    a, b = r.sent(Message)
    assert (a.text, a.players, a.where) == ("§4[Demon Lord] You will not leave.", (), "chat")
    assert (b.text, b.players, b.where) == ("§6<The Radiant One> Kneel.", ("Steve",), "title")


@pytest.mark.parametrize("look, displays", [("orb", 0), ("block", 1), ("meteor", 19)])
def test_a_flight_moves_each_tick_cleans_up_and_calls_back(rig, look, displays):
    r, iid = show(rig, look)
    assert r.rt.records[iid].state.lasts == pytest.approx(1.0)       # 30 blocks at 30 a second
    spawned = [s.uuid for s in r.sent(Summon) if s.entity_type == "minecraft:block_display"]
    assert len(spawned) == displays
    r.run_for(1.2)
    assert len(r.sent(Teleport)) == 10 * displays
    gone = [op.uuid for op in r.sent(Remove)] + [c.text.split()[1] for c in r.io.sent
                                                 if type(c).__name__ == "Command" and c.text.startswith("kill ")]
    assert sorted(gone) == sorted(spawned)
    assert r.rt.records[iid].state.landed == [0.0, 70.0, 30.0] and r.problems() == []


def test_notes_and_spans():
    assert note_sound("bell") == "minecraft:block.note_block.bell"
    assert note_sound("entity.generic.explode") == "minecraft:entity.generic.explode"
    assert note_pitch(12) == 1.0 and note_pitch(0) == 0.5 and note_pitch(24) == 2.0
    assert span(300) == "5m 00s" and span(30) == "30s"


def test_a_flight_keeps_to_the_clock_when_ticks_come_late(rig):
    r = rig({"show": SHOW})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    iid = r.start_scenario("show", what="meteor")
    r.tick(7, dt=0.15)                             # 1.05 s of slow ticks: fewer steps, the same flight
    assert r.rt.records[iid].state.landed == [0.0, 70.0, 30.0]
    assert len(r.sent(Teleport)) == 19 * 7
