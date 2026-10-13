"""The loop's order (the game engine spec, "One loop, in order"): read the queue, scenarios,
events, abilities, marks, write; everything a tick queued goes out in one batch."""

from vfg_engine.io import Command

ORDER = '''
class Watcher(Scenario):
    every = 0.1

    class Config(ScenarioConfig):
        summary = "Says when it ticks and when it hears a hit."
        duration_s = UNTIL_STOPPED

    def start(self, ctx):
        self.listen(Hit, "on_hit")

    def tick(self, ctx):
        self.command("say 1 scenario tick")

    def on_hit(self, ctx, ev):
        self.command("say 2 event hit")


class Ticker(Ability):
    every = 0.1

    class Config(AbilityConfig):
        summary = "Says when it ticks."
        damage = 0
        duration_s = UNTIL_STOPPED
        charge_s = 0
        cooldown_s = 0
        scale_per_point = 0

    def tick(self, ctx):
        self.command("say 3 ability tick")


class Tag(Mark):
    every = 0.1

    class Config(MarkConfig):
        summary = "Says when it ticks."
        duration_s = 60
        damage = 0

    def tick(self, ctx):
        self.command("say 4 mark tick")
'''


def test_one_tick_runs_scenarios_events_abilities_marks_then_writes_one_batch(rig):
    r = rig({"order": ORDER})
    assert r.start()["errors"] == []
    r.io.join("Steve")
    r.tick()
    r.op("start", scenario="watcher")
    r.op("grant", who="Steve", ability="ticker")
    r.op("apply", who="Steve", mark="tag")
    r.tick(2)
    r.io.hit("Steve", "some-zombie")
    before = len(r.io.batches)
    r.tick()
    assert len(r.io.batches) == before + 1                  # one batch a tick
    said = [op.text for op in r.io.batches[-1] if isinstance(op, Command)]
    assert said == ["say 1 scenario tick", "say 2 event hit", "say 3 ability tick", "say 4 mark tick"]


def test_events_are_handed_over_once_and_in_order(rig):
    r = rig({"order": ORDER})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("start", scenario="watcher")
    for _ in range(3):
        r.io.hit("Steve", "z")
    r.tick()
    r.tick()
    hits = [op.text for op in r.io.ops_of(Command) if op.text == "say 2 event hit"]
    assert len(hits) == 3


def test_a_failed_snapshot_or_batch_does_not_stop_the_loop(rig):
    r = rig({"order": ORDER})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="ticker")
    r.io.snapshot_raises = OSError("rcon hiccup")
    r.tick()
    r.io.snapshot_raises = None
    r.io.flush_raises = OSError("rcon hiccup")
    r.tick()
    r.io.flush_raises = None
    r.tick()
    assert r.records(cid="ticker")
    assert any("rcon hiccup" in m for m in r.lines())


def test_ops_submitted_from_another_thread_are_answered_in_the_next_tick(rig):
    import threading
    r = rig({"order": ORDER})
    r.start()
    got = {}
    t = threading.Thread(target=lambda: got.update(f=r.engine.submit("classes")))
    t.start()
    t.join()
    assert not got["f"].done()
    r.tick()
    assert sorted(c["id"] for c in got["f"].result(timeout=0)["classes"]) == ["tag", "ticker", "watcher"]


def test_a_failing_step_does_not_skip_the_rest_of_the_tick(rig, monkeypatch):
    from vfg_engine import items
    r = rig({"order": ORDER})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.op("grant", who="Steve", ability="ticker")

    def broken(rt):
        raise RuntimeError("an engine bug")
    monkeypatch.setattr(items, "reconcile", broken)
    status = r.engine.submit("status")
    r.tick()                                              # does not raise
    assert status.result(timeout=0)["ok"]                 # the inputs were still handled
    assert "say 3 ability tick" in [op.text for op in r.io.batches[-1] if isinstance(op, Command)]
    assert any("the holders step failed" in m for m in r.lines())


def test_a_request_is_answered_even_when_the_write_fails(rig, monkeypatch):
    r = rig(examples=True)
    r.start()
    r.io.join("Steve")
    r.tick()

    def broken(read):
        raise RuntimeError("an engine bug in the write")
    monkeypatch.setattr(r.rt.world_settings, "plan", broken)
    give = r.engine.submit("give", {"player": "Steve", "item": "fire_staff"})
    r.tick()
    reply = give.result(timeout=0)
    assert reply["ok"] is False and "write" in reply["error"] and "an engine bug" in reply["error"]
