#!/usr/bin/env python3
"""The 2026-10-11 session replayed through the brain's loop: Prototype_Brain_Loop's first Test.

    brain/.venv/bin/python integration/loop_replay.py --dry                 # no model, no server
    REALMC_ROOT=… brain/.venv/bin/python integration/loop_replay.py         # the real run
    brain/.venv/bin/python integration/loop_replay.py --only 4-12 --budget 0.3   # a pilot
    python3 integration/loop_replay.py --report OUT                         # the report again
    python3 integration/loop_replay.py --list                               # the 110 events

**What it replays.** The 110 brain calls of that night (integration/replay/loop/, built by its
build.py from the box's vfg.db, that night's brain.log and the implementers' transcripts): the 69
lines the brain was called for (action_oriented ones, and conversational ones Respond handed up,
with Respond's reason) and the 41 implementer reports, in the order their calls started. Each is
one loop of the new brain, run in this process, one after another.

**What is real.** A realmc server of its own (`--name loop`, its world fresh from the template, its
stand-in's vfg.db, feed and incantations set aside, `made/loop/` cleared), with a Carpet fake
player KrownJM, the owner, holding a plausible kit with an axe (session.json's `setup`); `vfg`
against it; the brain's classes as brain/__main__.py wires them; structured() on OpenRouter, on
the brain's own settings (brain/config.py: Gemini 3.8 Flash, low). Stand-ins: the implementers
(they record each hand-off's brief and each follow-up, and start nothing) and the feed: each
loop's context is built from the night's own feed rows before its call (`context_to`), with the
rows no call had read yet (`new`) unread, so the prompt cache sees what it saw that night. Since
nothing builds, what the night's implementers did to the world is done here at its time
(world.jsonl): an action they saved is copied into made/loop/ (as it was at the end of the night),
and an incantation they stored is stored with the same `vfg action run`. A report goes to the task
the loop opened where the night opened it; when the loop opened none there, the replay opens it
under the night's name (session.json's `fallback_name`), so the reports still have their task.

**The night's task numbers.** That brain gave every acted line a T<n>; the feed's AI rows carry
them. In each context they are written as the loop writes today: the six implementer tasks by the
name the loop gave them (else their fallback), `AI task <name> | new: …` and `follow-up: …`, `AI
result <name> | <name> done: …`; an action's T<n> is `-`.

**The brain's usage rows stay in the replay's folder.** Every one of its model calls' rows queues
in OUT, its call_id `replay <the brain's>`: this process's structured() is pointed there alone
(VFG_USAGE_QUEUE is not in the environment, since any `vfg usage add` would claim the queue it names
and send it). `vfg usage flush` and `vfg context mark` (the night's seqs are not the server's) are
answered here and never run. The OpenRouter key is OPEN_ROUTER_API from the environment, else found
as integration/brain_live.py finds it; it is in this process's environment, as a live brain's is,
so the vfg it runs has it too (incantation_create's alias call), and never printed or written. The
rows of those calls go to the server's stand-in, not to OUT: they are not the brain's.

**What it measures**, per event: the steps (each one's cost, tokens, cached share and ms, from its
usage row), the time to the reply (the loop's start to its `vfg say`), how it ended (reply, silent,
hand-off, or error), what ran and with what result, and each hand-off's brief. OUT/results.jsonl
holds it; OUT/report.md, and integration/replay/loop/report.md after a whole real run, says:
- the session's loop cost beside the $1.18 measured (no bar);
- the time to the reply of the asks that do not hand off, p90 (bar: 30 s);
- the cached share of every step after the first (bar: at least 85%);
- how many loops end in one step and how many take two or more (no bar);
- every hand-off's brief, each part checked present, and the five misread asks' done-when lists
  beside what was missed.

`--dry` runs it all with no model and no server: the dataset, every event's context (the real
brain/context.py, the cache marks, the NEW rows), and a fake loop per event, which answers as the
night's call did (one step; every fifth action ask in two, so the two-step paths are exercised),
its briefs the night's own words under the brief's headings; its usage rows are estimates, its
times made up. Its report goes to OUT only.

Options: `--name` (realmc server, default loop), `--out` (default $VFG_REPLAY_DIR, else the temp
folder, `vfg-loop-replay-<time>`), `--only 4-12,T207` / `--from N` / `--limit N` (events by
number or ref; contexts are built in full whatever runs), `--pace F` (wait F times the night's
gap between calls, at most `--max-gap` s; default 0, back to back), `--budget` (dollars, default
3), `--keep-up`, `--reasoning` (the brain's for this run, VFG_BRAIN_REASONING: for a comparison).
Never `down --all`, `--purge-key`, `--regen`, `key` or `carpet` on realmc: other builders share it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA = HERE / "replay" / "loop"
REALMC = HERE / "realmc"
sys.path[:0] = [str(REPO / "brain"), str(REPO / "lib" / "py"), str(REALMC)]

from brain.context import Context, Row, parse  # noqa: E402  (pure: no model, no I/O)

KEY = "OPEN_ROUTER_API"
MEASURED = 1.18          # the night's brain, GPT-6.1 Sol: the spec's figure
BAR_REPLY_S = 30.0       # an ask that does not hand off is done within this, at p90
BAR_CACHED = 0.85        # a step after the first reads at least this share of its prompt from the cache
# Gemini 3.8 Flash on OpenRouter, a million tokens (the spec's price table): the dry run's estimates.
PRICE_IN, PRICE_CACHED, PRICE_OUT = 0.75, 0.075, 3.75
OPENING_TOKENS = 25_000  # the spec: rules, menu, every action in full and the command list
CHARS_PER_TOKEN = 4.0
LOOP_MARKS = 2           # conversation marks in a step's prompt (brain/call.py)
CHECKPOINT = 5           # a mark every this many minute-blocks (VFG_BRAIN_CHECKPOINT's default)
QUEUE = "usage.pending.jsonl"
# The brief's headings, as brain/call.py's HandOff.brief() writes them, and what each holds.
HEADINGS = {"THE PLAYER'S WORDS": "words", "THE WHOLE PICTURE": "picture",
            "WHAT THE PLAYER TAKES FOR GRANTED": "assumed", "HOW IT IS CAST, AND FOR WHOM": "cast",
            "DONE WHEN": "done_when"}
PARTS = ("words", "picture", "assumed", "cast", "done_when")


def say(*parts) -> None:
    """The replay's own lines: on stderr, so stdout can be the brain's log."""
    print(" ".join(str(p) for p in parts), file=sys.stderr, flush=True)


def ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def short(text, n: int = 160) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def p90(xs: list[float]) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(0.9 * len(xs) + 0.5)) - 1))]


# ====================================================================== the dataset

class Dataset:
    """integration/replay/loop/: the feed, the 110 events, the world changes and session.json."""

    def __init__(self, folder: Path = DATA):
        self.folder = folder

        def jsonl(name: str) -> list[dict]:
            return [json.loads(x) for x in (folder / name).read_text(encoding="utf-8").splitlines() if x.strip()]
        self.feed = jsonl("feed.jsonl")
        self.events = jsonl("events.jsonl")
        self.world = jsonl("world.jsonl")
        self.session = json.loads((folder / "session.json").read_text(encoding="utf-8"))

    def check(self) -> list[str]:
        """The counts against the spec's: 110 calls, 21 talk, 16 actions, 32 hand-off, 41 reports,
        $1.18. Each disagreement, in words."""
        spec, got = self.session["spec"], {}
        for e in self.events:
            got[e["class"]] = got.get(e["class"], 0) + 1
        got["calls"] = len(self.events)
        got["cost"] = round(sum(e["original"]["usage"]["cost"] for e in self.events), 2)
        return [f"{k}: {got.get(k, 0)}, the spec says {v}" for k, v in spec.items() if got.get(k, 0) != v]

    def select(self, only: str = "", start: int = 0, limit: int = 0) -> list[dict]:
        """The events to run: `only` as numbers, ranges (4-12) and refs (#71, T207: all its reports),
        then from `start`, at most `limit`."""
        chosen = self.events
        if only:
            want: set[int] = set()
            for word in (w.strip() for w in only.split(",") if w.strip()):
                if m := re.fullmatch(r"(\d+)-(\d+)", word):
                    want |= set(range(int(m.group(1)), int(m.group(2)) + 1))
                elif word.isdigit():
                    want.add(int(word))
                else:
                    ref = word if word.startswith(("#", "T")) else "#" + word
                    hits = {e["n"] for e in self.events if e["ref"] == ref}
                    if not hits:
                        sys.exit(f"no event {word}: --list shows them")
                    want |= hits
            chosen = [e for e in chosen if e["n"] in want]
        if start:
            chosen = [e for e in chosen if e["n"] >= start]
        return chosen[:limit or None]


# ====================================================================== the night's names

TASK_ID = re.compile(r"\bT\d{3}\b")
FEED_HEAD = re.compile(r"^(?P<head>AI#\d+ \d\d:\d\d:\d\d) (?P<kind>\S+) (?P<task>\S+) \| (?P<text>.*)$", re.S)


class Names:
    """The night's six implementer tasks (T207 …) by the names the loop gave them: the first task
    it opened at the line where the night opened that one; else the night's own name, which the
    replay opens for it (`fallback`). Every other T<n> was an action's, which has no task now."""

    def __init__(self, session: dict):
        self.tasks = session["tasks"]
        self.given: dict[str, str] = {}
        self.fallen: set[str] = set()
        self.loose: list[tuple[int, str]] = []   # (event n, name): tasks the loop opened that no T<n> took yet

    def of(self, tid: str) -> str:
        """The task's name for a T<n> of the six, as the context shows it: the loop's, else the fallback."""
        return self.given.get(tid) or self.tasks[tid]["fallback_name"]

    def rewrite(self, line: str) -> str:
        """A feed line as the loop's brain would have written it: see the module's docstring."""
        m = FEED_HEAD.match(line)
        if not m:
            return line
        kind, task, text = m.group("kind"), m.group("task"), m.group("text")
        if TASK_ID.fullmatch(task):
            name = self.of(task) if task in self.tasks else "-"
            if name != "-":
                if kind == "task":
                    text = re.sub(rf"^{task}: ", "new: ", text)
                    text = re.sub(rf"^{task} follow-up: ", "follow-up: ", text)
                text = re.sub(rf"^{task} (done|failed)\b", rf"{name} \1", text)
            task = name
        text = TASK_ID.sub(lambda x: self.of(x.group(0)) if x.group(0) in self.tasks else x.group(0), text)
        return f"{m.group('head')} {kind} {task} | {text}"


def context_for(ds: Dataset, ev: dict, names: Names) -> Context:
    """The context the event's loop reads: the night's feed up to `context_to`, its T<n>s written as
    today, every row read but the event's NEW ones and its own line."""
    unread = set(ev["new"]) | ({ev["seq"]} if ev["kind"] == "line" else set())
    rows = []
    for r in ds.feed:
        if r["seq"] > ev["context_to"]:
            break
        row = parse(names.rewrite(r["line"]), seq=r["seq"], at=r["at"], processed=r["seq"] not in unread)
        if row is not None:
            rows.append(row)
    return Context(rows)


def line_row(ctx: Context, ev: dict) -> Row:
    return next(r for r in ctx.rows if r.seq == ev["seq"])


def running_at(ds: Dataset, ev: dict, names: Names) -> list[str]:
    """The tasks whose implementer ran when the night's call started: a run started before it and
    not reported yet. What the fake implementers say runs."""
    t, out = ts(ev["at"]), []
    for tid, task in ds.session["tasks"].items():
        if any(ts(r["started"]) <= t < ts(r["reported"] or r["started"]) for r in task["runs"]):
            out.append(names.of(tid))
    return out


# ====================================================================== what a loop did

@dataclass
class Record:
    """Everything one event's loop did, as the stand-ins and the queue saw it."""
    t0: float = 0.0
    vfg: list[dict] = field(default_factory=list)        # every vfg call: args, t, code, out
    said: list[dict] = field(default_factory=list)       # vfg say: t (s after t0), text, task
    hand_offs: list[dict] = field(default_factory=list)  # implementers.start: name, brief, t
    follow_ups: list[dict] = field(default_factory=list)  # implementers.follow_up: name, text, brief, session, t
    world: list[dict] = field(default_factory=list)      # the night's world changes done before it
    usage: list[dict] = field(default_factory=list)      # its usage rows, from the queue
    log: list[str] = field(default_factory=list)         # the brain's log lines while it ran
    rejected: list[dict] = field(default_factory=list)   # answers the Step's checks refused: its errors, the answer
    error: str = ""
    t_end: float = 0.0

    def at(self) -> float:
        return round(time.monotonic() - self.t0, 3)


class Recording:
    """A vfg runner that keeps a record of every call, and answers `vfg usage flush` and `vfg
    context mark` itself: the replay's usage rows stay in its queue, and the night's seqs are not
    the server's feed."""

    def __init__(self, run):
        self.run = run
        self.rec = Record()

    async def __call__(self, *args: str, timeout: float = 30):
        from brain.vfg import VfgResult
        start = self.rec.at()
        if args[:2] in (("usage", "flush"), ("context", "mark")):
            self.rec.vfg.append({"args": list(args[:3]), "t": start, "code": 0, "out": "(not run: the replay's)"})
            return VfgResult(0, "", "")
        if args and args[0] == "say" and "--to" not in args:
            task = args[args.index("--task") + 1] if "--task" in args else ""
            self.rec.said.append({"t": start, "text": args[1] if len(args) > 1 else "", "task": task})
        r = await self.run(*args, timeout=timeout)
        self.rec.vfg.append({"args": [short(a, 300) for a in args], "t": start, "took": round(self.rec.at() - start, 3),
                             "code": r.code, "out": short(r.stdout or r.stderr, 400)})
        return r


class FakeImplementers:
    """The implementers, starting nothing: each hand-off's brief and each follow-up recorded. Which
    tasks run is the night's (running_at), so TASKS and a report's open-or-closed read as they did."""

    def __init__(self):
        self.rec = Record()
        self.now: list[str] = []

    async def start(self, name: str, brief: str) -> None:
        self.rec.hand_offs.append({"name": name, "brief": brief, "t": self.rec.at()})

    async def follow_up(self, name: str, text: str, session: str | None = None, brief: str = "") -> None:
        self.rec.follow_ups.append({"name": name, "text": text, "brief": brief, "session": session, "t": self.rec.at()})

    def running(self) -> list[str]:
        return list(self.now)

    def session(self, name: str) -> None:
        return None

    async def close(self, grace_s: float) -> list[str]:
        return []


def brief_parts(text: str) -> dict:
    """A rendered brief's parts by its headings (brain/call.py's HandOff.brief()); `done_when` a list."""
    out: dict = {k: "" for k in PARTS}
    out["done_when"] = []
    key = None
    for line in (text or "").splitlines():
        head = line.strip().rstrip(":").upper()
        if head in HEADINGS:
            key = HEADINGS[head]
            continue
        if key == "done_when":
            item = re.sub(r"^\s*[-*•]\s*", "", line).strip()
            if item:
                out["done_when"].append(item)
        elif key:
            out[key] = (out[key] + "\n" + line).strip()
    return out


def parts_present(parts: dict) -> dict[str, bool]:
    return {k: bool(parts.get(k)) for k in PARTS}


# ====================================================================== the rigs

class Queue:
    """The run's usage queue, OUT/usage.pending.jsonl: the rows each event added, by offset."""

    def __init__(self, folder: Path):
        self.path = folder / QUEUE
        self.path.touch()

    def mark(self) -> int:
        return self.path.stat().st_size

    def since(self, mark: int) -> list[dict]:
        with self.path.open("rb") as f:
            f.seek(mark)
            return [json.loads(x) for x in f.read().decode("utf-8").splitlines() if x.strip()]

    def write(self, row: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def total(self) -> float:
        return sum(float(r.get("cost_usd") or 0) for r in self.since(0))


class DryRig:
    """No model, no server: a fake loop per event that answers as the night's call did. The
    contexts are real (brain/context.py); the usage rows estimate a step's prompt from them at
    Gemini's prices; the times are made up. It exercises everything but the brain and realmc."""

    model = "dry (no model)"

    def __init__(self, ds: Dataset, out: Path, queue: Queue):
        self.ds, self.out, self.queue = ds, out, queue
        self.last_prompt = ""   # the previous loop's last step's prompt text: what the cache holds

    async def start(self) -> dict:
        return {"rig": "dry", "model": self.model}

    async def world(self, change: dict, rec: Record) -> None:
        rec.world.append({**change, "done": "dry: not done"})

    def _prompt(self, ctx: Context, ev: dict) -> tuple[str, str]:
        """The conversation as a step's prompt holds it, and the part of it up to its last mark."""
        try:
            blocks = ctx.cached("", CHECKPOINT, LOOP_MARKS)
        except TypeError:   # a context.py from before the loop
            blocks = ctx.cached("", CHECKPOINT)
        text = "".join(t for t, _ in blocks[1:])
        last = max((i for i, (_, marked) in enumerate(blocks) if marked and i > 0), default=0)
        return text, "".join(t for t, _ in blocks[1:last + 1])

    async def run(self, ev: dict, ctx: Context, names: Names, fake: FakeImplementers, rec: Record) -> None:
        conversation, marked = self._prompt(ctx, ev)
        tail = 2_000 + len(ev.get("words") or ev.get("report") or "") / CHARS_PER_TOKEN
        night = ev["original"]
        # Every fifth action ask takes two steps (its actions, then the reply on their results), so
        # the report's two-step paths run; every other loop is the one step the night's call was.
        steps = 2 if ev["class"] == "actions" and ev["n"] % 5 == 0 else 1
        t = 0.0
        for k in range(1, steps + 1):
            tokens = OPENING_TOKENS + len(conversation) / CHARS_PER_TOKEN + tail + (k - 1) * 300
            if k > 1:
                cached = tokens - 120                               # all but the trailer: the step before's prompt
            elif self.last_prompt and self.last_prompt.startswith(marked):
                cached = OPENING_TOKENS + len(marked) / CHARS_PER_TOKEN
            elif self.last_prompt:
                cached = OPENING_TOKENS                             # the opening, shared by every loop
            else:
                cached = 0
            out_tokens = night["usage"]["output_tokens"] + 150      # its answer, and some reasoning
            cost = ((tokens - cached) * PRICE_IN + cached * PRICE_CACHED + out_tokens * PRICE_OUT) / 1e6
            ms = 2500 + int(tokens / 20)
            t += ms / 1000
            ref = (f"#{ev['seq']}" if ev["kind"] == "line" else names.of(ev["task"])) + f" s{k}"
            self.queue.write({"at": now_iso(), "component": "brain", "model": self.model,
                              "input_tokens": int(tokens), "output_tokens": out_tokens, "cost_usd": round(cost, 9),
                              "ref": f"replay {ref}"[:64], "cache_read_tokens": int(cached),
                              "cache_write_tokens": None, "ok": 1, "error": None, "ms": ms})
            if k == 1:   # the step that runs the actions
                for a in night.get("actions") or []:
                    rec.vfg.append({"args": ["action", "run", a["action"], "--for", ev.get("player", "")], "t": t,
                                    "code": 0, "out": "dry: " + short(a["result"], 200)})
                    t += 1.3
        self.last_prompt = conversation
        reply = night.get("reply") or ""
        for h in night["hand_offs"]:
            if h["kind"] == "new":
                name = names.of(h["task"])
                fake.rec.hand_offs.append({"name": name, "brief": dry_brief(ev, h), "t": t})
            else:
                fake.rec.follow_ups.append({"name": names.of(h["task"]), "text": h["brief"], "brief": h["brief"],
                                            "session": None, "t": t})
        if reply:
            rec.said.append({"t": t, "text": reply, "task": ""})
        rec.t_end = t

    async def close(self) -> None:
        pass


def dry_brief(ev: dict, h: dict) -> str:
    """The dry run's brief: the night's own, under the brief's headings, its sentences the done-when
    list, so the report's checks run on the night's briefs."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", h["brief"]) if s.strip()]
    items = "\n".join(f"- {s}" for s in sentences)
    return (f"THE PLAYER'S WORDS\n{ev.get('words', '')}\n\nTHE WHOLE PICTURE\n{h['brief']}\n\n"
            f"WHAT THE PLAYER TAKES FOR GRANTED\n\n\nHOW IT IS CAST, AND FOR WHOM\n\n\nDONE WHEN\n{items}")


class RealmcRig:
    """A realmc server of its own, KrownJM on it, and the brain's classes in this process."""

    def __init__(self, ds: Dataset, out: Path, queue: Queue, name: str, keep_up: bool, reasoning: str = ""):
        self.ds, self.out, self.queue, self.name, self.keep_up = ds, out, queue, name, keep_up
        self.reasoning = reasoning   # VFG_BRAIN_REASONING for this run, else the brain's own setting
        self.env: dict[str, str] = {}
        self.players = None
        self.wired = None

    # -- the server
    def realmc_env(self) -> dict[str, str]:
        """This environment with `realmc/env <name>`'s exports (as integration/brain_live.py reads
        them), VFG_SERVER the server, no other VFG_ setting, and never the key."""
        r = subprocess.run([str(REALMC / "env"), self.name], capture_output=True, text=True, check=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("VFG_", KEY))}
        for line in r.stdout.splitlines():
            m = re.match(r"^export (\w+)=(.*)$", line)
            if not m:
                continue
            value = m.group(2)
            if m.group(1) == "PATH":
                value = value.split(":", 1)[0].strip("'") + os.pathsep + env.get("PATH", "")
            else:
                value = value.strip("'")
            env[m.group(1)] = value
        env.update(VFG_SERVER=self.name, VFG_OWNER=self.ds.session["player"], VFG_BRAIN_DIR=str(self.out / "brain"))
        if self.reasoning:
            env["VFG_BRAIN_REASONING"] = self.reasoning
        return env

    def clean_slate(self) -> None:
        """The stand-in's vfg.db (its -wal and -shm), feed.log and incantations.json (an earlier run's
        "juicy steak" would still be bound) set aside in vfg/set-aside-<time>/, never deleted, and
        vfg.db made again by `vfg_bridge --init-db`; made/<name>/ moved into made/.replaced/
        (integration/brain_live.py's clean slate)."""
        box = Path(self.env["REALMC_DIR"]) / "vfg"
        old = [n for n in ("vfg.db", "vfg.db-wal", "vfg.db-shm", "feed.log", "incantations.json") if (box / n).exists()]
        if old:
            aside = box / ("set-aside-" + time.strftime("%Y%m%d-%H%M%S"))
            while aside.exists():
                aside = aside.with_name(aside.name + "-2")
            aside.mkdir()
            for n in old:
                (box / n).rename(aside / n)
            say(f"  set aside {', '.join(old)} in {aside.relative_to(box.parent)}")
        r = subprocess.run([str(box / "python" / "bin" / "python3"), "-m", "vfg_bridge", "--init-db"],
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1",
                                "VFG_DB": str(box / "vfg.db"), "PYTHONPATH": str(REPO / "bridge")},
                           cwd=box, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"vfg_bridge --init-db failed: {short(r.stdout + r.stderr, 600)}")
        made = REPO / "made" / self.name
        if made.is_dir() and any(made.iterdir()):
            kept = REPO / "made" / ".replaced" / f"{self.name}-{time.strftime('%Y%m%d-%H%M%S')}"
            kept.parent.mkdir(parents=True, exist_ok=True)
            made.rename(kept)
            say(f"  made/{self.name}/ moved to {kept.relative_to(REPO)}")

    def put_player(self) -> None:
        """KrownJM on the arena in survival, holding the session's kit: put back if he left or died."""
        setup = self.ds.session["setup"]
        who = setup["player"]
        if who in self.players.online():
            return
        self.players.spawn(who)
        self.players.cmd(f"clear {who}")
        self.players.cmd(f"effect clear {who}")
        for slot, item in setup["items"].items():
            self.players.cmd(f"item replace entity {who} {slot} with {item}")

    async def start(self) -> dict:
        key, where = find_key()   # before this process's environment becomes the server's
        if not key:
            raise RuntimeError(f"no {KEY} in the environment or in a .env above {REPO}")
        os.environ.setdefault("REALMC_ROOT", str(REALMC / ".server"))
        say(f"realmc {self.name}: up --fresh")
        r = subprocess.run([str(REALMC / "up"), self.name, "--fresh"], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"realmc up {self.name} failed: {short(r.stdout + r.stderr, 800)}")
        self.env = self.realmc_env()
        self.clean_slate()
        import players
        self.players = players.on(self.name)
        self.put_player()
        os.environ.clear()
        os.environ.update(self.env)          # the brain's vfg (PATH first realmc's bin/vfg) reaches this server
        os.environ[KEY] = key                # as a live brain's environment has it; never printed
        self.wired = await wire(where, self.out)
        return {"rig": "realmc", "server": self.name, "model": self.wired.model, "reasoning": self.wired.reasoning,
                "owner": self.wired.owner, "refs": self.wired.refs_dir, "key": self.wired.key_from}

    async def world(self, change: dict, rec: Record) -> None:
        """One of the night's implementers' changes, done now: an action and its script copied into
        made/<name>/, or an incantation stored with the night's own `vfg action run`."""
        if change["kind"] == "made":
            name, made = change["action"], REPO / "made" / self.name
            for kind, src in (("actions", DATA / "made" / "actions" / f"{name}.py"),
                              ("scripts", DATA / "made" / "scripts" / name)):
                dest = made / kind / src.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, dest, dirs_exist_ok=True)
                elif src.exists():
                    shutil.copy2(src, dest)
            rec.world.append({**change, "done": f"copied into made/{self.name}/"})
            return
        r = await self.wired.vfg_raw(*change["argv"], timeout=60)
        rec.world.append({**change, "done": f"exit {r.code}: {short(r.stdout or r.stderr, 200)}"})

    async def run(self, ev: dict, ctx: Context, names: Names, fake: FakeImplementers, rec: Record) -> None:
        self.put_player()
        await self.wired.event(ev, ctx, names)

    async def close(self) -> None:
        if self.wired is not None:
            await self.wired.close()
        if not self.keep_up:
            d = subprocess.run([str(REALMC / "down"), self.name], capture_output=True, text=True)
            say(f"realmc down {self.name}: exit {d.returncode}")


