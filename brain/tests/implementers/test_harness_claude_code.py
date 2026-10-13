"""The Claude Code adapter (harness/claude_code.py), with no CLI: its options and the CLI's
environment per provider, the guard as its PreToolUse hook, the SDK's messages as the harness's
events, and a run on a fake client with the relay's requests merged in."""
import asyncio
import json
import os

import pytest
from claude_agent_sdk import (AssistantMessage, ResultMessage, SystemMessage, TextBlock, ToolResultBlock,
                              ToolUseBlock, UserMessage)

from brain.implementers.config import ImplementerConfig
from brain.implementers.guard import DENY, GuardContext
from brain.implementers.harness import ADAPTERS, claude_code, make
from brain.implementers.harness.base import (ERROR, OK, STOPPED, TURNS, Compacted, Done, Request, Session, Text,
                                            ToolCall, ToolResult)
from brain.implementers.harness.claude_code import (MODEL_VARIABLES, TOOLS, ClaudeCode, Mapper, build_options,
                                                     guard_hook, session_env)


@pytest.fixture
def cfg(tmp_path) -> ImplementerConfig:
    return ImplementerConfig.load({"VFG_REPO": str(tmp_path), "VFG_HOME": str(tmp_path / "vfg-home"),
                                   "OPEN_ROUTER_API": "sk-or-secret"})


def anthropic(tmp_path) -> ImplementerConfig:
    return ImplementerConfig.load({"VFG_REPO": str(tmp_path), "VFG_HOME": str(tmp_path / "vfg-home"),
                                   "VFG_IMPL_PROVIDER": "anthropic"})


SYSTEM = "/tmp/implementer-system-abc.md"


