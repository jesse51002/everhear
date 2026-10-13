"""The brief an implementer starts with (brief.py): the brain's, the task's name and that what
it makes takes it, the saved places, then the EXAMPLES block; whatever fails is left out and the
brief still goes; one log line says each part's size."""
from brain.implementers import brief, examples
from brain.implementers.config import ImplementerConfig

from .fakes import FakeVfg, bad, ok
from .test_examples import JOB, Repo


def test_the_saved_places_line():
    assert brief.places_line(ok("castle 197 68 251 overworld (castle_steve)\nhome -368 70 912 overworld (house_alex)\n")) == (
        "SAVED PLACES: castle 197 68 251 overworld (castle_steve); home -368 70 912 overworld (house_alex)")
    assert brief.places_line(ok("none\n")) == brief.places_line(ok("")) == "SAVED PLACES: none"
    assert brief.places_line(bad("vfg: EC2 unreachable")) == "SAVED PLACES: unavailable (exit 1: vfg: EC2 unreachable)"
    assert brief.places_line(bad("vfg: error: argument command: invalid choice: 'place'", 2)) == brief.PLACES_OFF


async def test_the_brief_in_order_and_what_fails_left_out(tmp_path, monkeypatch):
    cfg = ImplementerConfig.load({"VFG_REPO": str(tmp_path)})
    seen = []

    async def build(cfg, job, task, run, sizes=None):
        seen.append((job, task))
        return "EXAMPLES, picked for %s." % task

    monkeypatch.setattr(examples, "build", build)
    vfg = FakeVfg().on("place", "list", answer=ok("castle 1 2 3 overworld (castle_steve)\n"))
    assert await brief.compose("A hut by the castle.\n", "hut_alex", cfg, vfg) == (
        "A hut by the castle.\n\n"
        "YOUR TASK: hut_alex. Name what you make hut_alex: vfg script new hut_alex, its action hut_alex, and run it "
        "as=hut_alex. Every vfg action run you send takes --task hut_alex, and a vfg place save --by hut_alex.\n"
        "SAVED PLACES: castle 1 2 3 overworld (castle_steve)\n\n"
        "EXAMPLES, picked for hut_alex.")
    assert seen == [("A hut by the castle.\n", "hut_alex")]  # the router reads the orchestrator's brief alone

    async def broken(cfg, job, task, run, sizes=None):
        raise RuntimeError("no toolkit")

    monkeypatch.setattr(examples, "build", broken)
    vfg.on("place", "list", answer=lambda argv: 1 / 0)
    assert await brief.compose("A hut.", "hut_alex", cfg, vfg) == "A hut.\n\n" + brief.task_line("hut_alex")


async def test_one_log_line_says_each_parts_size(tmp_path, monkeypatch):
    r = Repo(tmp_path)
    r.vfg.on("place", "list", answer=ok("castle 1 2 3 overworld (castle_steve)\n"))
    said = []
    monkeypatch.setattr(brief, "log", lambda area, msg: said.append((area, msg)))
    text = await brief.compose(JOB, "ice_beam_steve", r.cfg, r.vfg)
    lines = [m for a, m in said if a == "brief" and m.startswith("ice_beam_steve: ")]
    assert len(lines) == 1
    line = lines[0]
    head = brief.task_line("ice_beam_steve") + "\nSAVED PLACES: castle 1 2 3 overworld (castle_steve)"
    assert line.startswith("ice_beam_steve: %d chars: the brain's brief %d, task and places %d, header " % (
        len(text), len(JOB), len(head) + 4))
    for part in ("actions ", " (2 in full, by Jev)", "made actions ", " (1)", "scripts ", " (2)", "presets ",
                 "named ", "running 0 (0); route jev, state ok, "):
        assert part in line
    # the parts add up to the brief
    import re
    nums = [int(n) for n in re.findall(r"(?:brief|places|header|made|actions|scripts|presets|named|running) (\d+)",
                                       line)]
    assert sum(nums) == len(text)
    # with no EXAMPLES block
    async def broken(cfg, job, task, run, sizes=None):
        raise RuntimeError("no toolkit")

    monkeypatch.setattr(examples, "build", broken)
    said.clear()
    await brief.compose("A hut.", "hut_alex", r.cfg, r.vfg)
    assert [m for a, m in said if a == "brief" and m.startswith("hut_alex: ")] == [
        "hut_alex: the EXAMPLES block failed, left out: no toolkit",
        "hut_alex: %d chars: the brain's brief 6, task and places %d, no EXAMPLES block" % (
            len("A hut.\n\n" + brief.task_line("hut_alex") + "\nSAVED PLACES: castle 1 2 3 overworld (castle_steve)"),
            len(brief.task_line("hut_alex") + "\nSAVED PLACES: castle 1 2 3 overworld (castle_steve)") + 2)]
