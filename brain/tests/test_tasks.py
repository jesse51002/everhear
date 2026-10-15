"""The tasks: in memory by name, every change written through whole, each task's writes in the
order they were made, TASKS as the brain and Respond see it, and the merge after a feed restart."""

import asyncio
import json

import pytest

from brain.tasks import Tasks, board

from conftest import Clock


class Sent:
    """`send`: every row given, in the order the writes went out; a write can be held."""

    def __init__(self):
        self.rows: list[dict] = []
        self.hold: dict[str, asyncio.Event] = {}

    async def __call__(self, row: dict) -> bool:
        gate = self.hold.get(row["name"])
        if gate is not None:
            await gate.wait()
        await asyncio.sleep(0)
        self.rows.append(row)
        return True


def galick(tasks: Tasks) -> None:
    tasks.open("galick_gun_styrk", "Styrkaeopteryx", "make me a galick gun", "a Galick Gun for Styrkaeopteryx",
               "A Galick Gun for Styrkaeopteryx: charge and fire.")


async def test_each_change_writes_the_whole_row_and_one_tasks_writes_go_out_in_order():
    sent = Sent()
    tasks = Tasks(send=sent, clock=Clock())
    sent.hold["galick_gun_styrk"] = asyncio.Event()      # the first write is slow to land
    galick(tasks)
    tasks.set_session("galick_gun_styrk", "sess-1")
    tasks.mirror_made("galick_gun_styrk", {"content": ["galick_gun_styrk"], "grants": ["Styrkaeopteryx:galick_gun_styrk"]})
    tasks.close("galick_gun_styrk", "done", "galick_gun_styrk done: a Galick Gun")
    tasks.open("castle_steve", "Steve", "a castle", "a castle at spawn", "A stone castle at spawn.")
    for _ in range(10):
        await asyncio.sleep(0)
    assert [r["name"] for r in sent.rows] == ["castle_steve"]  # another task's write never waits on it
    sent.hold["galick_gun_styrk"].set()
    await tasks.drain(1.0)
    gun = [r for r in sent.rows if r["name"] == "galick_gun_styrk"]
    assert [(r["status"], r["session"], r["made"]["content"]) for r in gun] == [
        ("open", None, []), ("open", "sess-1", []), ("open", "sess-1", ["galick_gun_styrk"]),
        ("done", "sess-1", ["galick_gun_styrk"])]
    assert gun[-1] == tasks.get("galick_gun_styrk") and set(gun[-1]) == {
        "name", "description", "player", "status", "line", "brief", "made", "variables", "session", "result",
        "created_at", "updated_at", "closed_at"}
    assert [r["updated_at"] for r in gun] == sorted(r["updated_at"] for r in gun) and gun[-1]["closed_at"]
    assert json.loads(json.dumps(gun[-1], ensure_ascii=False)) == gun[-1]
    assert tasks.writes == {}


async def test_drain_waits_for_the_writes_then_cuts_off_what_has_not_landed():
    sent = Sent()
    tasks = Tasks(send=sent)
    sent.hold["galick_gun_styrk"] = asyncio.Event()      # never lands
    galick(tasks)
    tasks.open("castle_steve", "Steve", "a castle", "a castle at spawn", "A stone castle at spawn.")
    await tasks.drain(0.05)
    assert [r["name"] for r in sent.rows] == ["castle_steve"] and tasks.writes == {}


async def test_a_change_is_written_only_when_it_changes_something_and_an_unknown_name_does_nothing():
    sent = Sent()
    tasks = Tasks(send=sent, clock=Clock())
    galick(tasks)
    tasks.describe("galick_gun_styrk", "")               # no DESCRIPTION line
    tasks.describe("galick_gun_styrk", "A Galick Gun for Styrkaeopteryx: charge and fire.")  # the same
    tasks.set_session("galick_gun_styrk", None)
    tasks.mirror_made("galick_gun_styrk", None)
    tasks.close("nobody_here", "done", "x")
    tasks.follow_up("nobody_here", "x", "y")
    await tasks.drain(1.0)
    assert len(sent.rows) == 1 and tasks.names() == {"galick_gun_styrk"}
    tasks.mirror_made("galick_gun_styrk", {"content": ["galick_gun_styrk"], "bindings": ["styrkaeopteryx.galick"]})
    tasks.mirror_made("galick_gun_styrk", {"content": ["galick_gun_styrk", "galick_beam", "galick_beam"],
                                           "bindings": ["styrkaeopteryx.galick"]})
    tasks.mirror_made("galick_gun_styrk", {"content": ["galick_gun_styrk", "galick_beam"],
                                           "bindings": ["styrkaeopteryx.galick"]})      # the same: nothing written
    assert tasks.get("galick_gun_styrk")["made"] == {"content": ["galick_gun_styrk", "galick_beam"],
                                                     "bindings": ["styrkaeopteryx.galick"], "grants": []}
    # a class gone and a binding undone leave the row, as a deploy mirrors the folder
    tasks.mirror_made("galick_gun_styrk", {"content": ["galick_beam"], "bindings": []})
    await tasks.drain(1.0)
    assert len(sent.rows) == 4
    assert tasks.get("galick_gun_styrk")["made"] == {"content": ["galick_beam"], "bindings": [], "grants": []}


