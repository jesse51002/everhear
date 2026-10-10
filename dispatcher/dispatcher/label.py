"""Label the replay's tasks for the menu as it is now, with a stronger model than the selector's.

    uv run python -m dispatcher.label --from <old labels.jsonl> --to <new labels.jsonl>
                                      [--model anthropic/claude-opus-5.5] [--reasoning high]
                                      [--batch 8] [--jobs 2] [--max-cost 10] [--reserve 2] [--estimate]

Reads the task ids from the old labels file and the tasks from ~/.vfg/orch/inbox.jsonl
(VFG_ORCH_DIR), both READ-ONLY, and asks the model, through structured(), for each task's
label in the selector's own shape: its parts in order, each to an action or the orchestrator,
with the instruction the selector should write. The model sees more than the selector does:
every action's line and the values it takes, and the sentences of every task in order (what a
"Change to T14" refers to). Each part also carries `key`, a regex the selector's instruction or
orchestrator text for it must match, and `also`, other routes that would do that part well
enough. Like the replay, every task's moment has RUNNING NOW: none and SAVED PLACES: none.

It prints its cost estimate first, from the prompts' sizes and the model's price on OpenRouter,
and the account's credits left; it stops there when the estimate is over --max-cost, when the
credits left would not cover it and --reserve (or with --estimate). The model's answer
for each batch is kept beside --to as <to>.raw/<first id>.json, so a run that stopped picks up
where it was. The labels file it writes opens with a line saying what it is, then one line
per task, which `python -m dispatcher.replay --eval` reads:
`{"id", "task", "accept", "either", "parts", "label": {"parts": [...]}, "why", "by"}`.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Literal

import httpx
import vfg_actions
import vfg_llm
from pydantic import BaseModel, Field, model_validator

from . import actions, config
from .replay import WHOLE, norm, read_tasks
from .selector import INSTRUCTION_MAX, MAX_PICKS, MERGE, PLACEHOLDER

MODEL = "anthropic/claude-opus-5.5"
CHARS_PER_TOKEN = 3.6  # English prose and JSON, as Claude's tokenizer counts it, roughly
OUTPUT_PER_TASK = 300  # tokens of label JSON per task
REASONING_PER_TASK = (600, 2000)  # tokens of thinking per task at high effort: likely, worst

RULES = """\
You are writing the answer key for a test of a router, the selector, in EverHear: an AI that runs a \
Minecraft server (vanilla Java 26.3) for a few friends who talk to it. Another AI, the conversation, sends \
the selector one task at a time: a sentence and its context. The selector splits the task into its parts and \
sends each part either to an ACTION, a ready-made script that does one thing in seconds, or to the \
ORCHESTRATOR, a slow AI that takes minutes, writes new scripts, and can do anything.

The tasks are real, from a playtest on 2026-10-08, before these actions existed. Label each one as the \
selector SHOULD route it today, with the actions listed below on its menu. Be exact: the selector is scored \
against your labels.

THE RULES
1. An action gets a task, or a separate part of one, when it does it COMPLETELY, right now, with values \
players could give it. Judge by the action's line and the values it takes, listed below: when the ask needs \
something none of its values covers, the action does not do it completely. Small differences in degree are \
fine (an action's own sizes, counts and looks are what players get); a missing behaviour is not.
2. A build, a weapon, a staff, a gun, a rain, a boss, a rule that keeps going, and anything asked for before \
are actions now, whenever an action on the list does them. An action that runs until stopped is still the \
right answer for something that should keep going.
3. Anything no action does completely is the orchestrator's: something new; a fix of earlier work (a task \
opening "Fix" is always the orchestrator's, whole); a change that no value covers; and checking, \
investigating, verifying or reporting on things.
4. In this test nothing is running and there are no saved places at any task's moment (RUNNING NOW: none, \
SAVED PLACES: none). So `edit` and `stop`, which only act on a running copy of an action, have nothing to act \
on, and a change, undo, cancel or stop of earlier work ("Change to T…", "Cancel T…", "remove …", "stop …") \
is the orchestrator's: it is the one that knows what the earlier task made. The exception: when the task's \
own words are a complete new ask that an action does by itself, as in "Change to T14: send a much bigger \
asteroid onto Steve" (a new meteor, bigger), that part is the action's, with orchestrator in `also`.
5. `command` runs up to five vanilla commands, at once: giving items, armor, food and enchanted, named or \
custom items whose powers vanilla components give; giving or clearing effects; clearing inventories; time \
and weather; game rules, such as stopping the day cycle or keeping inventory; game modes; attributes, such as \
size, speed, health or gravity; sounds and titles; killing mobs; summoning a FEW mobs; teleporting a player \
to another player; looking up where someone is. NEVER a block placed, removed or replaced (setblock, fill \
and clone are refused): that is an action's that builds or replaces it, else the orchestrator's. A special \
item with a power that needs a script (a gun, a staff or wand that does something on right-click, an \
exploding bow) is never command's: it is the action's that gives it, else the orchestrator's.
6. `teleport` moves players to a place (a biome, a structure, coordinates, far away, their spawn or last \
death); to another player is command's. `summon_around` is many of ONE kind of mob round each player, one \
part per kind. `save_place` saves where someone stands.
7. Split the task into its parts: every thing it asks for is one part, even when one action could do \
several ("give Steve 64 rockets and speed" is two parts, both command). Only the TASK's asks are parts: the \
context tells you who asked, what they said and what was promised, and may make a vague task precise, but \
an aside in the context is not another part. Never add a part that only helps another (no save_place before \
a build), nor one that does nothing.
8. The parts run in the order given. More than four action parts (adjacent command parts count as one) is \
handed to the orchestrator whole.