def test_on_openrouter_the_cli_talks_to_the_relay_with_one_model_and_the_guard_its_only_gate(cfg, monkeypatch):
    monkeypatch.setattr(claude_code, "as_root", lambda: False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "owner-login")
    g = GuardContext(repo=cfg.repo, task="hot_fireball")
    o = build_options(cfg, SYSTEM, g, "http://127.0.0.1:5/r/0123456789abcdef", resume="s1")
    assert (o.model, o.effort, o.thinking, o.max_turns, o.permission_mode, o.cwd, o.resume) == (
        "deepseek/deepseek-v4.1-flash", None, {"type": "disabled"}, 100, "bypassPermissions", cfg.repo, "s1")
    assert o.system_prompt == {"type": "file", "path": SYSTEM}
    assert o.tools == TOOLS == ["Bash", "Read", "Write", "Edit", "Glob", "Grep"] and o.allowed_tools == []
    assert (o.setting_sources, o.skills, o.strict_mcp_config, o.settings) == ([], [], True, None)
    assert o.hooks["PreToolUse"][0].hooks
    e = o.env
    assert e["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:5/r/0123456789abcdef"
    assert (e["ANTHROPIC_AUTH_TOKEN"], e["ANTHROPIC_API_KEY"], e["CLAUDE_CODE_OAUTH_TOKEN"]) == ("sk-or-secret", "", "")
    assert all(e[k] == "deepseek/deepseek-v4.1-flash" for k in MODEL_VARIABLES)
    assert e["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert e["CLAUDE_CODE_TOTAL_TOKENS_REMINDER"] == "off"  # no tokens-left note before each turn
    assert e["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == e["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "1000000"
    assert e["PYTHONPYCACHEPREFIX"] == cfg.brain_dir + "/pycache"


def test_as_root_it_runs_dontask_with_every_use_of_each_tool_allowed(cfg, monkeypatch):
    monkeypatch.setattr(claude_code, "as_root", lambda: True)
    o = build_options(cfg, SYSTEM, GuardContext(repo=cfg.repo), "http://127.0.0.1:5/r/x")
    assert (o.permission_mode, o.allowed_tools) == ("dontAsk", TOOLS)


def test_on_anthropic_the_owners_login_at_low_effort_with_no_key_and_no_address(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_code, "as_root", lambda: False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-never")
    c = anthropic(tmp_path)
    o = build_options(c, SYSTEM, GuardContext(repo=c.repo))
    assert (o.model, o.effort, o.thinking, o.permission_mode) == ("claude-haiku-5-5", "low", None, "bypassPermissions")
    assert json.loads(o.settings) == {"promptCacheTtl": "5m"}
    assert o.env["CLAUDE_CODE_TOTAL_TOKENS_REMINDER"] == "off"
    assert not {"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL",
                "CLAUDE_CODE_AUTO_COMPACT_WINDOW"} & set(o.env)


def test_the_clis_environment_has_no_api_key_and_nothing_of_a_parent_session():
    env = session_env({"PATH": "/bin", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "u", "CLAUDECODE": "1",
                       "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_CODE_OAUTH_TOKEN": "t", "NODE_OPTIONS": "x",
                       "OPEN_ROUTER_API": "or"})
    assert env == {"PATH": "/bin", "CLAUDE_CODE_OAUTH_TOKEN": "t", "OPEN_ROUTER_API": "or"}


def test_the_registry_makes_claude_code_and_refuses_a_name_it_does_not_have(cfg):
    assert "claude-code" in ADAPTERS and isinstance(make(cfg), ClaudeCode)
    cfg.harness = "nope"
    with pytest.raises(ValueError, match="VFG_IMPL_HARNESS=nope is no harness; these are: claude-code"):
        make(cfg)


# ---------------------------------------------------------------- the guard, as the CLI's hook

def pre(tool: str, **inp) -> dict:
    return {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp,
            "effort": {"level": "low"}}


async def test_the_hook_denies_in_the_sdks_shape_lets_the_rest_through_and_records_what_it_makes(tmp_path):
    g = GuardContext(repo=str(tmp_path), task="hot_fireball")
    noted: set = set()
    hook = guard_hook(g, noted)
    denied = await hook(pre("Bash", command="curl https://example.com"), "t1", None)
    assert denied == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": DENY.shape}}
    owner = await hook(pre("Bash", command="vfg cmd 'op Steve'"), "t2", None)
    assert owner["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert noted == {"t1", "t2", ("Bash", '{"command":"curl https://example.com"}'),
                     ("Bash", '{"command":"vfg cmd \'op Steve\'"}')}
    assert await hook(pre("Bash", command="vfg engine grant Steve hot_fireball"), "t3", None) == {}
    assert g.made["grants"] == ["Steve:hot_fireball"]
    assert await hook(pre("Write", file_path="made/main/content/hot_fireball.py", content="x"), "t4", None) == {}
    assert g.made["content"] == ["hot_fireball"]
    assert (await hook(pre("Write", file_path="engine/library/fireball.py", content="x"), "t5", None)
            )["hookSpecificOutput"]["permissionDecisionReason"] == DENY.made_only("main")
    assert await hook({"hook_event_name": "PostToolUse", "tool_name": "Bash"}, "t6", None) == {}


# ---------------------------------------------------------------- the SDK's messages as events

def init(session="s1", key="none", mode="bypassPermissions") -> SystemMessage:
    return SystemMessage(subtype="init", data={"type": "system", "subtype": "init", "session_id": session,
                                               "model": "deepseek-flash", "permissionMode": mode, "apiKeySource": key})


def result(text="Done: built.", subtype="success", is_error=False, errors=None, session="s1", denials=None):
    return ResultMessage(subtype=subtype, duration_ms=4200, duration_api_ms=3000, is_error=is_error, num_turns=7,
                         session_id=session, result=text, errors=errors, permission_denials=denials)


def assistant(*blocks, mid=None, usage=None) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), model="m", message_id=mid, usage=usage)


def test_the_sdks_messages_as_events():
    m = Mapper("hot_fireball", "openrouter", "deepseek/deepseek-v4.1-flash")
    assert m.events(init("s1", key="ANTHROPIC_AUTH_TOKEN")) == [Session("s1")]  # the key is the provider's: fine
    assert m.events(init("s1")) == []  # the same session: nothing new
    assert m.events(assistant(TextBlock("Making it."), TextBlock("  "),
                              ToolUseBlock(id="tu1", name="Bash", input={"command": "vfg action list"}),
                              mid="msg_1", usage={"input_tokens": 9})) == [
        Text("Making it."), ToolCall("Bash", {"command": "vfg action list"}, "tu1")]  # usage: the relay's
    assert m.events(SystemMessage(subtype="compact_boundary", data={"compact_metadata": {
        "trigger": "auto", "pre_tokens": 990000}})) == [Compacted("auto", 990000)]
    assert m.events(SystemMessage(subtype="status", data={})) == []
    assert m.events(SystemMessage(subtype="compact_boundary", data={"compactMetadata": {
        "trigger": "manual", "preTokens": 5}})) == [Compacted("manual", 5)]  # the other spelling
    assert m.events(SystemMessage(subtype="status", data={"status": "compacting"})) == []  # logged
    assert m.events(result("Done: a fireball.", session="s2")) == [
        Session("s2"), Done(OK, "Done: a fireball.", turns=7, seconds=4.2)]


def test_how_the_result_says_it_ended():
    m = Mapper("t", "openrouter", "x", session="s1")
    assert m.events(result(None, "error_max_turns", True))[-1].ended == TURNS
    assert m.events(result("API Error: 529", "success", True))[-1] == Done(ERROR, "API Error: 529", turns=7, seconds=4.2)
    assert m.events(result(None, "error_during_execution", True, ["boom"]))[-1].text == "boom"
    d = m.events(result(denials=[{"tool_name": "Bash", "tool_input": {"command": "rm x"}}]))[-1]
    assert d.refused == ['Bash {"command":"rm x"}']
    # the guard's own refusals are among the CLI's denials: only the rest are the CLI's
    guarded = Mapper("t", "openrouter", "x", "s1", {"tu9", ("Bash", '{"command":"vfg cmd \'op Steve\'"}')})
    d = guarded.events(result(denials=[{"tool_name": "Bash", "tool_use_id": "tu9", "tool_input": {"command": "a"}},
                                       {"tool_name": "Bash", "tool_input": {"command": "vfg cmd 'op Steve'"}},
                                       {"tool_name": "Edit", "tool_use_id": "tu1", "tool_input": {"file_path": "x"}}]))[-1]
    assert d.refused == ['Edit {"file_path":"x"}']


def test_on_anthropic_an_api_key_stops_it_and_each_request_is_read_from_its_messages():
    m = Mapper("t", "anthropic", "claude-haiku-5-5")
    assert m.events(init("s1", key="ANTHROPIC_API_KEY"))[-1] == Done(
        STOPPED, "the CLI would use an API key (ANTHROPIC_API_KEY), not the subscription, so it was stopped")
    m = Mapper("t", "anthropic", "claude-haiku-5-5")
    m.events(init("s1"))
    u1 = {"input_tokens": 10, "cache_read_input_tokens": 120000, "cache_creation_input_tokens": 500, "output_tokens": 1}
    assert m.events(assistant(TextBlock("a"), mid="msg_1", usage=u1)) == [Text("a")]
    m.events(assistant(TextBlock("b"), mid="msg_1", usage=dict(u1, output_tokens=80)))  # the same request, later
    second = m.events(assistant(TextBlock("c"), mid="msg_2", usage=dict(u1, output_tokens=5)))
    [r1] = [e for e in second if isinstance(e, Request)]
    assert (r1.model, r1.input, r1.cache_read, r1.cache_write, r1.output, r1.size, r1.id) == (
        "claude-haiku-5-5", 10, 120000, 500, 80, 120510, "msg_1")
    assert m.events(assistant(TextBlock("d"), mid="msg_1", usage=u1)) == [Text("d")]  # given once
    end = m.events(result())
    assert [(e.id, e.output) for e in end if isinstance(e, Request)] == [("msg_2", 5)] and isinstance(end[-1], Done)


# ---------------------------------------------------------------- a run, on a fake client

class FakeClient:
    """The SDK client: its scripted messages, given by receive_messages(). Before message i it plays
    the relay's requests `relay[i]`; a query() sent while it runs is echoed back as a user message
    when the script says ("echo", n): the n-th message sent, as the CLI echoes what it reads. "wait"
    holds the script until the test sets `go`; "never" holds it for good."""

    def __init__(self, options, script, relay=None):
        self.options = options
        self.script = script
        self.relay = relay or {}
        self.prompts = []
        self.disconnected = False
        self.run = None
        self.go = asyncio.Event()  # a test lets the script run on

    async def connect(self):
        pass

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def receive_messages(self):
        for i, m in enumerate(self.script):
            for r in self.relay.get(i, []):
                self.run.request(r)
            if m == "wait":
                await self.go.wait()
                continue
            if m == "never":  # the CLI says nothing more
                await asyncio.Event().wait()
            if isinstance(m, tuple) and m[0] == "echo":
                m = UserMessage(content=self.prompts[m[1]])
            await asyncio.sleep(0)
            yield m

    async def interrupt(self):
        pass

    async def disconnect(self):
        self.disconnected = True


def harness_with(cfg, monkeypatch, script, relay=None):
    monkeypatch.setattr(claude_code, "scrub_environ", lambda: None)
    made = []

    def factory(options):
        made.append(FakeClient(options, script, relay))
        return made[-1]

    real_begin = claude_code.ClaudeCodeRun.begin

    async def begin(self, prompt):  # the fake client needs its run, to play the relay
        self.client.run = self
        await real_begin(self, prompt)

    monkeypatch.setattr(claude_code.ClaudeCodeRun, "begin", begin)
    return ClaudeCode(cfg, client_factory=factory), made


async def test_a_run_gives_the_clients_messages_and_the_relays_requests_in_order_then_closes(cfg, monkeypatch):
    req = Request("deepseek/deepseek-v4.1-flash", 100, 90000, 0, 50, 0.0)
    h, made = harness_with(cfg, monkeypatch, [
        init("s1"), ("echo", 0), assistant(ToolUseBlock(id="a", name="Bash", input={"command": "ls"})),
        UserMessage(content=[ToolResultBlock(tool_use_id="a", content="Exit code 2\nls: no such file", is_error=True)]),
        result("Done: it flies.")], {1: [req], 3: [req]})
    run = await h.start("hot_fireball", SYSTEM, "make a fireball", GuardContext(repo=cfg.repo))
    events = [e async for e in run.events()]
    assert [type(e).__name__ for e in events] == ["Session", "Request", "ToolCall", "Request", "ToolResult", "Done"]
    assert events[4] == ToolResult("a", True, "Exit code 2\nls: no such file")
    assert run.session == "s1" and made[0].prompts == ["make a fireball"]
    assert made[0].options.env["ANTHROPIC_BASE_URL"] == run.address
    assert made[0].options.extra_args == {"replay-user-messages": None}
    assert run.address.startswith("http://127.0.0.1:%d/r/" % h.relay.port)
    assert not await run.send("and bigger")  # ended: the runner resumes instead
    await run.close()
    assert made[0].disconnected
    run.request(req)  # after the run: dropped
    await h.close()
    assert h.relay.port is None


async def test_a_message_is_read_in_the_turn_and_one_that_lands_at_its_end_starts_the_next(cfg, monkeypatch):
    h, made = harness_with(cfg, monkeypatch, [
        init("s1"), ("echo", 0), "wait",
        ("echo", 1),  # read after the tool call in flight, in the same turn
        result("Done: built, and blue.", session="s1"),  # the second message is not read yet: on it goes
        init("s1"), ("echo", 2), result("Done: built, blue and loud.", session="s1")])
    run = await h.start("t", SYSTEM, "build it", GuardContext(repo=cfg.repo))
    assert await run.send("MESSAGE from the brain: make it blue")
    assert await run.send("MESSAGE from the brain: and loud")
    made[0].go.set()
    events = [e async for e in run.events()]
    [d] = [e for e in events if isinstance(e, Done)]
    assert (d.text, d.turns, d.seconds) == ("Done: built, blue and loud.", 14, 8.4)  # the last result's text; both results' turns and time
    assert made[0].prompts == ["build it", "MESSAGE from the brain: make it blue", "MESSAGE from the brain: and loud"]
    assert not await run.send("too late")
    await run.close()


async def test_a_run_needs_its_providers_key(tmp_path):
    c = ImplementerConfig.load({"VFG_REPO": str(tmp_path), "VFG_HOME": str(tmp_path / "h")})
    with pytest.raises(RuntimeError, match="no key for openrouter: set OPEN_ROUTER_API"):
        await ClaudeCode(c, client_factory=lambda o: 1 / 0).start("t", SYSTEM, "b", GuardContext(repo=c.repo))


def test_the_key_is_in_no_repr(cfg):
    assert "sk-or-secret" not in repr(cfg) and "sk-or-secret" not in repr(ClaudeCode(cfg).__dict__.get("cfg"))
    assert os.environ.get("OPEN_ROUTER_API") is None  # the fixture's key never reached this process


async def test_two_messages_the_cli_reads_as_one_are_both_read(cfg, monkeypatch):
    h, made = harness_with(cfg, monkeypatch, [
        init("s1"), ("echo", 0), "wait",
        UserMessage(content="MESSAGE from the brain: make it blue\nMESSAGE from the brain: and loud"),
        result("Done: blue and loud.")])
    run = await h.start("t", SYSTEM, "build it", GuardContext(repo=cfg.repo))
    assert await run.send("MESSAGE from the brain: make it blue")
    assert await run.send("MESSAGE from the brain: and loud")
    made[0].go.set()
    [d] = [e async for e in run.events() if isinstance(e, Done)]
    assert (d.text, d.unread) == ("Done: blue and loud.", [])
    await run.close()


async def test_a_message_no_turn_takes_up_ends_the_run_unread_after_a_grace(cfg, monkeypatch):
    monkeypatch.setattr(claude_code, "UNREAD_GRACE_S", 0.05)
    h, made = harness_with(cfg, monkeypatch, [init("s1"), ("echo", 0), "wait", result("Done: built."), "never"])
    run = await h.start("t", SYSTEM, "build it", GuardContext(repo=cfg.repo))
    assert await run.send("MESSAGE from the brain: altered by the CLI")
    made[0].go.set()  # the result comes; then nothing: no turn reads the message
    [d] = [e async for e in run.events() if isinstance(e, Done)]
    assert (d.text, d.unread) == ("Done: built.", ["MESSAGE from the brain: altered by the CLI"])
    assert not await run.send("too late")
    await run.close()


async def test_a_stop_during_a_turn_ends_the_run_at_its_result_with_the_message_unread(cfg, monkeypatch):
    h, made = harness_with(cfg, monkeypatch, [init("s1"), ("echo", 0), "wait",
                                              result("interrupted", "error_during_execution", True)])
    run = await h.start("t", SYSTEM, "build it", GuardContext(repo=cfg.repo))
    assert await run.send("MESSAGE from the brain: and loud")
    await run.stop()
    made[0].go.set()
    [d] = [e async for e in run.events() if isinstance(e, Done)]
    assert (d.ended, d.unread) == (ERROR, ["MESSAGE from the brain: and loud"])  # at once: no next turn waited for
    await run.close()


def test_the_relay_carries_openrouters_routing_and_only_there(tmp_path):
    on_or = ClaudeCode(ImplementerConfig.load({"VFG_REPO": str(tmp_path), "VFG_HOME": str(tmp_path), "OPEN_ROUTER_API": "k"}))
    assert on_or.relay.extra["provider"]["order"] == ["digitalocean", "ionstream", "together"]
    on_ds = ClaudeCode(ImplementerConfig.load({"VFG_REPO": str(tmp_path), "VFG_HOME": str(tmp_path), "DEEPSEEK_API_KEY": "k"}))
    assert on_ds.relay.extra == {}
