"""Snapshots and code's put-back (putback.py): what a run is about to change, kept before each call
the guard lets through; and after a cancel or a limit, each class and test file made what it was,
one engine reload, each binding and each grant as it was (CONTRACT2, "Put-back of a run")."""
import json
import os

from brain.implementers import putback
from brain.implementers.guard import GuardContext, gate
from brain.implementers.putback import Snapshot, put_back

from .fakes import FakeVfg, bad, ok

TASK = "ice_staff_steve"


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def edit(path):
    return {"tool_name": "Edit", "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"}}


def write(path):
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": "x"}}


def put(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def setup(tmp_path, vfg=None):
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    vfg = vfg or FakeVfg()
    snap = Snapshot(repo=repo, server="main", task=TASK, vfg=vfg)
    g = GuardContext(repo=repo, task=TASK, snapshot=snap)
    return repo, vfg, snap, g


class Engine:
    """The engine's bindings and grants as the fake vfg answers them, changed by hand between calls."""

    def __init__(self, vfg: FakeVfg, bindings=(), grants=()):
        self.bindings = list(bindings)
        self.grants = list(grants)  # (user, ability)
        vfg.on("engine", "incantations", answer=lambda argv: ok(json.dumps({"ok": True, "incantations": self.bindings})))
        vfg.on("engine", "instances", answer=lambda argv: ok(json.dumps({"ok": True, "instances": [
            {"instance": "ability:%s#%d" % (a, n), "kind": "ability", "id": a, "user": u}
            for n, (u, a) in enumerate(self.grants)]})))


def entry(player, phrase, ability=None, **kw):
    out = {"id": "%s.%s" % (player.lower(), phrase.replace(" ", "_")), "player": player, "phrase": phrase,
           "aliases": [], "everyday_aliases": [], "ability": ability, "cooldown_s": 5}
    out.update(kw)
    return out


async def test_made_files_come_back_what_the_run_created_goes_and_the_engine_reloads(tmp_path):
    repo, vfg, snap, g = setup(tmp_path)
    put(repo, "made/main/content/ice_staff_steve.py", "v1")  # the task's own, from an earlier run
    put(repo, "made/main/content/castle_alex.py", "c1")
    assert await gate(edit("made/main/content/ice_staff_steve.py"), g) is None
    put(repo, "made/main/content/ice_staff_steve.py", "v2")
    assert await gate(edit("made/main/content/ice_staff_steve.py"), g) is None  # noted once: v1 is kept
    put(repo, "made/main/content/ice_staff_steve.py", "v3")
    assert await gate(write("made/main/content/ice_staff_steve_bolt.py"), g) is None
    put(repo, "made/main/content/ice_staff_steve_bolt.py", "new")
    assert await gate(write(os.path.join(repo, "made/main/tests/ice_staff_steve.py")), g) is None
    put(repo, "made/main/tests/ice_staff_steve.py", "test")
    assert await gate(edit("/etc/hosts"), g) is not None  # refused: never noted
    assert await gate(edit("engine/library/fireball.py"), g) is not None
    did, failed = await put_back(snap)
    assert failed == []
    assert did == ["restored made/main/content/ice_staff_steve.py", "removed made/main/content/ice_staff_steve_bolt.py",
                   "removed made/main/tests/ice_staff_steve.py", "reloaded the engine"]
    assert open(os.path.join(repo, "made/main/content/ice_staff_steve.py")).read() == "v1"
    assert open(os.path.join(repo, "made/main/content/castle_alex.py")).read() == "c1"  # untouched
    assert vfg.calls == [["engine", "reload"]]  # one reload, of every file: no binding or grant to read
    # again: all is back; the engine is reloaded once more, as the files are
    assert await put_back(snap) == (["reloaded the engine"], [])


async def test_a_test_file_alone_needs_no_reload_and_a_reload_that_fails_is_said(tmp_path):
    repo, vfg, snap, g = setup(tmp_path, FakeVfg().on("engine", "reload", answer=bad("vfg: the engine is not running", 6)))
    assert await gate(write("made/main/tests/ice_staff_steve.py"), g) is None
    put(repo, "made/main/tests/ice_staff_steve.py", "test")
    assert await put_back(snap) == (["removed made/main/tests/ice_staff_steve.py"], [])
    assert vfg.calls == []
    assert await gate(write("made/main/content/ice_staff_steve.py"), g) is None
    put(repo, "made/main/content/ice_staff_steve.py", "x")
    did, failed = await put_back(snap)
    assert did == ["removed made/main/content/ice_staff_steve.py"]
    assert failed == ["could not reload the engine (exit 6: vfg: the engine is not running), so it may still run the "
                      "run's classes"]
    assert putback.line(did, failed) == (
        "Code put back: removed made/main/content/ice_staff_steve.py. It could not: could not reload the engine "
        "(exit 6: vfg: the engine is not running), so it may still run the run's classes.")


async def test_a_binding_the_run_made_is_unbound_and_one_it_replaced_or_unbound_is_bound_again(tmp_path):
    vfg = FakeVfg()
    old_meteor = entry("Alex", "rain fire", None, scenario="meteor", args={"count": 3, "at": "caster"})
    engine = Engine(vfg, bindings=[entry("Steve", "fire ball", "fireball"), old_meteor,
                                   entry("Steve", "freeze", "frost", aliases=["froze", "freez"])])
    repo, vfg, snap, g = setup(tmp_path, vfg)
    assert await gate(bash("vfg engine bind Steve 'ice ball' ice_staff_steve --by %s && "
                           "vfg engine bind Steve 'Fire Ball' ice_staff_steve --by %s" % (TASK, TASK)), g) is None
    assert await gate(bash("vfg engine unbind Alex 'rain fire'; vfg engine bind Steve freeze ice_staff_steve --by %s"
                           % TASK), g) is None
    assert await gate(bash("vfg engine bind Steve 'ice ball' ice_staff_steve --by %s" % TASK), g) is None  # noted once
    assert len(vfg.made("engine", "incantations")) == 1  # read once, at the run's first bind
    # what the run did, as the engine has it now
    engine.bindings = [entry("Steve", "fire ball", "ice_staff_steve"), entry("Steve", "ice ball", "ice_staff_steve"),
                       entry("Steve", "freeze", "ice_staff_steve")]
    did, failed = await put_back(snap)
    assert failed == []
    # each as it was: its aliases as listed (no alias call), a scenario's cooldown too
    assert vfg.made("engine", "bind") == [
        ["engine", "bind", "Alex", "rain fire", "--scenario", "meteor", "at=caster", "count=3", "--cooldown", "5",
         "--aliases", "[]"],
        ["engine", "bind", "Steve", "fire ball", "fireball", "--aliases", "[]"],
        ["engine", "bind", "Steve", "freeze", "frost", "--aliases", '["froze","freez"]']]
    assert vfg.made("engine", "unbind") == [["engine", "unbind", "Steve", "ice ball"]]
    assert did == ["bound alex.rain_fire again as it was", "bound steve.fire_ball again as it was",
                   "bound steve.freeze again as it was", "unbound steve.ice_ball"]
    # bound by the brain with its task, the listing naming it: --by goes again too
    assert putback.bind_words(entry("Steve", "x", "y", by="castle_alex"))[-2:] == ["--by", "castle_alex"]
    marked = entry("Steve", "ice beam", "y", aliases=["ice cream", "ice been"], everyday_aliases=["ice cream"])
    assert putback.bind_words(marked)[5:] == ["--aliases", '["ice cream","ice been"]', "--everyday", '["ice cream"]']


async def test_bindings_the_implementer_put_back_itself_are_left_alone_and_a_failed_read_is_said(tmp_path):
    vfg = FakeVfg()
    engine = Engine(vfg, bindings=[entry("Steve", "fire ball", "fireball")])
    repo, vfg, snap, g = setup(tmp_path, vfg)
    assert await gate(bash("vfg engine bind Steve 'fire ball' ice_staff_steve --by %s; "
                           "vfg engine bind Steve 'ice ball' ice_staff_steve --by %s" % (TASK, TASK)), g) is None
    engine.bindings = [entry("Steve", "fire ball", "fireball")]  # as it was: the implementer put both back
    assert await put_back(snap) == ([], [])
    assert vfg.made("engine", "bind") == vfg.made("engine", "unbind") == []
    vfg.on("engine", "incantations", answer=bad("vfg: the engine is not running", 6))
    assert await put_back(snap) == ([], ["could not read the bindings to put them back (exit 6: vfg: the engine is "
                                         "not running)"])
    # a read that fails before the bind: that binding is not noted, and the next bind reads again
    repo, vfg, snap, g = setup(tmp_path / "again", FakeVfg().on("engine", "incantations", answer=bad("no", 4)))
    assert await gate(bash("vfg engine bind Steve 'x' fireball --by %s" % TASK), g) is None
    assert snap.bound == {} and snap.incantations is None


async def test_a_grant_the_run_made_is_revoked_and_one_it_revoked_is_granted_again(tmp_path):
    vfg = FakeVfg()
    engine = Engine(vfg, grants=[("Steve", "fireball"), ("Alex", "frost")])
    repo, vfg, snap, g = setup(tmp_path, vfg)
    assert await gate(bash("vfg engine grant Steve ice_staff_steve && vfg engine grant steve fireball && "
                           "vfg engine revoke Alex frost && vfg engine grant Alex ice_staff_steve"), g) is None
    assert len(vfg.made("engine", "instances")) == 1
    engine.grants = [("Steve", "fireball"), ("Steve", "ice_staff_steve")]  # Alex's went with a reload, say
    did, failed = await put_back(snap)
    assert failed == []
    assert vfg.made("engine", "revoke") == [["engine", "revoke", "Steve", "ice_staff_steve"]]
    assert vfg.made("engine", "grant") == [["engine", "grant", "Alex", "frost"]]  # the one it had, Steve's fireball, stays
    assert did == ["granted frost to Alex again", "revoked ice_staff_steve from Steve"]


async def test_the_order_files_reload_bindings_grants(tmp_path):
    vfg = FakeVfg()
    engine = Engine(vfg)
    repo, vfg, snap, g = setup(tmp_path, vfg)
    assert await gate(write("made/main/content/ice_staff_steve.py"), g) is None
    put(repo, "made/main/content/ice_staff_steve.py", "x")
    assert await gate(bash("vfg engine reload ice_staff_steve && vfg engine grant Steve ice_staff_steve && "
                           "vfg engine bind Steve 'ice ball' ice_staff_steve --by %s" % TASK), g) is None
    engine.grants, engine.bindings = [("Steve", "ice_staff_steve")], [entry("Steve", "ice ball", "ice_staff_steve")]
    vfg.calls.clear()
    did, failed = await put_back(snap)
    assert [c[:2] for c in vfg.calls] == [["engine", "reload"], ["engine", "incantations"], ["engine", "unbind"],
                                          ["engine", "instances"], ["engine", "revoke"]]
    assert did == ["removed made/main/content/ice_staff_steve.py", "reloaded the engine", "unbound steve.ice_ball",
                   "revoked ice_staff_steve from Steve"] and failed == []


def test_the_line_when_nothing_changed():
    assert putback.line([], []) == "Code put back: nothing; the run had changed no made file, binding or grant."


def test_a_value_as_a_word():
    assert [putback.word(v) for v in (True, 6.0, 0.5, "a b", ["x", 1], {"k": 1})] == [
        "true", "6", "0.5", "a b", '["x",1]', '{"k":1}']


async def test_a_snapshot_kept_on_disk_comes_back_whole(tmp_path):
    vfg = FakeVfg()
    Engine(vfg, bindings=[entry("Steve", "fire ball", "fireball")], grants=[("Steve", "fireball")])
    repo, vfg, snap, g = setup(tmp_path, vfg)
    put(repo, "made/main/content/ice_staff_steve.py", "v1\x00\xff")
    assert await gate(edit("made/main/content/ice_staff_steve.py"), g) is None
    assert await gate(bash("vfg engine bind Steve 'fire ball' ice_staff_steve --by %s; vfg engine grant Alex x" % TASK),
                      g) is None
    path = str(tmp_path / "brain" / "implementer-snapshots" / "ice_staff_steve.json")
    putback.save(snap, path)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600" and oct(os.stat(os.path.dirname(path)).st_mode & 0o777) == "0o700"
    back = putback.load(path, vfg)
    assert (back.files, back.incantations, back.bound, back.granted, back.grants, back.task) == (
        snap.files, snap.incantations, snap.bound, snap.granted, snap.grants, TASK)
    assert back.bound == {"steve.fire_ball": ["Steve", "fire ball"]} and back.granted == ["steve:fireball"]
    assert putback.load(str(tmp_path / "none.json"), vfg) is None
    with open(path, "w") as f:
        f.write("{broken")
    assert putback.load(path, vfg) is None
    # one kept before the engine (copies and places, no bindings): not read
    with open(path, "w") as f:
        json.dump({"repo": repo, "server": "main", "home": "/h", "task": TASK, "files": {}, "dirs": {}, "copies": {},
                   "running_before": None, "places": {}}, f)
    assert putback.load(path, vfg) is None
