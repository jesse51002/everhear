"""The block queue, self.blocks (DESIGN2 §6): split at chunk edges and 32,768 blocks, only loaded
chunks, protected boxes and the bedrock floor spared, slowed by mspt, spread over ticks, undone at
the end, and done(ctx) when it empties."""

import json

from vfg_engine.blocks import CMDS_PER_TICK, subtract
from vfg_engine.io import Fill, Forceload, SetBlock

BUILDER = '''
class Build(Scenario):
    class Config(ScenarioConfig):
        summary = "Builds what it is told."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        what: str = Field("box", description="box, mix, sphere, replace, floor, undo or far")

    def start(self, ctx):
        w = self.args.what
        if w == "box":
            self.blocks.fill((0, 64, 0), (40, 70, 40), "glass")
        elif w == "mix":
            self.blocks.fill((0, 64, 0), (5, 64, 5), "50% stone 50% andesite", cell=1)
        elif w == "sphere":
            self.blocks.sphere((0, 80, 0), 4, "ice", hollow=True)
        elif w == "replace":
            self.blocks.replace(((0, 60, 0), (3, 63, 3)), "stone > 50% gold_block 50% keep", cell=1)
        elif w == "floor":
            self.blocks.fill((0, -64, 0), (3, -60, 3), "air")
        elif w == "undo":
            self.blocks.fill((0, 64, 0), (3, 66, 3), "ice", mode="keep", undo=True)
        elif w == "far":
            self.blocks.forceload((1000, 64, 1000), (1010, 64, 1010))
            self.blocks.fill((1000, 64, 1000), (1010, 64, 1010), "obsidian")
        elif w == "unloaded":
            self.blocks.fill((2000, 64, 2000), (2010, 64, 2010), "obsidian")

    def done(self, ctx):
        self.result("built", done=self.blocks.done)
        if self.args.what != "undo":
            self.end()
'''


def _rig(rig, tmp_path=None, **kw):
    r = rig({"build": BUILDER}, **kw)
    assert r.start()["errors"] == []
    r.io.join("Steve")
    r.tick()
    return r


def fills(r):
    return [op for op in r.io.sent if isinstance(op, (Fill, SetBlock))]


def test_a_big_fill_is_cut_at_chunk_edges_spread_over_ticks_then_done(rig):
    r = _rig(rig)
    fut = r.engine.submit("start", {"scenario": "build", "args": {"what": "box"}, "wait_s": 10})
    r.tick()
    first = len(fills(r))
    assert 0 < first <= CMDS_PER_TICK
    r.run_for(2)
    pieces = fills(r)
    assert len(pieces) == 9                               # 41 x 41 is 3 x 3 chunk columns
    for op in pieces:
        assert op.a[0] >> 4 == op.b[0] >> 4 and op.a[2] >> 4 == op.b[2] >> 4
        assert (op.b[0] - op.a[0] + 1) * (op.b[1] - op.a[1] + 1) * (op.b[2] - op.a[2] + 1) <= 32768
    assert r.io.block_at("minecraft:overworld", 40, 70, 40) == "minecraft:glass"
    reply = fut.result(timeout=0)
    assert reply["result"] == {"summary": "built", "facts": {"done": True}}


def test_a_mix_is_picked_per_cell_the_same_every_time(rig):
    r = _rig(rig)
    r.op("start", scenario="build", args={"what": "mix"})
    r.run_for(1)
    got = {r.io.block_at("minecraft:overworld", x, 64, z) for x in range(6) for z in range(6)}
    assert got == {"minecraft:stone", "minecraft:andesite"}
    first = dict(r.io.blocks)
    r.io.blocks.clear()
    r.op("start", scenario="build", args={"what": "mix"})
    r.run_for(1)
    assert r.io.blocks == first


def test_a_hollow_sphere_and_replace_rules(rig):
    r = _rig(rig)
    r.op("start", scenario="build", args={"what": "sphere"})
    r.run_for(1)
    assert r.io.block_at("minecraft:overworld", 0, 83, 0) == "minecraft:ice"      # the shell's top
    assert r.io.block_at("minecraft:overworld", 0, 80, 0) == "minecraft:air"      # hollow inside
    r.op("start", scenario="build", args={"what": "replace"})
    r.run_for(1)
    got = [r.io.block_at("minecraft:overworld", x, y, z) for x in range(4) for y in range(60, 64) for z in range(4)]
    assert set(got) == {"minecraft:stone", "minecraft:gold_block"}               # keep left some as they were


