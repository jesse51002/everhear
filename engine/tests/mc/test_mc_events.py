"""McIo.events against a model of the datapack's queue (everhear:io/events and its page): paging, the
dedupe by number, a connection lost between the two exchanges, overflow, and a counter that went back.
No Minecraft needed; test_mc_live.py does the same on a real server."""

import contextlib
import logging

import pytest

from vfg_engine.mc import McIo, RconError

STEVE = [-2040022415, -1202044924, -1353431686, -1758138665]
STEVE_UUID = "8667ba71-b85a-4004-af54-457a9734eed7"
PAGE = 12


def snbt(v):
    """Python values printed the way 26.3's `data get` prints them."""
    if isinstance(v, dict):
        return "{" + ", ".join("%s: %s" % (k, snbt(x)) for k, x in v.items()) + "}"
    if isinstance(v, list):
        if len(v) == 4 and all(isinstance(x, int) for x in v) and any(abs(x) > 1000 for x in v):
            return "[I; " + ", ".join(str(x) for x in v) + "]"
        return "[" + ", ".join(snbt(x) for x in v) + "]"
    if isinstance(v, str):
        return '"%s"' % v
    if isinstance(v, float):
        return "%rf" % v
    return str(v)


class FakeServer:
    """The datapack's queue and the commands McIo sends about it, behind an Rcon-like face."""

    class Stats:
        connects = 1

    def __init__(self, max_len=2048):
        self.q, self.n, self.over, self.max = [], 0, 0, max_len
        self.page = {}
        self.fail_next_get = False
        self.stats = self.Stats()
        self.sent = []
        self.mirror_masks = []

    def push(self, kind="use", **d):
        if len(self.q) >= self.max:
            self.over += 1
            return
        self.n += 1
        self.q.append({"d": d or {"item": "fire_staff", "count": 1, "slot": "mainhand"}, "t": 100 + self.n,
                       "u": STEVE, "k": kind, "n": self.n})

    def snap(self):
        return {"p": [{"u": STEVE, "d": "minecraft:overworld", "p": [0.5, 85.0, 0.5], "e": [0.5, 86.62, 0.5],
                       "r": [0.0, 0.0], "h": 20.0, "mh": 20.0, "g": 0, "s": 0, "l": {"k": "none", "d": 1536}}],
                "e": [], "time": 100 + self.n}

    @contextlib.contextmanager
    def held(self):
        yield self

    def command(self, cmd):
        self.sent.append(cmd)
        if cmd.startswith("function everhear:io/events {upto:") or cmd.startswith("function everhear:io/tick {upto:"):
            upto = int(cmd.split("upto:")[1].rstrip("}"))
            while self.n >= upto and self.q and self.q[0]["n"] <= upto:    # as io/events: not when behind
                self.q.pop(0)
            self.page = {"q": [dict(e) for e in self.q[:PAGE]], "len": len(self.q), "last": self.n,
                         "over": self.over}
            return "Running function " + cmd.split()[1]
        if cmd in ("data get storage everhear:out ev", "data get storage everhear:out"):
            if self.fail_next_get:
                self.fail_next_get = False
                raise RconError("RCON failed on %r: no reply within the timeout" % cmd)
            doc = self.page if cmd.endswith(" ev") else {"ev": self.page, "snap": self.snap()}
            return "Storage everhear:out has the following contents: " + snbt(doc)
        if cmd == "function everhear:io/snap":
            return "Running function everhear:io/snap"
        if cmd == "data get storage everhear:out snap":
            return "Storage everhear:out has the following contents: " + snbt(self.snap())
        if cmd.startswith("data modify storage everhear:io track set value"):
            return "Modified storage everhear:io"
        if cmd.startswith("function everhear:io/mirror {r:"):
            import signal
            import threading
            self.mirror_masks.append((threading.current_thread().name,
                                      signal.SIGALRM in signal.pthread_sigmask(signal.SIG_BLOCK, set())))
            return "Running function everhear:io/mirror"
        if cmd == "data get storage everhear:mirror":
            husk = {"p": [3.5, 85.0, 3.5], "d": "minecraft:overworld", "u": [1, 2, 3, 10], "h": 20.0, "y": 70}
            steve = {"p": [0.5, 85.0, 0.5], "d": "minecraft:overworld", "u": STEVE, "h": 20.0, "y": 108}
            # a player, as a pack from before players were left out still sends: dropped
            return "Storage everhear:mirror has the following contents: " + snbt({"e": [husk, steve], "time": 5})
        if cmd == "list uuids":
            return "There are 1 of a max of 20 players online: Steve (%s)" % STEVE_UUID
        raise AssertionError("unexpected command %r" % cmd)


@pytest.fixture
def io():
    srv = FakeServer()
    m = McIo(rcon=srv, mirror=False)
    m.names.update(srv.command("list uuids"))
    return m, srv


def numbers(events):
    return [e.n for e in events]


