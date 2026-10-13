"""The OpenCode adapter (harness/opencode.py), with no OpenCode: its config and environment per
provider, OpenCode's tool calls in the guard's shape, and runs against a fake `opencode serve` that
speaks the parts of its HTTP API the adapter uses (sessions, prompts, the event stream, messages,
status, abort) and asks the guard before every tool, as the plugin does. The plugin's own JS is run
under node when node is there."""
import asyncio
import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from brain.implementers.config import ImplementerConfig
from brain.implementers.guard import DENY, GuardContext
from brain.implementers.harness import ADAPTERS, make
from brain.implementers.harness import opencode as oc
from brain.implementers.harness.base import (ERROR, OK, STOPPED, TURNS, Done, Request, Session, Text, ToolCall,
                                             ToolResult)

OWNER = "That is the server owner's to do; report it."


@pytest.fixture
def cfg(tmp_path) -> ImplementerConfig:
    (tmp_path / "repo").mkdir()
    return ImplementerConfig.load({"VFG_REPO": str(tmp_path / "repo"), "VFG_HOME": str(tmp_path / "vfg-home"),
                                   "OPEN_ROUTER_API": "sk-or-secret", "VFG_IMPL_HARNESS": "opencode"})


@pytest.fixture
def system(tmp_path) -> Path:
    p = tmp_path / "implementer-system-abc.md"
    p.write_text("THE RULES\n")
    return p


# ---------------------------------------------------------------- config, environment, registry

def test_the_registry_makes_opencode(cfg):
    assert ADAPTERS["opencode"] == "brain.implementers.harness.opencode:OpenCode"
    assert isinstance(make(cfg), oc.OpenCode) and make(cfg).name == "opencode"


def test_the_config_has_one_model_our_prompt_thinking_off_every_tool_allowed_and_no_key(cfg):
    c = oc.server_config(cfg, "/b/implementer-system-abc.md")
    assert (c["model"], c["small_model"], c["default_agent"], c["enabled_providers"]) == (
        "openrouter/deepseek/deepseek-v4.1-flash", "openrouter/deepseek/deepseek-v4.1-flash", "vfg", ["openrouter"])
    p = c["provider"]["openrouter"]
    assert p["npm"] == "@openrouter/ai-sdk-provider"
    assert p["options"] == {"apiKey": "{env:VFG_OPENCODE_KEY}", "baseURL": "https://openrouter.ai/api/v1"}
    assert p["models"]["deepseek/deepseek-v4.1-flash"]["limit"] == {"context": 1_000_000, "output": 32_000}
    a = c["agent"]["vfg"]
    assert a["prompt"] == "{file:/b/implementer-system-abc.md}" and a["mode"] == "primary"
    assert a["options"] == {"reasoning": {"enabled": False}, "provider": {
        "allow_fallbacks": True, "order": ["digitalocean", "ionstream", "together"], "ignore": ["venice", "morph", "relace"]}}
    allow = {"*": "allow", "question": "deny", "plan_enter": "deny", "plan_exit": "deny"}
    assert a["permission"] == c["permission"] == allow
    assert (c["snapshot"], c["autoupdate"], c["share"], c["lsp"], c["formatter"], c["mcp"], c["instructions"]) == (
        False, False, "disabled", False, False, {}, [])
    assert c["compaction"] == {"auto": False, "prune": False}
    assert c["plugin"] == [Path(oc.PLUGIN).as_uri()] and os.path.isfile(oc.PLUGIN)
    assert "sk-or-secret" not in json.dumps(c)


def test_on_deepseeks_own_api_thinking_is_off_by_its_own_field(tmp_path):
    c = ImplementerConfig.load({"VFG_REPO": str(tmp_path), "VFG_HOME": str(tmp_path / "h"), "DEEPSEEK_API_KEY": "sk-ds"})
    conf = oc.server_config(c, "/s.md")
    assert conf["model"] == "deepseek/deepseek-flash"
    assert conf["provider"]["deepseek"]["npm"] == "@ai-sdk/openai-compatible"
    assert conf["provider"]["deepseek"]["options"]["baseURL"] == "https://api.deepseek.com"
    assert conf["agent"]["vfg"]["options"] == {"thinking": {"type": "disabled"}}


def test_the_address_is_the_providers_openai_style_one(tmp_path):
    def base(env):
        return oc.openai_base(ImplementerConfig.load({"VFG_REPO": str(tmp_path), "VFG_HOME": str(tmp_path), **env}))
    assert base({"OPEN_ROUTER_API": "k"}) == "https://openrouter.ai/api/v1"
    assert base({"OPEN_ROUTER_API": "k", "VFG_IMPL_BASE_URL": "http://127.0.0.1:9/api"}) == "http://127.0.0.1:9/api/v1"
    assert base({"DEEPSEEK_API_KEY": "k"}) == "https://api.deepseek.com"
    assert base({"DEEPSEEK_API_KEY": "k", "VFG_IMPL_BASE_URL": "https://x.test/anthropic"}) == "https://x.test"


