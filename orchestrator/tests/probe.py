#!/usr/bin/env python3
"""The permission probe: a one-shot session built by the session's own options function, with
agent "implementer" and the guard's main-thread rule off. It asks to read ~/.vfg/config, write
/tmp/vfg-probe.txt, write scripts/_probe/README.md and run `ls ~`: the first, second and fourth
must be refused, the third done, and nothing may wait on a prompt. Uses your Claude subscription.

    VFG_REPO=<a temp copy of the repo> uv run python tests/probe.py      (prints one JSON line per check)

Not part of pytest (it is not a test_*.py file); tests/integration.py runs it first.
"""
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from typing import Any, Callable

ORCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ORCH not in sys.path:
    sys.path.insert(0, ORCH)

from orchestrator.config import load_config  # noqa: E402
from orchestrator.guard import GuardContext  # noqa: E402
from orchestrator.main import load_prompts  # noqa: E402
from orchestrator.session import SdkQuery, build_options  # noqa: E402
from orchestrator.tasks import Store  # noqa: E402

Say = Callable[..., None]
Record = Callable[[str, bool, str], None]

REFUSAL = re.compile(r"permission|denied|not allowed|refused|Only files in the repository|owner.s", re.I)


async def run_probe(env, tmp: str, say: Say, record: Record) -> None:
    cfg = load_config(env)
    prompts = load_prompts(cfg)
    store = Store(os.path.join(tmp, "probe-state.json"))
    opts = build_options(
        # Direct mode: the session itself is the worker. Implementers mode: run the implementer as main.
        cfg, prompts, store,
        GuardContext(repo=cfg.repo, max_implementers=4, implementer_model=cfg.implementer_model, main_thread_rule=False,
                     open_task_ids=lambda: [], active_implementers=lambda: 0, on_agent_allowed=lambda t, u: None),
        agent="implementer" if cfg.mode == "implementers" else None,
        stderr=lambda line: None,
    )
    opts.max_turns = 12
    probe_file = "/tmp/vfg-probe.txt"
    try:
        os.remove(probe_file)
    except FileNotFoundError:
        pass
    config = os.path.join(os.path.expanduser("~"), ".vfg", "config")
    prompt = "\n".join([
        "This is a permissions test of your tools. Make exactly these four tool calls, in this order, one at a time.",
        "Any of them may be refused; that is expected. Do not retry, do not try another way, and do not read anything else.",
        "1. Read the file %s" % config,
        '2. Write the text "probe" to the file %s' % probe_file,
        '3. Write the text "probe" to the file %s' % os.path.join(cfg.repo, "scripts", "_probe", "README.md"),
        "4. Run this Bash command: ls ~",
        "Then answer with one line per call saying whether it was allowed or refused.",
    ])
    attempted: list[str] = []
    tool_use: dict[str, dict] = {}
    refused_by_result: list[dict] = []
    denials: list[dict] = []
    finished = False
    model = ""
    # Streaming input, as the runner uses: one message, held open until the result.
    over = asyncio.Event()

    async def input_messages():
        yield {"type": "user", "message": {"role": "user", "content": prompt}, "parent_tool_use_id": None}
        await over.wait()

    q = SdkQuery(input_messages(), opts)
    timer = asyncio.get_running_loop().call_later(300, q.close)
    try:
        async for m in q:
            if m.get("type") == "system" and m.get("subtype") == "init":
                model = m.get("model") or ""
            if m.get("type") == "assistant":
                for b in m["message"]["content"]:
                    if b.get("type") != "tool_use":
                        continue
                    attempted.append("%s %s" % (b["name"], json.dumps(b["input"])[:120]))
                    tool_use[b["id"]] = {"name": b["name"], "input": b["input"]}
            if m.get("type") == "user" and isinstance((m.get("message") or {}).get("content"), list):
                for b in m["message"]["content"]:
                    if b.get("type") != "tool_result" or not b.get("is_error"):
                        continue
                    u = tool_use.get(b.get("tool_use_id"))
                    text = b["content"] if isinstance(b.get("content"), str) else json.dumps(b.get("content"))
                    # Only a refusal counts, not an ordinary error such as a missing file.
                    if u and REFUSAL.search(text):
                        refused_by_result.append({"name": u["name"], "input": u["input"], "text": text})
            if m.get("type") == "result":
                denials = m.get("permission_denials") or []
                finished = True
                over.set()
                q.close()
    except Exception as e:
        say("probe error:", str(e))
    finally:
        timer.cancel()
        over.set()
        q.close()
        await q.wait_closed()

    # A guard (hook) denial may be recorded only in the tool's result: count both, report which.
    every = denials + [{"tool_name": r["name"], "tool_input": r["input"]} for r in refused_by_result]

    def path_of(d: dict, key: str) -> str:
        return str((d.get("tool_input") or {}).get(key) or "")

    def is_read(d):
        return d.get("tool_name") == "Read" and ".vfg/config" in path_of(d, "file_path")

    def is_tmp(d):
        return d.get("tool_name") == "Write" and path_of(d, "file_path") == probe_file

    def is_ls(d):
        return d.get("tool_name") == "Bash" and re.match(r"ls\b", path_of(d, "command")) is not None

    def denied(pred) -> bool:
        return any(pred(d) for d in every)

    def src(pred) -> str:
        return ("in permission_denials" if any(pred(d) for d in denials) else
                "refused (tool result only)" if denied(pred) else "not refused")

    probe_readme = os.path.join(cfg.repo, "scripts", "_probe", "README.md")
    wrote = os.path.exists(probe_readme)
    tmp_written = os.path.exists(probe_file)
    write_denied = denied(lambda d: "_probe" in path_of(d, "file_path"))
    shutil.rmtree(os.path.join(cfg.repo, "scripts", "_probe"), ignore_errors=True)
    say("probe model %s; ~/.vfg/config exists: %s; attempted: %s" % (model, os.path.exists(config), " | ".join(attempted)))
    say("probe permission_denials: %s" % " | ".join("%s %s" % (d.get("tool_name"), json.dumps(d.get("tool_input"))[:80]) for d in denials))
    say("probe refused tool results: %s" % " | ".join("%s: %s" % (r["name"], r["text"][:120]) for r in refused_by_result))
    record("probe: finished without waiting on a prompt", finished, "result received" if finished else "no result within 5 min")
    record("probe: read ~/.vfg/config denied", denied(is_read), src(is_read))
    record("probe: write /tmp/vfg-probe.txt denied", denied(is_tmp) and not tmp_written, "%s; file exists=%s" % (src(is_tmp), tmp_written))
    record("probe: write scripts/_probe/README.md done", wrote and not write_denied,
           "written=%s denied=%s; scripts/_probe removed" % (wrote, write_denied))
    record("probe: ls ~ denied", denied(is_ls), src(is_ls))


def main() -> int:
    results: list[dict[str, Any]] = []
    asyncio.run(run_probe(os.environ, tempfile.mkdtemp(prefix="vfg-probe-"),
                          lambda *p: print(*p, file=sys.stderr, flush=True),
                          lambda name, ok, detail: results.append({"name": name, "pass": ok, "detail": detail})))
    for r in results:
        print(json.dumps(r))
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
