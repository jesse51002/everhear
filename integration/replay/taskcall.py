#!/usr/bin/env python3
"""The orchestrator, measured before it is built: the follow-up replay of the named-tasks design.

The design (named tasks): only an implementer's work is a task, and a task has a name, such as
`frost_bow_alex`, which is also its script's, its action's and its copy's, and a description of
what the thing is now. The brain still routes; when it sends a part to `implementer`, code makes
one more structured() call on a small model, the orchestrator (the LLM version of the old
orchestrator: one call, no agent, no session), which answers `{task: "new" | "edit" | "stop" |
<name>, name, description, brief, reply, copy, values, stop}`: whether the work follows up a task
already made, is new, or only edits or stops copies under RUNNING NOW; the new task's name; what
the thing is now and how a player uses it; the implementer's brief; and, for the line's first
implementer part, the line's one chat reply, since the brain says nothing on such a line. It sees
the conversation as the brain does, and nothing of the actions, the commands or the world.

This replays it on the 2026-10-08 playtest, model calls only, nothing run:
- THE PARTS: every part the brain's full raw-line replay (lines.py's lines.jsonl, `--lines`) sent
  to `implementer`, in the order Jev fed the lines; a line's parts one after another. The brain's
  `task` field is ignored, since the orchestrator replaces it, and a T<n> its instruction names
  becomes "the earlier task", so the brain names no task for it.
- THE CALL sees its own short rules (taskcall_prompts/<version>.md), every task so far (its name,
  player, status, when last asked, description and last result), the whole conversation up to the
  line, built as playtest.py builds it and cached at checkpoints as the brain's is
  (`Context.cached`), the line, the brain's instruction, the line's other implementer parts, its
  action parts (with any drop) and its talk parts, so the reply covers them, and whether the reply
  is this call's. No action menu, no actions, no Minecraft commands, no positions or items.
  RUNNING NOW, and what each task made, are unknown in this replay, and the tail says so, so an
  edit or a stop is listed, never scored. A name that is a library action's, an empty reply where
  one is due, or an edit or a stop with no copy is sent back once by the answer's validator.
- THE CONVERSATION's tasks are its own, from the inbox, but under the replay's names: each T<n> is
  attributed to the name the orchestrator gave the line it came from (below), and its rows read
  `AI task: <name>: new: …`, `AI task: <name>: change: …` and `AI result: <name> done: …`. A T<n>
  no orchestrator call took (the brain ran actions or only talked for its line, or its line was
  not a direct ask) is the brain's own work, shown as `AI action: …`: it is not a task. Any T<n>
  in a row's text becomes the name, or "an earlier task". A row's text is fixed when it enters
  the context, so the cached prefix only grows.
- THE TASKS: a "new" answer makes a task under its name, made unique with `_2`, `_3`; a follow-up
  rewrites that task's description; an edit or a stop changes no task, and counts as naming the
  task whose name its copy carries. After a line's parts, each of the conversation's tasks for
  the line is attributed to one of the names its parts gave: a `Change to T<x>` or `Cancel T<x>`
  to the part that named the task holding T<x> if one did, else a follow-up part, else the first
  part; a fresh task to a part that said new, else the first part. A task's chain is its first T
  id and every later one attributed to it, so a follow-up to T<x> is right when it names the task
  whose chain holds T<x>, as the replay built the chains (a miss does not carry on into later
  lines). A task's status is open while a T<n> of its chain has no report yet, else its last
  report's; unknown when its chain has none.

    (cd brain && uv sync --frozen --inexact)
    PY=brain/.venv/bin/python
    $PY integration/replay/taskcall.py --db DIR/vfg/vfg.db --lines FULL/lines.jsonl --out OUT \\
        --model deepseek/deepseek-v4-flash --reasoning off --limit 15   # a pilot: the first 15 parts
    $PY integration/replay/taskcall.py --db … --lines … --out OUT --model …   # every part
    python3 integration/replay/taskcall_score.py OUT

A second run into the same OUT goes on after the last line recorded, the tasks rebuilt from the
recorded answers. In OUT: taskcall.jsonl (one row per line: its parts, each answer, the name it
got, ms, cost and tokens, and the truth: each conversation task of the line, its follow-up
targets and the names whose chains held them then), tasks.json (every task at the end: name,
player, description, its chain, every description it had), meta.json, and the whole prompt of
every 40th call in prompts/.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import playtest  # noqa: E402  (puts brain/ on the path)
from playtest import RESULT_MAX, BRIEF_MAX, Playtest, short  # noqa: E402

import httpx  # noqa: E402
import vfg_llm  # noqa: E402
from brain import actions, config  # noqa: E402
from brain.call import IMPLEMENTER, library  # noqa: E402
from brain.context import Context, Row  # noqa: E402
from pydantic import BaseModel, Field, model_validator  # noqa: E402

PROMPTS = HERE / "taskcall_prompts"
NAME = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
TID = re.compile(r"\bT(\d+)\b")
FOLLOW_UP = re.compile(r"^\W*(change\s+to|cancel)\s+(T\d+)", re.I)
NEW, STOP, EDIT = "new", "stop", "edit"
REPLY_MAX = 300  # as the brain's: aim under 80 characters, never over 200, refused past 300
PROMPT_EVERY = 40
ACTION_WHAT = 70  # characters of an action's words before its result, in an `AI action` result row


class EditValue(BaseModel):
    """One value of an edit, as text; code reads it by the copy's action's field type."""
    key: str = Field(description="the value's name, as RUNNING NOW shows it")
    value: str = Field(description="its new value, as text: 0, true, 30, minecraft:ice")


class AnswerBase(BaseModel):
    task: str = Field(description='"new" for work no task made; "stop" to only stop copies under RUNNING NOW; '
                                  'else the name, from TASKS SO FAR, of the task whose thing this changes, fixes, '
                                  'extends, undoes or removes')
    name: str = Field(description="a new task: its name, lowercase letters, digits and underscores, the thing "
                                  "then whose (frost_bow_alex); a follow-up: the task's own name; a stop: empty")
    description: str = Field(description="one or two sentences: what the thing is now, after this ask, and for "
                                         "whom; a follow-up rewrites the task's description with the change in "
                                         "it; a stop: empty")
    brief: str = Field(description="one to three sentences the implementer acts on alone; a follow-up's says "
                                   "what to change; a stop: empty")
    reply: str = Field("", description="the line's one chat line, when the prompt says the reply is yours; "
                                       "else empty")
    stop: list[str] = Field(default_factory=list, description="a stop: the name of each copy to stop, from "
                                                              "RUNNING NOW; else empty")
    copy: str = Field("", description="an edit: the name of the copy to change, from RUNNING NOW; else empty")
    values: list[EditValue] = Field(default_factory=list, description="an edit: each value to change; else "
                                                                      "empty")


def answer_type(names: set[str], banned: set[str], running: set[str] | None,
                replying: bool = False) -> type[AnswerBase]:
    """The answer, checked against the tasks and the running copies there are now (None: not known,
    as in this replay, so the copies an edit or a stop names are checked for their form alone), and
    for a reply when the line's reply is this call's. Its JSON schema never changes."""

    def is_copy(c: str) -> bool:
        return c in running if running is not None else bool(NAME.match(c))

    class TaskAnswer(AnswerBase):
        @model_validator(mode="after")
        def _checked(self):
            task, name = self.task.strip(), self.name.strip()
            copies = [c.strip() for c in self.stop if c.strip()]
            if replying and not self.reply.strip():
                raise ValueError("the reply is empty: this part's call writes the line's one chat line")
            if len(self.reply) > REPLY_MAX:
                raise ValueError(f"the reply is {len(self.reply)} characters: one chat line, under 200")
            if task == EDIT:
                if not is_copy(self.copy.strip()):
                    raise ValueError(f"{self.copy!r} is not a copy under RUNNING NOW")
                if not [v for v in self.values if v.key.strip()]:
                    raise ValueError("an edit changes no value: give each value to change, or answer a task")
                return self
            if self.copy.strip() or self.values:
                raise ValueError("`copy` and `values` are for an edit alone: leave them empty, or answer task "
                                 "\"edit\"")
            if task == STOP:
                if not copies:
                    raise ValueError("a stop names no copy: list each copy to stop, from RUNNING NOW")
                bad = [c for c in copies if not is_copy(c)]
                if bad:
                    raise ValueError(f"{', '.join(bad)}: not a copy under RUNNING NOW")
                return self
            if copies:
                raise ValueError("`stop` is for a stop alone: leave it empty, or answer task \"stop\"")
            if task != NEW and task not in names:
                raise ValueError(f"task {task!r} is not on TASKS SO FAR: answer \"new\", \"edit\", \"stop\", "
                                 "or one of its names")
            if task == NEW:
                if not NAME.match(name):
                    raise ValueError(f"name {name!r}: lowercase letters, digits and underscores, starting with a "
                                     "letter, 2 to 32 characters")
                if name in banned:
                    raise ValueError(f"{name} is a library action's name: add whose it is, as frost_bow_alex")
            if not self.description.strip():
                raise ValueError("the description is empty: say what the thing is now, for whom")
            if not self.brief.strip():
                raise ValueError("the brief is empty")
            return self

    TaskAnswer.__name__ = "TaskAnswer"
    return TaskAnswer


@dataclass
class Task:
    name: str
    player: str
    description: str
    line: str
    made: str                                         # when the line that made it was fed
    asked: str                                        # when it was last asked for (made or followed up)
    chain: list[str] = field(default_factory=list)    # the conversation's T ids it covers, in order
    history: list[dict] = field(default_factory=list)  # every answer that touched it


def heads(text: str) -> tuple[str, list[str], str]:
    """A conversation task's words as (kind, targets, rest): kind fresh, change or cancel; the T
    ids its head names before the first colon (`Change to T115 and T124: …`); what follows."""
    m = FOLLOW_UP.match(text)
    if not m:
        return "fresh", [], text
    head, _, rest = text.partition(":")
    kind = "cancel" if m.group(1).lower() == "cancel" else "change"
    return kind, [f"T{n}" for n in TID.findall(head)], rest.strip() or text


class Replay:
    """The tasks, the attribution of the conversation's T ids, and the context as it grows."""

    def __init__(self, pt: Playtest):
        self.pt = pt
        self.tasks: dict[str, Task] = {}
        self.owner: dict[str, str] = {}   # T id -> name
        self.entries = pt.entries()
        self.ctx = Context()
        self.i = 0

    # ---------------------------------------------------------------- the context

    def sub(self, text: str) -> str:
        """Every T<n> in a text as its task's name, or "an earlier task"."""
        return TID.sub(lambda m: self.owner.get(m.group(0), "an earlier task"), text)

    def render(self, row: Row) -> None:
        """A conversation task's row under the name the replay gave it, or as the brain's action."""
        if row.type != "AI":
            return
        if row.kind == "say":
            row.text = self.sub(row.text)
            return
        tid = row.task
        name = self.owner.get(tid)
        if row.kind == "task":
            kind, _, rest = heads(self.pt.task_by_id[tid]["task"])
            rest = self.sub(short(rest, BRIEF_MAX))
            if name:
                row.text, row.task = f"{name}: {'new' if kind == 'fresh' else kind}: {rest}", name
            else:
                row.text, row.kind, row.task = rest, "action", ""
        elif row.kind == "result":
            at, status, report = self.pt.closed[tid]
            report = self.sub(short(report, RESULT_MAX))
            if name:
                row.text, row.task = f"{name} {status}: {report}", name
            else:
                what = self.sub(short(heads(self.pt.task_by_id[tid]["task"])[2], ACTION_WHAT))
                row.text, row.kind, row.task = f"{what} — {status}: {report}", "action", ""

    def until(self, at: str) -> None:
        while self.i < len(self.entries) and self.entries[self.i].seen <= at:
            row = self.entries[self.i].row
            self.render(row)
            self.ctx.add(row)
            self.i += 1

    # ---------------------------------------------------------------- the tasks

    def status(self, t: Task, at: str) -> tuple[str, str]:
        """(status, last result line) at `at`, from the playtest's reports on its chain."""
        if not t.chain:
            return "unknown", ""
        reports = sorted((self.pt.closed[x] for x in t.chain if x in self.pt.closed and self.pt.closed[x][0] <= at))
        pending = [x for x in t.chain if not (x in self.pt.closed and self.pt.closed[x][0] <= at)]
        last = ""
        if reports:
            r_at, r_status, r_text = reports[-1]
            last = f"{r_at[11:19]} {r_status}: {self.sub(short(r_text, RESULT_MAX))}"
        if pending:
            return "open", last
        return reports[-1][1], last

    def index(self, at: str) -> str:
        if not self.tasks:
            return "TASKS SO FAR: none yet"
        rows = []
        for t in self.tasks.values():
            status, last = self.status(t, at)
            status = "status unknown (no report for it in this replay)" if status == "unknown" else status
            rows.append(f"- {t.name}, for {t.player or 'nobody'}, {status}, last asked {t.asked[11:19]}: "
                        f"{t.description}" + (f"\n  last result {last}" if last else ""))
        return ("TASKS SO FAR (oldest first: name, for whom, status, when last asked, what it is now, last "
                "result; what each made is not known in this replay):\n" + "\n".join(rows))

    def unique(self, name: str) -> str:
        if name not in self.tasks:
            return name
        k = 2
        while f"{name}_{k}" in self.tasks:
            k += 1
        return f"{name}_{k}"

    def apply(self, answer: dict, who: str, words: str, at: str) -> tuple[str, str]:
        """(kind, name): a new task made, a task's description rewritten, or an edit or a stop, which
        change no task; their name is the copy's when that is a task's (a copy carries its task's
        name)."""
        task = answer["task"].strip()
        if task == STOP:
            return STOP, next((c for c in answer.get("stop", []) if c in self.tasks), "")
        if task == EDIT:
            copy = answer.get("copy", "")
            return EDIT, copy if copy in self.tasks else ""
        if task == NEW:
            name = self.unique(answer["name"].strip())
            self.tasks[name] = Task(name, who, answer["description"].strip(), words, at, at)
            kind = NEW
        else:
            name, kind = task, "follow-up"
            t = self.tasks[name]
            t.description, t.asked = answer["description"].strip(), at
        self.tasks[name].history.append({"at": at, "kind": kind, "description": answer["description"].strip(),
                                         "brief": answer["brief"].strip(), "words": words})
        return kind, name

    def holders(self, targets: list[str]) -> list[str]:
        return [t.name for t in self.tasks.values() if any(x in t.chain for x in targets)]

    def truth(self, brain_row: dict, fed: str) -> list[dict]:
        """The conversation's tasks for the line, read before its parts are answered: each one's kind,
        its follow-up targets, whether every target was written after the line reached the brain
        (it could not be named), and the names whose chains hold a target now."""
        out = []
        for t in brain_row.get("truth", []):
            kind, targets, _ = heads(t["task"])
            later = bool(targets) and all(self.pt.task_by_id.get(x, {}).get("at", "") > fed for x in targets)
            out.append({"id": t["id"], "task": t["task"], "kind": kind, "targets": targets, "later": later,
                        "right": self.holders(targets) if targets else []})
        return out

    def attribute(self, truth: list[dict], given: list[dict]) -> None:
        """Each conversation task of the line to one of the names its parts gave (the docstring)."""
        if not given:
            return
        fresh_used: set[int] = set()
        for t in [x for x in truth if x["kind"] != "fresh"] + [x for x in truth if x["kind"] == "fresh"]:
            pick = None
            if t["kind"] != "fresh":
                pick = next((g for g in given if g["name"] in t["right"]), None)
                pick = pick or next((g for g in given if g["kind"] != NEW), None)
            else:
                pick = next((g for k, g in enumerate(given) if g["kind"] == NEW and k not in fresh_used), None)
                if pick is not None:
                    fresh_used.add(given.index(pick))
            pick = pick or given[0]
            t["owner"] = pick["name"]
            if t["id"] not in self.owner:
                self.owner[t["id"]] = pick["name"]
                self.tasks[pick["name"]].chain.append(t["id"])


