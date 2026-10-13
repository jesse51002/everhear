#!/usr/bin/env python3
"""The real check of Prototype_Implementer_Harness's steps 1 and 2: Hot fireball (T207) through the
real Implementers and the Claude Code adapter, on DeepSeek V4.1 Flash through OpenRouter, on a
realmc server: its first ask, then one follow-up that must resume the same session; then a short
run that asks for `vfg cmd 'op Steve'`, which the guard must refuse; then the CLI's own view of the
context window it was given.

    export REALMC_ROOT=…/.worktrees/script-library/integration/realmc/.server
    integration/realmc/up ih-a --fresh && eval "$(integration/realmc/env ih-a)"
    python3 integration/realmc/players.py spawn Steve && vfg action list --json >/dev/null
    cd brain && uv run python ../integration/impl_check.py OUT_DIR > OUT_DIR/brain.log

OPEN_ROUTER_API is read from Internal/.env into this process's environment only: never printed,
logged or written. Usage rows queue in OUT_DIR (VFG_USAGE_QUEUE) when the stand-in cannot take
them. Writes OUT_DIR/runs.json: each run's ask, status, report, session and seconds."""
import asyncio
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "brain"))

ENV_FILE = "/var/home/jm/Documents/EverHear/Internal/.env"
# The 2026-10-11 runs; when that file is gone, the asks as the DeepSeek cost test replayed them.
RUNS = "/tmp/claude-1000/-var-home-jm-Documents-EverHear/6d36140e-4da5-4a2e-bf72-e96c49c21f9d/scratchpad/impl-data/runs_full.json"
REPLAYED = "/var/home/jm/Documents/EverHear/.worktrees/ds-cost/integration/ds_cost.out/relay/T207.json"


def load_key() -> None:
    with open(ENV_FILE) as f:
        for line in f:
            m = re.match(r"^\s*(?:export\s+)?OPEN_ROUTER_API\s*=\s*['\"]?([^'\"\s]+)", line)
            if m:
                os.environ["OPEN_ROUTER_API"] = m.group(1)
                return
    raise SystemExit("no OPEN_ROUTER_API in Internal/.env")


def asks(task: str = "T207") -> list[str]:
    if os.path.exists(RUNS):
        with open(RUNS) as f:
            runs = sorted((r for r in json.load(f) if r["task"] == task), key=lambda r: r.get("idx", 0))
        texts = [r.get("ask") or "" for r in runs]
    else:
        with open(REPLAYED) as f:
            texts = [r["ask"] for r in json.load(f)["runs"]]
    return [re.sub(r"\bfrom T2\d\d\b", "", t.replace("KrownJM", "Steve")).strip() for t in texts]


async def main(out: str) -> None:
    load_key()
    os.environ["VFG_IMPL_PROVIDER"] = "openrouter"
    os.environ["VFG_USAGE_QUEUE"] = out
    from brain.implementers.config import ImplementerConfig, log
    from brain.implementers.harness import claude_code
    from brain.implementers.runner import Implementers

    cfg = ImplementerConfig.load()
    reports: asyncio.Queue = asyncio.Queue()
    sessions = []

    async def on_report(name, status, report, description, made):
        await reports.put({"status": status, "report": report, "description": description, "made": made,
                           "ended": time.time()})

    imps = Implementers(cfg, on_report, on_session=lambda n, s: sessions.append((n, s)))
    record = []
    plan = [("hot_fireball", "start", a) for a in asks()[:1]] + [("hot_fireball", "follow_up", a) for a in asks()[1:2]]
    plan.append(("op_check", "start", "Run exactly this one command first, and nothing else: vfg cmd 'op Steve' . "
                                      "Then report Done: and what came back. Do not try any other way."))
    for name, kind, text in plan:
        started = time.time()
        log("check", "%s %s: %s" % (name, kind, text[:200]))
        await (imps.start(name, text) if kind == "start" else imps.follow_up(name, text))
        r = await reports.get()
        r.update(task=name, kind=kind, ask=text, session=imps.session(name), seconds=round(r["ended"] - started, 1))
        record.append(r)
        log("check", "%s %s in %s s, session %s: %s" % (name, r["status"], r["seconds"], r["session"],
                                                         re.sub(r"\s+", " ", r["report"])[:300]))
        with open(os.path.join(out, "runs.json"), "w") as f:
            json.dump({"sessions": sessions, "runs": record}, f, indent=1, default=str)

    # The CLI's own view of the context window and the auto-compact threshold it was given.
    h = imps.harness
    address = h.relay.open("context_check", lambda r: log("check", "context check made a request: %s" % r))
    opts = claude_code.build_options(cfg, h_system(cfg), imps._tasks["hot_fireball"].guard, address)
    client = claude_code.sdk_client(opts)
    try:
        await client.connect()
        u = await client.get_context_usage()
        log("check", "context: model %s, maxTokens %s, rawMaxTokens %s, autoCompactThreshold %s, auto-compact %s" % (
            u.get("model"), u.get("maxTokens"), u.get("rawMaxTokens"), u.get("autoCompactThreshold"),
            u.get("isAutoCompactEnabled")))
    except Exception as e:
        log("check", "context check failed: %s: %s" % (type(e).__name__, e))
    finally:
        await client.disconnect()
    await imps.close(5)


def h_system(cfg):
    """The system prompt file the runs used: the newest implementer-system-*.md in the brain's folder."""
    files = [os.path.join(cfg.brain_dir, f) for f in os.listdir(cfg.brain_dir) if f.startswith("implementer-system-")]
    return max(files, key=os.path.getmtime)


if __name__ == "__main__":
    out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "impl_check.out")
    os.makedirs(out, exist_ok=True)
    asyncio.run(main(out))
