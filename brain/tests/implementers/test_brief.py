"""The brief an implementer starts with (brief.py): the brain's, the task, the saved places, then
the EXAMPLES block; whatever fails is left out and the brief still goes."""
from brain.implementers import brief, examples
from brain.implementers.config import ImplementerConfig

from .fakes import FakeVfg, bad, ok


def test_the_saved_places_line():
    assert brief.places_line(ok("castle 197 68 251 overworld (T23)\nhome -368 70 912 overworld (T41)\n")) == (
        "SAVED PLACES: castle 197 68 251 overworld (T23); home -368 70 912 overworld (T41)")
    assert brief.places_line(ok("none\n")) == brief.places_line(ok("")) == "SAVED PLACES: none"
    assert brief.places_line(bad("vfg: EC2 unreachable")) == "SAVED PLACES: unavailable (exit 1: vfg: EC2 unreachable)"
    assert brief.places_line(bad("vfg: error: argument command: invalid choice: 'place'", 2)) == brief.PLACES_OFF


async def test_the_brief_in_order_and_what_fails_left_out(tmp_path, monkeypatch):
    cfg = ImplementerConfig.load({"VFG_REPO": str(tmp_path)})
    seen = []

    async def build(cfg, job, task, run):
        seen.append((job, task))
        return "EXAMPLES, picked for %s." % task

    monkeypatch.setattr(examples, "build", build)
    vfg = FakeVfg().on("place", "list", answer=ok("castle 1 2 3 overworld (T2)\n"))
    assert await brief.compose("A hut by the castle.\n", "T9", cfg, vfg) == (
        "A hut by the castle.\n\n"
        "YOUR TASK: T9. Every vfg action run you send takes --task T9, and a vfg place save --by T9.\n"
        "SAVED PLACES: castle 1 2 3 overworld (T2)\n\n"
        "EXAMPLES, picked for T9.")
    assert seen == [("A hut by the castle.\n", "T9")]  # the router reads the brain's brief alone

    async def broken(cfg, job, task, run):
        raise RuntimeError("no toolkit")

    monkeypatch.setattr(examples, "build", broken)
    vfg.on("place", "list", answer=lambda argv: 1 / 0)
    assert await brief.compose("A hut.", "T9", cfg, vfg) == (
        "A hut.\n\nYOUR TASK: T9. Every vfg action run you send takes --task T9, and a vfg place save --by T9.")