def test_the_owners_login_is_claude_codes_not_opencodes(tmp_path):
    c = ImplementerConfig.load({"VFG_REPO": str(tmp_path), "VFG_HOME": str(tmp_path), "VFG_IMPL_PROVIDER": "anthropic"})
    with pytest.raises(ValueError, match="does not run in OpenCode"):
        oc.server_config(c, "/s.md")


def test_the_servers_environment_is_its_own_and_a_tools_shell_gets_the_owners_back(cfg):
    dirs = oc.Dirs(os.path.join(cfg.brain_dir, "opencode"))
    source = {"PATH": "/bin", "HOME": "/home/jm", "XDG_CACHE_HOME": "/home/jm/.mycache", "OPENCODE": "1",
              "OPENCODE_CONFIG_DIR": "/home/jm/.oc", "OPEN_ROUTER_API": "sk-or-secret"}
    env = oc.server_env(cfg, dirs, "http://127.0.0.1:5/guard", "guard-secret", "pw", source)
    assert env["XDG_CONFIG_HOME"] == os.path.join(dirs.root, "config")
    assert env["XDG_DATA_HOME"] == os.path.join(dirs.root, "data")
    assert env["XDG_CACHE_HOME"] == os.path.join(dirs.root, "cache")
    assert env["XDG_STATE_HOME"] == os.path.join(dirs.root, "state")
    assert env["OPENCODE_CONFIG"] == os.path.join(dirs.root, "opencode.json")
    assert env["OPENCODE_TEST_HOME"] == dirs.root
    assert "OPENCODE" not in env and "OPENCODE_CONFIG_DIR" not in env
    assert all(env[k] == "1" for k in oc.FLAGS)
    assert (env["VFG_OPENCODE_KEY"], env["VFG_GUARD_URL"], env["VFG_GUARD_SECRET"], env["OPENCODE_SERVER_PASSWORD"]) == (
        "sk-or-secret", "http://127.0.0.1:5/guard", "guard-secret", "pw")
    assert env["PATH"] == "/bin" and env["HOME"] == "/home/jm"
    shell = json.loads(env["VFG_SHELL_ENV"])
    assert shell == {"XDG_CONFIG_HOME": "/home/jm/.config", "XDG_DATA_HOME": "/home/jm/.local/share",
                     "XDG_CACHE_HOME": "/home/jm/.mycache", "XDG_STATE_HOME": "/home/jm/.local/state",
                     "VFG_OPENCODE_KEY": "", "VFG_GUARD_URL": "", "VFG_GUARD_SECRET": "",
                     "OPENCODE_SERVER_PASSWORD": "", "OPENCODE_TEST_HOME": ""}


# ---------------------------------------------------------------- OpenCode's tools in the guard's shape

