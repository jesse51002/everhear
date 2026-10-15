"""held_use / sneak_gesture / steps / chunks_entered / director, fed by FakeIo's events and
snapshots through the engine: a held right-click pressed, repeating, charged and let go; three
quick crouches; a step every two blocks and a landing; each chunk once; a clock that counts only
time online."""

from vfg_engine.io import Message

GUN = '''
class Gun(Item):
    class Config(ItemConfig):
        summary = "Hold to fire; held a second, it charges."
        base = "carrot_on_a_stick"
        name = "Gun"
        active_in = "mainhand"

    def start(self, ctx):
        self.listen(Use, "on_use")

    def on_use(self, ctx, ev):
        held_use(self, ctx, ev, hold_s=1.0, repeat_s=0.2)

    def on_press(self, ctx):
        self.message("press")

    def on_hold(self, ctx):
        self.message("hold")

    def on_charged(self, ctx):
        self.message("charged")

    def on_release(self, ctx, held_s):
        self.message("release %.1f" % held_s)
'''

WATCH = '''
class Watch(Scenario):
    every = 0.1

    class Config(ScenarioConfig):
        summary = "Watches what players do."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        mode: str = Field("three_taps", description="the gesture")

    class State(State):
        steps: list = []
        chunks: list = []
        events: list = []

    def start(self, ctx):
        self.listen(Sneak, "on_sneak")
        self.listen(Unsneak, "on_sneak")

    def on_sneak(self, ctx, ev):
        if sneak_gesture(self, ctx, ev, self.args.mode):
            self.message("gesture")

    def on_sneaking(self, ctx):
        self.message("sneaking")

    def tick(self, ctx):
        for name in players(ctx):
            s = steps(self, ctx, name)
            if s:
                self.state.steps = self.state.steps + [[round(s.pos[0], 1), s.fell]]
            for box in chunks_entered(self, ctx, name):
                self.state.chunks = self.state.chunks + [list(box[0])]
        for event, name in director(self, ctx, ["whisper", "never"], [1, 0], min_s=5, max_s=5):
            self.state.events = self.state.events + [[event, name, round(ctx.now - 1000, 1)]]
'''


def texts(r):
    return [m.text for m in r.sent(Message)]


def test_held_use_presses_repeats_charges_and_lets_go(rig):
    r = rig({"gun": GUN})
    r.start()
    r.io.join("Steve")
    r.io.hold("Steve", "gun")
    r.tick()
    for _ in range(8):                               # held 1.6 s: a Use every 0.2 s
        r.io.use("Steve")
        r.tick(2)
    r.run_for(1)
    said = [t for t in texts(r) if not t.startswith("§")]
    assert said[0] == "press" and said.count("press") == 1
    assert said.count("charged") == 1 and said[-1].startswith("release")
    assert 6 <= said.count("hold") <= 9
    assert 1.4 <= float(said[-1].split()[1]) <= 1.8
    fills = [m for m in r.sent(Message) if m.where == "actionbar"]
    assert fills and fills[0].text.startswith("§e") and any(m.text.startswith("§a") for m in fills)
    assert r.problems() == []


def test_letting_go_early_never_charges(rig):
    r = rig({"gun": GUN})
    r.start()
    r.io.join("Steve")
    r.io.hold("Steve", "gun")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    r.io.use("Steve")
    r.run_for(1)
    said = [t for t in texts(r) if not t.startswith("§")]
    assert "charged" not in said and said[0] == "press" and said[-1].startswith("release")


def test_three_taps(rig):
    r = rig({"watch": WATCH})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.start_scenario("watch")
    for _ in range(3):
        r.io.sneak("Steve", True)
        r.tick(2)
        r.io.sneak("Steve", False)
        r.tick(2)
    assert texts(r).count("gesture") == 1
    r.run_for(3)                                     # slow taps: none
    for _ in range(3):
        r.io.sneak("Steve", True)
        r.tick(6)
        r.io.sneak("Steve", False)
        r.tick(6)
    assert texts(r).count("gesture") == 1


def test_a_held_crouch_calls_while_held(rig):
    r = rig({"watch": WATCH})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.start_scenario("watch", mode="held")
    r.io.sneak("Steve", True)
    r.run_for(1)
    r.io.sneak("Steve", False)
    r.run_for(1)
    said = texts(r)
    assert said.count("gesture") == 1 and 7 <= said.count("sneaking") <= 11


def test_steps_on_the_ground_and_a_landing(rig):
    r = rig({"watch": WATCH})
    r.start()
    io = r.io
    io.join("Steve", pos=(0.5, 64.0, 0.5))
    r.tick()
    iid = r.start_scenario("watch")
    for i in range(1, 11):                            # walk 5 blocks east
        io.move("Steve", (0.5 + 0.5 * i, 64.0, 0.5))
        r.tick()
    p = io.players["Steve"]
    p.on_ground = False                               # jump off a ledge 4 high
    io.move("Steve", (5.5, 68.0, 0.5))
    r.tick()
    io.move("Steve", (5.5, 64.0, 0.5))
    p.on_ground = True
    r.tick()
    steps = r.rt.records[iid].state.steps
    assert [x for x, _ in steps] == [1.0, 3.0, 5.0, 5.5] and steps[-1][1] == 4.0   # first seen at 1.0


def test_each_chunk_once(rig):
    r = rig({"watch": WATCH})
    r.start()
    io = r.io
    io.join("Steve", pos=(1.0, 64.0, 1.0))
    r.tick()
    iid = r.start_scenario("watch")
    for x in (5.0, 17.0, 3.0, 40.0):
        io.move("Steve", (x, 64.0, 1.0))
        r.tick()
    assert r.rt.records[iid].state.chunks == [[0, 32, 0], [16, 32, 0], [32, 32, 0]]


def test_the_director_counts_only_time_online(rig):
    r = rig({"watch": WATCH})
    r.start()
    r.io.join("Steve")
    r.tick()
    iid = r.start_scenario("watch")
    r.run_for(11)
    events = r.rt.records[iid].state.events
    assert [e[:2] for e in events] == [["whisper", "Steve"], ["whisper", "Steve"]]
    r.io.leave("Steve")
    r.run_for(20)
    assert len(r.rt.records[iid].state.events) == 2   # offline: the clock waits
    r.io.join("Steve")
    r.run_for(6)
    assert len(r.rt.records[iid].state.events) == 3


def test_a_short_held_crouch_counts_at_once(rig):
    r = rig({"watch": WATCH})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.start_scenario("watch", mode="held")
    r.io.sneak("Steve", True)
    r.tick()                                       # one tick down: the Sneak event
    r.io.sneak("Steve", False)
    r.tick()                                       # up the next: the Unsneak
    r.run_for(1)
    said = texts(r)
    assert said.count("gesture") == 1 and said.count("sneaking") == 1


def test_a_held_crouch_under_late_ticks_still_calls_and_ends(rig):
    r = rig({"watch": WATCH})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.start_scenario("watch", mode="held")
    r.io.sneak("Steve", True)
    r.tick(dt=0.3)                                 # a slow host: one late tick sees him down
    r.tick(dt=0.3)
    r.io.sneak("Steve", False)
    r.tick(dt=0.3)
    r.run_for(1)
    n = texts(r).count("sneaking")
    assert n == 2                                  # at the Sneak, and once on the late tick: no burst
