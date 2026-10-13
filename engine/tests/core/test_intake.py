"""What the engine takes from the datapack's queue (vfg_engine.intake): its own damage reported
back as a hit never loops, stale input is not acted on, and a page handed over again after a crash
is not acted on twice, while a new world's counter is accepted."""

import json

from vfg_engine.engine import Engine
from vfg_engine.io import Command, Damage, RawEvent

LEECH = '''
class Leech(Ability):
    class Config(AbilityConfig):
        summary = "Every hit you land deals 2 more."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = UNTIL_STOPPED
        damage = 2

    def start(self, ctx):
        self.listen(Hit, "on_hit")
        self.listen(Hurt, "on_hurt", about="anyone")

    def on_hit(self, ctx, ev):
        self.damage(ev.target, 2)

    def on_hurt(self, ctx, ev):
        self.command("say hurt %s engine=%s" % (ev.who, ev.engine))
'''

PRESSER = '''
class Presser(Item):
    class Config(ItemConfig):
        summary = "Says each use."
        base = "carrot_on_a_stick"
        name = "Presser"
        active_in = "mainhand"

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        self.command("say used %d" % ev.n)


class Doom(Mark):
    class Config(MarkConfig):
        summary = "Ends on death."
        duration_s = 600
        damage = 0

    def start(self, ctx):
        self.modifier("scale", -0.5, "add_multiplied_total")
'''


def _leech(rig):
    r = rig({"leech": LEECH})
    r.start()
    r.io.join("Steve")
    zombie = r.io.add_entity(pos=(0.0, 64.0, 2.0), health=100.0)
    r.tick()
    r.op("grant", who="Steve", ability="leech")
    return r, zombie


def test_the_engines_own_damage_does_not_trigger_a_hit_loop(rig):
    r, zombie = _leech(rig)
    r.io.hit("Steve", zombie)                     # one real hit
    r.tick(10)
    assert len(r.io.ops_of(Damage)) == 1          # answered once, not for ever
    assert r.io.entities[zombie].health == 98.0
    r.io.hit("Steve", zombie)                     # a later real hit is still heard
    r.tick(3)
    assert len(r.io.ops_of(Damage)) == 2


def test_without_it_fakeio_reproduces_the_loop(rig, monkeypatch):
    r, zombie = _leech(rig)
    monkeypatch.setattr(r.engine.intake, "mark", lambda ev, now: ev)
    r.io.hit("Steve", zombie)
    r.tick(10)
    assert len(r.io.ops_of(Damage)) >= 9          # the loop the intake stops


def test_a_hurt_the_engine_caused_is_flagged(rig):
    r, _ = _leech(rig)
    r.io.join("Alex", pos=(0.0, 64.0, 2.0))
    r.tick()
    r.io.hit("Steve", "Alex")                     # PvP: a real hit on Alex, then the leech's 2
    r.tick(3)
    said = [op.text for op in r.io.ops_of(Command)]
    assert said == ["say hurt Alex engine=True"]
    assert len(r.io.ops_of(Damage)) == 1


def test_stale_player_input_is_not_acted_on_and_deaths_are_kept(rig):
    r = rig({"presser": PRESSER})
    r.start()
    r.io.join("Steve")
    r.io.hold("Steve", "presser")
    r.tick()
    r.op("apply", who="Steve", mark="doom")
    r.tick()
    # the engine was down for a minute: a use and a death were queued then
    r.io.use("Steve")
    r.io.kill("Steve")
    r.io.respawn("Steve")
    r.io.hold("Steve", "presser")
    r.io.game_tick += 60 * 20
    r.io.use("Steve")                             # and one now
    r.tick(2)
    said = [op.text for op in r.io.ops_of(Command)]
    assert len(said) == 1 and said[0].startswith("say used ")
    assert not r.records("mark", "doom")          # the death was acted on
    assert any("stale event(s) (use" in m for m in r.lines())