WHAT TO WRITE, for each task
- `parts`, in the order they run. Each has:
  - `part`: the task's own words for it, short.
  - `to`: the action that does it completely, or "orchestrator".
  - `instruction`: for an action, one short sentence in plain words, with the player names, places and \
amounts exactly as the task says them, never a command or an id ("give KrownJM 64 firework rockets", \
"teleport KrownJM to a snowy biome"); for the orchestrator, the part as a short task. A task that is wholly \
the orchestrator's is ONE part to orchestrator, with the task's first words as its part.
  - `key`: a short case-insensitive regex for the thing this part asks for, which any correct instruction or \
orchestrator text for it must contain: the noun, never a player's name or a verb such as give. Use stems and \
alternatives so a paraphrase still matches: "rocket|firework", "endermen|enderm", "speed", "netherite". It \
must match your own instruction.
  - `also`: other routes for this part that would serve the player about as well, usually none. Use it when \
two actions both do it, when command could do it with vanilla items as well as an action, or for rule 4's \
exception.
- `why`: one line: which action does it and why, or what no action covers.
- `unsure`: true only when careful people would route the task differently and no answer is clearly better.
"""


def action_view(catalog) -> str:
    """Each action: its line, its script and how it runs, and the values the planner fills."""
    out = []
    for name in catalog.names():
        lines = vfg_actions.describe(name, catalog.get(name)).splitlines()
        takes = [ln for ln in lines[3:] if ln.startswith("* ")]
        out.append("\n".join([lines[0], "  " + lines[1]] + ["  " + t[2:] for t in takes]))
    return "\n".join(out)


def label_type(names: list[str], ids: list[str]) -> type[BaseModel]:
    To = Literal[tuple(names) + (WHOLE,)]  # type: ignore[valid-type]

    class Part(BaseModel):
        part: str
        to: To  # type: ignore[valid-type]
        instruction: str
        key: str
        also: list[To] = Field(default_factory=list)  # type: ignore[valid-type]

        @model_validator(mode="after")
        def _sound(self):
            said = self.instruction.strip()
            if self.to != WHOLE and not said:
                raise ValueError(f"the {self.to} part {self.part!r} has no instruction")
            if self.to != WHOLE and len(said) > INSTRUCTION_MAX:
                raise ValueError(f"the {self.to} instruction is over {INSTRUCTION_MAX} characters")
            if said and PLACEHOLDER.match(said):
                raise ValueError(f"the instruction {said!r} does nothing")
            try:
                rx = re.compile(self.key, re.IGNORECASE)
            except re.error as e:
                raise ValueError(f"the key {self.key!r} is not a regex: {e}") from None
            if not rx.search(f"{self.part} {self.instruction}"):
                raise ValueError(f"the key {self.key!r} matches neither {self.part!r} nor its instruction")
            return self

    class Label(BaseModel):
        id: str
        why: str
        unsure: bool
        parts: list[Part]

    class Labels(BaseModel):
        labels: list[Label]

        @model_validator(mode="after")
        def _every_task(self):
            got = [x.id for x in self.labels]
            if sorted(got) != sorted(ids):
                raise ValueError(f"label exactly these tasks, once each: {', '.join(ids)}; got {', '.join(got)}")
            if any(not x.parts for x in self.labels):
                raise ValueError("every task has at least one part")
            return self

    return Labels


# ------------------------------------------------------------------ routes

def picks_of(parts: list[dict]) -> list[str]:
    """The actions the selector's code would run for these parts: adjacent command parts merged,
    at most MERGE to a pick."""
    out, run = [], 0
    for p in parts:
        if p["to"] == "command":
            if run == 0 or run == MERGE:
                out.append("command")
                run = 0
            run += 1
        else:
            run = 0
            if p["to"] != WHOLE:
                out.append(p["to"])
    return out


def route(parts: list[dict]) -> str:
    picks = picks_of(parts)
    if not picks or len(picks) > MAX_PICKS:
        return WHOLE
    return norm(",".join(picks) + ("+" if any(p["to"] == WHOLE for p in parts) else ""))


def accepted(parts: list[dict]) -> list[str]:
    """Every route the label accepts: the parts as labelled, each part's `also` in its place, and
    the whole task to the orchestrator when any of those routes has an orchestrator part (the
    selector may hand a task on whole when it is unsure; nothing is lost)."""
    choices = [[p["to"]] + [a for a in p.get("also", []) if a != p["to"]] for p in parts]
    routes = set()
    for combo in itertools.islice(itertools.product(*choices), 512):
        routes.add(route([{**p, "to": to} for p, to in zip(parts, combo)]))
    if any(r == WHOLE or r.endswith("+" + WHOLE) for r in routes):
        routes.add(WHOLE)
    first = route(parts)
    return [first] + sorted(routes - {first})


def line_for(task: dict, label: dict, by: str) -> dict:
    parts = [{k: p[k] for k in ("part", "to", "instruction", "key", "also")} for p in label["parts"]]
    # `either` (not scored) is set by hand only: a label the model was unsure of carries its
    # alternatives in `also`, so it is scored against all of them.
    return {"id": task["id"], "task": task["task"], "accept": accepted(parts), "either": False,
            "parts": [p["key"] for p in parts] if len(parts) > 1 else [], "label": {"parts": parts},
            "why": label.get("why", ""), "unsure": bool(label.get("unsure")), "by": by}


# ------------------------------------------------------------------ the run

def prompt_for(head: str, index: str, batch: list[dict]) -> str:
    asks = "\n\n".join(f"{t['id']}\nTASK: {t['task']}\nCONTEXT: {t['context'] or '(none)'}" for t in batch)
    return (f"{head}\nEVERY TASK OF THE PLAYTEST, IN ORDER (for reference: what a \"Change to T…\" means)\n{index}\n\n"
            f"LABEL THESE {len(batch)} TASKS, each once: {', '.join(t['id'] for t in batch)}\n"
            f"RUNNING NOW: none\nSAVED PLACES: none\n\n{asks}\n")


async def price(model: str) -> tuple[float, float]:
    """Dollars per token, in and out, from OpenRouter's public model list."""
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.get("https://openrouter.ai/api/v1/models")
        r.raise_for_status()
    for m in r.json()["data"]:
        if m["id"] == model:
            return float(m["pricing"]["prompt"]), float(m["pricing"]["completion"])
    raise SystemExit(f"{model} is not on OpenRouter's model list")