def find_key() -> tuple[str | None, str | None]:
    """The OpenRouter key and where it came from, as integration/brain_e2e.py finds it; never printed."""
    spec = importlib.util.spec_from_file_location("brain_e2e", HERE / "brain_e2e.py")
    e2e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e2e)
    return e2e.find_key()


def load_refs():
    """The command references for 26.3, read as the brain reads a complete cache, never written:
    ~/.vfg/brain/refs/26.3, else a realmc stand-in's. (refs, folder) or (None, None)."""
    from brain import refs as refs_module
    root = Path(os.environ.get("REALMC_ROOT") or REALMC / ".server")
    for d in [Path.home() / ".vfg" / "brain" / "refs" / "26.3", *sorted(root.glob("*/home/.vfg/*/refs/26.3"))]:
        if (d / "complete.json").is_file():
            mark = json.loads((d / "complete.json").read_text())
            if mark.get("format") == refs_module.FORMAT:
                return refs_module._read_complete(d, lambda *a: None), str(d)
    return None, None


# ---------------------------------------------------------------------- the wiring (phase 2)

@dataclass
class Wired:
    """The brain's loop wired in this process, and how one event runs through it."""
    brain: object
    tasks: object
    vfg: Recording
    fake: FakeImplementers
    model: str
    reasoning: str
    owner: str
    refs_dir: str | None
    key_from: str | None
    vfg_raw: object
    later: object
    copies: object
    world: object

    async def event(self, ev: dict, ctx: Context, names: Names) -> None:
        """One event's loop: the context set, then the line or the report, as brain/loop.py takes
        them out of a turn (the replay's events come one at a time, and nothing comes back on a
        watch to settle on); the background vfg calls it left (its rows) waited for."""
        self.brain.ctx = ctx
        started = self.brain.clock()   # the turn's start, as Brain.turn() gives it: "reply at" covers it all
        if ev["kind"] == "line":
            await self.brain.line(line_row(ctx, ev), None, handed_up=ev["handed_up"], started=started)
        else:
            name = names.of(ev["task"])
            if self.tasks.get(name) is None:      # the loop opened none, and no line opened it here
                open_fallback(self.tasks, ev, name)
            await self.brain.report(name, ev["status"], ev["report"], ev["description"], ev["made"],
                                    following=ev["following"], started=started)
        await self.later.drain(30)

    async def close(self) -> None:
        await self.copies.stop()
        await self.world.close()
        await self.later.drain(10)