def test_events_in_order_and_paged(io):
    m, srv = io
    for _ in range(30):
        srv.push()
    evs = m.events()
    assert numbers(evs) == list(range(1, 31))
    assert evs[0].player == "Steve" and evs[0].data["item"] == "fire_staff"
    # three pages: 12, 12, 6; the last page stays in the queue until the next call takes it
    assert sum(1 for c in srv.sent if c.startswith("function")) == 3
    assert [e["n"] for e in srv.q] == list(range(25, 31))
    assert m.events() == []
    assert srv.q == []


def test_nothing_is_handed_over_twice_or_lost_across_a_lost_reply(io):
    m, srv = io
    for _ in range(5):
        srv.push()
    assert numbers(m.events()) == [1, 2, 3, 4, 5]
    for _ in range(4):
        srv.push()
    srv.fail_next_get = True                      # the page was copied, then the reply lost
    with pytest.raises(RconError):
        m.events()
    for _ in range(2):
        srv.push()
    assert numbers(m.events()) == [6, 7, 8, 9, 10, 11]


def test_a_page_read_twice_is_deduped(io):
    m, srv = io
    for _ in range(3):
        srv.push()
    assert numbers(m.events()) == [1, 2, 3]
    m.last_n = 1                                  # as if the engine had seen only the first
    assert numbers(m.events()) == [2, 3]
    m.last_n = 3
    assert m.events() == []


def test_an_overflow_is_counted_and_logged(io, caplog):
    m, srv = io
    srv.max = 5
    for _ in range(8):
        srv.push()
    with caplog.at_level(logging.ERROR, logger="vfg_engine.mc"):
        evs = m.events()
    assert numbers(evs) == [1, 2, 3, 4, 5]
    assert m.overflowed == 3
    assert "3 event(s) turned away" in caplog.text


def test_a_counter_that_went_back_starts_over(io, caplog):
    m, srv = io
    m.last_n = 500                                # the engine had read up to 500 in another world
    for _ in range(3):
        srv.push()
    with caplog.at_level(logging.WARNING, logger="vfg_engine.mc"):
        assert numbers(m.events()) == [1, 2, 3]
    assert "went back from 500 to 3" in caplog.text


def test_a_backlog_is_read_in_bounded_pages_per_call(io, monkeypatch):
    import vfg_engine.mc.mcio as mcio
    monkeypatch.setattr(mcio, "MAX_PAGES", 2)
    m, srv = io
    for _ in range(40):
        srv.push()
    assert numbers(m.events()) == list(range(1, 25))       # two pages this tick
    assert numbers(m.events()) == list(range(25, 41))      # the rest next tick


def test_a_hit_on_a_player_names_them(io):
    m, srv = io
    srv.push("hit", target=STEVE, target_type=108)
    ev = m.events()[0]
    assert ev.kind == "hit" and ev.data["target"] == "Steve"


def test_a_tick_is_one_function_and_one_read(io):
    m, srv = io
    m.snapshot(track=())                          # the first: sets the tracked list
    srv.push()
    srv.sent.clear()
    evs = m.events()
    snap = m.snapshot(track=())
    assert numbers(evs) == [1] and "Steve" in snap.players
    assert srv.sent == ["function everhear:io/tick {upto:0}", "data get storage everhear:out"]
    srv.sent.clear()
    m.events()
    m.snapshot(track=["0000ee00-0000-4000-8000-000000000001"])   # a new list: a snapshot of its own
    assert srv.sent[:2] == ["function everhear:io/tick {upto:1}", "data get storage everhear:out"]
    assert srv.sent[2].startswith("data modify storage everhear:io track set value")
    assert srv.sent[3:] == ["function everhear:io/snap", "data get storage everhear:out snap"]


def test_a_write_between_drops_the_read_ahead(io):
    m, srv = io
    m.snapshot(track=())
    m.events()
    srv.sent.clear()
    m.flush([])
    m.snapshot(track=())
    assert srv.sent == ["function everhear:io/snap", "data get storage everhear:out snap"]


def test_the_mirror_thread_never_takes_the_cut(io):
    """The core's cut is a SIGALRM for the main thread: the mirror's thread has it blocked."""
    import time
    from vfg_engine.mc import reading
    m, srv = io
    m.tables = reading.Tables(types=[""] * 120, blocks=[])
    m.tables.types[70] = "minecraft:husk"
    m.tables.types[108] = "minecraft:player"
    mirror = m.mirror()
    mirror.interval = 0.02
    mirror.start()
    try:
        end = time.monotonic() + 2
        while mirror.passes < 2 and time.monotonic() < end:
            time.sleep(0.01)
    finally:
        mirror.stop()
    assert mirror.passes >= 2 and mirror.age_s < 1
    assert mirror.get("00000001-0000-0002-0000-00030000000a").type == "minecraft:husk"
    assert [e.uuid for e in mirror.entities_near("minecraft:overworld", (3, 85, 3), 2)] == [
        "00000001-0000-0002-0000-00030000000a"]
    assert mirror.entities_near("minecraft:the_nether", (3, 85, 3), 2) == []
    assert [e.type for e in mirror.all()] == ["minecraft:husk"]          # the player is dropped
    assert not any(c == "list uuids" for c in srv.sent[1:])              # and no names asked for it
    assert srv.mirror_masks and all(name == "vfg-engine-mirror" and blocked for name, blocked in srv.mirror_masks)