async def balance() -> float | None:
    """The OpenRouter account's credits left, in dollars; None when it cannot be read."""
    try:
        key = vfg_llm._key()
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.get("https://openrouter.ai/api/v1/credits", headers={"Authorization": f"Bearer {key}"})
        d = r.json()["data"]
        return float(d["total_credits"]) - float(d["total_usage"])
    except Exception:  # noqa: BLE001 — only a guard; the key is never printed
        return None


async def _main(args) -> int:
    cfg = config.load()
    old = [json.loads(x) for x in Path(args.source).expanduser().read_text(encoding="utf-8").splitlines() if x.strip()]
    ids = [x["id"] for x in old if "id" in x]
    tasks = read_tasks(cfg.orch_dir / "inbox.jsonl", only=set(ids))
    if [t["id"] for t in tasks] != ids:
        print(f"the inbox has {len(tasks)} of the {len(ids)} labelled tasks", file=sys.stderr)
        return 1
    catalog = actions.load(cfg.repo, cfg.server)
    names = catalog.names()
    head = RULES + f"\nTHE ACTIONS ({len(names)}), each with its line, how it runs, and the values it takes\n" \
        + action_view(catalog) + "\n"
    index = "\n".join(f"{t['id']}: {' '.join(t['task'].split())}" for t in tasks)
    dest = Path(args.dest).expanduser()
    raw = dest.with_name(dest.name + ".raw")
    raw.mkdir(parents=True, exist_ok=True)
    have = {x["id"] for f in raw.glob("*.json") for x in json.loads(f.read_text(encoding="utf-8"))["labels"]}
    batches = [tasks[i:i + args.batch] for i in range(0, len(tasks), args.batch)]
    batches = [b for b in batches if not {t["id"] for t in b} <= have][:args.batches]  # what is left to label
    prompts = [prompt_for(head, index, b) for b in batches]
    left_tasks = sum(len(b) for b in batches)

    p_in, p_out = await price(args.model)
    tokens_in = sum(len(p) for p in prompts) / CHARS_PER_TOKEN
    out_lo = left_tasks * (OUTPUT_PER_TASK + REASONING_PER_TASK[0])
    out_hi = left_tasks * (OUTPUT_PER_TASK + REASONING_PER_TASK[1])
    est_lo, est_hi = tokens_in * p_in + out_lo * p_out, tokens_in * p_in + out_hi * p_out
    print(f"{len(tasks)} tasks, {len(have)} labelled already; {left_tasks} in {len(batches)} batch(es) of up to "
          f"{args.batch}, {len(names)} actions, model "
          f"{args.model} at {args.reasoning} reasoning (${p_in * 1e6:g}/M in, ${p_out * 1e6:g}/M out)\n"
          f"estimate: {tokens_in / 1e3:.0f}k tokens in (${tokens_in * p_in:.2f}), {out_lo / 1e3:.0f}k-"
          f"{out_hi / 1e3:.0f}k out with thinking (${out_lo * p_out:.2f}-${out_hi * p_out:.2f}): "
          f"${est_lo:.2f}-${est_hi:.2f} in all", flush=True)
    if est_hi > args.max_cost:
        print(f"over ${args.max_cost:g}: not run", flush=True)
        return 2
    left = await balance()
    print(f"OpenRouter credits left: {'unknown' if left is None else f'${left:.2f}'}", flush=True)
    if args.estimate:
        return 0
    if left is not None and left < est_hi + args.reserve:
        print(f"not run: the estimate's ${est_hi:.2f} and ${args.reserve:g} kept for everything else need more "
              f"than the ${left:.2f} left", flush=True)
        return 2

    gate = asyncio.Semaphore(max(1, args.jobs))
    spent = [0.0]

    async def one(batch: list[dict], prompt: str) -> None:
        bids = [t["id"] for t in batch]
        path = raw / f"{bids[0]}.json"
        async with gate:
            if spent[0] > args.max_cost:
                return
            answer = await vfg_llm.structured(prompt, label_type(names, bids), model=args.model,
                                              reasoning=args.reasoning, timeout=args.timeout)
        spent[0] += answer.cost
        path.write_text(json.dumps({"ids": bids, "cost": answer.cost, "ms": answer.ms,
                                    "labels": answer.value.model_dump()["labels"]}, ensure_ascii=False, indent=1))
        print(f"{bids[0]}-{bids[-1]}  {answer.ms / 1000:.0f} s  ${answer.cost:.4f}  (${spent[0]:.2f} so far)",
              flush=True)

    async with httpx.AsyncClient(timeout=args.timeout) as http:
        vfg_llm.client = http
        try:
            results = await asyncio.gather(*(one(b, p) for b, p in zip(batches, prompts)), return_exceptions=True)
        finally:
            vfg_llm.client = None
    for b, r in zip(batches, results):
        if isinstance(r, BaseException):
            print(f"{b[0]['id']}-{b[-1]['id']} failed: {r}", flush=True)

    done = {}
    cost = 0.0
    for f in sorted(raw.glob("*.json")):
        got = json.loads(f.read_text(encoding="utf-8"))
        cost += got["cost"]
        for x in got["labels"]:
            done[x["id"]] = x
    missing = [i for i in ids if i not in done]
    if missing:
        print(f"{len(missing)} task(s) not labelled yet ({', '.join(missing[:10])}…): run again", flush=True)
        return 1
    by = f"{args.model} ({args.reasoning})"
    header = {"relabelled": "for the actions menu", "date": date.today().isoformat(), "menu": len(names),
              "actions": names, "model": args.model, "reasoning": args.reasoning, "cost": round(cost, 4),
              "running": "none", "places": "none", "from": Path(args.source).name,
              "note": "labelled by the model above, then spot-checked by hand: a line with `hand` was changed"}
    lines = [header] + [line_for(t, done[t["id"]], by) for t in tasks]
    dest.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
    print(f"wrote {len(lines) - 1} labels to {dest}; the labelling cost ${cost:.4f}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m dispatcher.label", description=__doc__.split("\n\n")[0])
    p.add_argument("--from", dest="source", required=True, help="the old labels file: its task ids, in order")
    p.add_argument("--to", dest="dest", required=True, help="the new labels file")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--reasoning", default="high")
    p.add_argument("--batch", type=int, default=8, help="tasks per call (default 8)")
    p.add_argument("--jobs", type=int, default=2,
                   help="calls at a time (default 2: OpenRouter holds back each call's most it could cost)")
    p.add_argument("--reserve", type=float, default=2.0,
                   help="dollars of OpenRouter credit left for everything else (default 2)")
    p.add_argument("--timeout", type=float, default=900, help="seconds per call, its retries included")
    p.add_argument("--max-cost", type=float, default=10.0, help="dollars: stop when the estimate or the spend is over")
    p.add_argument("--estimate", action="store_true", help="print the estimate and stop")
    p.add_argument("--batches", type=int, help="only the first n batches: a pilot")
    return asyncio.run(_main(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