def _first_life(rig):
    r = rig({"presser": PRESSER})
    r.start()
    r.io.join("Steve")
    r.io.hold("Steve", "presser")
    r.tick()
    for _ in range(3):
        r.io.use("Steve")
    taken = list(r.io._events)
    r.tick()
    r.run_for(1.2)                                # saved
    assert [op.text for op in r.io.ops_of(Command)] == ["say used 1", "say used 2", "say used 3"]
    assert json.loads((r.tmp / "state.json").read_text())["last_n"] == 3
    return r, taken


def _restart(r):
    r.io.sent.clear()
    r.engine = Engine(r.io, r.settings, clock=r.clock)    # the old one crashed: no close
    r.engine.start()


def test_a_page_handed_again_after_a_crash_is_not_acted_on_twice(rig):
    r, taken = _first_life(rig)
    r.io.hand_again(taken)
    _restart(r)
    assert r.io.last_n == 3                       # the Io was told where the engine was
    r.io.use("Steve")
    r.tick(2)
    assert [op.text for op in r.io.ops_of(Command)] == ["say used 4"]


def test_the_core_drops_the_page_itself_when_the_io_does_not_dedupe(rig):
    r, taken = _first_life(rig)
    r.io.hand_again(taken)
    r.io.last_n = None                            # an Io with no counter of its own
    _restart(r)
    r.io.use("Steve")
    r.tick(2)
    assert [op.text for op in r.io.ops_of(Command)] == ["say used 4"]
    assert any("handed over again" in m for m in r.lines())


def test_a_new_worlds_counter_is_accepted(rig):
    r, _ = _first_life(rig)
    r.io.new_world()
    _restart(r)
    assert r.io.last_n == 0
    r.io.use("Steve")
    r.tick(2)
    assert [op.text for op in r.io.ops_of(Command)] == ["say used 1"]
    assert any("a new world" in m for m in r.lines())


def test_a_counter_far_below_is_a_new_one_even_unreported(rig):
    r, _ = _first_life(rig)
    r.engine.intake.last_n = 5000
    r.engine.save(force=True)
    r.io.new_world()
    r.io.last_n = None
    _restart(r)
    r.io.use("Steve")
    r.tick(2)
    assert [op.text for op in r.io.ops_of(Command)] == ["say used 1"]


def test_screen_keeps_order_and_numbers():
    from vfg_engine.intake import Intake
    i = Intake(stale_s=5)
    i.last_n, i.skip_upto = 10, 10
    raw = [RawEvent(n, "use", "Steve", {}, 1000) for n in (9, 10, 11, 12)]
    assert [r.n for r in i.screen(raw, 1000)] == [11, 12]
    assert i.last_n == 12 and i.skip_upto is None
    old = [RawEvent(13, "hit", "Steve", {}, 0), RawEvent(14, "death", "Steve", {}, 0),
           RawEvent(15, "use", "Steve", {}, 990)]
    assert [r.n for r in i.screen(old, 1000)] == [14, 15]


KILLER = '''
class Killer(Ability):
    class Config(AbilityConfig):
        summary = "Every hit you land deals 5 more; says each kill."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = UNTIL_STOPPED
        damage = 5

    def start(self, ctx):
        self.listen(Hit, "on_hit")
        self.listen(Kill, "on_kill")

    def on_hit(self, ctx, ev):
        self.damage(ev.target, 5)

    def on_kill(self, ctx, ev):
        self.command("say kill %s engine=%s" % (ev.target_type, ev.engine))
'''


def test_a_mob_killed_by_the_engines_damage_is_flagged_and_its_killing_blow_is_not_a_hit(rig):
    r = rig({"killer": KILLER})
    r.start()
    r.io.join("Steve")
    zombie = r.io.add_entity(pos=(0.0, 64.0, 2.0), health=4.0)
    r.tick()
    r.op("grant", who="Steve", ability="killer")
    r.io.hit("Steve", zombie)              # a real hit; the 5 it answers with kills the zombie
    r.tick(3)
    said = [op.text for op in r.io.ops_of(Command)]
    assert said == ["say kill minecraft:zombie engine=True"]
    assert len(r.io.ops_of(Damage)) == 1
    assert r.records("ability", "killer")  # the killing blow's hit (no target) did not reach on_hit