def neutral(instruction: str) -> str:
    """The brain's instruction with the T<n> it names taken out, since the brain no longer names
    tasks: `from T22`, `in T146` go, `T92's` is "the earlier task's", any other is dropped."""
    text = re.sub(r"\s+(?:from|in|by|of)\s+(?:the\s+)?T\d+\b", "", instruction)
    text = re.sub(r"\bT\d+'s\b", "the earlier task's", text)
    text = re.sub(r"\bT\d+\b\s*", "", text)
    return re.sub(r"\s+([:;,.])", r"\1", re.sub(r"  +", " ", text)).strip()


def git_head() -> str:
    r = subprocess.run(["git", "-C", str(playtest.REPO), "rev-parse", "--short", "HEAD"], capture_output=True,
                       text=True)
    dirty = subprocess.run(["git", "-C", str(playtest.REPO), "status", "--porcelain", str(HERE)],
                           capture_output=True, text=True).stdout.strip()
    return r.stdout.strip() + (" (integration/replay changed)" if dirty else "")


def tail(rp: Replay, at: str, who: str, words: str, parts: list[dict], k: int, given: list[dict],
         others_of_line: list[dict]) -> str:
    p = parts[k]
    others = []
    for j, q in enumerate(parts):
        if j == k:
            continue
        went = (f" -> {given[j]['kind']}, {given[j]['name']}" if j < len(given) and given[j].get("name")
                else " (asked after this one)" if j > k else "")
        others.append(f"- \"{q['part']}\": {q['shown']}{went}")
    more = ("\n\nTHE LINE'S OTHER IMPLEMENTER PARTS:\n" + "\n".join(others)) if others else ""
    acts = [q for q in others_of_line if q["to"] != "talk"]
    talk = [q for q in others_of_line if q["to"] == "talk"]
    if acts:
        more += ("\n\nTHE LINE'S ACTION PARTS (the brain runs them; your reply is said once they are done):\n"
                 + "\n".join(f"- {q['to']}: {neutral(q['instruction'])}"
                              + (f" [dropped, it cannot be done: {q['drop']}]" if q.get("drop") else "")
                              for q in acts))
    if talk:
        more += ("\n\nTHE LINE'S TALK PARTS (only your reply answers them):\n"
                 + "\n".join(f"- \"{q['part']}\"" for q in talk))
    first = given[0].get("answer", {}).get("reply", "") if given else ""
    more += ("\n\nTHE REPLY: yours. This is the line's first implementer part: `reply` is the line's one chat line, "
             "covering every part of it." if k == 0 else
             f"\n\nTHE REPLY: not yours; the line's first implementer part wrote it (\"{first}\"): leave `reply` "
             "empty.")
    return (f"\n{rp.index(at)}\n\n"
            "RUNNING NOW: unknown in this replay (the copies running then are not known), so no copy can be named "
            "here.\n\n"
            f"THE LINE, {at[11:19]}: {who} said: {words}\n\n"
            f"THE BRAIN'S INSTRUCTION FOR THIS PART: {p['shown']}\n"
            f"(the part of the line it covers: \"{p['part']}\"){more}\n")


