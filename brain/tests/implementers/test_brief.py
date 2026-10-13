"""The brief an implementer starts with (brief.py): the brain's, the task's name and that what
it makes takes it, the saved places (the engine's), then the EXAMPLES block; whatever fails is left
out and the brief still goes; one log line says each part's size."""
import json
import re

from brain.implementers import brief, examples
from brain.implementers.config import ImplementerConfig

from .fakes import FakeVfg, bad, ok
from .test_examples import JOB, Repo

PLACES = {"ok": True, "version": 4, "places": [
    {"name": "castle", "dim": "minecraft:overworld", "pos": [197, 68, 251], "yaw": 90, "by": "castle_steve", "at": 1},
    {"name": "home", "dim": "overworld", "pos": [-368.25, 70.0, 912.71], "yaw": 0, "by": None, "at": 2}]}
CASTLE = {"ok": True, "version": 1, "places": [PLACES["places"][0]]}
CASTLE_LINE = "SAVED PLACES: castle 197 68 251 overworld (castle_steve)"


def test_the_saved_places_line():
    assert brief.places_line(PLACES) == (
        "SAVED PLACES: castle 197 68 251 overworld (castle_steve); home -368.2 70 912.7 overworld")
    assert brief.places_line({"ok": True, "places": []}) == brief.places_line({"ok": True}) == "SAVED PLACES: none"
    assert brief.places_line(None, "exit 6: vfg: the engine is not running") == (
        "SAVED PLACES: unavailable (exit 6: vfg: the engine is not running)")


async def test_the_places_are_the_engines_read_fresh():
    vfg = FakeVfg().on("engine", "places", answer=ok(json.dumps(CASTLE)))
    assert await brief.saved_places(vfg) == CASTLE_LINE
    assert vfg.calls == [["engine", "places", "--json"]]
    # refused, with the engine's words in its reply
    vfg.on("engine", "places", answer=type(ok())(1, json.dumps({"ok": False, "error": "no world yet"}), ""))
    assert await brief.saved_places(vfg) == "SAVED PLACES: unavailable (exit 1: no world yet)"
    vfg.on("engine", "places", answer=bad("vfg: EC2 unreachable"))
    assert await brief.saved_places(vfg) == "SAVED PLACES: unavailable (exit 1: vfg: EC2 unreachable)"


async def test_the_brief_in_order_and_what_fails_left_out(tmp_path, monkeypatch):
    cfg = ImplementerConfig.load({"VFG_REPO": str(tmp_path)})
    seen = []

    async def build(cfg, job, task, run, sizes=None):
        seen.append((job, task))
        return "EXAMPLES, picked for %s." % task

    monkeypatch.setattr(examples, "build", build)
    vfg = FakeVfg().on("engine", "places", answer=ok(json.dumps(CASTLE)))
    assert await brief.compose("A hut by the castle.\n", "hut_alex", cfg, vfg) == (
        "A hut by the castle.\n\n"
        "YOUR TASK: hut_alex. Name what you make hut_alex: your class made/main/content/hut_alex.py (another it "
        "needs, hut_alex_<what>), its test made/main/tests/hut_alex.py. Every vfg engine bind you send takes --by "
        "hut_alex.\n" + CASTLE_LINE + "\n\n"
        "EXAMPLES, picked for hut_alex.")
    assert seen == [("A hut by the castle.\n", "hut_alex")]  # the router reads the brain's brief alone

    async def broken(cfg, job, task, run, sizes=None):
        raise RuntimeError("no toolkit")

    monkeypatch.setattr(examples, "build", broken)
    vfg.on("engine", "places", answer=lambda argv: 1 / 0)
    assert await brief.compose("A hut.", "hut_alex", cfg, vfg) == "A hut.\n\n" + brief.task_line("hut_alex", "main")
    assert "made/lobby/content/hut_alex.py" in brief.task_line("hut_alex", "lobby")


async def test_one_log_line_says_each_parts_size(tmp_path, monkeypatch):
    r = Repo(tmp_path)
    r.vfg.on("engine", "places", answer=ok(json.dumps(CASTLE)))
    said = []
    monkeypatch.setattr(brief, "log", lambda area, msg: said.append((area, msg)))
    text = await brief.compose(JOB, "ice_staff_steve", r.cfg, r.vfg)
    lines = [m for a, m in said if a == "brief" and m.startswith("ice_staff_steve: ")]
    assert len(lines) == 1
    line = lines[0]
    head = brief.task_line("ice_staff_steve", "main") + "\n" + CASTLE_LINE
    assert line.startswith("ice_staff_steve: %d chars: the brain's brief %d, task and places %d, header " % (
        len(text), len(JOB), len(head) + 4))
    for part in ("made ", "classes ", " (2), tests 0 (0), named ", " (2); route jev, state ok, instances ok, "):
        assert part in line
    # the parts add up to the brief
    nums = [int(n) for n in re.findall(r"(?:brief|places|header|made|classes|tests|named) (\d+)", line)]
    assert sum(nums) == len(text)

    # with no EXAMPLES block
    async def broken(cfg, job, task, run, sizes=None):
        raise RuntimeError("no toolkit")

    monkeypatch.setattr(examples, "build", broken)
    said.clear()
    await brief.compose("A hut.", "hut_alex", r.cfg, r.vfg)
    head = brief.task_line("hut_alex", "main") + "\n" + CASTLE_LINE
    assert [m for a, m in said if a == "brief" and m.startswith("hut_alex: ")] == [
        "hut_alex: the EXAMPLES block failed, left out: no toolkit",
        "hut_alex: %d chars: the brain's brief 6, task and places %d, no EXAMPLES block" % (
            len("A hut.\n\n" + head), len(head) + 2)]
