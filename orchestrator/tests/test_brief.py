# Not in the TypeScript tests: the runner adds the saved places to every implementer's brief.
import os
import time

from helpers import temp_dir, use_fake_vfg, write

from orchestrator import brief
from orchestrator.guard import GuardContext, make_guard

PLACES = "castle 197 68 251 overworld (T23)\nhome -368 70 912 overworld (T41)\n"
AGENT = {"subagent_type": "implementer", "description": "T9 hut next to the castle", "prompt": "T9: a hut next to the castle.",
         "run_in_background": True}


def guard(**over):
    allowed: list[str] = []
    fields = dict(repo="/home/k/everhear", max_implementers=4, implementer_model="sonnet", main_thread_rule=True,
                  open_task_ids=lambda: ["T9"], active_implementers=lambda: 0,
                  on_agent_allowed=lambda t, u: allowed.append("%s:%s" % (t, u)))
    fields.update(over)
    g = GuardContext(**fields)
    g.allowed = allowed
    return g


def agent_call(tool_input):
    return {"session_id": "s", "transcript_path": "t", "cwd": "/home/k/everhear", "hook_event_name": "PreToolUse",
            "tool_name": "Agent", "tool_input": tool_input, "tool_use_id": "u1"}


# ---------------------------------------------------------------- the SAVED PLACES step

async def test_saved_places_one_line_joined_with_semicolons_read_fresh():
    d = temp_dir()
    fake = use_fake_vfg(d)
    os.environ["VFG_FAKE_PLACES"] = os.path.join(d, "places")
    write(os.environ["VFG_FAKE_PLACES"], PLACES)
    out = await brief.rewrite("T9: a hut.\n", AGENT)
    assert out == "T9: a hut.\n\nSAVED PLACES: castle 197 68 251 overworld (T23); home -368 70 912 overworld (T41)"
    write(os.environ["VFG_FAKE_PLACES"], "castle 197 68 251 overworld (T23)\n")
    assert (await brief.rewrite("x", AGENT)).endswith("SAVED PLACES: castle 197 68 251 overworld (T23)")
    assert fake.calls() == [["place", "list"], ["place", "list"]]  # once per brief, never cached


async def test_saved_places_none():
    use_fake_vfg(temp_dir())
    assert await brief.rewrite("x", AGENT) == "x\n\nSAVED PLACES: none"


async def test_saved_places_unavailable_says_why_and_the_brief_still_goes():
    use_fake_vfg(temp_dir())
    os.environ["VFG_FAKE_PLACES_FAIL"] = "EC2 unreachable (fake)"
    assert await brief.rewrite("x", AGENT) == "x\n\nSAVED PLACES: unavailable (exit 1: vfg: EC2 unreachable (fake))"


async def test_saved_places_a_slow_vfg_is_cut_off(monkeypatch):
    use_fake_vfg(temp_dir())
    monkeypatch.setattr(brief, "PLACES_TIMEOUT_MS", 200)
    os.environ["VFG_FAKE_PLACES_SLEEP"] = "5"
    t = time.monotonic()
    assert await brief.rewrite("x", AGENT) == "x\n\nSAVED PLACES: unavailable (timed out)"
    assert time.monotonic() - t < 2


def test_the_runner_waits_10_s_for_the_places():
    assert brief.PLACES_TIMEOUT_MS == 10_000
    assert brief.STEPS == [brief.saved_places]


# ---------------------------------------------------------------- the steps

async def test_the_steps_run_in_order_each_on_the_brief_so_far(monkeypatch):
    seen = []

    async def first(text, tool_input):
        seen.append(("first", text, tool_input["description"]))
        return text + " A"

    async def second(text, tool_input):
        seen.append(("second", text, tool_input["description"]))
        return text + " B"

    monkeypatch.setattr(brief, "STEPS", [first, second])
    assert await brief.rewrite("x", AGENT) == "x A B"
    assert seen == [("first", "x", AGENT["description"]), ("second", "x A", AGENT["description"])]


async def test_a_step_that_raises_or_gives_no_text_is_skipped_leaving_the_brief_as_it_was(monkeypatch):
    async def first(text, tool_input):
        return text + " A"

    async def broken(text, tool_input):
        raise RuntimeError("no network")

    async def silent(text, tool_input):
        return None

    async def last(text, tool_input):
        return text + " B"

    monkeypatch.setattr(brief, "STEPS", [first, broken, silent, last])
    assert await brief.rewrite("x", AGENT) == "x A B"


# ---------------------------------------------------------------- the guard's hook on Agent

async def test_an_allowed_brief_goes_out_rewritten_whole_with_no_permission_decision(monkeypatch):
    use_fake_vfg(temp_dir())

    async def mark(text, tool_input):
        return text + " [rewritten]"

    monkeypatch.setattr(brief, "STEPS", [mark])
    g = guard()
    out = await make_guard(g)(agent_call(AGENT), "u1", {"signal": None})
    # The whole input, only the prompt changed: the CLI checks it against the Agent tool's schema.
    assert out == {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                          "updatedInput": {**AGENT, "prompt": AGENT["prompt"] + " [rewritten]"}}}
    assert g.allowed == ["T9:u1"]


async def test_a_refused_agent_call_is_not_rewritten():
    d = temp_dir()
    fake = use_fake_vfg(d)
    out = await make_guard(guard())(agent_call({**AGENT, "description": "hut next to the castle"}), "u1", {"signal": None})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "updatedInput" not in out["hookSpecificOutput"]
    assert fake.calls() == []  # no vfg place list for a brief that does not go out


async def test_the_brief_hook_never_stops_the_call(monkeypatch):
    use_fake_vfg(temp_dir())

    async def boom(text, tool_input):
        raise RuntimeError("bug")

    monkeypatch.setattr(brief, "rewrite", boom)
    g = guard()
    assert await make_guard(g)(agent_call(AGENT), "u1", {"signal": None}) == {}  # the brief goes as it was
    assert g.allowed == ["T9:u1"]
    monkeypatch.undo()
    no_prompt = {k: v for k, v in AGENT.items() if k != "prompt"}
    assert await make_guard(guard())(agent_call(no_prompt), "u1", {"signal": None}) == {}