async def test_open_refuses_a_taken_or_bad_name_and_a_follow_up_opens_the_task_again():
    tasks = Tasks(clock=Clock())                          # no send: nothing is written
    galick(tasks)
    for bad in ("galick_gun_styrk", "Galick", "7gun", "g", "cast", "x" * 33):
        with pytest.raises(ValueError):
            tasks.open(bad, "Steve", "…", "…", "…")
    tasks.close("galick_gun_styrk", "done", "galick_gun_styrk done: a Galick Gun")
    tasks.follow_up("galick_gun_styrk", "make it pierce blocks", "A Galick Gun that pierces blocks.")
    t = tasks.get("galick_gun_styrk")
    assert (t["status"], t["closed_at"], t["brief"], t["description"], t["result"]) == (
        "open", None, "make it pierce blocks", "A Galick Gun that pierces blocks.",
        "galick_gun_styrk done: a Galick Gun")
    tasks.note("galick_gun_styrk", "galick_gun_styrk: piercing next")
    assert tasks.get("galick_gun_styrk")["status"] == "open"
    tasks.get("galick_gun_styrk")["status"] = "failed"   # a copy: the memory is untouched
    assert tasks.open_ones()[0]["name"] == "galick_gun_styrk"


def test_tasks_shows_every_task_oldest_first_and_none_yet():
    assert Tasks().show() == "TASKS: none yet"
    tasks = Tasks(rows=[
        {"name": "castle_steve", "description": "A stone castle at spawn, 40x40.", "player": "Steve", "status": "done",
         "created_at": "2026-10-11T18:10:00.000Z"},
        {"name": "galick_gun_styrk", "description": "A Galick Gun for Styrkaeopteryx: " + "x" * 200,
         "player": "Styrkaeopteryx", "status": "open", "made": {"content": ["galick_gun_styrk"]},
         "created_at": "2026-10-11T18:22:33.120Z"},
        {"name": "lost_one", "description": "", "status": "failed", "created_at": "2026-10-11T18:30:00.000Z"},
        {"description": "no name: left out"}])
    shown = tasks.show(["galick_gun_styrk"]).splitlines()
    assert shown[0] == ("TASKS (every task on this server, oldest first: its name, for whom, its status, what it "
                        "is now; you never name one):")
    assert shown[1] == "- castle_steve for Steve, done: A stone castle at spawn, 40x40."
    assert shown[2].startswith("- galick_gun_styrk for Styrkaeopteryx, an implementer is on it: A Galick Gun")
    assert len(shown[2].split(": ", 1)[1]) == 160 and shown[2].endswith("…")
    assert shown[3] == "- lost_one for nobody, failed: " and len(shown) == 4
    assert tasks.get("galick_gun_styrk")["made"] == {"content": ["galick_gun_styrk"], "bindings": [], "grants": []}


async def test_merge_adds_what_the_memory_lacks_writes_back_what_the_read_lacks_and_the_memory_wins():
    sent = Sent()
    tasks = Tasks(send=sent, clock=Clock())
    galick(tasks)
    tasks.open("castle_steve", "Steve", "a castle", "a castle at spawn", "A stone castle at spawn.")
    await tasks.drain(1.0)
    sent.rows.clear()
    castle = tasks.get("castle_steve")
    tasks.merge([{"name": "castle_steve", "description": "an older castle", "status": "failed"},
                 {"name": "ice_trail_alex", "description": "An ice trail for Alex.", "player": "Alex",
                  "status": "done", "created_at": "2026-10-11T09:00:00.000Z"}])
    await tasks.drain(1.0)
    assert tasks.get("castle_steve")["description"] == "A stone castle at spawn."  # the memory wins
    assert tasks.get("ice_trail_alex")["status"] == "done"
    # the box had lost galick_gun_styrk, and held castle_steve older than the memory: both written back, whole
    assert sorted(r["name"] for r in sent.rows) == ["castle_steve", "galick_gun_styrk"]
    assert [r for r in sent.rows if r["name"] == "castle_steve"] == [castle]
    sent.rows.clear()
    tasks.merge(tasks.all())  # the box as the memory: nothing to write
    await tasks.drain(1.0)
    assert sent.rows == []