def test_opencodes_tools_map_to_the_guards_names_and_keys():
    r = "/repo"
    assert oc.guard_calls("bash", {"command": "ls", "description": "x"}, r) == [
        {"tool_name": "Bash", "tool_input": {"command": "ls"}}]
    assert oc.guard_calls("bash", {"command": "ls", "workdir": "/etc"}, r) == [
        {"tool_name": "Bash", "tool_input": {"command": "ls"}}, {"tool_name": "Glob", "tool_input": {"path": "/etc"}}]
    assert oc.guard_calls("read", {"filePath": "a.py", "offset": 3}, r) == [
        {"tool_name": "Read", "tool_input": {"file_path": "/repo/a.py"}}]
    assert oc.guard_calls("write", {"filePath": "/repo/made/main/x.py", "content": "c"}, r) == [
        {"tool_name": "Write", "tool_input": {"file_path": "/repo/made/main/x.py", "content": "c"}}]
    assert oc.guard_calls("edit", {"filePath": "x.py", "oldString": "a", "newString": "b"}, r) == [
        {"tool_name": "Edit", "tool_input": {"file_path": "/repo/x.py", "old_string": "a", "new_string": "b",
                                             "replace_all": None}}]
    assert oc.guard_calls("glob", {"pattern": "**/*.py", "path": "/tmp"}, r) == [
        {"tool_name": "Glob", "tool_input": {"pattern": "**/*.py", "path": "/tmp"}}]
    assert oc.guard_calls("grep", {"pattern": "def", "include": "*.py"}, r) == [
        {"tool_name": "Grep", "tool_input": {"pattern": "def", "glob": "*.py"}}]
    assert oc.guard_calls("list", {"path": "/"}, r) == [{"tool_name": "Glob", "tool_input": {"path": "/"}}]
    patch = ("*** Begin Patch\n*** Add File: made/main/actions/x.py\n+a\n*** Update File: /repo/scripts/y.py\n"
             "@@\n*** Delete File: z.py\n*** End Patch")
    assert oc.guard_calls("apply_patch", {"patchText": patch}, r) == [
        {"tool_name": "Write", "tool_input": {"file_path": "/repo/made/main/actions/x.py"}},
        {"tool_name": "Edit", "tool_input": {"file_path": "/repo/scripts/y.py"}},
        {"tool_name": "Edit", "tool_input": {"file_path": "/repo/z.py"}}]
    assert oc.guard_calls("multiedit", {"filePath": "q.py", "edits": []}, r) == [
        {"tool_name": "Edit", "tool_input": {"file_path": "/repo/q.py"}}]
    assert oc.guard_calls("webfetch", {"url": "https://x"}, r) == [
        {"tool_name": "WebFetch", "tool_input": {"url": "https://x"}}]
    assert oc.guard_calls("websearch", {"query": "q"}, r) == [
        {"tool_name": "WebSearch", "tool_input": {"query": "q"}}]
    assert oc.guard_calls("task", {"prompt": "p", "subagent_type": "general"}, r) == [
        {"tool_name": "task", "tool_input": {"prompt": "p", "subagent_type": "general"}}]
    # a known tool with arguments the guard does not read maps to the guard's keys alone
    assert oc.guard_calls("read", {"filePath": "/etc/passwd", "offset": 10, "limit": 5}, r) == [
        {"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}}]
    assert oc.guard_calls("bash", {"command": "ls", "timeout": 5000}, r) == [
        {"tool_name": "Bash", "tool_input": {"command": "ls"}}]


def test_web_tools_are_refused_and_the_harnesss_own_tools_go_through(cfg):
    g = guard(cfg)
    from brain.implementers.guard import decide
    for tool, args in [("webfetch", {"url": "https://x"}), ("websearch", {"query": "q"})]:
        assert [decide(c, g) for c in oc.guard_calls(tool, args, cfg.repo)] == [DENY.web]
    for tool, args in [("task", {"prompt": "p"}), ("todowrite", {"todos": []}), ("skill", {"name": "x"}),
                       ("some_new_tool", {"x": 1})]:
        assert [decide(c, g) for c in oc.guard_calls(tool, args, cfg.repo)] == [None]
    assert "some_new_tool" in oc._told and "task" not in oc._told


# ---------------------------------------------------------------- a fake `opencode serve`

class FakeOpenCode:
    """Sessions, prompts and a model that takes its steps from `steps`, in order, over every
    session: {"tool": name, "args": {...}}, {"text": ...}, {"error": message}, {"child": {...}} (a
    subagent's session with one tool call), {"ask": permission}, and "gate": an asyncio.Event the
    step waits for. It asks the guard before each tool, as the plugin does."""

    def __init__(self, steps=()):
        self.steps = list(steps)
        self.sessions: dict[str, dict] = {}
        self.listeners: list[asyncio.Queue] = []
        self.requests: list[tuple[str, str, dict | None]] = []
        self.replies: list[tuple[str, dict]] = []
        self.n = 0
        self.lose_next = False  # the next prompt to a busy session lands as its loop ends, unread
        self.env: dict = {}
        self.argv: list = []
        self.cwd = ""
        self.code = None
        self.exited = asyncio.Event()
        self.server = None
        self.address = ""
        self.spawns = 0

    # the Process the spawner returns
    async def wait(self) -> int:
        await self.exited.wait()
        return self.code

    def terminate(self) -> None:
        self.exit(0)

    def kill(self) -> None:
        self.exit(-9)

    def exit(self, code: int) -> None:
        if self.exited.is_set():
            return
        self.code = code
        self.server.close()
        for q in self.listeners:
            q.put_nowait(None)
        self.exited.set()

    async def spawn(self, argv, env, cwd):
        self.argv, self.env, self.cwd = argv, env, cwd
        self.code, self.exited, self.spawns = None, asyncio.Event(), self.spawns + 1
        self.server = await asyncio.start_server(self.serve, "127.0.0.1", 0)
        self.address = "http://127.0.0.1:%d" % self.server.sockets[0].getsockname()[1]
        return self

    def id(self, kind: str) -> str:
        self.n += 1
        return "%s_%06d" % (kind, self.n)

    def emit(self, typ: str, props: dict) -> None:
        for q in self.listeners:
            q.put_nowait({"type": typ, "properties": props})

    # the HTTP side
    async def serve(self, reader, writer):
        try:
            line = await reader.readline()
            if not line:
                return
            method, target, _ = line.decode().split(" ", 2)
            headers = {}
            while (h := await reader.readline()) not in (b"\r\n", b"\n", b""):
                k, _, v = h.decode().partition(":")
                headers[k.strip().lower()] = v.strip()
            n = int(headers.get("content-length") or 0)
            body = json.loads(await reader.readexactly(n)) if n else None
            auth = "Basic " + base64.b64encode(b"opencode:" + self.env["OPENCODE_SERVER_PASSWORD"].encode()).decode()
            if headers.get("authorization") != auth:
                return await self.answer(writer, 401, {"error": "unauthorized"})
            path = target.split("?")[0]
            self.requests.append((method, path, body))
            if path == "/event":
                return await self.events(writer)
            status, out = await self.route(method, path, body)
            await self.answer(writer, status, out)
        finally:
            writer.close()

    async def answer(self, writer, status, out):
        data = b"" if out is None else json.dumps(out).encode()
        writer.write(b"HTTP/1.1 %d X\r\ncontent-type: application/json\r\ncontent-length: %d\r\nconnection: close\r\n\r\n%s"
                     % (status, len(data), data))
        await writer.drain()

    async def events(self, writer):
        q: asyncio.Queue = asyncio.Queue()
        self.listeners.append(q)
        writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\nconnection: close\r\n\r\n")
        writer.write(b"data: %s\n\n" % json.dumps({"type": "server.connected", "properties": {}}).encode())
        await writer.drain()
        try:
            while (ev := await q.get()) is not None:
                writer.write(b"data: %s\n\n" % json.dumps(ev).encode())
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            self.listeners.remove(q)

    async def route(self, method, path, body):
        parts = path.strip("/").split("/")
        if method == "POST" and path == "/session":
            sid = self.id("ses")
            self.sessions[sid] = {"title": body.get("title"), "parentID": None, "messages": [], "busy": False,
                                  "late": []}
            self.emit("session.created", {"sessionID": sid, "info": {"id": sid, "title": body.get("title")}})
            return 200, {"id": sid, "title": body.get("title")}
        if method == "GET" and path == "/session/status":
            return 200, {sid: {"type": "busy"} for sid, s in self.sessions.items() if s["busy"]}
        if parts[0] == "session" and len(parts) >= 2:
            s = self.sessions.get(parts[1])
            if s is None:
                return 404, {"name": "NotFoundError"}
            if method == "GET" and len(parts) == 2:
                return 200, {"id": parts[1], "parentID": s["parentID"]}
            if method == "GET" and parts[2:] == ["message"]:
                return 200, s["messages"]
            if method == "POST" and parts[2:] == ["prompt_async"]:
                self.prompt(parts[1], body)
                return 204, None
            if method == "POST" and parts[2:] == ["abort"]:
                if s.get("task") and not s["task"].done():
                    s["task"].cancel()
                return 200, True
        if method == "POST" and parts[0] == "permission":
            self.replies.append((parts[1], body))
            return 200, True
        return 404, {"name": "NotFound"}

    # the model's side
    def prompt(self, sid, body):
        s = self.sessions[sid]
        mid = body.get("messageID") or self.id("msg")
        parts = [{"id": p.get("id") or self.id("prt"), "type": "text", "text": p["text"], "messageID": mid,
                  "sessionID": sid} for p in body["parts"]]
        msg = {"info": {"id": mid, "role": "user", "sessionID": sid, "agent": body.get("agent"),
                        "model": body.get("model")}, "parts": parts}
        old = next((m for m in s["messages"] if m["info"]["id"] == mid), None)
        if old is not None:
            old["parts"] = parts
        elif self.lose_next and s["busy"]:
            self.lose_next = False
            s["late"].append(msg)
            return
        else:
            s["messages"].append(msg)
        self.emit("message.updated", {"sessionID": sid, "info": msg["info"]})
        if not s["busy"]:
            s["busy"] = True
            s["task"] = asyncio.get_running_loop().create_task(self.loop(sid))

    async def loop(self, sid):
        s = self.sessions[sid]
        self.emit("session.status", {"sessionID": sid, "status": {"type": "busy"}})
        try:
            while True:
                user = [m for m in s["messages"] if m["info"]["role"] == "user"][-1]
                asst = [m for m in s["messages"] if m["info"]["role"] == "assistant"]
                last = asst[-1]["info"] if asst else {}
                if (last.get("error") or last.get("finish") not in (None, "tool-calls")) and \
                        last.get("parentID") == user["info"]["id"]:
                    break
                if not await self.step(sid, user, self.steps.pop(0) if self.steps else {"text": "Done: nothing more."}):
                    break
        except asyncio.CancelledError:
            asst = [m for m in s["messages"] if m["info"]["role"] == "assistant"]
            err = {"name": "MessageAbortedError", "data": {"message": "Aborted"}}
            if asst:
                asst[-1]["info"]["error"] = err
            self.emit("session.error", {"sessionID": sid, "error": err})
        finally:
            s["messages"] += s["late"]
            s["late"] = []
            s["busy"] = False
            self.emit("session.status", {"sessionID": sid, "status": {"type": "idle"}})
            self.emit("session.idle", {"sessionID": sid})

    def part(self, sid, mid, **kw):
        p = {"id": self.id("prt"), "sessionID": sid, "messageID": mid, **kw}
        self.emit("message.part.updated", {"sessionID": sid, "part": p, "time": 0})
        return p

    async def step(self, sid, user, step) -> bool:
        if step.get("gate"):
            await step["gate"].wait()
        s = self.sessions[sid]
        info = {"id": self.id("msg"), "role": "assistant", "sessionID": sid, "parentID": user["info"]["id"]}
        msg = {"info": info, "parts": []}
        s["messages"].append(msg)
        self.emit("message.updated", {"sessionID": sid, "info": info})
        self.part(sid, info["id"], type="step-start")
        if "error" in step:
            info["error"] = {"name": "APIError", "data": {"message": step["error"]}}
            self.emit("session.error", {"sessionID": sid, "error": info["error"]})
            return False
        if "ask" in step:
            self.emit("permission.asked", {"id": "per_1", "sessionID": sid, "permission": step["ask"], "patterns": ["*"]})
        if "child" in step:
            child = self.id("ses")
            self.sessions[child] = {"title": "sub", "parentID": sid, "messages": [], "busy": False, "late": []}
            if not step.get("quiet"):  # quiet: the guard hears of it before the event says so
                self.emit("session.created", {"sessionID": child, "info": {"id": child, "parentID": sid}})
            cmid = self.id("msg")
            await self.tool(child, cmid, step["child"]["tool"], step["child"]["args"])
            self.part(child, cmid, type="step-finish", reason="stop",
                      tokens={"input": 7, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}})
        if "tool" in step:
            msg["parts"].append(await self.tool(sid, info["id"], step["tool"], step["args"]))
            info["finish"] = "tool-calls"
        else:
            text = step.get("text", "")
            p = {"id": self.id("prt"), "sessionID": sid, "messageID": info["id"], "type": "text", "text": text,
                 "time": {"start": 1, "end": 2}}
            msg["parts"].append(p)
            self.emit("message.part.updated", {"sessionID": sid, "part": {**p, "time": {"start": 1}}, "time": 0})
            self.emit("message.part.updated", {"sessionID": sid, "part": p, "time": 0})
            self.emit("message.part.updated", {"sessionID": sid, "part": p, "time": 0})  # OpenCode says some twice
            info["finish"] = "stop"
        tokens = step.get("tokens") or {"input": 100, "output": 10, "reasoning": 0, "cache": {"read": 900, "write": 0}}
        fin = self.part(sid, info["id"], type="step-finish", reason=info["finish"], tokens=tokens, cost=0)
        self.emit("message.part.updated", {"sessionID": sid, "part": fin, "time": 0})  # and this one twice
        self.emit("message.updated", {"sessionID": sid, "info": info})
        return True

    async def tool(self, sid, mid, name, args):
        call = self.id("call")
        p = {"id": self.id("prt"), "sessionID": sid, "messageID": mid, "type": "tool", "tool": name, "callID": call,
             "state": {"status": "running", "input": args}}
        self.emit("message.part.updated", {"sessionID": sid, "part": dict(p), "time": 0})
        deny = await self.ask_guard(sid, call, name, args)
        p["state"] = ({"status": "error", "input": args, "error": deny} if deny else
                      {"status": "completed", "input": args, "output": "ok\n"})
        self.emit("message.part.updated", {"sessionID": sid, "part": p, "time": 0})
        return p

    async def ask_guard(self, sid, call, tool, args) -> str | None:
        """What the plugin does: ask, throw the reason, refuse when it cannot ask."""
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(self.env["VFG_GUARD_URL"], json={"tool": tool, "session": sid, "call": call,
                                                                  "args": args},
                                 headers={"authorization": "Bearer " + self.env["VFG_GUARD_SECRET"]})
        except httpx.HTTPError as e:
            return "The guard could not be reached, so this call was not run: %s" % e
        if r.status_code != 200:
            return "The guard did not answer (HTTP %d), so this call was not run." % r.status_code
        return r.json().get("deny")


def harness(cfg, fake: FakeOpenCode) -> oc.OpenCode:
    return oc.OpenCode(cfg, spawner=fake.spawn)


async def collect(run, timeout=10.0) -> list:
    out = []
    async with asyncio.timeout(timeout):
        async for ev in run.events():
            out.append(ev)
    return out


def guard(cfg, task="hot_fireball") -> GuardContext:
    return GuardContext(repo=cfg.repo, task=task)


# ---------------------------------------------------------------- runs

async def test_a_run_gives_its_session_tool_calls_requests_text_and_done_and_the_guard_refuses_op(cfg, system):
    fake = FakeOpenCode([
        {"tool": "bash", "args": {"command": "ls", "description": "list"}},
        {"tool": "bash", "args": {"command": "vfg cmd 'op Steve'", "description": "op"}},
        {"tool": "read", "args": {"filePath": "/etc/passwd"}},
        {"text": "Done: it casts.\nDESCRIPTION: a fireball",
         "tokens": {"input": 50, "output": 20, "reasoning": 5, "cache": {"read": 1000, "write": 3}}},
    ])
    h = harness(cfg, fake)
    run = await h.start("hot_fireball", system, "Build a fireball", guard(cfg))
    evs = await collect(run)
    await run.close()
    await h.close()

    assert isinstance(evs[0], Session) and evs[0].session == run.session
    assert fake.sessions[run.session]["title"] == "vfg hot_fireball"  # set: OpenCode never asks for one
    calls = [e for e in evs if isinstance(e, ToolCall)]
    assert [(c.name, c.input) for c in calls] == [("Bash", {"command": "ls"}), ("Bash", {"command": "vfg cmd 'op Steve'"}),
                                                  ("Read", {"file_path": "/etc/passwd"})]
    results = {r.id: r for r in evs if isinstance(r, ToolResult)}
    assert results[calls[0].id] == ToolResult(calls[0].id, False, "ok\n")
    assert results[calls[1].id] == ToolResult(calls[1].id, True, OWNER)  # the model gets the reason
    assert results[calls[2].id] == ToolResult(calls[2].id, True, DENY.read_scope(cfg.server))
    reqs = [e for e in evs if isinstance(e, Request)]
    assert len(reqs) == 4  # each step once, though OpenCode said one twice
    assert (reqs[-1].model, reqs[-1].input, reqs[-1].cache_read, reqs[-1].cache_write, reqs[-1].output) == (
        "deepseek/deepseek-v4.1-flash", 50, 1000, 3, 25)
    assert [e.text for e in evs if isinstance(e, Text)] == ["Done: it casts.\nDESCRIPTION: a fireball"]
    done = evs[-1]
    assert isinstance(done, Done) and (done.ended, done.text, done.turns, done.refused) == (
        OK, "Done: it casts.\nDESCRIPTION: a fireball", 4, [])
    prompt = next(b for m, p, b in fake.requests if p.endswith("/prompt_async"))
    assert prompt == {"agent": "vfg", "model": {"providerID": "openrouter", "modelID": "deepseek/deepseek-v4.1-flash"},
                      "parts": [{"type": "text", "text": "Build a fireball"}]}


async def test_the_server_is_started_once_isolated_with_its_config_written_and_stopped_at_close(cfg, system):
    fake = FakeOpenCode([{"text": "Done: a."}, {"text": "Done: b."}])
    h = harness(cfg, fake)
    for task in ("a_task", "b_task"):
        run = await h.start(task, system, "go", guard(cfg, task))
        await collect(run)
        await run.close()
    assert fake.spawns == 1
    assert fake.argv[1:] == ["serve", "--port", "0", "--hostname", "127.0.0.1"] and fake.cwd == cfg.repo
    root = os.path.join(cfg.brain_dir, "opencode")
    assert fake.env["XDG_DATA_HOME"] == os.path.join(root, "data") and os.path.isdir(os.path.join(root, "data"))
    with open(os.path.join(root, "opencode.json")) as f:
        written = json.load(f)
    assert written["agent"]["vfg"]["prompt"] == "{file:%s}" % system
    assert "sk-or-secret" not in json.dumps(written)
    assert fake.env["VFG_OPENCODE_KEY"] == "sk-or-secret"
    await h.close()
    assert fake.exited.is_set() and not h.alive()


async def test_a_message_while_busy_is_read_at_the_next_step_and_done_waits_for_every_answer(cfg, system):
    gate1, gate2 = asyncio.Event(), asyncio.Event()
    fake = FakeOpenCode([{"tool": "bash", "args": {"command": "ls"}, "gate": gate1},
                         {"text": "Done: first.", "gate": gate2},
                         {"text": "Done: with both messages."}])
    h = harness(cfg, fake)
    run = await h.start("hot_fireball", system, "first", guard(cfg))
    got = asyncio.ensure_future(collect(run))
    await asyncio.sleep(0.1)
    assert await run.send("MESSAGE: bigger") is True
    gate1.set()
    await asyncio.sleep(0.2)
    assert await run.send("MESSAGE: and blue") is True  # lands as the model ends its turn
    gate2.set()
    evs = await got
    done = evs[-1]
    assert (done.ended, done.text, done.turns) == (OK, "Done: with both messages.", 3)
    assert await run.send("too late") is False  # the runner resumes with it
    texts = [p["text"] for m in fake.sessions[run.session]["messages"] if m["info"]["role"] == "user" for p in m["parts"]]
    assert texts == ["first", "MESSAGE: bigger", "MESSAGE: and blue"]
    await run.close()
    await h.close()


async def test_a_prompt_left_unread_as_the_session_went_idle_is_given_again(cfg, system):
    gate = asyncio.Event()
    fake = FakeOpenCode([{"text": "Done: first.", "gate": gate}, {"text": "Done: the late one too."}])
    h = harness(cfg, fake)
    run = await h.start("hot_fireball", system, "first", guard(cfg))
    got = asyncio.ensure_future(collect(run))
    await asyncio.sleep(0.1)
    fake.lose_next = True
    assert await run.send("MESSAGE: late") is True
    gate.set()
    evs = await got
    assert (evs[-1].ended, evs[-1].text) == (OK, "Done: the late one too.")
    kicks = [b for m, p, b in fake.requests if p.endswith("/prompt_async") and b.get("messageID")]
    assert len(kicks) == 1 and kicks[0]["parts"][0]["text"] == "MESSAGE: late" and kicks[0]["parts"][0]["id"]
    users = [m for m in fake.sessions[run.session]["messages"] if m["info"]["role"] == "user"]
    assert [len(m["parts"]) for m in users] == [1, 1]  # the same message and part, not a second
    await run.close()
    await h.close()


async def test_resume_prompts_the_same_session_and_an_unknown_one_is_refused(cfg, system):
    fake = FakeOpenCode([{"text": "Done: one."}, {"text": "Done: bigger."}])
    h = harness(cfg, fake)
    first = await h.start("hot_fireball", system, "go", guard(cfg))
    await collect(first)
    await first.close()
    run = await h.resume("hot_fireball", system, first.session, "FOLLOW-UP: bigger", guard(cfg))
    evs = await collect(run)
    await run.close()
    assert run.session == first.session and not any(isinstance(e, Session) for e in evs)
    assert (evs[-1].ended, evs[-1].text, evs[-1].turns) == (OK, "Done: bigger.", 1)
    assert len(fake.sessions) == 1
    with pytest.raises(RuntimeError, match="no session ses_nope"):
        await h.resume("hot_fireball", system, "ses_nope", "x", guard(cfg))
    await h.close()


async def test_stop_aborts_the_session_and_the_run_ends_stopped(cfg, system):
    gate = asyncio.Event()
    fake = FakeOpenCode([{"tool": "bash", "args": {"command": "ls"}}, {"text": "never", "gate": gate}])
    h = harness(cfg, fake)
    run = await h.start("hot_fireball", system, "go", guard(cfg))
    got = asyncio.ensure_future(collect(run))
    await asyncio.sleep(0.2)
    await run.stop()
    evs = await got
    assert (evs[-1].ended, evs[-1].text, evs[-1].errors) == (STOPPED, "it was stopped", [])
    assert ("POST", "/session/%s/abort" % run.session, {}) in fake.requests
    assert await run.send("x") is False
    await run.close()
    await h.close()


async def test_the_turn_cap_aborts_the_run_with_done_turns(cfg, system):
    cfg.max_turns = 2
    fake = FakeOpenCode([{"tool": "bash", "args": {"command": "ls"}}] * 2 +
                        [{"tool": "bash", "args": {"command": "ls"}, "gate": asyncio.Event()}])  # aborted as it waits
    h = harness(cfg, fake)
    run = await h.start("hot_fireball", system, "go", guard(cfg))
    evs = await collect(run)
    assert (evs[-1].ended, evs[-1].turns) == (TURNS, 2)
    assert len([e for e in evs if isinstance(e, Request)]) == 2
    await run.close()
    await h.close()


async def test_a_providers_error_ends_the_run_in_an_error(cfg, system):
    fake = FakeOpenCode([{"error": "No auth credentials found"}])
    h = harness(cfg, fake)
    run = await h.start("hot_fireball", system, "go", guard(cfg))
    evs = await collect(run)
    assert evs[-1].ended == ERROR and evs[-1].text == "OpenCode: APIError: No auth credentials found"
    assert evs[-1].errors == ["APIError: No auth credentials found"]
    await run.close()
    await h.close()


async def test_a_subagents_calls_are_guarded_and_counted_as_the_runs(cfg, system):
    fake = FakeOpenCode([{"child": {"tool": "bash", "args": {"command": "vfg cmd 'op Steve'"}}, "quiet": True,
                          "text": "Done: asked a helper."}])
    h = harness(cfg, fake)
    run = await h.start("hot_fireball", system, "go", guard(cfg))
    evs = await collect(run)
    child_results = [e for e in evs if isinstance(e, ToolResult)]
    assert child_results and child_results[0].error and child_results[0].text == OWNER
    assert len([e for e in evs if isinstance(e, Request)]) == 2 and evs[-1].turns == 2  # the helper's request too
    await run.close()
    await h.close()


async def test_opencodes_own_permission_ask_is_answered_and_logged(cfg, system):
    fake = FakeOpenCode([{"ask": "external_directory", "text": "Done."}])
    h = harness(cfg, fake)
    run = await h.start("hot_fireball", system, "go", guard(cfg))
    await collect(run)
    await asyncio.sleep(0.1)
    assert fake.replies == [("per_1", {"reply": "once"})]
    await run.close()
    await h.close()


async def test_the_runs_end_when_the_server_dies_and_the_next_run_starts_it_again(cfg, system):
    fake = FakeOpenCode([{"gate": asyncio.Event(), "text": "never"}])
    h = harness(cfg, fake)
    run = await h.start("hot_fireball", system, "go", guard(cfg))
    got = asyncio.ensure_future(collect(run))
    await asyncio.sleep(0.1)
    fake.exit(1)
    with pytest.raises(RuntimeError, match="exited"):
        await got
    await run.close()
    fake.steps = [{"text": "Done: again."}]
    run = await h.start("hot_fireball", system, "go", guard(cfg))
    assert (await collect(run))[-1].text == "Done: again." and fake.spawns == 2
    await run.close()
    await h.close()


async def test_a_new_system_prompt_starts_the_server_again_once_no_run_is_open(cfg, system, tmp_path):
    fake = FakeOpenCode([{"text": "Done: a."}, {"text": "Done: b."}])
    h = harness(cfg, fake)
    run = await h.start("a_task", system, "go", guard(cfg))
    await collect(run)
    await run.close()
    other = tmp_path / "implementer-system-def.md"
    other.write_text("NEW RULES\n")
    run = await h.start("b_task", other, "go", guard(cfg))
    await collect(run)
    await run.close()
    assert fake.spawns == 2 and h.system == str(other)
    await h.close()


async def test_no_key_no_server(tmp_path, system):
    c = ImplementerConfig.load({"VFG_REPO": str(tmp_path), "VFG_HOME": str(tmp_path / "h"), "VFG_IMPL_HARNESS": "opencode"})
    fake = FakeOpenCode()
    with pytest.raises(RuntimeError, match="no key for openrouter"):
        await oc.OpenCode(c, spawner=fake.spawn).start("t", system, "go", GuardContext(repo=c.repo))
    assert fake.spawns == 0


# ---------------------------------------------------------------- the guard's endpoint

async def test_the_guards_endpoint_wants_its_secret_and_an_open_run(cfg, system):
    fake = FakeOpenCode([{"gate": asyncio.Event(), "text": "x"}])
    h = harness(cfg, fake)
    run = await h.start("hot_fireball", system, "go", guard(cfg))
    url = fake.env["VFG_GUARD_URL"]
    async with httpx.AsyncClient() as c:
        wrong = await c.post(url, json={"tool": "bash", "session": run.session, "args": {"command": "ls"}},
                             headers={"authorization": "Bearer nope"})
        assert wrong.status_code == 403
        ok = {"authorization": "Bearer " + fake.env["VFG_GUARD_SECRET"]}
        allowed = await c.post(url, json={"tool": "bash", "session": run.session, "call": "c1",
                                          "args": {"command": "ls"}}, headers=ok)
        assert allowed.json() == {"deny": None}
        owner = await c.post(url, json={"tool": "bash", "session": run.session, "call": "c2",
                                        "args": {"command": "vfg cmd 'op Steve'"}}, headers=ok)
        assert owner.json() == {"deny": OWNER} and "c2" in run.denied
        library = await c.post(url, json={"tool": "write", "session": run.session, "call": "c3",
                                          "args": {"filePath": "scripts/x.py", "content": "x"}}, headers=ok)
        assert library.json()["deny"] == DENY.library(cfg.server)
        web = await c.post(url, json={"tool": "webfetch", "session": run.session, "call": "c4",
                                      "args": {"url": "https://example.com"}}, headers=ok)
        assert web.json() == {"deny": DENY.web} and "c4" in run.denied
        stranger = await c.post(url, json={"tool": "bash", "session": "ses_other", "args": {"command": "ls"}},
                                headers=ok)
        assert "No implementer run is open" in stranger.json()["deny"]
    await run.stop()
    await run.close()
    await h.close()


# ---------------------------------------------------------------- the plugin's JS, under node

PLUGIN_DRIVER = r"""
const mod = await import(process.argv[1]);
const hooks = await mod.VfgGuard({});
const out = {};
for (const [name, args] of [["allow", {command: "ls"}], ["deny", {command: "vfg cmd 'op Steve'"}]]) {
  try { await hooks["tool.execute.before"]({tool: "bash", sessionID: "ses_1", callID: name}, {args}); out[name] = "ran" }
  catch (e) { out[name] = "threw: " + e.message }
}
const env = {XDG_CACHE_HOME: "/x"};
await hooks["shell.env"]({cwd: "/"}, {env});
out.env = env;
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("answer", ["guard", "down", "broken"])
async def test_the_plugin_throws_the_guards_reason_and_refuses_when_it_cannot_ask(tmp_path, answer):
    async def serve(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        n = int(next(l.split(b":")[1] for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")))
        body = json.loads(await reader.readexactly(n))
        assert b"Bearer s3cret" in head
        if answer == "broken":
            data, status = b"oops", 500
        else:
            data, status = json.dumps({"deny": OWNER if "op Steve" in body["args"]["command"] else None}).encode(), 200
        writer.write(b"HTTP/1.1 %d X\r\ncontent-length: %d\r\nconnection: close\r\n\r\n%s" % (status, len(data), data))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    if answer == "down":
        server.close()
        await server.wait_closed()
    env = {"PATH": os.environ["PATH"], "VFG_GUARD_URL": "http://127.0.0.1:%d/guard" % port, "VFG_GUARD_SECRET": "s3cret",
           "VFG_SHELL_ENV": json.dumps({"XDG_CACHE_HOME": "/home/jm/.cache", "VFG_GUARD_SECRET": ""})}
    proc = await asyncio.create_subprocess_exec("node", "--input-type=module", "-e", PLUGIN_DRIVER, oc.PLUGIN, env=env,
                                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), 20)
    server.close()
    got = json.loads(out.decode().strip().splitlines()[-1])
    if answer == "guard":
        assert got["allow"] == "ran" and got["deny"] == "threw: " + OWNER
    elif answer == "down":
        assert got["allow"].startswith("threw: The guard could not be reached, so this call was not run")
    else:
        assert got["allow"] == "threw: The guard did not answer (HTTP 500), so this call was not run."
    assert got["env"] == {"XDG_CACHE_HOME": "/home/jm/.cache", "VFG_GUARD_SECRET": ""}