def open_fallback(tasks, ev: dict, name: str) -> None:
    """The night's task opened under its own name, with its player, line and first brief."""
    row = ev.get("task_row") or {}
    tasks.open(name, row.get("player") or "", row.get("line") or ev.get("words") or "", row.get("brief") or "",
               short(row.get("brief") or "", 160))


async def wire(where: str, out: Path) -> Wired:
    """The brain's classes as brain/__main__.py's serve() wires them, in this process: the real
    vfg (through Recording) to this server, structured() on the brain's settings with the call_id
    `replay <ref>`, the fake implementers, a Context the replay sets per event. Written against the
    brain as it stands; PHASE 2 redoes this one function against the loop's constructors."""
    import logging

    import vfg_llm
    from pydantic import ValidationError

    from brain import actions, config, running
    from brain.__main__ import _LogLines, asker
    from brain.act import Acts
    from brain.call import Call
    from brain.loop import Brain
    from brain.respond import Respond
    from brain.tasks import Tasks
    from brain.vfg import Later
    from brain.vfg import vfg as run_vfg
    from brain.world import World

    vfg_llm._queue_dir = lambda: out      # this process's rows only: see the module's docstring
    for logger_name, area in ((vfg_llm.__name__, "llm"), ("vfg_actions", "actions")):   # as brain/__main__ main()
        logger = logging.getLogger(logger_name)
        logger.addHandler(_LogLines(area))
        logger.propagate = False
    cfg = config.load()
    rec = Recording(run_vfg)
    fake = FakeImplementers()
    later = Later(rec)
    catalog = actions.load(cfg.repo, cfg.server)
    hidden = actions.hidden_commands()
    refs, refs_dir = load_refs()

    def replayed(ask):
        """structured() with the call_id `replay <ref>`, and every answer its output's checks refuse
        (structured() sends it back once, and logs only that it did) kept in the event's record:
        the call, each error's field and message, and the answer as it came."""
        async def asked(prompt, output, **kw):
            kw["call_id"] = f"replay {kw['call_id']}"[:64]
            validate = output.model_validate_json

            def checked(text, *a, **k):
                try:
                    return validate(text, *a, **k)
                except ValidationError as e:
                    rec.rec.rejected.append({
                        "call_id": kw["call_id"], "output": output.__name__,
                        "errors": [{"field": ".".join(map(str, x["loc"])) or "(the answer)", "message": x["msg"]}
                                   for x in e.errors(include_url=False)],
                        "answer": text[:4000]})
                    raise
            output.model_validate_json = checked
            try:
                return await ask(prompt, output, **kw)
            finally:
                with contextlib.suppress(AttributeError):
                    delattr(output, "model_validate_json")
        return asked

    async def send(row: dict) -> bool:
        r = await rec("task", "set", json.dumps(row, ensure_ascii=False), timeout=30)
        return r.code == 0

    copies = running.Refresher(rec, cfg.running_ttl_s)
    world = World(rec, cfg.running_file, catalog, refresher=copies)
    tasks = Tasks(rows=[], send=send)
    call = Call(catalog, replayed(asker(cfg.model, cfg.reasoning, cfg.timeout_s, cfg.providers)), owner=cfg.owner,
                hidden=hidden, checkpoint=cfg.checkpoint)
    respond = Respond(catalog, replayed(asker(cfg.respond_model, cfg.respond_reasoning, cfg.timeout_s,
                                              cfg.respond_providers)), owner=cfg.owner, checkpoint=cfg.checkpoint)
    acts = Acts(vfg=rec, later=later, catalog=catalog, tasks=tasks, implementers=fake, world=world,
                repo=cfg.repo, server=cfg.server, action_timeout_s=cfg.action_timeout_s)
    brain = Brain(ctx=Context(), call=call, respond=respond, acts=acts, world=world, tasks=tasks, implementers=fake,
                  later=later, catalog=catalog, refs=refs, start=0, load=None, menu=None, steps=cfg.loop_steps,
                  seconds=cfg.loop_seconds, waiting=cfg.turn_waiting, settle_s=cfg.turn_settle_s)
    copies.start()
    return Wired(brain=brain, tasks=tasks, vfg=rec, fake=fake, model=cfg.model, reasoning=cfg.reasoning,
                 owner=cfg.owner, refs_dir=refs_dir, key_from=where, vfg_raw=run_vfg, later=later, copies=copies,
                 world=world)


