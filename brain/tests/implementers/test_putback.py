"""Snapshots and code's put-back (putback.py): what a run is about to change, kept before each call
the guard lets through; and after a cancel or a limit, each made file, folder, copy and place made
what it was."""
import json
import os

from brain.implementers import putback
from brain.implementers.guard import GuardContext, gate
from brain.implementers.putback import Snapshot, put_back

from .fakes import FakeVfg, bad, ok


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def edit(path):
    return {"tool_name": "Edit", "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"}}


def put(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def running(home, copies):
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, "running.json"), "w") as f:
        json.dump({"fetched_at": 0, "copies": copies}, f)


def setup(tmp_path, vfg=None):
    repo, home = str(tmp_path / "repo"), str(tmp_path / "home")
    os.makedirs(repo)
    vfg = vfg or FakeVfg()
    snap = Snapshot(repo=repo, server="main", home=home, task="ice_beam_steve", vfg=vfg)
    g = GuardContext(repo=repo, task="ice_beam_steve", snapshot=snap)
    return repo, home, vfg, snap, g


async def test_made_files_come_back_and_what_the_run_created_goes(tmp_path):
    repo, home, vfg, snap, g = setup(tmp_path)
    put(repo, "made/main/scripts/ice_beam_steve/main.py", "v1")  # the task's own, from an earlier run
    put(repo, "made/main/actions/ice_beam_steve.py", "a1")
    assert await gate(edit("made/main/scripts/ice_beam_steve/main.py"), g) is None
    put(repo, "made/main/scripts/ice_beam_steve/main.py", "v2")
    assert await gate(edit("made/main/scripts/ice_beam_steve/main.py"), g) is None  # noted once: v1 is kept
    put(repo, "made/main/scripts/ice_beam_steve/main.py", "v3")
    assert await gate(bash("vfg script new frost_ring --from ice_beam && vfg preset save ice_beam_steve chill cold"), g) is None
    put(repo, "made/main/scripts/frost_ring/main.py", "new")
    put(repo, "made/main/scripts/frost_ring/helper.py", "new")
    put(repo, "made/main/actions/frost_ring.py", "new")
    put(repo, "made/main/presets/cold.py", "new")
    assert await gate(edit("made/main/scripts/frost_ring/main.py"), g) is None  # inside a folder that goes
    assert await gate({"tool_name": "Write", "tool_input": {"file_path": "made/main/presets/snow.py", "content": "x"}},
                      g) is None
    put(repo, "made/main/presets/snow.py", "x")
    assert await gate(edit("/etc/hosts"), g) is not None  # refused: never noted
    did, failed = await put_back(snap)
    assert failed == []
    assert sorted(did) == sorted(["restored made/main/scripts/ice_beam_steve/main.py", "removed made/main/actions/frost_ring.py",
                                  "removed made/main/presets/cold.py", "removed made/main/presets/snow.py",
                                  "removed made/main/scripts/frost_ring/"])
    assert open(os.path.join(repo, "made/main/scripts/ice_beam_steve/main.py")).read() == "v1"
    assert open(os.path.join(repo, "made/main/actions/ice_beam_steve.py")).read() == "a1"  # untouched
    assert not os.path.exists(os.path.join(repo, "made/main/scripts/frost_ring"))
    assert vfg.calls == []  # no copy and no place: nothing for vfg
    # again: all is back, nothing to do
    assert await put_back(snap) == ([], [])


BEAM = {"action": "beam", "script": "beam", "values": {"radius": 5, "color": "red", "loud": True}, "task": "beam_alex",
        "started": 100, "runs": "until_stopped"}
RAIN = {"action": "tnt_rain", "script": "tnt_rain", "values": {"count": 3}, "task": None, "started": 50,
        "runs": "until_stopped"}


async def test_copies_the_run_started_stop_and_those_it_changed_or_stopped_come_back(tmp_path):
    repo, home, vfg, snap, g = setup(tmp_path)
    running(home, {"beam_alex": BEAM, "old_rain": RAIN, "other": dict(RAIN, task="zoo_alex")})
    assert await gate(bash("vfg script set beam_alex radius=9 loud=false"), g) is None
    assert await gate(bash("vfg action run frost player=Steve --task ice_beam_steve"), g) is None
    assert await gate(bash("vfg script stop old_rain"), g) is None
    running(home, {"beam_alex": dict(BEAM, values={"radius": 9, "color": "red", "loud": False}),
                   "frost": {"action": "frost", "script": "frost", "values": {}, "task": "ice_beam_steve", "started": 200},
                   "zoo_new": dict(RAIN, task="zoo_alex", started=300),  # another task's: left alone
                   "other": dict(RAIN, task="zoo_alex")})
    did, failed = await put_back(snap)
    assert failed == []
    assert vfg.calls == [["script", "stop", "frost"], ["script", "set", "beam_alex", "loud=true", "radius=5"],
                         ["action", "run", "tnt_rain", "count=3", "as=old_rain"]]
    assert did == ["stopped frost", "set beam_alex back (loud, radius)", "started old_rain again as it was"]


