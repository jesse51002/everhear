"""The kit's heavy cases fit inside a content call (the 50 ms cut on the box): a 1000-block beam
through 200 mobs, a rings blast of power 16 among 100, hollow shapes at their caps, a castle-sized
box, a radius-100 crater with lava, 50 mobs thrown at once, the longest tune.

A wall-clock time against the real cut would fail on a loaded PC, so the test asserts what makes the
time instead: the work a call does (fills planned within the caps; live reads and what they bring
back, counted on FakeIo) and an estimate of the call on the box: its own Python, the best of three
runs under a test cut of a second, plus each live read at what B measured on 26.3 (MINECRAFT.md:
entities 0.2 ms a read and 0.04 ms an entity, probe 0.02 ms a point, ground_y 0.06 ms, a ray up to
1.3 ms). The estimate must stay under 40 ms."""

import re

import pytest

from vfg_engine.kit.blocks import MOST_PARTS, shape_parts

BOX_MS = 40.0
READ_MS = {"entities": 0.2, "entity": 0.04, "probe": 0.02, "point": 0.02, "ground_y": 0.06, "ray": 1.3}

HEAVY = '''
class Heavy(Scenario):
    class Config(ScenarioConfig):
        summary = "One heavy kit call."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        case: str = Field("beam", description="which")

    def start(self, ctx):
        c = self.args.case
        if c == "beam":
            found = near_line(self, ctx, (0, 65, 0), (0, 65, 1000), 3, "mobs")
            hit(self, ctx, found, 60, "magic")
            beam_line(self, ctx, (0, 65, 0), (0, 65, 1000), "minecraft:end_rod", every=1.5)
        elif c == "blast":
            blast(self, ctx, (0, 64, 300), 16, "rings", 32)
        elif c in ("cylinder", "pyramid"):
            build_shape(self, ctx, (0, 64, 600), c, 64, 32, 64, "70% stone_bricks 30% cobblestone",
                        "south", hollow=True)
        elif c == "dome":
            build_shape(self, ctx, (0, 64, 600), c, 48, 24, 48, "70% stone_bricks 30% cobblestone",
                        "south", hollow=True)
        elif c == "castle":
            build_shape(self, ctx, (0, 64, 600), "box", 101, 40, 101, "70% stone_bricks 30% cobblestone",
                        "south", hollow=True)
        elif c == "too_big":
            build_shape(self, ctx, (0, 64, 600), "dome", 101, 40, 101)
        elif c == "too_many":
            build_shape(self, ctx, (0, 64, 600), "dome", 64, 64, 64, hollow=True)
        elif c == "crater":
            crater(self, ctx, (0, 30, 900), 100, "lava")
        elif c == "push":
            push(self, ctx, targets(self, ctx, (300, 64, 0), 40, "mobs"), "up", 30)
        elif c == "tune":
            melody(self, ctx, "spirit_bomb", at=(0, 64, 0), to="everyone")
'''

# case: how many fills it may plan (None: not a build)
FILLS = {
    "beam": None, "blast": None, "push": None, "tune": None,
    "dome": len(shape_parts("dome", 48, 24, 48, True)),
    "cylinder": len(shape_parts("cylinder", 64, 32, 64, True)),
    "pyramid": len(shape_parts("pyramid", 64, 32, 64, True)),
    "castle": len(shape_parts("box", 101, 40, 101, True)),
    "crater": 1 + 34,
}


@pytest.fixture
def heavy(rig):
    # slow_ms 0: every call's time is logged; cut_ms 1000: a loaded PC cannot cut a call here
    r = rig({"heavy": HEAVY}, slow_ms=0.0, cut_ms=1000.0)
    r.start()
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0))
    io.join("Alex", pos=(300.0, 64.0, 0.0))
    hp = 1e9                     # nobody dies: each of the three runs meets the same crowd
    for i in range(200):
        io.add_entity("minecraft:zombie", ((i % 5) - 2.0, 64.0, 5.0 * i), health=hp)
    for i in range(100):
        io.add_entity("minecraft:husk", ((i % 10) * 4 - 20.0, 64.0, 280.0 + (i // 10) * 4), health=hp)
    for i in range(50):
        io.add_entity("minecraft:skeleton", (300.0 + (i % 10) * 3 - 15, 64.0, (i // 10) * 3 - 7.0), health=hp)
    r.tick()
    return r


def _ms(r, before):
    """The start call's time, from its log line."""
    for line in r.lines()[before:]:
        m = re.search(r"heavy#\d+\.start took ([\d.]+) ms", line)
        if m:
            return float(m.group(1))
    return None


def _count_reads(io):
    """Count the live reads the content makes, and what they bring back."""
    n = {k: 0 for k in READ_MS}

    def wrap(name, fn):
        def counted(*a, **k):
            got = fn(*a, **k)
            n[name] += 1
            if name == "entities":
                n["entity"] += len(got)
            elif name == "probe":
                n["point"] += len(a[1])
            return got
        return counted
    for name in ("entities", "probe", "ground_y", "ray"):
        setattr(io, name, wrap(name, getattr(io, name)))
    return n


@pytest.mark.parametrize("case", sorted(FILLS))
def test_a_heavy_call_does_bounded_work_and_fits_the_cut_on_the_box(heavy, case):
    r = heavy
    if FILLS[case] is not None:
        assert FILLS[case] <= MOST_PARTS
    reads = _count_reads(r.io)
    best, per_call = None, None
    for i in range(3):
        before = len(r.lines())
        mark = dict(reads)
        reply = r.op("start", scenario="heavy", args={"case": case})
        assert reply["ok"], reply
        lines = r.lines()[before:]
        assert not [m for m in lines if "failed" in m or "cut" in m], lines
        ms = _ms(r, before)
        assert ms is not None, lines
        best = ms if best is None else min(best, ms)
        per_call = {k: reads[k] - mark[k] for k in reads}
        r.op("stop", instance=reply["instance"])
    box = best + sum(per_call[k] * READ_MS[k] for k in READ_MS)
    assert box < BOX_MS, "%s: %.1f ms of Python + reads %s = %.1f ms on the box, over %.0f" % (
        case, best, per_call, box, BOX_MS)


def test_shapes_past_their_caps_are_refused_in_words(heavy):
    r = heavy
    r.op("start", scenario="heavy", args={"case": "too_big"})
    assert any("a dome is at most 64 blocks across" in m for m in r.lines())
    r.op("start", scenario="heavy", args={"case": "too_many"})
    assert any("a dome 64 x 64 x 64 hollow is 4375 boxes, over the %d" % MOST_PARTS in m for m in r.lines())