def test_the_bedrock_floor_and_protected_boxes_are_spared(rig, tmp_path):
    prot = tmp_path / "protected.json"
    prot.write_text(json.dumps([{"name": "castle", "dim": "minecraft:overworld", "box": [10, 60, 10, 20, 80, 20]}]))
    r = _rig(rig, protected=str(prot))
    r.op("start", scenario="build", args={"what": "floor"})
    r.op("start", scenario="build", args={"what": "box"})
    r.run_for(3)
    assert r.io.block_at("minecraft:overworld", 0, -64, 0) == "minecraft:bedrock"
    assert r.io.block_at("minecraft:overworld", 0, -63, 0) == "minecraft:air"
    assert r.io.block_at("minecraft:overworld", 15, 66, 15) == "minecraft:air"    # inside the castle: untouched
    assert r.io.block_at("minecraft:overworld", 9, 66, 9) == "minecraft:glass"
    assert r.io.block_at("minecraft:overworld", 21, 66, 21) == "minecraft:glass"


def test_a_half_written_protected_file_keeps_the_last_good_boxes(rig, tmp_path):
    """An unparseable protected.json gave no boxes, so nothing was protected until the file changed
    again. Now it keeps the boxes last read, with an error in the log."""
    prot = tmp_path / "protected.json"
    prot.write_text(json.dumps([{"name": "castle", "dim": "minecraft:overworld", "box": [10, 60, 10, 20, 80, 20]}]))
    r = _rig(rig, protected=str(prot))
    r.op("start", scenario="build", args={"what": "floor"})
    r.run_for(1)                                                    # the file is read: the castle
    prot.write_text('[{"name": "castle", "dim": "minecraft:overw')  # being rewritten
    r.op("start", scenario="build", args={"what": "box"})
    r.run_for(3)
    assert r.io.block_at("minecraft:overworld", 15, 66, 15) == "minecraft:air"    # still spared
    assert r.io.block_at("minecraft:overworld", 9, 66, 9) == "minecraft:glass"
    bad = [m for m in r.lines() if "protected file" in m and "unreadable" in m]
    assert len(bad) == 1 and "keeping the 1 boxes last read" in bad[0]          # once, not every tick


def test_protected_boxes_go_only_when_the_file_is_really_gone(tmp_path):
    from vfg_engine.blocks import Protected
    prot = tmp_path / "protected.json"
    prot.write_text(json.dumps([{"dim": "overworld", "box": [0, 0, 0, 1, 1, 1]}]))
    p = Protected(str(prot))
    assert p.get("minecraft:overworld") == [([0, 0, 0], [1, 1, 1])]
    for typo in ['[{"dim": "overworld", "box": [0, 0, 0, 1, 1]}]', "{not json", "null", '[{"box": "x"}]']:
        prot.write_text(typo)
        assert p.get("minecraft:overworld") == [([0, 0, 0], [1, 1, 1])], typo
    prot.write_text(json.dumps([{"dim": "overworld", "box": [5, 5, 5, 2, 2, 2]}]))
    assert p.get("minecraft:overworld") == [([2, 2, 2], [5, 5, 5])]                # a good one is read
    prot.unlink()
    assert p.get("minecraft:overworld") == []
    prot.write_text("{half")
    assert p.get("minecraft:overworld") == []        # unreadable, with nothing before it: nothing


def test_it_slows_down_with_the_server(rig):
    r = _rig(rig)
    r.io.mspt_value = 48.0
    r.op("start", scenario="build", args={"what": "box"})
    r.run_for(1)
    assert not fills(r)                                    # the server is lagging: nothing
    r.io.mspt_value = 20.0
    r.run_for(1.5)
    assert len(fills(r)) == 9


def test_undo_puts_air_back_where_its_blocks_stand(rig):
    r = _rig(rig)
    iid = r.op("start", scenario="build", args={"what": "undo"})["instance"]
    r.run_for(1)
    assert r.io.block_at("minecraft:overworld", 1, 65, 1) == "minecraft:ice"
    r.io.set_block("minecraft:overworld", (2, 65, 2), "minecraft:dirt")         # someone changed one
    r.op("stop", instance=iid)
    r.run_for(1)
    assert r.io.block_at("minecraft:overworld", 1, 65, 1) == "minecraft:air"
    assert r.io.block_at("minecraft:overworld", 2, 65, 2) == "minecraft:dirt"


def test_unloaded_chunks_are_skipped_and_forceloaded_ones_waited_for(rig):
    r = _rig(rig)
    r.io.unloaded |= {("minecraft:overworld", cx, cz) for cx in range(60, 70) for cz in range(60, 70)}
    r.io.unloaded |= {("minecraft:overworld", 125, 125)}
    r.op("start", scenario="build", args={"what": "unloaded"})
    r.run_for(1)
    assert r.io.block_at("minecraft:overworld", 2005, 64, 2005) == "minecraft:air"
    assert any("not loaded" in m for m in r.lines())
    r.io.unloaded |= {("minecraft:overworld", cx, cz) for cx in range(62, 64) for cz in range(62, 64)}
    r.op("start", scenario="build", args={"what": "far"})
    r.tick()
    (fl,) = [op for op in r.io.sent if isinstance(op, Forceload)]
    assert fl.add and fl.a == (992, 992)
    r.run_for(2)                                           # placed once loaded, then done and ended
    assert r.io.block_at("minecraft:overworld", 1005, 64, 1005) == "minecraft:obsidian"
    assert [op.add for op in r.io.sent if isinstance(op, Forceload)] == [True, False]   # released