async def test_a_copy_restarted_on_changed_code_starts_again_on_the_code_as_it_was(tmp_path):
    repo, home, vfg, snap, g = setup(tmp_path, FakeVfg().on("action", "run", answer=bad("vfg: no such player")))
    running(home, {"beam_alex": BEAM})
    with open(os.path.join(home, "actions.json"), "w") as f:  # color is fixed: not given again
        json.dump({"actions": {"beam": {"fixed": {"color": "red"}}}}, f)
    put(repo, "made/main/scripts/beam/main.py", "v1")
    assert await gate(edit("made/main/scripts/beam/main.py"), g) is None
    put(repo, "made/main/scripts/beam/main.py", "v2")
    assert await gate(bash("vfg action run edit copy=beam_alex radius=7 --task ice_beam_steve"), g) is None
    did, failed = await put_back(snap)  # the values are the same, but its code was put back
    assert vfg.calls == [["script", "stop", "beam_alex"],
                         ["action", "run", "beam", "loud=true", "radius=5", "as=beam_alex", "--task", "beam_alex"]]
    assert did == ["restored made/main/scripts/beam/main.py"]
    assert failed == ["could not start beam_alex again (exit 1: vfg: no such player)"]
    assert putback.line(did, failed) == ("Code put back: restored made/main/scripts/beam/main.py. It could not: could "
                                         "not start beam_alex again (exit 1: vfg: no such player).")


async def test_a_place_the_run_saved_is_forgotten_or_saved_again_where_it_was(tmp_path):
    places = {"castle": {"pos": [10, 64, -3], "dim": "overworld", "by": "castle_steve"}}
    vfg = FakeVfg().on("place", "list", answer=ok(json.dumps(places)))
    repo, home, vfg, snap, g = setup(tmp_path, vfg)
    assert await gate(bash("vfg place save castle --player Steve --by ice_beam_steve; "
                           "vfg place save arena --at 0 85 0 --by ice_beam_steve"), g) is None
    assert len(vfg.made("place", "list")) == 2  # once a place
    did, failed = await put_back(snap)
    assert vfg.made("place", "forget") == [["place", "forget", "arena"]]
    assert vfg.made("place", "save") == [["place", "save", "castle", "--at", "10", "64", "-3", "--dim", "overworld",
                                          "--by", "castle_steve"]]
    assert (did, failed) == (["forgot the place arena", "saved the place castle where it was"], [])


def test_the_line_when_nothing_changed():
    assert putback.line([], []) == "Code put back: nothing; the run had changed no made file, copy or place."


def test_a_copy_is_started_again_with_the_values_it_was_given_only(tmp_path):
    home = str(tmp_path)
    with open(os.path.join(home, "actions.json"), "w") as f:
        json.dump({"actions": {"beam": {"fixed": {"color": "red"}, "fields": {
            "radius": {"default": 5}, "color": {"default": "red"}, "loud": {"default": False},
            "targets": {"default": []}, "player": {"default": "@a"}}}}}, f)
    values = {"radius": 5, "color": "red", "loud": True, "targets": ["zombie", "skeleton"], "player": "Steve",
              "speed": 1.0}
    assert putback.given_words(home, "beam", values) == [
        "loud=true", "player=Steve", "speed=1", 'targets=["zombie","skeleton"]']
    assert putback.given_words(home, "unknown", {"a": 1}) == ["a=1"]  # no cache entry: every value, as words


async def test_a_snapshot_kept_on_disk_comes_back_whole(tmp_path):
    repo, home, vfg, snap, g = setup(tmp_path)
    put(repo, "made/main/scripts/ice_beam_steve/main.py", "v1\x00\xff")
    running(home, {"beam_alex": BEAM})
    assert await gate(edit("made/main/scripts/ice_beam_steve/main.py"), g) is None
    assert await gate(bash("vfg script new frost_ring --from ice_beam; vfg script set beam_alex radius=9"), g) is None
    path = str(tmp_path / "brain" / "implementer-snapshots" / "ice_beam_steve.json")
    putback.save(snap, path)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600" and oct(os.stat(os.path.dirname(path)).st_mode & 0o777) == "0o700"
    back = putback.load(path, vfg)
    assert (back.files, back.dirs, back.copies, back.running_before, back.places, back.task) == (
        snap.files, snap.dirs, snap.copies, snap.running_before, snap.places, "ice_beam_steve")
    assert putback.load(str(tmp_path / "none.json"), vfg) is None
    with open(path, "w") as f:
        f.write("{broken")
    assert putback.load(path, vfg) is None


async def test_the_skeleton_tests_vfg_writes_are_noted_and_removed(tmp_path):
    repo, home, vfg, snap, g = setup(tmp_path)
    assert await gate(bash("vfg script new frost_ring --from ice_beam && vfg test frost_ring"), g) is None
    assert await gate(bash("vfg test ice_beam_steve -n 2"), g) is None
    put(repo, "made/main/tests/frost_ring.py", "skeleton")
    put(repo, "made/main/tests/ice_beam_steve.py", "skeleton")
    did, failed = await put_back(snap)
    assert failed == [] and "removed made/main/tests/frost_ring.py" in did and "removed made/main/tests/ice_beam_steve.py" in did
    assert not os.path.exists(os.path.join(repo, "made/main/tests/frost_ring.py"))