# ====================================================================== the run

async def replay(args, ds: Dataset, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    queue = Queue(out)
    chosen = ds.select(args.only, args.start, args.limit)
    whole = len(chosen) == len(ds.events)
    rig = DryRig(ds, out, queue) if args.dry else RealmcRig(ds, out, queue, args.name, args.keep_up, args.reasoning)
    names = Names(ds.session)
    results = out / "results.jsonl"
    results.write_text("")
    meta = {"started": now_iso(), "dry": bool(args.dry), "head": git_head(), "events": [e["n"] for e in chosen],
            "whole": whole, "pace": args.pace, "max_gap": args.max_gap, "budget": args.budget, "note": args.note,
            "dataset": str(ds.folder)}
    say(f"loop replay: {len(chosen)} of {len(ds.events)} events into {out}; "
        f"{'dry, no model and no server' if args.dry else 'realmc ' + args.name}")
    log_file = (out / "brain.log").open("a", encoding="utf-8")
    world = list(ds.world)
    stopped = ""
    try:
        with contextlib.redirect_stdout(log_file):
            meta.update(await rig.start())
            (out / "meta.json").write_text(json.dumps(meta, indent=1))
            chosen_n = {e["n"] for e in chosen}
            last_at, last_took = None, 0.0
            for ev in ds.events:
                if ev["n"] not in chosen_n:
                    continue
                if args.pace and last_at is not None:
                    gap = min(max(0.0, ts(ev["at"]) - last_at) * args.pace, args.max_gap)
                    await asyncio.sleep(max(0.0, gap - last_took))
                fake = rig.wired.fake if not args.dry else FakeImplementers()
                rec = Record(t0=time.monotonic())
                if not args.dry:
                    rig.wired.vfg.rec = rec
                fake.rec = rec
                while world and world[0]["at"] <= ev["at"]:
                    await rig.world(world.pop(0), rec)
                fake.now = running_at(ds, ev, names)
                ctx = context_for(ds, ev, names)
                mark, log_mark = queue.mark(), log_file.tell()
                rec.t0 = time.monotonic()
                try:
                    await asyncio.wait_for(rig.run(ev, ctx, names, fake, rec), args.event_timeout)
                except Exception as e:  # noqa: BLE001 — one event's failure is its result; the run goes on
                    rec.error = f"{type(e).__name__}: {short(e, 400)}"
                if not rec.t_end:
                    rec.t_end = rec.at()
                log_file.flush()
                rec.usage = queue.since(mark)
                rec.log = read_from(out / "brain.log", log_mark)
                take_names(ds, ev, names, rec, rig if not args.dry else None)
                row = result_row(ds, ev, rec, names)
                with results.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                say(f"{ev['n']:>3} {ev['ref']:<6} {ev['class']:<8} -> {row['ended']:<8} {len(row['steps'])} step(s) "
                    f"${row['cost']:.4f} reply {fmt_s(row['t_reply'])}  {short(row['reply'], 70)}")
                last_at, last_took = ts(ev["at"]), rec.t_end
                if queue.total() > args.budget:
                    stopped = f"the budget, ${args.budget:g}, was spent after event {ev['n']}"
                    say("stopped: " + stopped)
                    break
    finally:
        try:
            with contextlib.redirect_stdout(log_file):
                await rig.close()
        finally:
            log_file.close()
    meta.update(ended=now_iso(), stopped=stopped, names=names.given, fallen=sorted(names.fallen))
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    return out


EARLIER = 3   # events back a task the loop opened early may be the one the night opened later


def take_names(ds: Dataset, ev: dict, names: Names, rec: Record, rig) -> None:
    """At a line where the night opened a task: the loop's first new task there is its name; else
    one the loop opened at most EARLIER events before that no other T<n> took (the night asked a
    question first, as at #192, and opened it on the answer, #194, while the loop opened it at
    once); with none, the night's own name, opened by the replay (for the reports to come)."""
    if ev["kind"] != "line":
        return
    names.loose += [(ev["n"], h["name"]) for h in rec.hand_offs]
    for h in ev["original"]["hand_offs"]:
        if h["kind"] != "new" or h["task"] in names.given:
            continue
        here = [x for x in names.loose if x[0] == ev["n"]]
        took = here[0] if here else next(((n, name) for n, name in reversed(names.loose) if ev["n"] - n <= EARLIER),
                                          None)
        if took is not None:
            names.given[h["task"]] = took[1]
            names.loose.remove(took)
        else:
            names.fallen.add(h["task"])
            if rig is not None and rig.wired.tasks.get(names.of(h["task"])) is None:
                open_fallback(rig.wired.tasks, {"task_row": {"player": ev["player"], "line": ev["words"],
                                                             "brief": h["brief"]}}, names.of(h["task"]))


def read_from(path: Path, offset: int) -> list[str]:
    with path.open("rb") as f:
        f.seek(offset)
        return f.read().decode("utf-8", "replace").splitlines()


def git_head() -> str:
    r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "brain"], capture_output=True,
                           text=True).stdout.strip()
    return r.stdout.strip() + (" (brain/ changed)" if dirty else "")


