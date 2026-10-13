#!/usr/bin/env python3
"""The real check of Prototype_Implementer_Harness's step 5: Hot fireball (T207) through the real
Implementers and the OpenCode adapter (VFG_IMPL_HARNESS=opencode), on DeepSeek V4.1 Flash through
OpenRouter, on a realmc server. Its parts, by name, in the order given (both by default):

- `op`: a short run told to run `vfg cmd 'op Steve'`, which the guard must refuse, the model
  seeing the guard's reason as the tool's error;
- `fireball`: Hot fireball's first ask, then its first follow-up, which must resume the same
  session.

    export REALMC_ROOT=…/.worktrees/script-library/integration/realmc/.server
    integration/realmc/up ih-c --fresh && eval "$(integration/realmc/env ih-c)"
    python3 integration/realmc/players.py spawn Steve && vfg action list --json >/dev/null
    cd brain && uv run python ../integration/impl_check_opencode.py OUT_DIR [op] [fireball] > OUT_DIR/brain.log

What the implementers make goes in made/<VFG_SERVER>/, by default made/<the realmc server's name>/.

OPEN_ROUTER_API is read from Internal/.env into this process's environment only (OpenCode's server
gets it in its own): never printed, logged or written. OpenCode reaches OpenRouter through a
recording pass-through on 127.0.0.1 (VFG_IMPL_BASE_URL), which keeps, per request, what OpenRouter
answered: its generation id, the provider that served it, its usage and the cost it billed, and
whether the request asked for thinking off; the key goes through in its header, unread. A run is
stopped once the check's requests, priced by config.PRICES, pass BUDGET dollars. Writes
OUT_DIR/check.json: each run's ask, status, report, session, seconds, requests and cost; each
session's thinking tokens, from OpenCode's own record; the cache hit rate; and what OpenRouter
billed, request by request."""
import asyncio
import json
import os
import re
import sys
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "brain"))

ENV_FILE = "/var/home/jm/Documents/EverHear/Internal/.env"
REPLAYED = "/var/home/jm/Documents/EverHear/.worktrees/ds-cost/integration/ds_cost.out/relay/T207.json"
BUDGET = 0.80
OP_ASK = ("Run exactly this one command first, and nothing else: vfg cmd 'op Steve' . Then report Done: and what "
          "came back, word for word. Do not try any other way.")


def load_key() -> str:
    with open(ENV_FILE) as f:
        for line in f:
            m = re.match(r"^\s*(?:export\s+)?OPEN_ROUTER_API\s*=\s*['\"]?([^'\"\s]+)", line)
            if m:
                os.environ["OPEN_ROUTER_API"] = m.group(1)
                return m.group(1)
    raise SystemExit("no OPEN_ROUTER_API in Internal/.env")


def asks() -> list[str]:
    with open(REPLAYED) as f:
        texts = [r["ask"] for r in json.load(f)["runs"]]
    return [re.sub(r"\bfrom T2\d\d\b", "", t.replace("KrownJM", "Steve")).strip() for t in texts]


async def spent(key: str) -> float | None:
    """What the key has spent on OpenRouter, in dollars; None when it cannot be told."""
    for path in ("/api/v1/key", "/api/v1/auth/key"):
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get("https://openrouter.ai" + path, headers={"authorization": "Bearer " + key})
                r.raise_for_status()
                return float(r.json()["data"]["usage"])
        except Exception as e:
            print("openrouter spend at %s: %s" % (path, type(e).__name__), file=sys.stderr)
    return None