def test_the_queue_is_saved(rig):
    r = _rig(rig)
    r.io.mspt_value = 48.0
    r.op("start", scenario="build", args={"what": "box"})
    r.tick()
    jobs = r.engine.state()["blocks"]
    assert len(jobs) == 1 and jobs[0]["owner"].startswith("scenario:build#")
    json.dumps(jobs)


def test_subtract_leaves_the_rest():
    parts = subtract([0, 0, 0], [9, 9, 9], [3, 3, 3], [5, 5, 5])
    cells = {(x, y, z) for lo, hi in parts for x in range(lo[0], hi[0] + 1)
             for y in range(lo[1], hi[1] + 1) for z in range(lo[2], hi[2] + 1)}
    assert len(cells) == 1000 - 27 and (4, 4, 4) not in cells


BIG = '''
import time as _clock


class Crater(Scenario):
    class Config(ScenarioConfig):
        summary = "A nuke's crater."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        r: float = Field(1000, description="radius")
        n: int = Field(0, description="fills of a mix to queue")

    def start(self, ctx):
        if self.args.n:
            cpu = _clock.thread_time()
            for i in range(self.args.n):
                self.blocks.fill((i, 64, 0), (i, 66, 2), "71% stone 29% andesite")
            self.result("queued", cpu_us=(_clock.thread_time() - cpu) * 1e6 / self.args.n)
        else:
            self.blocks.sphere((0, 0, 0), self.args.r, "air")
'''


def test_a_huge_sphere_is_planned_over_ticks_in_loaded_chunks_only(rig):
    """What is spread stays spread: the call only queues the job (it plans no chunk column), and
    each tick plans at most COLUMNS_PER_TICK columns that ask Minecraft anything. Work is counted,
    not timed, so a busy box cannot fail it."""
    from vfg_engine.blocks import COLUMNS_PER_TICK, VISITS_PER_TICK
    r = rig({"crater": BIG})
    r.start()
    r.io.join("Steve")
    # only the chunks round the middle are loaded
    r.io.unloaded |= {("minecraft:overworld", cx, cz) for cx in range(-63, 63) for cz in range(-63, 63)
                      if abs(cx) > 2 or abs(cz) > 2}
    queue = r.rt.block_queue
    planned_in_a_call, asked, visited = [], [], []
    column, loaded = queue._column, r.io.loaded

    def counted_column(job, index):
        planned_in_a_call.append(r.rt.guard.active is not None)
        visited[-1] += 1
        return column(job, index)

    def counted_loaded(dim, x, z):
        asked[-1] += 1
        return loaded(dim, x, z)
    queue._column, r.io.loaded = counted_column, counted_loaded
    step = queue.step

    def counted_step():
        asked.append(0)
        visited.append(0)
        step()
    queue.step = counted_step
    reply = r.op("start", scenario="crater")
    assert reply["ok"] and len(queue.jobs) == 1
    r.tick(10)
    assert not any(planned_in_a_call)                       # planned only in the engine's write step
    assert max(asked) <= COLUMNS_PER_TICK and max(visited) <= VISITS_PER_TICK
    assert queue.jobs and queue.jobs[0]["next"] < queue.jobs[0]["columns"]     # still going: spread
    assert r.io.block_at("minecraft:overworld", 0, 0, 0) == "minecraft:air"     # the middle first
    sent = [op for op in r.io.sent if isinstance(op, Fill)]
    assert all(_volume(op.a, op.b) <= 32768 for op in sent)
    assert any(_volume(op.a, op.b) > 16 * 16 * 8 for op in sent)               # the core: big boxes


def test_thousands_of_fills_fit_in_one_call(rig):
    """Queuing a fill is cheap: its mix is read once for all of them, and each costs a few
    microseconds of this thread's CPU (CPU time, not wall time: a busy box does not change it
    much). At 20 us a fill, 2500 would fit in the 50 ms a call has; it measures about 3."""
    from vfg_engine import blocks
    r = rig({"crater": BIG})
    r.start()
    misses = blocks._parsed.cache_info().misses
    reply = r.op("start", scenario="crater", args={"n": 5000})
    assert reply["ok"]
    (rec,) = r.records("scenario")
    assert r.rt.block_queue.pending(rec.iid) > 4900          # queued; the first few placed this tick
    assert blocks._parsed.cache_info().misses - misses == 1   # the mix read once, not 5000 times
    assert rec.notes["result"]["facts"]["cpu_us"] < 20