def fmt_s(x) -> str:
    return "-" if x is None else f"{x:.1f} s"


# A step's fields that would do nothing, dropped and logged (brain/call.py).
DROPPED = re.compile(r" step end \S+: dropped, it would do nothing: ")
# The brain's line at the end of a loop (brain/loop.py log_loop()).
LOOP_LINE = re.compile(r" loop \S+(?: s\d+)? [^:]*: (?P<steps>\d+) step\(s\), \$[\d.]+\+?, [^,]+ cached, "
                       r"(?:reply at (?P<reply>[\d.]+) s|no reply), [\d.]+ s, ended (?P<how>cut off|\w+)")


def result_row(ds: Dataset, ev: dict, rec: Record, names: Names) -> dict:
    """One event's line in results.jsonl."""
    steps = []
    for k, u in enumerate(rec.usage, 1):
        prompt, cached = int(u.get("input_tokens") or 0), int(u.get("cache_read_tokens") or 0)
        steps.append({"n": k, "ref": u.get("ref"), "cost": u.get("cost_usd"), "prompt": prompt, "cached": cached,
                      "cached_share": round(cached / prompt, 4) if prompt else None,
                      "out": u.get("output_tokens"), "ms": u.get("ms"), "ok": u.get("ok", 1), "error": u.get("error")})
    hand_offs = []
    for h in rec.hand_offs:
        parts = brief_parts(h["brief"])
        hand_offs.append({"name": h["name"], "brief": h["brief"], "parts": parts, "present": parts_present(parts)})
    # How it ended: the brain's own loop line says (`… ended reply`); else what the stand-ins saw.
    loop_line = next((m for x in reversed(rec.log) if (m := LOOP_LINE.search(x))), None)
    ended = (loop_line.group("how") if loop_line
             else "error" if rec.error and not rec.said and not rec.hand_offs
             else "handoff" if rec.hand_offs or rec.follow_ups else "reply" if rec.said else "silent")
    if rec.error and ended not in ("failed", "error"):
        ended = "error"
    first_say = rec.said[0]["t"] if rec.said else None
    night = ev["original"]
    return {
        "n": ev["n"], "ref": ev["ref"], "kind": ev["kind"], "class": ev["class"], "seq": ev.get("seq"),
        "task": ev.get("task"), "task_name": names.of(ev["task"]) if ev.get("task") else None, "run": ev.get("run"),
        "words": ev.get("words") or "", "steps": steps, "cost": round(sum(float(s["cost"] or 0) for s in steps), 6),
        "t_reply": first_say, "t_end": rec.t_end, "ended": ended,
        "brain_reply_s": float(loop_line.group("reply")) if loop_line and loop_line.group("reply") else None,
        "reply": rec.said[0]["text"] if rec.said else "", "said": rec.said,
        "ran": [v for v in rec.vfg if v["args"][:2] == ["action", "run"]],
        "vfg": rec.vfg, "hand_offs": hand_offs, "follow_ups": rec.follow_ups, "world": rec.world,
        "error": rec.error, "rejected": rec.rejected,
        "dropped": [x.split(" step ", 1)[-1] for x in rec.log if DROPPED.search(x)], "log": rec.log[-60:],
        "night": {"cost": night["usage"]["cost"], "ms": night["usage"]["ms"], "reply": night["reply"],
                  "cached_share": round(night["usage"]["cached_tokens"] / night["usage"]["input_tokens"], 4)
                  if night["usage"]["input_tokens"] else None,
                  "hand_offs": [{"task": h["task"], "name": names.of(h["task"]), "kind": h["kind"], "run": h["run"],
                                 "brief": h["brief"]} for h in night["hand_offs"]],
                  "actions": [a["action"] for a in night.get("actions") or []]},
    }