class Recorder:
    """A pass-through to https://openrouter.ai that keeps what each answer says it cost."""

    def __init__(self):
        self.seen: list[dict] = []
        self.port = 0
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0))

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self._server.close()
        await self._http.aclose()

    async def _serve(self, reader, writer):
        from brain.implementers.harness.opencode import _read_request
        try:
            req = await _read_request(reader)
            if req is None:
                return
            method, target, headers, body = req
            rec = {"path": target}
            try:
                b = json.loads(body) if body else {}
                rec.update(reasoning_sent=b.get("reasoning"), messages=len(b.get("messages") or []),
                           tools=len(b.get("tools") or []))
            except ValueError:
                pass
            keep = {k: v for k, v in headers.items() if k in ("authorization", "content-type", "accept", "http-referer",
                                                              "x-title", "user-agent")}
            r = await self._http.send(self._http.build_request(method, "https://openrouter.ai" + target, headers=keep,
                                                               content=body), stream=True)
            writer.write(("HTTP/1.1 %d X\r\ncontent-type: %s\r\ntransfer-encoding: chunked\r\nconnection: close\r\n\r\n"
                          % (r.status_code, r.headers.get("content-type", ""))).encode())
            rec["status"] = r.status_code
            buf = b""
            async for chunk in r.aiter_bytes():
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    if line.startswith(b"data:") and b'"usage"' in line:
                        try:
                            d = json.loads(line[5:])
                            rec.update(id=d.get("id"), provider=d.get("provider"), model=d.get("model"),
                                       usage=d.get("usage"))
                        except ValueError:
                            pass
                writer.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                await writer.drain()
            writer.write(b"0\r\n\r\n")
            await writer.drain()
            await r.aclose()
            self.seen.append(rec)
        except Exception as e:
            print("recorder: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        finally:
            writer.close()


class Tap:
    """The OpenCode adapter, its runs' events kept for the check, and a run stopped over budget."""

    def __init__(self, inner, usage):
        self.inner = inner
        self.name = inner.name
        self.usage = usage
        self.runs: list[dict] = []
        self.cost = 0.0

    async def start(self, task, system, brief, guard):
        return TapRun(self, await self.inner.start(task, system, brief, guard), task, "start", None)

    async def resume(self, task, system, session, text, guard):
        return TapRun(self, await self.inner.resume(task, system, session, text, guard), task, "resume", session)

    async def close(self):
        await self.inner.close()


class TapRun:
    def __init__(self, tap: Tap, run, task: str, how: str, resumed: str | None):
        self.tap, self.run = tap, run
        self.rec = {"task": task, "how": how, "resumed": resumed, "session": run.session, "requests": [], "calls": [],
                    "results": [], "texts": [], "done": None, "began": time.time()}
        tap.runs.append(self.rec)

    @property
    def session(self):
        return self.run.session

    async def events(self):
        from brain.implementers.harness.base import Done, Request, Text, ToolCall, ToolResult
        async for ev in self.run.events():
            if isinstance(ev, Request):
                c = self.tap.usage.cost(ev)
                self.tap.cost += c
                self.rec["requests"].append({"input": ev.input, "cache_read": ev.cache_read, "cache_write": ev.cache_write,
                                             "output": ev.output, "size": ev.size, "at": ev.at, "cost": c})
                if self.tap.cost > BUDGET:
                    print("over budget ($%.3f): stopping" % self.tap.cost, file=sys.stderr)
                    asyncio.get_running_loop().create_task(self.run.stop())
            elif isinstance(ev, ToolCall):
                self.rec["calls"].append({"id": ev.id, "name": ev.name, "input": ev.input})
            elif isinstance(ev, ToolResult):
                self.rec["results"].append({"id": ev.id, "error": ev.error, "text": ev.text[:300]})
            elif isinstance(ev, Text):
                self.rec["texts"].append(ev.text[:500])
            elif isinstance(ev, Done):
                self.rec["done"] = {"ended": ev.ended, "turns": ev.turns, "seconds": ev.seconds, "errors": ev.errors,
                                    "refused": ev.refused, "text": ev.text[:2000]}
            yield ev

    async def send(self, text):
        return await self.run.send(text)

    async def stop(self):
        await self.run.stop()

    async def close(self):
        await self.run.close()


async def main(out: str, parts: list[str]) -> None:
    key = load_key()
    recorder = Recorder()
    await recorder.start()
    os.environ["VFG_IMPL_BASE_URL"] = "http://127.0.0.1:%d/api" % recorder.port
    os.environ["VFG_IMPL_PROVIDER"] = "openrouter"
    os.environ["VFG_IMPL_HARNESS"] = "opencode"
    os.environ.setdefault("VFG_SERVER", os.environ.get("REALMC_NAME") or "main")
    os.environ["VFG_USAGE_QUEUE"] = out
    from brain.implementers import usage
    from brain.implementers.config import ImplementerConfig, log
    from brain.implementers.harness.opencode import OpenCode
    from brain.implementers.runner import Implementers

    cfg = ImplementerConfig.load()
    assert cfg.harness == "opencode" and cfg.provider == "openrouter", (cfg.harness, cfg.provider)
    oc = OpenCode(cfg)
    tap = Tap(oc, usage)
    reports: asyncio.Queue = asyncio.Queue()
    sessions = []

    async def on_report(name, status, report, description, made):
        await reports.put({"status": status, "report": report, "description": description, "made": made,
                           "ended": time.time()})

    imps = Implementers(cfg, on_report, on_session=lambda n, s: sessions.append((n, s)), harness=tap)
    before = await spent(key)
    a = asks()
    plan = []
    for p in parts:
        if p == "op":
            plan.append(("op_check", "start", OP_ASK))
        elif p == "fireball":
            plan += [("hot_fireball", "start", a[0]), ("hot_fireball", "follow_up", a[1])]
    record = []
    for name, kind, text in plan:
        if tap.cost > BUDGET:
            log("check", "over budget: %s %s not run" % (name, kind))
            break
        started = time.time()
        log("check", "%s %s: %s" % (name, kind, text[:200]))
        await (imps.start(name, text) if kind == "start" else imps.follow_up(name, text))
        r = await asyncio.wait_for(reports.get(), cfg.time_limit_s + 120)
        r.update(task=name, kind=kind, ask=text, session=imps.session(name), seconds=round(r["ended"] - started, 1))
        record.append(r)
        log("check", "%s %s in %s s, session %s: %s" % (name, r["status"], r["seconds"], r["session"],
                                                         re.sub(r"\s+", " ", r["report"])[:300]))

    # OpenCode's own record of each session: its steps, and any thinking in them.
    thinking = {}
    for sid in sorted({s for _, s in sessions}):
        try:
            msgs = await oc.messages(sid)
        except Exception as e:
            thinking[sid] = {"error": str(e)}
            continue
        steps = [p for m in msgs for p in m.get("parts") or [] if p.get("type") == "step-finish"]
        thinking[sid] = {
            "steps": len(steps),
            "reasoning_tokens": sum((p.get("tokens") or {}).get("reasoning") or 0 for p in steps),
            "reasoning_parts": sum(1 for m in msgs for p in m.get("parts") or [] if p.get("type") == "reasoning"),
            "reasoning_details": sum(len(((p.get("metadata") or {}).get("openrouter") or {}).get("reasoning_details") or [])
                                     for m in msgs for p in m.get("parts") or []),
        }
    await imps.close(5)
    after = None
    for _ in range(6):  # OpenRouter's count catches up in a few seconds
        await asyncio.sleep(5)
        after = await spent(key)
        if after is not None and before is not None and after - before >= tap.cost * 0.5:
            break

    await recorder.stop()
    billed = [r for r in recorder.seen if r.get("usage")]
    reqs = [q for run in tap.runs for q in run["requests"]]
    size = sum(q["size"] for q in reqs) or 1
    summary = {
        "runs": record,
        "harness_runs": tap.runs,
        "sessions": sessions,
        "thinking": thinking,
        "requests": len(reqs),
        "input_uncached": sum(q["input"] for q in reqs),
        "cache_read": sum(q["cache_read"] for q in reqs),
        "cache_write": sum(q["cache_write"] for q in reqs),
        "output": sum(q["output"] for q in reqs),
        "cache_hit_rate": round(sum(q["cache_read"] for q in reqs) / size, 4),
        "cost_by_our_table": round(tap.cost, 4),
        "openrouter_key_spend_delta": None if before is None or after is None else round(after - before, 4),
        "openrouter_billed": round(sum(float((r["usage"] or {}).get("cost") or 0) for r in billed), 4),
        "openrouter_requests": recorder.seen,
        "openrouter_cached": sum(((r["usage"] or {}).get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                                 for r in billed),
        "openrouter_prompt": sum((r["usage"] or {}).get("prompt_tokens") or 0 for r in billed),
        "openrouter_reasoning": sum(((r["usage"] or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
                                    or 0 for r in billed),
        "providers": sorted({str(r.get("provider")) for r in billed}),
    }
    with open(os.path.join(out, "check.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    log("check", "requests %d, cache hit %.1f%%, cost by our table $%.4f, OpenRouter billed $%.4f for %d requests "
        "(the key's spend moved %s, other users of the key included), providers %s, reasoning tokens %d" % (
            summary["requests"], 100 * summary["cache_hit_rate"], summary["cost_by_our_table"],
            summary["openrouter_billed"], len(billed), summary["openrouter_key_spend_delta"],
            ", ".join(summary["providers"]), summary["openrouter_reasoning"]))


if __name__ == "__main__":
    out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "impl_check_opencode.out")
    os.makedirs(out, exist_ok=True)
    asyncio.run(main(out, sys.argv[2:] or ["op", "fireball"]))