async def test_a_write_that_failed_is_sent_again_until_it_lands_and_a_newer_change_supersedes_it():
    tries = []

    async def flaky(row: dict) -> bool:  # EC2 out of reach for the first two tries
        tries.append(row)
        return len(tries) > 2

    tasks = Tasks(send=flaky, clock=Clock())
    tasks.retry_s = (0.01, 0.01, 0.01)
    galick(tasks)
    tasks.close("galick_gun_styrk", "done", "galick_gun_styrk done: a Galick Gun")
    for _ in range(50):
        await asyncio.sleep(0.005)
        if not tasks.writes:
            break
    assert tasks.writes == {}
    # the open row's write failed and was never retried: the close superseded it; the close's own
    # write failed once, then landed
    assert [r["status"] for r in tries] == ["open", "done", "done"]
    assert tries[-1] == tasks.get("galick_gun_styrk")

    fails = []

    async def down(row: dict) -> bool:
        fails.append(row)
        return False

    tasks = Tasks(send=down, clock=Clock())
    tasks.retry_s = (0.01, 0.01)
    galick(tasks)
    await asyncio.sleep(0.1)
    assert len(fails) == 3 and tasks.writes == {}  # one try and two retries, then given up, logged


def row(name: str, **more) -> dict:
    """A task's row, as the brain keeps it and `vfg task list --json` prints it."""
    base = {"name": name, "description": f"what {name} is", "player": "Alex", "status": "done",
            "line": "make me one", "brief": "make one",
            "made": {"content": [], "bindings": [], "grants": []}, "variables": None,
            "session": None, "result": "", "created_at": "2026-10-10T18:20:00.000Z",
            "updated_at": "2026-10-10T18:21:00.000Z", "closed_at": None}
    return {**base, **more}


def test_the_board_shows_each_task_oldest_first_with_what_it_made_what_of_it_runs_and_its_result():
    tasks = [
        row("lava_moat_sam", player="Sam", status="open", description="A lava moat round Sam's base, 3 wide.",
            created_at="2026-10-10T18:25:00.000Z", updated_at="2026-10-10T18:26:10.500Z"),
        row("frost_bow_alex", description="A Frost Bow for Alex: right-click to shoot freezing arrows.",
            made={"content": ["frost_bow_alex"], "bindings": [], "grants": ["Alex:frost_bow_alex"]},
            result="frost_bow_alex done: the bow is in Alex's hand", created_at="2026-10-09T23:59:00.000Z",
            updated_at="2026-10-09T23:59:30.000Z"),
    ]
    instances = [{"instance": "ability:frost_bow_alex#4", "kind": "ability", "id": "frost_bow_alex", "user": "Alex"},
                 {"instance": "scenario:horde#2", "kind": "scenario", "id": "horde", "user": None}]
    lines = board(tasks, instances, busy=["lava_moat_sam"], today="2026-10-10").splitlines()
    assert lines[0].startswith("TASKS (oldest first: name, for whom, status, when last written")
    assert lines[1] == ("- frost_bow_alex, for Alex, done, 2026-10-09 23:59:30: A Frost Bow for Alex: right-click "
                        "to shoot freezing arrows.")  # yesterday: its date shown
    assert lines[2] == "  made: content frost_bow_alex; grants Alex:frost_bow_alex; running now: ability:frost_bow_alex#4"
    assert lines[3] == "  last result: frost_bow_alex done: the bow is in Alex's hand"
    assert lines[4] == ("- lava_moat_sam, for Sam, open, an implementer is on it, 18:26:10: A lava moat round Sam's "
                        "base, 3 wide.")
    assert lines[5] == "  made: nothing yet; running now: none" and len(lines) == 6  # no result yet: no line
    assert board([], instances) == "TASKS: none yet"
    assert "  made: not known; running now: none" in board([row("castle_bob", made=None)], [], today="2026-10-10")