async def run(args) -> None:
    out = Path(args.out)
    (out / "prompts").mkdir(parents=True, exist_ok=True)
    prompt_file = Path(args.prompt) if args.prompt else PROMPTS / f"{args.version}.md"
    pt = Playtest(Path(args.db))
    cfg = config.load()
    catalog = actions.load(cfg.repo, cfg.server)
    banned = set(library(catalog))
    rules = prompt_file.read_text(encoding="utf-8").rstrip("\n").replace("{owner}", cfg.owner or "not named")
    opening = (f"{rules}\n\nTHE CONVERSATION SO FAR (oldest first; each row: its time in UTC, who, and for a "
               "player's line Jev's class):\n")
    ask = functools.partial(vfg_llm.structured, model=args.model, reasoning=config.Config.effort_of(args.reasoning),
                            providers=[x for x in args.providers.split(",") if x] or None, timeout=args.timeout)

    brain = {r["n"]: r for r in (json.loads(x) for x in Path(args.lines).read_text().splitlines() if x.strip())}
    rp = Replay(pt)
    asks = [e for e in rp.entries if e.row.type == "LINE" and e.n in brain
            and any(p["to"] == IMPLEMENTER for p in brain[e.n].get("parts", []))]
    rows_file = out / "taskcall.jsonl"
    done = {}
    if rows_file.exists():
        done = {r["n"]: r for r in (json.loads(x) for x in rows_file.read_text().splitlines() if x.strip())}
    total_parts = sum(sum(p["to"] == IMPLEMENTER for p in brain[e.n]["parts"]) for e in asks)
    meta = {"started": datetime.now(UTC).isoformat(timespec="seconds"), "db": str(args.db), "lines": str(args.lines),
            "head": git_head(), "model": args.model, "reasoning": args.reasoning, "providers": args.providers,
            "timeout_s": args.timeout, "checkpoint": args.checkpoint, "owner": cfg.owner,
            "prompt": str(prompt_file), "version": prompt_file.stem,
            "prompt_sha": hashlib.sha256(prompt_file.read_bytes()).hexdigest()[:12],
            "lines_with_parts": len(asks), "parts": total_parts, "already": len(done)}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"{len(asks)} lines, {total_parts} implementer parts ({len(done)} lines already recorded); {args.model} "
          f"{args.reasoning}; prompt {meta['version']} {meta['prompt_sha']}; head {meta['head']}", flush=True)

    spent, calls, count_parts = 0.0, 0, 0
    async with httpx.AsyncClient(timeout=args.timeout) as http:
        vfg_llm.client = http
        with rows_file.open("a", encoding="utf-8") as f:
            for e in asks:
                br = brain[e.n]
                rp.until(e.seen)
                truth = rp.truth(br, e.seen)
                parts = [{"part": p["part"], "instruction": p["instruction"],
                          "shown": neutral(p["instruction"]), "brain_task": p["task"]}
                         for p in br["parts"] if p["to"] == IMPLEMENTER]
                if args.limit and count_parts >= args.limit and e.n not in done:
                    break
                given: list[dict] = []
                old = done.get(e.n)
                for k, p in enumerate(parts):
                    if old is not None:
                        g = old["parts"][k]
                        if "answer" in g:
                            g["kind"], g["name"] = rp.apply(g["answer"], br["who"], br["words"], e.seen)
                        given.append(g)
                        continue
                    names = set(rp.tasks)
                    blocks = rp.ctx.cached(opening, args.checkpoint)
                    text = tail(rp, e.seen, br["who"], br["words"], parts, k, given,
                                [q for q in br["parts"] if q["to"] != IMPLEMENTER])
                    if calls % PROMPT_EVERY == 0:
                        (out / "prompts" / f"{e.n}-{k}.txt").write_text(
                            "".join(f"{'[MARK] ' if m else ''}{t}" for t, m in blocks) + text, encoding="utf-8")
                    g = dict(p)
                    t0 = time.perf_counter()
                    try:
                        a = await ask(text, answer_type(names, banned, None, replying=k == 0), cached=blocks)
                    except vfg_llm.LlmError as err:
                        g.update(error=str(err)[:500], ms=int((time.perf_counter() - t0) * 1000))
                    else:
                        v = a.value
                        g.update(answer={"task": v.task.strip(), "name": v.name.strip(),
                                         "description": v.description.strip(), "brief": v.brief.strip(),
                                         "reply": v.reply.strip() if k == 0 else "",
                                         "stop": [c.strip() for c in v.stop if c.strip()], "copy": v.copy.strip(),
                                         "values": [{"key": x.key, "value": x.value} for x in v.values]},
                                 ms=a.ms, cost=a.cost, prompt_tokens=a.prompt_tokens, cached_tokens=a.cached_tokens,
                                 completion_tokens=a.completion_tokens)
                        g["kind"], g["name"] = rp.apply(g["answer"], br["who"], br["words"], e.seen)
                        spent += a.cost
                    g["tasks_before"] = len(names)
                    calls += 1
                    given.append(g)
                    rate = g.get("cached_tokens", 0) / g["prompt_tokens"] if g.get("prompt_tokens") else 0
                    print(f"#{e.n}.{k} {g['ms']} ms ${g.get('cost', 0):.4f} {g.get('prompt_tokens', 0)} in "
                          f"{rate:.0%} cached | {g.get('kind', 'ERROR')} {g.get('name', '')} | "
                          f"{p['shown'][:70]!r}", flush=True)
                count_parts += len(parts)
                rp.attribute(truth, [g for g in given if g.get("name")])
                if old is None:
                    row = {"n": e.n, "fed": e.seen, "who": br["who"], "words": br["words"], "parts": given,
                           "truth": truth, "context_rows": len(rp.ctx.rows)}
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    f.flush()
                if spent > args.budget:
                    print(f"stopping: ${spent:.2f} spent, over --budget {args.budget}", flush=True)
                    break
        vfg_llm.client = None
    (out / "tasks.json").write_text(json.dumps(
        [{"name": t.name, "player": t.player, "description": t.description, "line": t.line, "made": t.made,
          "asked": t.asked, "chain": t.chain, "history": t.history} for t in rp.tasks.values()],
        ensure_ascii=False, indent=1))
    print(f"{calls} calls, ${spent:.4f}; {len(rp.tasks)} tasks")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="the playtest's vfg.db")
    ap.add_argument("--lines", required=True, help="the brain's full raw-line replay, lines.py's lines.jsonl")
    ap.add_argument("--out", required=True, help="where taskcall.jsonl, tasks.json and meta.json go")
    ap.add_argument("--model", required=True, help="the orchestrator's model, an OpenRouter id")
    ap.add_argument("--reasoning", default="low", help="off, or an effort (low)")
    ap.add_argument("--providers", default="", help="OpenRouter providers to try first, comma-separated")
    ap.add_argument("--version", default="v3", help="the prompt, taskcall_prompts/<version>.md (v3)")
    ap.add_argument("--prompt", default="", help="a prompt file instead of --version")
    ap.add_argument("--checkpoint", type=int, default=5, help="a cache mark every this many minute-blocks (5)")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds a call may take, retries included (60)")
    ap.add_argument("--limit", type=int, default=0, help="stop after the line that reaches this many parts")
    ap.add_argument("--budget", type=float, default=1.0, help="stop once this many dollars are spent (1)")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
