"""Replay: the playtest's tasks through the complexity selector alone, to judge a model or a prompt.

    uv run python -m dispatcher.replay [--model <id>] [--limit <n>] [--only T3,T9] [--jobs 8]
                                       [--eval <labels.jsonl>]

Reads ~/.vfg/orch/inbox.jsonl (VFG_ORCH_DIR) READ-ONLY, sends each task through the
selector with the loaded actions' menu, `SAVED PLACES: none` and the providers of
VFG_DISPATCH_PROVIDERS, `--jobs` at a time, and
prints each selection with its cost and ms as it comes back, then the totals: p50 and p90
ms over every task and over the model calls alone, the cost, and the picks naming a script
that is not in scripts/, both those that got through and those the selector caught and
handed on whole. It acts on nothing: no vfg, no action, no reply.

`--eval` scores every selection against a labels file, one JSON line per task:
`{"id": "T7", "accept": ["command+orchestrator", "orchestrator"], "either": false}`. A
route is `orchestrator` for the whole task, or the picked actions' names, sorted and joined
by `,`, with `+orchestrator` when there is also an orchestrator part. A task marked
`either` is not scored for agreement. Work is LOST when every accepted route has an
orchestrator part and the selection has picks and none: that part was left out, or given to
an action that cannot do it. A multi-part task may also list its parts, one regex each,
`"parts": ["speed", "golden apple"]`: a part is DROPPED, and the task's work lost, when its
words are in no pick's instruction and not in the orchestrator text of a selection that
has picks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from pathlib import Path

import httpx
import vfg_llm
from vfg_llm import LlmError

from . import actions, config
from .selector import Selector, describe, unknown_scripts

PLACES = "SAVED PLACES: none"
WHOLE = "orchestrator"


def read_tasks(path: Path, limit: int | None = None, only: set[str] | None = None) -> list[dict]:
    """Each task line once, in order; anything else skipped."""
    seen, tasks = set(), []
    with open(path, encoding="utf-8") as f:  # read-only
        for raw in f:
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict) or not isinstance(obj.get("task"), str) or not obj["task"].strip():
                continue
            tid = obj.get("id")
            if tid in seen or (only and tid not in only):
                continue
            seen.add(tid)
            tasks.append({"id": tid, "task": obj["task"].strip(), "context": str(obj.get("context") or "").strip()})
            if limit and len(tasks) >= limit:
                break
    return tasks


def read_labels(path: Path) -> dict[str, dict]:
    labels = {}
    with open(path, encoding="utf-8") as f:
        for raw in f:
            if raw.strip():
                obj = json.loads(raw)
                labels[obj["id"]] = {"accept": {norm(r) for r in obj["accept"]}, "either": bool(obj.get("either")),
                                     "parts": list(obj.get("parts") or [])}
    return labels


# ------------------------------------------------------------------ scoring

def norm(route: str) -> str:
    """`teleport,command+orchestrator` -> `command,teleport+orchestrator`."""
    route = route.strip()
    if route == WHOLE:
        return WHOLE
    names, plus, _ = route.partition("+")
    return ",".join(sorted({n.strip() for n in names.split(",") if n.strip()})) + ("+" + WHOLE if plus else "")


def route_of(selection) -> str:
    """What the dispatcher does with it: no picks is the whole task handed on."""
    if not selection.picks:
        return WHOLE
    return norm(",".join(p.action for p in selection.picks) + ("+" if selection.orchestrator.strip() else ""))


def lost(route: str, accept: set[str]) -> bool:
    """Picks and no orchestrator part, when every accepted route needs the orchestrator."""
    if route == WHOLE or route.endswith("+" + WHOLE):
        return False
    return bool(accept) and all(r == WHOLE or r.endswith("+" + WHOLE) for r in accept)


def dropped(selection, parts: list[str]) -> list[str]:
    """The labelled parts the selection names nowhere; none when the whole task is handed on."""
    if not selection.picks:
        return []
    said = " | ".join([p.instruction for p in selection.picks] + [selection.orchestrator])
    return [rx for rx in parts if not re.search(rx, said, re.IGNORECASE)]


def pct(times: list[int], q: float) -> int:
    """The q-th percentile, nearest rank."""
    if not times:
        return 0
    s = sorted(times)
    return s[max(0, math.ceil(q * len(s)) - 1)]


# ------------------------------------------------------------------ the run

async def replay(tasks: list[dict], selector: Selector, out=sys.stdout, *, labels: dict[str, dict] | None = None,
                 scripts: set[str] | frozenset[str] = frozenset(), jobs: int = 8) -> dict:
    gate = asyncio.Semaphore(max(1, jobs))
    results: dict[str, dict] = {}

    async def one(t: dict) -> None:
        task = " ".join(t["task"].split())
        async with gate:
            caught: list[str] = []
            try:
                answer = await selector.select(t["task"], t["context"], PLACES, caught)
            except LlmError as e:
                r = {"failed": str(e), "route": WHOLE, "invented": [], "caught": [], "dropped": []}  # handed on whole
                head, body = f"{t['id']}  FAILED  {task[:100]}", f"    {e}"
            else:
                label = (labels or {}).get(t["id"]) or {}
                r = {"ms": answer.ms, "cost": answer.cost, "route": route_of(answer.value),
                     "invented": unknown_scripts(answer.value, scripts), "caught": caught,
                     "dropped": dropped(answer.value, label.get("parts", []))}
                head = f"{t['id']}  {answer.ms} ms  ${answer.cost:.6f}  {task[:100]}"
                body = f"    {describe(answer.value)}"
        marks = [f"INVENTED {x}" for x in r["invented"]] + [f"handed on whole for {x}" for x in r["caught"]]
        label = (labels or {}).get(t["id"])
        if label:
            r["lost"] = lost(r["route"], label["accept"]) or bool(r["dropped"])
            marks += [f"DROPPED {x}" for x in r["dropped"]]
            r["agree"] = r["route"] in label["accept"]
            r["scored"] = not label["either"]
            if r["lost"]:
                marks.append("LOST")
            if r["scored"] and not r["agree"]:
                marks.append(f"MISROUTE {r['route']}, expected {' or '.join(sorted(label['accept']))}")
        results[t["id"]] = r
        print(head + "".join(f"  [{m}]" for m in marks) + "\n" + body, file=out, flush=True)

    await asyncio.gather(*(one(t) for t in tasks))

    ok = [r for r in results.values() if "failed" not in r]
    times = [r["ms"] for r in ok]
    called = [r["ms"] for r in ok if r["ms"] or r["cost"]]  # a task the selector answered without a model call costs 0 ms
    summary = {"tasks": len(tasks), "failed": len(results) - len(ok), "cost": sum(r["cost"] for r in ok),
               "median_ms": pct(times, 0.5), "p90_ms": pct(times, 0.9),
               "calls": len(called), "calls_median_ms": pct(called, 0.5), "calls_p90_ms": pct(called, 0.9),
               "invented": sum(len(r["invented"]) for r in results.values()),
               "caught": sum(1 for r in results.values() if r["caught"])}
    print(f"\n{summary['tasks']} tasks, {summary['failed']} failed; ${summary['cost']:.6f} in all; "
          f"p50 {summary['median_ms']} ms, p90 {summary['p90_ms']} ms; "
          f"{summary['calls']} model calls: p50 {summary['calls_median_ms']} ms, p90 {summary['calls_p90_ms']} ms; "
          f"{summary['invented']} pick(s) naming a script not in scripts/, "
          f"{summary['caught']} task(s) handed on whole for one", file=out, flush=True)
    if labels is not None:
        scored = [i for i, r in results.items() if r.get("scored")]
        agree = [i for i in scored if results[i]["agree"]]
        multi = [i for i in results if labels.get(i, {}).get("parts")]
        summary.update(scored=len(scored), agree=len(agree),
                       lost=sum(1 for r in results.values() if r.get("lost")),
                       unlabelled=sum(1 for i in results if i not in labels),
                       multipart=len(multi), covered=sum(1 for i in multi if not results[i]["dropped"]))
        share = 100 * len(agree) / len(scored) if scored else 0.0
        print(f"route agreement {len(agree)}/{len(scored)} ({share:.1f}%), {summary['lost']} lost, "
              f"{len(results) - len(scored) - summary['unlabelled']} either, {summary['unlabelled']} unlabelled; "
              f"every part named in {summary['covered']}/{summary['multipart']} multi-part task(s)",
              file=out, flush=True)
    return summary


async def _main(args) -> int:
    cfg = config.load()
    inbox = cfg.orch_dir / "inbox.jsonl"
    only = {x.strip() for x in args.only.split(",") if x.strip()} if args.only else None
    tasks = read_tasks(inbox, args.limit, only)
    catalog = actions.load(cfg.repo)
    if not catalog:
        print("no actions loaded: nothing to select from", file=sys.stderr)
        return 1
    labels = read_labels(Path(args.eval).expanduser()) if args.eval else None
    scripts = actions.scripts(cfg.repo)

    async def ask(prompt, output, *, system=None, model):
        return await vfg_llm.structured(prompt, output, system=system, model=model, reasoning=cfg.effort,
                                        providers=list(cfg.providers) or None, timeout=cfg.model_timeout_s)

    selector = Selector(catalog, ask, args.model or cfg.model, scripts=list(scripts))
    print(f"{len(tasks)} tasks from {inbox}, model {args.model or cfg.model}, providers "
          f"{','.join(cfg.providers) or 'OpenRouter’s choice'}, {args.jobs} at a time\n", flush=True)
    async with httpx.AsyncClient(timeout=30) as http:
        vfg_llm.client = http
        try:
            await replay(tasks, selector, labels=labels, scripts=frozenset(scripts), jobs=args.jobs)
        finally:
            vfg_llm.client = None
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m dispatcher.replay", description=__doc__.split("\n\n")[0])
    p.add_argument("--model", help="the model to judge (default VFG_DISPATCH_MODEL)")
    p.add_argument("--limit", type=int, help="only the first n tasks")
    p.add_argument("--only", help="only these task ids, comma-separated: T3,T9")
    p.add_argument("--jobs", type=int, default=8, help="how many calls at a time (default 8)")
    p.add_argument("--eval", help="a labels file to score against, one JSON line per task")
    return asyncio.run(_main(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