# ====================================================================== the report

def load_results(out: Path) -> tuple[list[dict], dict]:
    rows = [json.loads(x) for x in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8")) if (out / "meta.json").exists() else {}
    return rows, meta


def misread_named(m: dict, items: list[str]) -> bool:
    return bool(re.search(m["named"], " ".join(items), re.I))


def report(out: Path, ds: Dataset) -> str:
    """OUT/report.md: the spec's four measures, every hand-off's brief, the five misread asks, and
    every event beside the night's call."""
    rows, meta = load_results(out)
    dry = meta.get("dry")
    lines_ = [r for r in rows if r["kind"] == "line"]
    cost = sum(r["cost"] for r in rows)
    night_cost = sum(r["night"]["cost"] for r in rows)
    asks = [r for r in lines_ if r["ended"] != "handoff"]
    times = [r["t_reply"] if r["t_reply"] is not None else r["t_end"] for r in asks]
    later = [s for r in rows for s in r["steps"][1:] if s["prompt"]]
    later_share = (sum(s["cached"] for s in later) / sum(s["prompt"] for s in later)) if later else None
    firsts = [r["steps"][0] for r in rows if r["steps"] and r["steps"][0]["prompt"]]
    first_share = (sum(s["cached"] for s in firsts) / sum(s["prompt"] for s in firsts)) if firsts else None
    one = sum(1 for r in rows if len(r["steps"]) == 1)
    more = sum(1 for r in rows if len(r["steps"]) >= 2)
    none = sum(1 for r in rows if not r["steps"])
    p = p90(times)
    out_md = []
    w = out_md.append
    title = "Brain loop replay of 2026-10-11" + (" — DRY RUN" if dry else "")
    w(f"# {title}\n")
    if dry:
        w("**A dry run: no model and no server.** Each loop is a stand-in that answers as the night's call did; "
          "the costs and cached shares are estimates from the real contexts at Gemini 3.8 Flash's prices, and the "
          "times are made up. It checks the dataset, the contexts and this report, not the brain.\n")
    w(f"_Run {meta.get('started', '?')} → {meta.get('ended', '?')}; {meta.get('rig', '?')}"
      f"{' ' + meta['server'] if meta.get('server') else ''}; model {meta.get('model', '?')} "
      f"{meta.get('reasoning', '')}; head {meta.get('head', '?')}; {len(rows)} of {len(ds.events)} events"
      f"{'' if meta.get('whole') else ' (a part of the session)'}; pace {meta.get('pace', 0)}"
      f"{'; stopped: ' + meta['stopped'] if meta.get('stopped') else ''}._\n")
    if meta.get("note"):
        w(f"_{meta['note']}_\n")
    w("## The measures\n")
    w("| Measure | Bar | Got | |")
    w("|---|---|---|---|")
    scale = f" (the night's calls for these events: ${night_cost:.2f})" if not meta.get("whole") else ""
    w(f"| The loop's brain cost for the session | none: set beside the $1.18 measured | **${cost:.2f}**{scale} | "
      f"{'' if not meta.get('whole') else f'{cost / MEASURED:.0%} of the night'} |")
    w(f"| Time to the reply, asks that do not hand off, p90 | {BAR_REPLY_S:g} s | **{fmt_s(p)}** over {len(asks)} "
      f"(median {fmt_s(statistics.median(times) if times else None)}) | "
      f"{verdict(p is not None and p <= BAR_REPLY_S, p is None)} |")
    w(f"| Cached share of every step after the first | at least {BAR_CACHED:.0%} | "
      f"**{pct(later_share)}** over {len(later)} steps | "
      f"{verdict(later_share is not None and later_share >= BAR_CACHED, later_share is None)} |")
    w(f"| Loops that end in one step, and in two or more | none | **{one}** in one, **{more}** in two or more"
      f"{f', {none} with no step' if none else ''} | |")
    w("")
    w(f"A first step's cached share (across asks, the cache between loops): {pct(first_share)} over {len(firsts)}. "
      f"The night's calls: {pct(night_share(rows))}.\n")
    w("### By what the night's call did\n")
    w("| The night | Events | Loop cost | Night cost | One step | Two or more "
      "| Ended reply / silent / hand-off / failed |")
    w("|---|---|---|---|---|---|---|")
    for cls in ("talk", "actions", "hand-off", "report"):
        rs = [r for r in rows if r["class"] == cls]
        if not rs:
            continue
        ends = [sum(1 for r in rs if r["ended"] == e) for e in ("reply", "silent", "handoff")]
        ends.append(sum(1 for r in rs if r["ended"] not in ("reply", "silent", "handoff")))
        w(f"| {cls} | {len(rs)} | ${sum(r['cost'] for r in rs):.3f} | ${sum(r['night']['cost'] for r in rs):.3f} | "
          f"{sum(1 for r in rs if len(r['steps']) == 1)} | {sum(1 for r in rs if len(r['steps']) >= 2)} | "
          f"{' / '.join(map(str, ends))} |")
    w("")
    errs = [r for r in rows if r["error"]]
    if errs:
        w(f"**{len(errs)} events raised**: "
          + "; ".join(f"{r['n']} {r['ref']}: {short(r['error'], 120)}" for r in errs) + "\n")

    back = [(r, x) for r in rows for x in r.get("rejected") or []]
    if back:
        w(f"**{len(back)} answers failed the Step's checks** and were sent back once (structured()); "
          f"{sum(1 for r in rows if r.get('rejected'))} events had one:\n")
        for r, x in back:
            said = "; ".join(f"`{e['field']}`: {short(e['message'], 200)}" for e in x["errors"])
            w(f"- {r['n']} {x['call_id'].removeprefix('replay ')}: {said.replace('|', '/')}")
        w("")

    drops = [(r, x) for r in rows for x in r.get("dropped") or []]
    if drops:
        w(f"**{len(drops)} steps had fields dropped** as doing nothing (brain/call.py, logged, not sent back): "
          + "; ".join(f"{r['n']} {r['ref']}: {short(x, 120)}" for r, x in drops).replace("|", "/") + "\n")

    # -- the briefs
    w("## The hand-off briefs\n")
    news = [(r, h) for r in rows for h in r["hand_offs"]]
    full = sum(1 for _, h in news if all(h["present"].values()))
    w(f"{len(news)} new tasks handed off, {full} with every part of the brief "
      f"(the player's words, the whole picture, what the player takes for granted, how it is cast and for whom, "
      f"a done-when list); {sum(len(r['follow_ups']) for r in rows)} follow-ups. The night opened 6 tasks and sent "
      f"35 follow-ups.\n")
    for r, h in news:
        missing = [k for k, ok in h["present"].items() if not ok]
        w(f"### {r['n']}. {r['ref']} → `{h['name']}`" + (f" — missing: {', '.join(missing)}" if missing else ""))
        w(f"> {short(r['words'], 300)}\n")
        theirs = [x for x in r["night"]["hand_offs"] if x["kind"] == "new"]
        w(f"- **the night's brief** ({theirs[0]['task']}): {short(theirs[0].get('brief'), 400)}" if theirs
          else "- **the night** opened no task here")
        for k in ("picture", "assumed", "cast"):
            if h["parts"].get(k):
                w(f"- **{k}**: {short(h['parts'][k], 400)}")
        w("- **done when**:")
        for item in h["parts"]["done_when"] or ["(none)"]:
            w(f"  - {short(item, 200)}")
        w("")
    agreement = follow_up_agreement(rows)
    if agreement:
        w("### Follow-up or new, against the night\n")
        w(agreement + "\n")

    # -- the misreads
    w("## The five misread asks\n")
    w("Each ask's brief, from the loop that opened its task, beside what the night's implementer missed. "
      "_Named_ is a word match on the done-when list (a heuristic); the lists are here to judge.\n")
    w("| Task | What was missed | Its brief's done-when list | Named |")
    w("|---|---|---|---|")
    named_n = 0
    for m in ds.session["misreads"]:
        brief = None
        for r in rows:
            if r["kind"] == "line" and r["seq"] in m["asks"] and r["hand_offs"]:
                brief = r["hand_offs"][0]
                break
        items = brief["parts"]["done_when"] if brief else []
        ok = bool(brief) and misread_named(m, items)
        named_n += ok
        listed = "<br>".join("• " + short(x, 140).replace("|", "/") for x in items) if brief else \
            f"no hand-off at #{', #'.join(map(str, m['asks']))}"
        w(f"| {m['task']} {m['run']} (#{', #'.join(map(str, m['asks']))}) | {m['missed']} | {listed} | "
          f"{'yes' if ok else 'no'} |")
    w(f"\n{named_n} of {len(ds.session['misreads'])} named. Passes when: TODO, Jesse's to set.\n")

    # -- every event
    w("## Every event\n")
    w("| # | Event | The night | Loop | Steps | Cost (night) | Cached, step 1 | Reply | What ran / handed off |")
    w("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        did = [v["args"][2] + ("" if v["code"] == 0 else f" (exit {v['code']})")
               for v in r["ran"] if len(v["args"]) > 2]
        did += [f"→ {h['name']}" for h in r["hand_offs"]] + [f"↻ {f['name']}" for f in r["follow_ups"]]
        what = r["words"] if r["kind"] == "line" else f"{r['task_name']} {r['run']} report"
        night = r["class"] + "".join(f" {x['task']} {x['run']}" for x in r["night"]["hand_offs"])
        w(f"| {r['n']} | {r['ref']} {short(what, 60).replace('|', '/')} | {night} | {r['ended']} | "
          f"{len(r['steps'])} | ${r['cost']:.4f} (${r['night']['cost']:.4f}) | "
          f"{pct(r['steps'][0]['cached_share']) if r['steps'] else '-'} | {fmt_s(r['t_reply'])} | "
          f"{'; '.join(did).replace('|', '/') or '-'} |")
    return "\n".join(out_md) + "\n"


def verdict(ok: bool, unknown: bool = False) -> str:
    return "not measured" if unknown else "PASS" if ok else "FAIL"


def pct(x) -> str:
    return "-" if x is None else f"{x:.1%}"


def night_share(rows: list[dict]) -> float | None:
    xs = [r["night"]["cached_share"] for r in rows if r["night"]["cached_share"] is not None]
    return statistics.mean(xs) if xs else None


def follow_up_agreement(rows: list[dict]) -> str:
    """Where the night followed up a task: did the loop follow up the same one, open a new task, or
    neither."""
    same = new = other = 0
    for r in rows:
        followed = {h["name"] for h in r["night"]["hand_offs"] if h["kind"] == "follow-up"}
        if not followed:
            continue
        loop = {f["name"] for f in r["follow_ups"]}
        if followed & loop:
            same += 1
        elif r["hand_offs"]:
            new += 1
        else:
            other += 1
    if not same + new + other:
        return ""
    return (f"Of the {same + new + other} events where the night followed a task up, the loop followed up the same "
            f"task in {same}, opened a new task in {new}, and did neither in {other}.")


# ====================================================================== the command

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry", action="store_true", help="no model, no server: the dataset, the contexts, a fake loop")
    ap.add_argument("--name", default="loop", help="the realmc server (default loop)")
    ap.add_argument("--out", help="the run's folder (default $VFG_REPLAY_DIR or the temp folder)")
    ap.add_argument("--only", default="", help="events by number, range or ref: 4-12,#71,T207")
    ap.add_argument("--from", dest="start", type=int, default=0, help="the first event's number")
    ap.add_argument("--limit", type=int, default=0, help="at most this many events")
    ap.add_argument("--pace", type=float, default=0.0, help="wait this times the night's gap between calls (0: none)")
    ap.add_argument("--max-gap", type=float, default=300.0, help="the longest wait between two events, seconds")
    ap.add_argument("--budget", type=float, default=3.0, help="dollars of OpenRouter, then it stops")
    ap.add_argument("--event-timeout", type=float, default=300.0, help="one event's loop, seconds")
    ap.add_argument("--keep-up", action="store_true", help="leave the server running at the end")
    ap.add_argument("--reasoning", default="", help="the brain's reasoning for this run (VFG_BRAIN_REASONING): "
                                                   "off or an effort; default the brain's own")
    ap.add_argument("--note", default="", help="a line for the report's head: which code the run is on, say")
    ap.add_argument("--report", metavar="OUT", help="write OUT/report.md again from OUT's results, run nothing")
    ap.add_argument("--list", action="store_true", help="print the events and exit")
    ap.add_argument("--data", default=str(DATA), help="the dataset's folder")
    args = ap.parse_args()

    ds = Dataset(Path(args.data))
    wrong = ds.check()
    c = ds.session["counts"]
    say(f"dataset: {c['calls']} calls ({c['talk']} talk, {c['actions']} actions, {c['hand-off']} hand-off, "
        f"{c['report']} reports), ${c['cost']:.2f} on {', '.join(ds.session['model'])}"
        + ("" if not wrong else "; NOT the spec's: " + "; ".join(wrong)))
    if args.list:
        for e in ds.events:
            what = e.get("words") or f"{e['task']} {e['run']} {e['status']}"
            say(f"{e['n']:>3} {e['ref']:<6} {e['class']:<8} {e['at'][11:19]} ${e['original']['usage']['cost']:.4f} "
                f"{short(what, 90)}")
        return 0
    if args.report:
        out = Path(args.report)
        (out / "report.md").write_text(report(out, ds), encoding="utf-8")
        say(f"wrote {out / 'report.md'}")
        return 0
    out = Path(args.out or os.environ.get("VFG_REPLAY_DIR") or
               Path(tempfile.gettempdir()) / f"vfg-loop-replay-{time.strftime('%Y%m%d-%H%M%S')}")
    asyncio.run(replay(args, ds, out))
    text = report(out, ds)
    (out / "report.md").write_text(text, encoding="utf-8")
    rows, meta = load_results(out)
    if not args.dry and meta.get("whole") and not meta.get("stopped"):
        (DATA / "report.md").write_text(text, encoding="utf-8")
        say(f"wrote {DATA / 'report.md'}")
    say(f"wrote {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