def test_the_mirror_reach_is_public(rig):
    src = BIG.replace('self.blocks.sphere((0, 0, 0), self.args.r, "air")',
                      'self.result("reach", radius=ctx.world.mirror_radius); self.end()')
    r = rig({"crater": src})
    r.start()
    r.io.mirror_radius = 48
    assert r.op("start", scenario="crater", wait_s=1)["result"]["facts"] == {"radius": 48.0}


def _volume(a, b):
    return (abs(b[0] - a[0]) + 1) * (abs(b[1] - a[1]) + 1) * (abs(b[2] - a[2]) + 1)


ORDERED = '''
class Hut(Scenario):
    class Config(ScenarioConfig):
        summary = "Clears a space, then builds a wall in it."
        duration_s = UNTIL_STOPPED

    def start(self, ctx):
        self.blocks.forceload((0, 64, 0), (15, 64, 15))
        self.blocks.fill((0, 64, 0), (31, 64, 15), "air")        # its first chunk waits to load
        self.blocks.fill((20, 64, 0), (20, 64, 15), "glass")     # the wall, in the second
'''


def test_one_owners_jobs_go_in_order(rig):
    r = rig({"hut": ORDERED})
    r.start()
    r.io.join("Steve")
    r.io.unloaded.add(("minecraft:overworld", 0, 0))              # loaded once forceloaded
    for x in range(32):
        for z in range(16):
            r.io.set_block("minecraft:overworld", (x, 64, z), "minecraft:dirt")
    r.tick()
    r.op("start", scenario="hut")
    r.run_for(1)
    assert r.io.block_at("minecraft:overworld", 20, 64, 5) == "minecraft:glass"   # not wiped by the clearing
    assert r.io.block_at("minecraft:overworld", 5, 64, 5) == "minecraft:air"


CHEST = '''
class Stash(Scenario):
    class Config(ScenarioConfig):
        summary = "A chest in a far chunk, and more stacks in it."
        duration_s = UNTIL_STOPPED

    def start(self, ctx):
        self.blocks.forceload((100, 64, 100), (100, 64, 100))
        self.blocks.setblock((100, 64, 100), "minecraft:chest[facing=north]")
        for i in range(3):
            self.blocks.data((100, 64, 100), "Items", '{Slot:%db,id:"minecraft:diamond",count:1}' % i)

    def done(self, ctx):
        self.result("stocked")
        self.end()
'''


def _stash(rig):
    r = rig({"stash": CHEST})
    r.start()
    r.io.join("Steve")
    r.io.unloaded.add(("minecraft:overworld", 6, 6))       # the chest's chunk: loaded once forceloaded
    r.tick()
    return r


def test_block_data_goes_after_its_block_across_an_unloaded_chunk_then_done(rig):
    from vfg_engine.io import BlockData, SetBlock
    r = _stash(rig)
    fut = r.engine.submit("start", {"scenario": "stash", "wait_s": 10})
    r.tick()                                                 # the chunk is not loaded yet: all wait
    assert not [op for op in r.io.sent if isinstance(op, (SetBlock, BlockData))] and not fut.done()
    r.run_for(1)
    kinds = [type(op).__name__ for op in r.io.sent if isinstance(op, (SetBlock, BlockData))]
    assert kinds == ["SetBlock", "BlockData", "BlockData", "BlockData"]       # in the order asked
    assert [d[2] for d in r.io.block_data[("minecraft:overworld", 100, 64, 100)]] == [
        '{Slot:%db,id:"minecraft:diamond",count:1}' % i for i in range(3)]
    assert fut.result(timeout=0)["result"]["summary"] == "stocked"           # done after the data


def test_block_data_jobs_are_counted_and_saved(rig):
    import json
    r = _stash(rig)
    iid = r.op("start", scenario="stash")["instance"]
    assert r.rt.block_queue.pending(iid) == 4                # the block and its three data jobs
    jobs = json.loads(json.dumps(r.engine.state()["blocks"]))
    assert [j["kind"] for j in jobs] == ["box", "data", "data", "data"]
    r.rt.block_queue.load(jobs, {iid})
    assert r.rt.block_queue.pending(iid) == 4
    r.op("stop", instance=iid)
    assert r.rt.block_queue.pending(iid) == 0                # dropped with its owner


def test_block_data_is_refused_in_words(rig):
    import pytest
    r = rig({"hut": ORDERED})
    r.start()
    obj = r.rt.objects[r.op("start", scenario="hut")["instance"]]
    with pytest.raises(ValueError, match="how is append, prepend, merge, set"):
        obj.blocks.data((0, 64, 0), "Items", "{}", how="insert")
    with pytest.raises(ValueError, match="needs a path"):
        obj.blocks.data((0, 64, 0), "", "{}")
