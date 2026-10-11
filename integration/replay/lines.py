#!/usr/bin/env python3
"""The raw-line replay with the whole conversation: Prototype_Brain's first required test.

Every direct-ask LINE of the 2026-10-08 playtest (357, Jev's `direct_ask`, today's
`action_oriented`), in the order Jev fed them, through the brain's real call: `brain.call.Call`
with the real catalog, prompt and command references, built as `brain/__main__.py` builds it, on
the brain's own settings (`brain.config`: GPT-6.1 Sol, low, VFG_OWNER). Model calls only: no
server, no `vfg`, nothing run; the answers are recorded and lines_score.py scores them.

The context is the brain's at that moment (playtest.py): every row it would have had by the time
Jev fed the line, the AI's chat, the conversation's tasks as `AI task` rows and their results as
`AI result` rows, so it sees task ids as it would live; the open tasks are the ones not yet
closed then; the world names who is online and says the rest is unknown. What is NEW is what no
read took in: each call is a live read, and the batch timer (60 s with no read and something
unread) takes in the rest, as timer.py would. The calls run one after another, so the prompt
cache works as it does live, and each records its time, cost, prompt and cached tokens.

    (cd brain && uv sync --frozen --inexact)
    brain/.venv/bin/python integration/replay/lines.py --db DIR/vfg/vfg.db --out OUT --limit 20   # a pilot
    brain/.venv/bin/python integration/replay/lines.py --db DIR/vfg/vfg.db --out OUT              # all 357
    brain/.venv/bin/python integration/replay/lines_score.py OUT

A second run into the same OUT goes on from the last line recorded. `--only 13,15,18` runs those
lines alone, the context still built in full. In OUT: lines.jsonl (one row per line: the line,
what the conversation did with it, the decision, ms, cost and tokens), meta.json, and the whole
prompt of every 50th call in prompts/.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import playtest  # noqa: E402  (puts brain/ on the path)
from playtest import Clock, Playtest  # noqa: E402

import httpx  # noqa: E402
import vfg_llm  # noqa: E402
from brain import actions, config  # noqa: E402
from brain import refs as refs_module  # noqa: E402
from brain.__main__ import asker  # noqa: E402
from brain.call import PROMPT, Call, Event  # noqa: E402

REFS = [Path.home() / ".vfg" / "brain" / "refs" / "26.3", Path.home() / ".vfg" / "dispatch" / "refs" / "26.3"]
PROMPT_EVERY = 50


def load_refs():
    """The command references for 26.3, read as the brain reads a complete cache, never written."""
    for d in REFS:
        if (d / "complete.json").is_file():
            mark = json.loads((d / "complete.json").read_text())
            if mark.get("format") != refs_module.FORMAT:
                sys.exit(f"{d} is format {mark.get('format')}, the brain reads {refs_module.FORMAT}: "
                         "it would rewrite it; copy it elsewhere first")
            return refs_module._read_complete(d, lambda *a: None), d
    sys.exit(f"no command references in {' or '.join(map(str, REFS))}")


def git_head() -> str:
    r = subprocess.run(["git", "-C", str(playtest.REPO), "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", str(playtest.REPO), "status", "--porcelain", "brain/prompts"],
                           capture_output=True, text=True).stdout.strip()
    return r.stdout.strip() + (" (brain/prompts changed)" if dirty else "")


def part_dict(p) -> dict:
    return {"part": p.part, "to": p.to, "task": getattr(p, "task", ""), "instruction": p.instruction, "drop": p.drop,
            "action": [{"key": v.key, "value": v.value} for v in p.action] if p.action is not None else None}


def truth(pt: Playtest, ln) -> list[dict]:
    out = []
    for t in ln.tasks:
        new, old = pt.labels.get(t["id"], {}), pt.labels_old.get(t["id"], {})
        out.append({"id": t["id"], "task": t["task"], "accept": new.get("accept"), "unsure": new.get("unsure"),
                    "accept_old": old.get("accept"), "either_old": old.get("either")})
    return out


async def run(args) -> None:
    out = Path(args.out)
    (out / "prompts").mkdir(parents=True, exist_ok=True)
    pt = Playtest(Path(args.db))
    cfg = config.load()
    entries = pt.entries()
    clock = Clock(entries, getattr(cfg, "batch_s", 60.0))
    asks = [e for e in entries if e.row.type == "LINE" and e.row.cls == "action_oriented"]
    only = {int(x) for x in args.only.split(",")} if args.only else None
    chosen = [e for e in asks if only is None or e.n in only][:args.limit or None]
    upto = asks.index(chosen[-1]) + 1 if chosen else 0

    catalog = actions.load(cfg.repo, cfg.server)
    hidden = actions.hidden_commands()
    refs, refs_dir = load_refs()
    call = Call(catalog, asker(cfg.model, cfg.reasoning, cfg.timeout_s, cfg.providers), owner=cfg.owner,
                hidden=hidden, checkpoint=cfg.checkpoint)
    rows_file = out / "lines.jsonl"
    done = set()
    if rows_file.exists():
        done = {json.loads(x)["n"] for x in rows_file.read_text().splitlines() if x.strip()}
    meta = {"started": datetime.now(UTC).isoformat(timespec="seconds"), "db": str(args.db), "head": git_head(),
            "model": cfg.model, "reasoning": cfg.reasoning, "providers": list(cfg.providers), "owner": cfg.owner,
            "checkpoint": cfg.checkpoint, "batch_s": getattr(cfg, "batch_s", 60.0), "timeout_s": cfg.timeout_s,
            "actions": len(catalog.names()), "refs": str(refs_dir),
            "prompt_sha": hashlib.sha256(PROMPT.read_bytes()).hexdigest()[:12],
            "asks": len(asks), "chosen": len(chosen), "already": len(done & {e.n for e in chosen})}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"{len(chosen)} lines ({meta['already']} already recorded) of {len(asks)}; {cfg.model} {cfg.reasoning}; "
          f"owner {cfg.owner}; {len(catalog.names())} actions; head {meta['head']}", flush=True)

    spent, count = 0.0, 0
    chosen_ns = {e.n for e in chosen}
    async with httpx.AsyncClient(timeout=30) as http:
        vfg_llm.client = http
        with rows_file.open("a", encoding="utf-8") as f:
            for e in asks[:upto]:
                new = [r for r in clock.read(e.seen) if r.seq != e.row.seq]
                if e.n not in chosen_ns or e.n in done:
                    continue
                ln = pt.by_n[e.n]
                seen = pt.seen(e.seen, ln.who)
                tasks = pt.open_tasks(e.seen)
                event = Event("LINE", e.row)
                blocks = call.cached(clock.ctx, refs)
                tail = call.tail(event, seen, tasks, new)
                row = {"n": e.n, "id": ln.id, "at": ln.at, "fed": e.seen, "who": ln.who, "words": ln.text,
                       "covered": ln.covered, "truth": truth(pt, ln), "context_rows": len(clock.ctx.rows),
                       "blocks": len(blocks) - 1, "marks": sum(1 for _, m in blocks if m),
                       "new": len(new), "open_tasks": sum(1 for x in tasks.splitlines() if x.startswith("- ")),
                       "chars": sum(len(t) for t, _ in blocks) + len(tail)}
                if count % PROMPT_EVERY == 0 or args.dry:
                    (out / "prompts" / f"{e.n}.txt").write_text(
                        "".join(f"{'[MARK] ' if m else ''}{t}" for t, m in blocks) + tail, encoding="utf-8")
                if args.dry:
                    count += 1
                    continue
                t0 = time.perf_counter()
                try:
                    answer = await call.decide(event, clock.ctx, seen, tasks, new, refs)
                except vfg_llm.LlmError as err:
                    row.update(error=str(err)[:500], ms=int((time.perf_counter() - t0) * 1000))
                else:
                    d = answer.value
                    row.update(ms=answer.ms, cost=answer.cost, prompt_tokens=answer.prompt_tokens,
                               cached_tokens=answer.cached_tokens, completion_tokens=answer.completion_tokens,
                               parts=[part_dict(p) for p in d.parts], reply=d.reply)
                    spent += answer.cost
                count += 1
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                rate = (row.get("cached_tokens", 0) / row["prompt_tokens"]) if row.get("prompt_tokens") else 0
                routes = ",".join(p["to"] for p in row.get("parts", [])) or ("ERROR" if "error" in row else "-")
                print(f"#{e.n} {row['ms']} ms ${row.get('cost', 0):.4f} {row.get('prompt_tokens', 0)} in "
                      f"{rate:.0%} cached | {routes} | {ln.text[:60]!r}", flush=True)
                if spent > args.budget:
                    print(f"stopping: ${spent:.2f} spent, over --budget {args.budget}", flush=True)
                    break
        vfg_llm.client = None
    print(f"{count} calls, ${spent:.2f}; batches the timer fired before the last line: {len(clock.batches)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="the playtest's vfg.db")
    ap.add_argument("--out", required=True, help="where lines.jsonl and meta.json go")
    ap.add_argument("--limit", type=int, default=0, help="the first N lines only")
    ap.add_argument("--only", default="", help="these lines only, by their LINE numbers: 13,15,18")
    ap.add_argument("--dry", action="store_true", help="write each chosen line's prompt to prompts/, call nothing")
    ap.add_argument("--budget", type=float, default=4.0, help="stop once this many dollars are spent (4)")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
