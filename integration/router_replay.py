#!/usr/bin/env python3
"""The router replay: Prototype_Conversation_And_Router's first Test, the router alone.

    brain/.venv/bin/python integration/router_replay.py --unpack            # the data, out of the repo
    brain/.venv/bin/python integration/router_replay.py --list night        # the moments, no model
    brain/.venv/bin/python integration/router_replay.py --dry               # both sets, a fake model
    brain/.venv/bin/python integration/router_replay.py --set night --limit 5 --budget 0.1   # a pilot
    brain/.venv/bin/python integration/router_replay.py                     # both sets, reasoning off
    brain/.venv/bin/python integration/router_replay.py --reasoning low     # the same at low
    python3 integration/replay/router/score.py OUT [OUT …]                  # OUT/report.md
    python3 integration/replay/router/score.py --repo OUT-PLAYTEST OUT-NIGHT   # the repo's report.md
    brain/.venv/bin/python integration/router_replay.py --set night --resume --out OUT   # ask failed calls again
    brain/.venv/bin/python integration/router_replay.py --rescore OUT --out OUT2  # a playtest run judged again

**What it replays.** The real `brain.router.Router` (router.py, its prompt prompts/router.md, the real
catalog of library actions and the command references), one call at a time, on two sets, each line
handed to it as the conversation agent's hand-off (`RouterEvent("LINE", row, said="On it!")`: the
agent's real reply and reason are not known for these lines, so every one gets the same neutral pair):

- **playtest**: the 2026-10-08 playtest's follow-ups and new things, the set the named-tasks
  orchestrator was measured on (integration/replay/taskcall.md: Gemini named a follow-up's task 37
  times in 41 with prompt v3, 36 with the built orchestrator; DeepSeek V4 Flash 36). That replay's
  inputs (lines.py's lines.jsonl, the brain's parts for each line, and taskcall.py's own answers)
  were in a scratch folder that is gone, and lines.py no longer runs, so this set is REBUILT, by
  rules close to taskcall's (below), from what is still here: the playtest's vfg.db, the tasks the
  conversation wrote (~/.vfg/orch/inbox.jsonl), what each task's implementer wrote or edited
  (integration/replay/tasks.jsonl, the by-script truth taskcall_score.py reads) and the hand-checked
  route labels (~/.vfg/dispatch/eval/selector-actions-2026-10-10.jsonl).
- **night**: every one of the 64 lines Jev sent to the AI on the night of 2026-10-12/13 (the
  box's vfg.db and feed, brain-log-2026-10-13.log), in time order, hand-labelled T, F or U as in
  session-2026-10-13.md, each with what it should go to (NIGHT, below).

**The playtest set, rebuilt.** A conversation task (T<n>) is an implementer's work when its route
labels accept `orchestrator` (the brain would have handed it off); every other T<n> is the brain's
own actions, shown as `AI action: …` and never a task. An implementer's T<n> joins the task holding
its `Change to T<x>` target, else the task of the earliest implementer's T<n> that wrote or
edited one of its scripts (tasks.jsonl), so one thing stays one task, else it starts a task of its own, named from its first script (or its first words) and its player
(`ice_walk_krown`, `galick_gun_styrka`), described by its first ask and its latest change. The
conversation's rows show those names, as taskcall.py's did (`AI task <name>: new: …`, `AI result
<name> done: …`). TASKS at a line is every task whose first T<n> was written before the line was
fed: its status from the playtest's reports, what its scripts are, its last result. No loops: that
design had none, so WORK UNDER WAY is empty. What is scored:
- **Follow-ups by script** (the bar, at least 36 of 41 = 87.8%): a T<n> labelled an implementer's
  that edited a script an earlier T<n> wrote, where a task held an earlier T<n> that touched it when
  the line was fed. Right only when the router answers `implementer` with one of those tasks. One
  whose holder was written after the line was fed cannot be named and is left out (counted).
- **New things by script**: every T<n> that wrote a new script, edited none and is not a `Change to`
  (37). Right when the router does not glue it onto a task: `new_loop` or `new_implementer`. The 4 of
  them labelled an implementer's are counted apart, the nearest to taskcall's 9.

**The night set.** The context is the box's feed up to the line (brain/context.py, as the brain
reads it); the world is who is online, the rest not known. TASKS is the box's tasks table at that
moment (its rows rebuilt from the feed's `AI task` and `AI result` rows, the table's own `made`),
changed by the replay's own choices: a line that started one of the box's three tasks (#113
blizzard_styrka, #114 blizzard_freeze, #128 fire_aura_krown) and that the router sent
`new_implementer` makes the router's task instead (the box's name is written as the router's in the
rows after); sent to an implementer or to a loop already on a task, the box's task is that task;
sent to a new loop, the box's task appears when the night made it, as the loop's hand-off. A task
the router starts on any other line is open, an implementer on it, for the rest of the night (what
it built is not known). WORK UNDER WAY is a simulated Loops registry fed by the replay's own
choices: a `new_loop` thinks for `--think-s` seconds (15, about two no-reasoning DeepSeek steps;
the night's Gemini steps took a median 18.4 s, its Sol steps 6.0 s), then ends, saved, offered until
120 s after its creation; a line added to a thinking loop extends its thinking to `--think-s` after
the line, and one added to an ended loop inside its window takes it up for `--think-s` more. A
loop's last reply and record are those of the night's own loop for its first line (the brain log),
shown once it has ended. It uses brain/brain/loops.py's `Loops` when that module is there, so the
router reads WORK UNDER WAY as it will live; else a stand-in that writes the design's format.

**The bars on the night** (NIGHT's `bar`): the 7 repeated asks, #60 and #63 (#58's teleport), #90
(#86's fireball), #115 (#113's blizzard, "just wanted aura of ice"), #147 (#144's "no damage", U),
#266 and #267 (#263's frost aura, U), go to the loop or task the replay put their ask in (strict),
or, for a pure repeat, `nowhere` (counted apart); #114, KrownJM repeating Styrkaeopteryx's blizzard,
goes to #113's work, not a new task; and the T lines that are asks of their own start their own:
`new_loop` or `new_implementer`. The F and U lines run too and are scored apart.

**Model calls.** structured() through the router's own `ask` (brain/__main__.py's asker), on the
router's settings (brain/config.py: `VFG_ROUTER_*`, DeepSeek V4.1 Flash, reasoning off, hosts
together, coreweave, parasail) unless `--model`, `--reasoning`, `--providers` say otherwise; one
call at a time. Their usage rows queue in OUT (VFG_USAGE_QUEUE is set to OUT for this process),
component `router`, call_id `replay #<seq>`; nothing reaches a box unless sent on purpose. The key is
vfg_llm's own (OPEN_ROUTER_API, else ~/.vfg/openrouter.key), never printed or written. `--budget`
(dollars, default 1) stops a run that spends more.

**Out of the repo.** The data (`--data`, default $VFG_ROUTER_REPLAY_DATA, else ~/.vfg/router-replay):
`--unpack` puts the night's vfg.db, feed and brain log and the playtest's vfg.db there, never the
archives' .env; the runs go to DATA/runs/<set>-<model>-<reasoning>-<time>/ unless `--out`. Only
this script, the scorer and integration/replay/router/report.md are in the repo.

**In OUT**: results.jsonl (one row a call: the line, its label and what it should go to, the
router's answer whole, ms, cost, prompt, cached and completion tokens, the loops and tasks offered,
and the score inputs), meta.json, usage.pending.jsonl, and the whole prompt of every 10th call in
prompts/. `--dry` answers with a fake model: the right answer, but a wrong one every 7th line, so the
scorer's misses run too; it goes through the real Router (its prompt, its answer's checks) when
brain/brain/router.py is there, else a stand-in, and writes estimates for the usage.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import hashlib
import json
import os
import re
import sqlite3
import statistics
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPLAY = HERE / "replay"
sys.path[:0] = [str(REPO / "brain"), str(REPO / "lib" / "py"), str(REPO / "bridge"), str(REPLAY)]

from brain.context import Context, Row, parse  # noqa: E402
from brain.world import Seen  # noqa: E402

HOME = Path.home()
BACKUP = HOME / "Documents" / "MinecraftServers" / "aws-backup"
NIGHT_TGZ = BACKUP / "session-logs-2026-10-13-033024.tgz"
NIGHT_LOG = BACKUP / "brain-log-2026-10-13.log"
PLAYTEST_TGZ = BACKUP / "vfg-backup-2026-10-08-playtest.tgz"
LABELS = HOME / ".vfg" / "dispatch" / "eval" / "selector-actions-2026-10-10.jsonl"
REFS = [HOME / ".vfg" / "brain" / "refs" / "26.3", HOME / ".vfg" / "dispatch" / "refs" / "26.3"]
DEFAULT_DATA = Path(os.environ.get("VFG_ROUTER_REPLAY_DATA") or HOME / ".vfg" / "router-replay")

V41 = "deepseek/deepseek-v4.1-flash"
V41_HOSTS = ("together", "coreweave", "parasail")
SAID = "On it!"            # the conversation agent's reply every replayed hand-off carries
TAKE_UP_S = 120.0          # VFG_LOOP_TAKE_UP_S: an ended loop is offered this long after its creation
GRANT, CAP = 10, 20        # VFG_LOOP_STEPS, VFG_LOOP_CAP
PROMPT_EVERY = 10
NOT_SCRIPTS = {"edit", "both"}   # words tasks.jsonl's shorthand puts where a script's name goes
PLAYER_SHORT = {"KrownJM": "krown", "Styrkaeopteryx": "styrka", "Eclipszr": "eclipszr"}
# DeepSeek V4.1 Flash, a million tokens (the spec's price table, peak): the dry run's estimates.
PRICE_IN, PRICE_CACHED, PRICE_OUT = 0.30, 0.006, 1.20
CHARS_PER_TOKEN = 4.0
TID = re.compile(r"\bT(\d+)\b")
FOLLOW_UP = re.compile(r"^\W*(change\s+to|cancel)\s+(T\d+)", re.I)
STOP_WORDS = {"a", "an", "the", "to", "for", "of", "and", "or", "all", "every", "with", "give", "make", "change",
              "add", "set", "fix", "get", "put", "his", "her", "their", "its", "my", "on", "in", "into", "so",
              "that", "it", "is", "be", "can", "you", "also", "around", "each", "online", "players", "player",
              "krownjm", "styrkaeopteryx", "eclipszr", "s", "back", "new", "more", "some"}


def say(*parts) -> None:
    """The replay's own lines, on stderr."""
    print(" ".join(str(p) for p in parts), file=sys.stderr, flush=True)


def short(text, n: int = 160) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def when(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def plus(at: str, seconds: float) -> str:
    return iso(when(at) + timedelta(seconds=seconds))


def gap(a: str, b: str) -> float:
    """Seconds from `a` to `b`."""
    return (when(b) - when(a)).total_seconds()


# ====================================================================== the data

def unpack(data: Path) -> None:
    """The night's vfg.db, feed.log and brain log, and the playtest's vfg.db, into `data`: those
    members alone (the playtest's archive holds the box's .env too), never over what is there."""
    def extract(tgz: Path, members: list[str], dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tgz) as tar:
            for m in members:
                if (dest / m).exists():
                    say(f"  {dest / m} is there already: kept")
                    continue
                tar.extract(m, dest, filter="data")
                say(f"  {dest / m}")
    extract(NIGHT_TGZ, ["vfg/vfg.db", "vfg/feed.log"], data / "night")
    log = data / "night" / NIGHT_LOG.name
    if not log.exists():
        log.write_bytes(NIGHT_LOG.read_bytes())
        say(f"  {log}")
    extract(PLAYTEST_TGZ, ["vfg/vfg.db"], data / "playtest-2026-10-08")


def ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


@dataclass
class Moment:
    """One line handed to the router."""
    set: str
    n: int                    # the line's number in its feed (LINE#n)
    seq: int                  # its seq in the replay's context (the router's `#<seq>`)
    who: str
    words: str
    at: str                   # when it reaches the router: when Jev fed it
    label: str = ""           # night: T, F or U
    expect: dict = field(default_factory=dict)
    truth: list[dict] = field(default_factory=list)   # playtest: its scored T<n>s
    row: Row | None = None    # playtest: its row of the context


# ---------------------------------------------------------------------- the playtest

def player_short(who: str) -> str:
    return PLAYER_SHORT.get(who, re.sub(r"[^a-z0-9]", "", who.lower())[:8] or "all")


def slug(text: str, most: int = 2) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP_WORDS and not w.isdigit()]
    return "_".join(words[:most]) or "thing"


class PlaytestSet:
    """The 2026-10-08 set, rebuilt (the module's docstring): the tasks with their chains, fixed in
    advance; the context in the order the brain would have had it, the tasks' rows under their
    names; and the moments, the lines with a scored T<n>."""

    def __init__(self, data: Path, labels: Path = LABELS):
        import playtest
        import taskcall_score
        from brain import names
        self.names_mod = names
        self.pt = playtest.Playtest(data / "playtest-2026-10-08" / "vfg" / "vfg.db")
        self.order, self.ran = taskcall_score.runs()
        self.labels = {r["id"]: r for r in (json.loads(x) for x in labels.read_text().splitlines() if x.strip())
                       if "id" in r}
        self.short_max = playtest.BRIEF_MAX
        self.result_max = playtest.RESULT_MAX
        self.owner: dict[str, str] = {}
        self.tasks: dict[str, dict] = {}
        self._chains()
        self.entries = self.pt.entries()
        self.ctx = Context()
        self.i = 0

    def implementer(self, tid: str) -> bool:
        """The T<n> was an implementer's work: its route labels accept the orchestrator."""
        return any("orchestrator" in a for a in self.labels.get(tid, {}).get("accept") or [])

    def scripts(self, tid: str) -> set[str]:
        wrote, edited = self.ran.get(tid, (set(), set()))
        return (wrote | edited) - NOT_SCRIPTS

    def _chains(self) -> None:
        from taskcall import heads
        self.done: list[str] = []   # the implementers' T<n>s placed so far, oldest first
        for t in sorted(self.pt.tasks, key=lambda t: (t["at"], int(t["id"][1:]))):
            tid = t["id"]
            if not self.implementer(tid):
                continue
            kind, targets, rest = heads(t["task"])
            touched = self.scripts(tid)
            pick = next((self.owner[x] for x in targets if x in self.owner), None)
            if pick is None and touched:   # the task of the earliest implementer's T<n> that touched one
                pick = next((self.owner[x] for x in self.done if self.scripts(x) & touched), None)
            if pick is not None:
                k = self.tasks[pick]
                k["chain"].append(tid)
                k["scripts"] |= touched
            else:
                base = (sorted(self.ran.get(tid, (set(), set()))[0] - NOT_SCRIPTS) or sorted(touched) or
                        [slug(rest)])[0].strip("_")
                name = f"{base}_{player_short(t['player'])}".lower()
                name = re.sub(r"[^a-z0-9_]", "", name)[:32].strip("_")
                if not name[:1].isalpha():
                    name = "t_" + name
                name = self.names_mod.unique(name, set(self.tasks) | self.names_mod.RESERVED)
                self.tasks[name] = {"name": name, "player": t["player"], "chain": [tid], "scripts": set(touched),
                                    "first": rest, "line": t["context"]}
                pick = name
            self.owner[tid] = pick
            self.done.append(tid)

    # -- the context
    def sub(self, text: str) -> str:
        return TID.sub(lambda m: self.owner.get(m.group(0), "an earlier task"), text)

    def render(self, row: Row) -> None:
        """A conversation task's row under its task's name, or as the brain's action."""
        from taskcall import heads
        if row.type != "AI":
            return
        if row.kind == "say":
            row.text = self.sub(row.text)
            return
        tid = row.task
        name = self.owner.get(tid)
        if row.kind == "task":
            kind, _, rest = heads(self.pt.task_by_id[tid]["task"])
            rest = self.sub(short(rest, self.short_max))
            first = name and self.tasks[name]["chain"][0] == tid
            if name:
                row.text, row.task = f"{'new' if first else 'follow-up'}: {rest}", name
            else:
                row.text, row.kind, row.task = rest, "action", ""
        elif row.kind == "result":
            at, status, report = self.pt.closed[tid]
            report = self.sub(short(report, self.result_max))
            if name:
                row.text, row.task = f"{name} {status}: {report}", name
            else:
                what = self.sub(short(heads(self.pt.task_by_id[tid]["task"])[2], 70))
                row.text, row.kind, row.task = f"{what} — {status}: {report}", "action", ""

    def until(self, at: str) -> None:
        while self.i < len(self.entries) and self.entries[self.i].seen <= at:
            row = self.entries[self.i].row
            self.render(row)
            self.ctx.add(row)
            self.i += 1

    # -- the tasks at a moment
    def at(self, tid: str) -> str:
        return self.pt.task_by_id[tid]["at"]

    def visible(self, at: str) -> list[dict]:
        """Every task whose first T<n> was written by `at`, as a row of the tasks table."""
        from taskcall import heads
        out = []
        for k in self.tasks.values():
            chain = [x for x in k["chain"] if self.at(x) <= at]
            if not chain:
                continue
            reports = sorted(self.pt.closed[x] for x in chain if x in self.pt.closed and self.pt.closed[x][0] <= at)
            pending = [x for x in chain if not (x in self.pt.closed and self.pt.closed[x][0] <= at)]
            status = "open" if pending else reports[-1][1]
            first = self.sub(short(k["first"], 200))
            last = self.sub(short(heads(self.pt.task_by_id[chain[-1]]["task"])[2], 200))
            description = first if len(chain) == 1 else f"{first}; latest change: {last}"
            scripts = sorted(set().union(*(self.scripts(x) for x in chain if x in self.pt.closed
                                           and self.pt.closed[x][0] <= at)) or set())
            result = (f"{k['name']} {reports[-1][1]}: {self.sub(short(reports[-1][2], self.result_max))}"
                      if reports else None)
            out.append({"name": k["name"], "description": description, "player": k["player"] or None,
                        "status": status, "line": "", "brief": "", "result": result,
                        "made": {"scripts": scripts, "actions": [], "presets": [], "copies": []},
                        "created_at": self.at(chain[0]), "updated_at": self.at(chain[-1]),
                        "busy": bool(pending)})
        return out

    # -- the moments
    def moments(self) -> list[Moment]:
        """The lines with a scored T<n>, in the order Jev fed them (see the module's docstring)."""
        pos = {tid: i for i, tid in enumerate(self.order)}
        items: dict[int, list[dict]] = {}
        for t in self.pt.tasks:
            tid = t["id"]
            if tid not in self.ran or not t["refs"]:
                continue
            n = t["refs"][0]
            line = self.pt.by_n.get(n)
            if line is None or not line.fed:
                continue
            wrote, edited = self.ran[tid]
            earlier = self.order[:pos[tid]]
            prior = {sc for sc in edited if any(sc in self.ran[x][0] for x in earlier)}
            fresh = not FOLLOW_UP.match(t["task"])
            if prior:
                touched = [x for x in earlier if prior & (self.ran[x][0] | self.ran[x][1])]
                held = sorted({self.owner[x] for x in touched if x in self.owner})
                now = sorted({self.owner[x] for x in touched if x in self.owner and self.at(x) <= line.fed})
                # Each script's holders then: a line that edited two tasks' things is right in full
                # only when each is followed up (the scorer's `right_by_script`).
                by_script = {sc: sorted({self.owner[x] for x in earlier if x in self.owner and self.at(x) <= line.fed
                                         and sc in (self.ran[x][0] | self.ran[x][1])}) for sc in sorted(prior)}
                if not self.implementer(tid):
                    kind = "follow_up_actions"     # the brain's own actions on it: not scored
                elif not held:
                    kind = "follow_up_no_task"     # no task holds the script: the brain's actions made it
                elif not now:
                    kind = "follow_up_later"       # its holder was written after the line was fed
                else:
                    kind = "follow_up"
                items.setdefault(n, []).append({"id": tid, "kind": kind, "task": t["task"], "scripts": sorted(prior),
                                                "right": now, "right_by_script": {k: v for k, v in by_script.items() if v},
                                                "implementer": self.implementer(tid)})
            elif wrote and not edited and fresh:
                items.setdefault(n, []).append({"id": tid, "kind": "new", "task": t["task"],
                                                "scripts": sorted(wrote), "implementer": self.implementer(tid)})
        by_n = {e.n: e for e in self.entries if e.n}
        self.left = {k: sum(x["kind"] == k for its in items.values() for x in its)
                     for k in ("follow_up_no_task", "follow_up_later", "follow_up_actions")}
        out = []
        for n, its in items.items():
            if not any(x["kind"] in ("follow_up", "new") for x in its):
                continue
            e = by_n[n]
            line = self.pt.by_n[n]
            out.append(Moment("playtest", n, e.row.seq, line.who, e.row.text, e.seen, truth=its, row=e.row))
        out.sort(key=lambda m: m.at)
        return out

    def seen(self, at: str, who: str) -> Seen:
        return self.pt.seen(at, who)


# ---------------------------------------------------------------------- the night

# The night's 64 lines to the AI: (T/F/U as session-2026-10-13.md labelled them, what each should go
# to, the ask it belongs to, the thing it is about, the bar it counts for, whether `nowhere` is fine).
# kinds: new (new_loop or new_implementer); ask (the work the replay put an earlier line of the same
# ask in); thing (the work on that thing: any line's about it); thing_or_new (that, or new); talk (a
# line that needs nothing: nowhere; new_loop is harmless, a loop or task is glued). When the work an
# `ask` or `thing` line should join does not exist (its lines went nowhere), new or nowhere is right.
NIGHT: dict[int, dict] = {
    4: dict(label="T", kind="new", ask=4, thing="incantations"),
    5: dict(label="T", kind="talk"),
    8: dict(label="F", kind="ask", ask=4, thing="incantations", nowhere=True),
    14: dict(label="T", kind="new", ask=14, thing="steak"),
    20: dict(label="F", kind="talk"),
    22: dict(label="F", kind="talk"),
    32: dict(label="T", kind="new", ask=32, thing="time"),
    34: dict(label="T", kind="new", ask=34, thing="food_s"),
    38: dict(label="F", kind="talk"),
    41: dict(label="U", kind="talk"),
    52: dict(label="T", kind="new", ask=52, thing="incantations2"),
    54: dict(label="T", kind="ask", ask=52, thing="incantations2", nowhere=True),
    56: dict(label="F", kind="talk"),
    57: dict(label="F", kind="talk"),
    58: dict(label="T", kind="new", ask=58, thing="teleport"),
    60: dict(label="T", kind="ask", ask=58, thing="teleport", bar="repeat", nowhere=True),
    63: dict(label="T", kind="ask", ask=58, thing="teleport", bar="repeat", nowhere=True),
    74: dict(label="F", kind="talk"),
    78: dict(label="F", kind="talk"),
    80: dict(label="F", kind="talk"),
    82: dict(label="U", kind="new", ask=82, thing="destroy_everything"),
    86: dict(label="T", kind="new", ask=86, thing="fireball"),
    87: dict(label="T", kind="ask", ask=82, thing="destroy_everything"),
    90: dict(label="T", kind="ask", ask=86, thing="fireball", bar="repeat", nowhere=True),
    95: dict(label="F", kind="talk"),
    99: dict(label="F", kind="talk"),
    107: dict(label="F", kind="talk"),
    109: dict(label="F", kind="talk"),
    111: dict(label="F", kind="talk"),
    113: dict(label="T", kind="new", ask=113, thing="blizzard"),
    114: dict(label="F", kind="ask", ask=113, thing="blizzard", bar="blizzard", nowhere=True),
    115: dict(label="T", kind="ask", ask=113, thing="blizzard", bar="repeat"),
    116: dict(label="T", kind="new", ask=116, thing="fire_aura"),
    121: dict(label="F", kind="talk"),
    128: dict(label="T", kind="ask", ask=116, thing="fire_aura", bar="other_repeat", nowhere=True),
    139: dict(label="F", kind="talk"),
    141: dict(label="F", kind="talk"),
    144: dict(label="T", kind="thing", ask=144, thing="blizzard"),
    147: dict(label="U", kind="ask", ask=144, thing="blizzard", bar="repeat"),
    154: dict(label="T", kind="thing_or_new", ask=154, thing="blizzard"),
    156: dict(label="F", kind="talk"),
    162: dict(label="T", kind="thing_or_new", ask=162, thing="blizzard"),
    174: dict(label="T", kind="new", ask=174, thing="cooldown"),
    177: dict(label="F", kind="talk"),
    180: dict(label="T", kind="thing_or_new", ask=180, thing="blizzard"),
    183: dict(label="T", kind="thing_or_new", ask=183, thing="blizzard"),
    192: dict(label="F", kind="talk"),
    197: dict(label="U", kind="thing_or_new", ask=197, thing="blizzard"),
    # #200 and #201 answer the AI's "your flame aura or Styrka's ice aura?" (AI#199, asked of #197):
    # the ice aura, whatever work the replay put #197 in.
    200: dict(label="T", kind="thing", ask=200, thing="blizzard"),
    201: dict(label="T", kind="thing_or_new", ask=201, thing="blizzard"),
    203: dict(label="T", kind="thing_or_new", ask=203, thing="blizzard"),
    213: dict(label="F", kind="talk"),
    214: dict(label="T", kind="thing_or_new", ask=214, thing="cooldown", bar="other_repeat"),
    217: dict(label="T", kind="thing_or_new", ask=214, thing="cooldown"),   # answers AI#216: all my spells
    222: dict(label="T", kind="thing", ask=222, thing="fire_aura"),
    226: dict(label="F", kind="talk"),
    227: dict(label="F", kind="talk"),
    232: dict(label="T", kind="thing_or_new", ask=232, thing="blizzard"),
    233: dict(label="F", kind="talk"),
    249: dict(label="U", kind="thing", ask=249, thing="fire_aura"),
    # One ask: #260 "frost aura is unbound to me", the AI's "Freeze is bound to you again. Did you want
    # it unbound?" (AI#262, from #260's loop), #263 "it still gives me frost", and the answer #266 and
    # its repeat #267: the work of #260 and #263 is theirs.
    260: dict(label="U", kind="thing_or_new", ask=260, thing="blizzard"),
    263: dict(label="U", kind="thing_or_new", ask=260, thing="blizzard"),
    266: dict(label="T", kind="ask", ask=260, thing="blizzard", bar="repeat", nowhere=True),
    267: dict(label="T", kind="ask", ask=260, thing="blizzard", bar="repeat", nowhere=True),
}
NIGHT_DAY = "2026-10-13"
FEED_AI = re.compile(r"^AI#(?P<seq>\d+) (?P<hms>\S+) (?P<kind>\S+) (?P<task>\S+) \| (?P<text>.*)$", re.S)
LOG_BRAIN = re.compile(r"^(?P<at>\S+) brain #(?P<seq>\d+) s(?P<step>\d+) (?P<rest>.*)$")
LOG_RESPOND = re.compile(r"^(?P<at>\S+) respond #(?P<seq>\d+) \S+: '.*' -> [\"'](?P<reply>.*)[\"'] \(")


class NightSet:
    """The night's data: the feed, the lines, the box's tasks and their history, and what the
    night's own loop for each line replied and recorded."""

    def __init__(self, data: Path):
        folder = data / "night"
        con = ro(folder / "vfg" / "vfg.db")
        self.feed = [dict(r) for r in con.execute("SELECT seq, at, line FROM feed ORDER BY seq")]
        lines = [dict(r) for r in con.execute("SELECT at, player_name, text, fed_at, jev_class FROM lines "
                                              "WHERE source != 'ai'")]
        self.box = {r["name"]: dict(r) for r in con.execute("SELECT * FROM tasks")}
        con.close()
        fed = {(x["at"], x["player_name"]): x for x in lines}
        self.lines: dict[int, dict] = {}
        for f in self.feed:
            if not f["line"].startswith("LINE#"):
                continue
            row = parse(f["line"], seq=f["seq"], at=f["at"])
            if row is None or row.cls not in ("action_oriented", "conversational"):
                continue
            src = fed.get((f["at"], row.who)) or {}
            self.lines[f["seq"]] = {"seq": f["seq"], "who": row.who, "words": row.text, "said": f["at"],
                                    "at": src.get("fed_at") or f["at"], "source": row.source}
        # The box's tasks' history, from the feed's AI rows: (at, name, kind, text).
        self.history: list[tuple[str, str, str, str]] = []
        for f in self.feed:
            m = FEED_AI.match(f["line"])
            if not m or m.group("task") == "-" or m.group("kind") not in ("task", "result"):
                continue
            name, text = m.group("task"), m.group("text")
            if m.group("kind") == "task":
                kind = "new" if text.startswith("new:") else "follow-up"
                text = text.split(":", 1)[1].strip()
            else:
                kind = "failed" if text.startswith(f"{name} failed") else "done"
            self.history.append((f["at"], name, kind, text))
        self.made_as = {name: text for _, name, kind, text in self.history if kind == "new"}
        # The line that started each box task: its `line` words.
        self.origin: dict[str, int] = {}
        for name, t in self.box.items():
            hit = next((s for s, ln in self.lines.items() if " ".join(ln["words"].split()) ==
                        " ".join(str(t["line"] or "").split())), None)
            if hit is not None:
                self.origin[name] = hit
        self.night = self._night_loops(folder / NIGHT_LOG.name)

    @staticmethod
    def _night_loops(path: Path) -> dict[int, dict]:
        """What the night's own loop (or Respond) did for each line: its last step's reply and record."""
        out: dict[int, dict] = {}
        if not path.exists():
            return out
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if m := LOG_BRAIN.match(line):
                seq, rest = int(m.group("seq")), m.group("rest")
                got = out.setdefault(seq, {"reply": "", "record": "", "failed": False})
                if " FAILED " in f" {rest}":
                    got["failed"] = True
                    got["record"] = "failed: " + short(rest.split(":", 1)[-1], 120)
                    continue
                for part in rest.split(" || "):
                    if part.startswith("reply: "):
                        got["reply"] = part[7:].split(" -> ")[0].strip()
                    elif part.startswith("record: "):
                        got["record"] = part[8:].split(" -> ")[0].strip()
            elif m := LOG_RESPOND.match(line):
                out.setdefault(int(m.group("seq")), {"reply": m.group("reply"), "record": "", "failed": False})
        return out

    def moments(self) -> list[Moment]:
        out = []
        for seq, ln in self.lines.items():
            lab = NIGHT.get(seq)
            if lab is None:
                say(f"  LINE#{seq} has no label in NIGHT: run as talk")
                lab = dict(label="?", kind="talk")
            out.append(Moment("night", seq, seq, ln["who"], ln["words"], ln["at"], label=lab["label"], expect=dict(lab)))
        out.sort(key=lambda m: m.seq)
        return out

    def online(self, upto: int) -> list[str]:
        names: list[str] = []
        for f in self.feed:
            if f["seq"] > upto:
                break
            m = re.match(r"^GAME#\d+ \S+ (join|leave) (\S+)", f["line"])
            if m and m.group(1) == "join" and m.group(2) not in names:
                names.append(m.group(2))
            elif m and m.group(1) == "leave" and m.group(2) in names:
                names.remove(m.group(2))
        return names

    def seen(self, upto: int, who: str) -> Seen:
        names = self.online(upto)
        if who and who not in names:
            names.append(who)
        players = "\n".join(f"- {p}: position and items unknown" for p in names) or "nobody"
        return Seen(players=f"PLAYERS ONLINE (their position and items unknown in this replay):\n{players}",
                    clock="TIME: unknown. WEATHER: unknown.", running="RUNNING NOW: not known in this replay",
                    places="SAVED PLACES: not known in this replay",
                    incantations="INCANTATIONS: not known in this replay")


class Work:
    """Which loops and tasks are one piece of work: a union-find over `L<n>` and task names."""

    def __init__(self):
        self.up: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.up.setdefault(x, x)
        while self.up[x] != x:
            self.up[x] = self.up[self.up[x]]
            x = self.up[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.up[self.find(a)] = self.find(b)

    def group(self, x: str) -> set[str]:
        root = self.find(x)
        return {y for y in list(self.up) if self.find(y) == root}


class StandInLoops:
    """WORK UNDER WAY's registry when brain/brain/loops.py is not there yet: the design's format."""

    def __init__(self, clock):
        self.clock = clock      # () -> ISO UTC now
        self.loops: dict[str, SimpleNamespace] = {}
        self.n = 0

    def new(self, player: str, row: Row) -> SimpleNamespace:
        self.n += 1
        lp = SimpleNamespace(id=f"L{self.n}", player=player, created=self.clock(), lines=[row], pending=[],
                             steps=0, state="thinking", last_reply="", record="", limit=GRANT)
        self.loops[lp.id] = lp
        return lp

    def get(self, lid: str) -> SimpleNamespace | None:
        return self.loops.get(lid)

    def end(self, lp, *, steps: int, reply: str, record: str) -> None:
        lp.state, lp.steps, lp.last_reply, lp.record = "ended", steps, reply, record
        lp.pending.clear()

    def set_steps(self, lp, steps: int, read_before: str) -> None:
        lp.steps = steps
        lp.pending[:] = [r for r in lp.pending if r.at >= read_before]

    def offered(self) -> list[SimpleNamespace]:
        now = self.clock()
        return [lp for lp in self.loops.values() if lp.state == "thinking" or
                (lp.state == "ended" and gap(lp.created, now) < TAKE_UP_S and lp.steps < lp.limit)]

    def add(self, lp, row: Row) -> str:
        if lp not in self.offered():
            return "gone"
        lp.lines.append(row)
        lp.pending.append(row)
        lp.limit = min(CAP, GRANT * len(lp.lines))
        if lp.state == "ended":
            lp.state = "thinking"
            return "taken up"
        return "read next"

    def show(self) -> str:
        now = self.clock()
        rows = []
        for lp in self.offered():
            ago = gap(lp.created, now)
            lines = ", ".join(f"#{r.seq} “{short(r.text, 80)}” {r.hms}" + (" (added, not read yet)" if r in lp.pending
                                                                             else "") for r in lp.lines)
            if lp.state == "thinking":
                head = (f"- {lp.id}, for {lp.player}, thinking, step {lp.steps + 1} of {lp.limit}, started "
                        f"{lp.created[11:19]} ({ago:.0f} s ago)")
            else:
                left = TAKE_UP_S - ago
                head = (f"- {lp.id}, for {lp.player}, ended, saved: can take a line for {left:.0f} s more; "
                        f"{lp.steps} of {lp.limit} steps used" + (f"; did: {lp.record}" if lp.record else ""))
            rows.append(f"{head}; lines: {lines}" + (f"; last reply: “{lp.last_reply}”" if lp.last_reply else ""))
        if not rows:
            return "WORK UNDER WAY: no brain loops"
        return "WORK UNDER WAY (every brain loop that can take a line, every player's):\n" + "\n".join(rows)


class NightSim:
    """The night as the replay's own choices make it (the module's docstring): loops thinking and
    ended, the tasks shown, the box's tasks' names, and which loops and tasks are one piece of work."""

    def __init__(self, ns: NightSet, think_s: float):
        self.ns = ns
        self.think_s = think_s
        self.now = ""
        self.real = None
        self.loops = self._registry()
        self.think_until: dict[str, str] = {}
        self.read_at: dict[str, list[tuple[str, Row]]] = {}
        self.first: dict[str, int] = {}             # loop id -> the seq of its first line
        self.born: dict[str, str] = {}              # loop id -> when it was made (ISO)
        self.work = Work()
        self.alias: dict[str, str] = {}             # a box task's name -> the name shown
        self.decided: set[str] = set()              # box tasks whose origin line has been placed
        self.own: dict[str, dict] = {}              # tasks the router started: name -> row
        self.followed: dict[str, str] = {}          # task -> when the router last followed it up
        self.placed: dict[int, list[str]] = {}      # seq -> the work ids it went to (its own, or joined)
        self.ctx = Context()
        self.k = 0

    # -- the loops
    def _registry(self):
        """brain.loops.Loops when it is there (the router reads WORK UNDER WAY as it will live), else
        the stand-in."""
        clock = lambda: self.now  # noqa: E731
        try:
            from brain import loops as loops_mod
        except ImportError:
            return StandInLoops(clock)
        try:
            self.real = loops_mod
            return LoopsAdapter(loops_mod, clock)
        except Exception as e:  # noqa: BLE001 — a registry whose shape differs: the stand-in, said
            say(f"  brain/brain/loops.py is there but did not fit ({type(e).__name__}: {e}): the stand-in")
            self.real = None
            return StandInLoops(clock)

    def tick(self, at: str) -> None:
        """Time moves to `at`: a loop past its thinking ends, saved, with the night's reply and
        record for its first line; one still thinking has taken a step every half of --think-s, and
        reads a line added before its last step began."""
        self.now = at
        for lp in list(self.loops.loops.values()):
            if lp.state != "thinking":
                continue
            end = self.think_until[lp.id]
            night = self._night(lp)
            if at >= end:
                self.loops.end(lp, steps=self._steps(lp, end), reply=night["reply"], record=night["record"])
            else:
                self.loops.set_steps(lp, self._steps(lp, at), read_before=plus(at, -self.think_s / 2))

    def _steps(self, lp, at: str) -> int:
        """Steps taken by `at`: one every half of --think-s while it thinks (a take-up's steps count
        on from the loop's creation, a stand-in's simplification), never past its limit."""
        return min(lp.limit, max(1, int(gap(self.born[lp.id], at) // (self.think_s / 2))))

    def _night(self, lp) -> dict:
        return self.ns.night.get(self.first.get(lp.id, 0), {"reply": "", "record": ""})

    def offered(self) -> list[str]:
        return [lp.id for lp in self.loops.offered()]

    # -- the tasks
    def name_of(self, box: str) -> str:
        return self.alias.get(box, box)

    def shown_box(self, box: str) -> bool:
        """A box task is shown under its own name once its origin line was placed so that it stands
        (a new loop, nowhere) or when no line of the replay started it."""
        return box not in self.alias

    def tasks_at(self) -> list[dict]:
        """Every task at `now`, as tasks.board() takes them, `busy` when an implementer is on it."""
        rows: dict[str, dict] = {}
        for name, r in self.own.items():
            if r["created_at"] <= self.now:
                rows[name] = dict(r, busy=True)
        for at, box, kind, text in self.ns.history:
            if at > self.now:
                break
            name = self.name_of(box)
            # A box task merged into another box task (#114's into #113's) never existed in the replay:
            # its rows still open and close the task it is merged into, but its result and what it made
            # are not that task's.
            merged = name != box and name in self.ns.box
            row = rows.get(name)
            if row is None:
                own = self.ns.box.get(name) or self.ns.box[box]
                row = rows[name] = {"name": name, "description": self.ns.made_as.get(name) or
                                    (text if kind == "new" else own["description"]),
                                    "player": own["player"], "status": "open", "line": own["line"], "brief": "",
                                    "result": None, "made": {"scripts": [], "actions": [], "presets": [],
                                                             "copies": []},
                                    "created_at": at, "updated_at": at, "busy": True}
            row["updated_at"] = at
            if kind in ("new", "follow-up"):
                row["status"], row["busy"] = "open", True
            elif not merged:
                row["status"], row["busy"] = kind, False
                row["result"] = self.rename(text)
                made = json.loads(self.ns.box[box]["made"] or "{}")
                row["made"] = {k: list(dict.fromkeys(row["made"].get(k, []) + list(made.get(k) or [])))
                               for k in ("scripts", "actions", "presets", "copies")}
        for name, t in self.followed.items():
            if name in rows and t <= self.now and t >= rows[name]["updated_at"]:
                rows[name]["status"], rows[name]["busy"] = "open", True
        return sorted(rows.values(), key=lambda r: r["created_at"])

    def rename(self, text: str) -> str:
        for box, name in self.alias.items():
            text = re.sub(rf"\b{re.escape(box)}\b", name, text)
        return text

    # -- the context
    def context_to(self, seq: int) -> None:
        """The feed up to `seq`, the box's names as the replay shows them (a row's text is fixed when
        it enters, so the prefix only grows)."""
        seen_new: set[str] = {r.task for r in self.ctx.rows if r.type == "AI" and r.kind == "task"}
        while self.k < len(self.ns.feed) and self.ns.feed[self.k]["seq"] <= seq:
            f = self.ns.feed[self.k]
            self.k += 1
            row = parse(f["line"], seq=f["seq"], at=f["at"], processed=True)
            if row is None:
                continue
            if row.type == "AI" and row.task:
                name = self.name_of(row.task)
                if row.kind == "task" and row.text.startswith("new:") and name in seen_new:
                    row.text = "follow-up:" + row.text[4:]
                row.task = name
                row.text = self.rename(row.text)
                if row.kind == "task":
                    seen_new.add(name)
            self.ctx.add(row)

    # -- placing a line
    def place(self, m: Moment, row: Row, to: str, loop: str, tasks: list[str], hand_off: dict | None) -> dict:
        """The router's answer carried out on the simulation, as the core would place it: a line's
        follow-ups may be several, one a task (each followed up)."""
        tasks = [t for t in tasks if t]
        task = tasks[0] if tasks else ""
        out: dict = {"placed": to}
        origin = next((b for b, s in self.ns.origin.items() if s == m.seq), None)
        if to == "new_loop" or (to == "loop" and loop not in self.offered()):
            if to == "loop":
                out["placed"] = "new_loop (the loop it named takes no line)"
            lp = self.loops.new(m.who, row)
            self.first[lp.id] = m.seq
            self.born[lp.id] = self.now
            self.think_until[lp.id] = plus(self.now, self.think_s)
            self.work.find(lp.id)
            self.placed[m.seq] = [lp.id]
            out["loop"] = lp.id
            if origin:
                self.work.union("task:" + origin, lp.id)
        elif to == "loop":
            lp = self.loops.get(loop)
            how = self.loops.add(lp, row)
            if how == "gone":   # the core starts a new loop instead
                return self.place(m, row, "new_loop", "", [], None) | {"placed": "new_loop (the loop was gone)"}
            self.think_until[lp.id] = max(self.think_until.get(lp.id, self.now), plus(self.now, self.think_s))
            self.placed[m.seq] = [lp.id]
            out.update(loop=lp.id, added=how)
            if origin:
                first_origin = next((b for b, s in self.ns.origin.items() if s == self.first.get(lp.id)), None)
                if first_origin and first_origin != origin:
                    self.alias[origin] = self.name_of(first_origin)
                else:
                    self.work.union("task:" + origin, lp.id)
        elif to == "new_implementer" and hand_off:
            name = self.own_task(hand_off, m)
            self.placed[m.seq] = ["task:" + name]
            out["task"] = name
            if origin:
                self.alias[origin] = name
        elif to == "implementer" and task:
            for t in tasks:
                self.followed[t] = self.now
            self.placed[m.seq] = ["task:" + t for t in tasks]
            out["task"] = task if len(tasks) == 1 else tasks
            if origin and origin not in self.alias and task != origin:
                self.alias[origin] = task
        else:
            self.placed[m.seq] = []
            if origin and to == "nowhere":
                self.merge_into(origin, m)
        if origin:
            self.decided.add(origin)
        return out

    def merge_into(self, origin: str, m: Moment) -> None:
        """A line sent nowhere that started one of the box's tasks (#114, KrownJM's echo of the
        blizzard): in the replay it started nothing, so the box's task is the one its ask's work
        already is (NIGHT's `ask`): a task there, or the box task a loop there made; else it stays
        under its own name."""
        key = NIGHT.get(m.seq, {}).get("ask")
        work = self.work_of([s for s, x in NIGHT.items() if s < m.seq and x.get("ask") == key])
        task = next((w[5:] for w in sorted(work) if w.startswith("task:")), None)
        if task is None:
            made = [b for b, s in self.ns.origin.items() for w in work if w.startswith("L") and self.first.get(w) == s]
            task = made[0] if made else None
        if task and task != origin:
            self.alias[origin] = self.name_of(task)

    def own_task(self, h: dict, m: Moment) -> str:
        from brain import names
        taken = {r["name"] for r in self.tasks_at()} | set(self.own) | names.RESERVED
        name = names.unique(h.get("name") or "task", taken)
        self.own[name] = {"name": name, "description": h.get("description") or "", "player": m.who, "status": "open",
                          "line": m.words, "brief": "", "result": None,
                          "made": {"scripts": [], "actions": [], "presets": [], "copies": []},
                          "created_at": self.now, "updated_at": self.now}
        return name

    # -- what a line should go to
    def work_of(self, seqs: list[int]) -> set[str]:
        """Every loop and task in the same piece of work as where these lines went."""
        out: set[str] = set()
        for s in seqs:
            for w in self.placed.get(s, []):
                out |= self.work.group(w)
        return out


class LoopsAdapter:
    """brain.loops.Loops under the stand-in's face (new, get, add, offered, show, end, set_steps,
    `loops`), on the replay's clock: the registry's `clock` is the replay's `now` in seconds and its
    `now` the same as a datetime, so WORK UNDER WAY's times are the night's. Ending a loop and its
    steps are the replay's to set (loop.py does it live): an ended loop gets a stand-in prompt, so
    it counts as saved and can be taken up inside its window."""

    def __init__(self, mod, clock):
        self.mod = mod
        self.iso_clock = clock
        self.reg = mod.Loops(GRANT, CAP, TAKE_UP_S, clock=lambda: when(self.iso_clock()).timestamp(),
                             now=lambda: when(self.iso_clock()))

    @property
    def loops(self) -> dict:
        return self.reg.loops

    def new(self, player: str, row: Row):
        from brain.call import Event
        return self.reg.new(Event("LINE", row), player, row.text)

    def get(self, lid: str):
        return self.reg.get(lid)

    def offered(self):
        return list(self.reg.offered())

    def add(self, lp, row: Row) -> str:
        return self.reg.add(lp, row, said=SAID, reason="")

    def end(self, lp, *, steps: int, reply: str, record: str) -> None:
        lp.steps, lp.last_reply, lp.record = steps, reply, record
        lp.pending.clear()
        lp.prompt = lp.prompt or SimpleNamespace(stand_in=True)
        lp.how = "reply" if reply else "silent"
        self.reg.settle(lp)

    def set_steps(self, lp, steps: int, read_before: str) -> None:
        lp.steps = steps
        lp.pending[:] = [a for a in lp.pending if a.row.at >= read_before]

    def show(self) -> str:
        return self.reg.show(brief=False)


# ====================================================================== the router

def load_refs():
    """The command references for 26.3, read as the brain reads a complete cache, never written
    (integration/replay/lines.py's way)."""
    from brain import refs as refs_module
    for d in REFS:
        if (d / "complete.json").is_file():
            mark = json.loads((d / "complete.json").read_text())
            if mark.get("format") != refs_module.FORMAT:
                sys.exit(f"{d} is format {mark.get('format')}, the brain reads {refs_module.FORMAT}: it would "
                         "rewrite it; copy it elsewhere first")
            return refs_module._read_complete(d, lambda *a: None)
    say(f"no command references in {' or '.join(map(str, REFS))}: the router's opening says not loaded")
    return None


def router_module():
    try:
        from brain import router
        return router
    except ImportError:
        return None


class CreditGone(Exception):
    """OpenRouter refused a call for want of credit (402) twice, two minutes apart: the run stops,
    and `--resume` goes on from there once credit is back."""


CREDIT_WAIT_S = 120.0


class Rig:
    """The router, real or fake, and how each call is recorded."""

    def __init__(self, args, out: Path):
        from brain import actions, config
        self.args, self.out = args, out
        self.cfg = config.load()
        self.catalog = actions.load(self.cfg.repo, None)
        self.hidden = actions.hidden_commands()
        self.refs = load_refs()
        self.mod = router_module()
        self.model = args.model or getattr(self.cfg, "router_model", "") or V41
        self.reasoning = args.reasoning or getattr(self.cfg, "router_reasoning", "") or "off"
        prov = args.providers if args.providers is not None else ",".join(getattr(self.cfg, "router_providers", ())
                                                                          or V41_HOSTS)
        self.providers = tuple(x for x in prov.split(",") if x)
        thinks = self.reasoning not in ("off", "", "none")
        self.timeout = args.timeout or ((60.0 if thinks else 30.0) if args.reasoning else
                                        float(getattr(self.cfg, "router_timeout_s", 0) or 30.0))
        self.calls = 0
        self.spent = 0.0
        self.last: dict = {}
        self.fake_answer: dict | None = None
        if self.mod is None and not args.dry:
            sys.exit("brain/brain/router.py is not there yet: only --dry runs")
        if self.mod is not None:
            self.router = self.mod.Router(self.catalog, self._ask(), owner=self.cfg.owner, hidden=self.hidden,
                                          checkpoint=self.cfg.checkpoint)
        else:
            self.router = None

    def _ask(self):
        """structured() with the router's settings (or the fake), its call_id `replay …`, every call's
        prompt kept for the record."""
        import vfg_llm
        from brain.__main__ import asker
        real = None if self.args.dry else asker(self.model, self.reasoning, self.timeout, self.providers)

        async def ask(text, output, *, cached=None, component="router", call_id=""):
            self.last = {"text": text, "cached": cached or [], "output": output.__name__}
            call_id = f"replay {call_id}"[:64]
            if real is not None:
                return await real(text, output, cached=cached, component=component, call_id=call_id)
            prompt = sum(len(t) for t, _ in cached or []) + len(text)
            tokens = int(prompt / CHARS_PER_TOKEN)
            marked = max((i for i, (_, m) in enumerate(cached or []) if m), default=-1)
            hit = int(sum(len(t) for t, _ in (cached or [])[:marked + 1]) / CHARS_PER_TOKEN) if self.calls else 0
            value = output.model_validate(self.fake_answer)
            cost = ((tokens - hit) * PRICE_IN + hit * PRICE_CACHED + 300 * PRICE_OUT) / 1e6
            vfg_llm_row = {"at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                           "component": component, "model": "dry (no model)", "input_tokens": tokens,
                           "output_tokens": 300, "cost_usd": round(cost, 9), "ref": call_id,
                           "cache_read_tokens": hit, "cache_write_tokens": None, "ok": 1, "error": None, "ms": 900}
            with (self.out / "usage.pending.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(vfg_llm_row) + "\n")
            return vfg_llm.Answer(value=value, cost=cost, ms=900 + tokens // 100, prompt_tokens=tokens,
                                  cached_tokens=hit, completion_tokens=300)
        return ask

    async def route(self, m: Moment, row: Row, ctx: Context, seen: Seen, board: str, under_way: str,
                    loops: list[str], tasks: list[str], fake: dict) -> dict:
        """One call: the answer as a dict, with its measures; `error` when it failed for good."""
        self.fake_answer = fake
        t0 = time.perf_counter()
        if self.router is None:   # --dry before brain/brain/router.py: a stand-in answer, no prompt
            self.calls += 1
            return {"answer": fake, "ms": 0, "cost": 0.0, "prompt_tokens": 0, "cached_tokens": 0,
                    "completion_tokens": 0, "wall_ms": 0, "stand_in": True}
        import vfg_llm
        event = self.mod.RouterEvent("LINE", row=row, said=SAID, reason="")
        for attempt in (1, 2):
            try:
                a = await self.router.route(event, ctx, seen, board, under_way, loops=loops, tasks=tasks,
                                            refs=self.refs)
                break
            except vfg_llm.LlmError as e:
                if "402" not in str(e)[:40]:   # "OpenRouter 402: …": out of credit; anything else failed
                    self.calls += 1
                    return {"error": short(str(e), 500), "wall_ms": int((time.perf_counter() - t0) * 1000)}
                if attempt == 2:
                    raise CreditGone(short(str(e), 300)) from e
                say(f"  402 from OpenRouter (credit): waiting {CREDIT_WAIT_S:.0f} s, then one more try")
                await asyncio.sleep(CREDIT_WAIT_S)
        self.calls += 1
        self.spent += a.cost
        v = a.value
        answer = {"reason": v.reason, "to": v.to, "loop": v.loop, "reply": v.reply,
                  "hand_offs": [h.model_dump() for h in v.hand_offs],
                  "follow_ups": [f.model_dump() for f in v.follow_ups]}
        return {"answer": answer, "ms": a.ms, "cost": a.cost, "prompt_tokens": a.prompt_tokens,
                "cached_tokens": a.cached_tokens, "completion_tokens": a.completion_tokens,
                "wall_ms": int((time.perf_counter() - t0) * 1000)}

    def keep_prompt(self, name: str) -> None:
        if not self.last:
            return
        (self.out / "prompts").mkdir(exist_ok=True)
        text = "".join(f"{'[MARK] ' if m else ''}{t}" for t, m in self.last["cached"]) + self.last["text"]
        (self.out / "prompts" / f"{name}.txt").write_text(text, encoding="utf-8")


def fake_answer(kind: str, target: str, m: Moment, k: int) -> dict:
    """The dry run's answer: what the line should go to; every 7th call a new loop instead."""
    if k % 7 == 6 and kind != "new_loop":
        kind, target = "new_loop", ""
    base = {"reason": f"dry, so: {kind} {target}".strip(), "to": kind, "loop": "", "hand_offs": [], "follow_ups": [],
            "reply": ""}
    if kind == "loop":
        base["loop"] = target
    elif kind == "implementer":
        base["follow_ups"] = [{"task": target, "description": f"(dry) {short(m.words, 80)}",
                               "brief": f"(dry) {short(m.words, 120)}", "cancel": False}]
    elif kind == "new_implementer":
        base["hand_offs"] = [{"name": target, "description": f"(dry) {short(m.words, 80)}", "words": m.words,
                              "picture": "(dry)", "assumed": "(dry)", "cast": "(dry)", "done_when": ["(dry) it works"]}]
    elif kind == "nowhere":
        base["reply"] = "Still on it."
    return base


def dry_name(m: Moment) -> str:
    return f"{slug(m.words, 2)}_{player_short(m.who)}"[:32].strip("_")


# ====================================================================== the runs

def results_row(m: Moment, got: dict, extra: dict) -> dict:
    row = {"set": m.set, "n": m.n, "seq": m.seq, "who": m.who, "words": m.words, "at": m.at, "label": m.label,
           "expect": m.expect, "truth": m.truth}
    row.update(got)
    row.update(extra)
    return row


def recorded(out: Path, resume: bool) -> dict[int, dict]:
    """--resume: the rows a run into `out` already has with an answer, by the line's number; the
    file is written again with those alone, so the failed calls are asked again."""
    path = out / "results.jsonl"
    if not resume or not path.exists():
        return {}
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    good = {r["n"]: r for r in rows if "answer" in r and not r.get("skipped") and "error" not in r}
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in good.values()), encoding="utf-8")
    say(f"  resuming: {len(good)} answers kept, {len(rows) - len(good)} rows asked again")
    return good


async def run_playtest(args, rig: Rig, out: Path, data: Path) -> None:
    from brain import tasks as tasks_mod
    ps = PlaytestSet(data)
    kept = recorded(out, args.resume)
    moments = [m for m in select(ps.moments(), args) if m.n not in kept]
    say(f"playtest: {len(ps.tasks)} tasks rebuilt, {len(moments)} lines to route")
    (out / "playtest_set.json").write_text(json.dumps(
        {"tasks": [{"name": k["name"], "player": k["player"], "chain": k["chain"], "scripts": sorted(k["scripts"])}
                   for k in ps.tasks.values()], "left_out": ps.left}, indent=1))
    with (out / "results.jsonl").open("a", encoding="utf-8") as f:
        for k, m in enumerate(moments):
            ps.until(m.at)
            row = m.row
            rows = ps.visible(m.at)
            board = tasks_mod.board(rows, {}, [r["name"] for r in rows if r["busy"]], today=m.at[:10])
            names = [r["name"] for r in rows]
            under_way = "WORK UNDER WAY: no brain loops"
            fus = [x for x in m.truth if x["kind"] == "follow_up"]
            if fus:
                fake = fake_answer("implementer", fus[0]["right"][0], m, k)
            else:
                fake = fake_answer("new_implementer" if any(x["implementer"] for x in m.truth) else "new_loop",
                                   dry_name(m), m, k)
            got = await rig.route(m, row, ps.ctx, ps.seen(m.at, m.who), board, under_way, [], names, fake)
            if k % PROMPT_EVERY == 0:
                rig.keep_prompt(f"playtest-{m.n}")
            f.write(json.dumps(results_row(m, got, {"tasks": names, "loops": []}), ensure_ascii=False) + "\n")
            f.flush()
            report(m, got)
            if rig.spent > args.budget:
                say(f"stopping: ${rig.spent:.4f} spent, over --budget {args.budget}")
                break


async def run_night(args, rig: Rig, out: Path, data: Path) -> None:
    from brain import tasks as tasks_mod
    ns = NightSet(data)
    sim = NightSim(ns, args.think_s)
    say(f"night: {len(ns.lines)} lines to the AI; box tasks {', '.join(f'{b} (#{s})' for b, s in ns.origin.items())}; "
        f"loops: {'brain/brain/loops.py' if sim.real else 'the stand-in'}")
    moments = ns.moments()
    chosen = {m.seq for m in select(moments, args)}
    kept = getattr(args, "kept", None) or recorded(out, args.resume)
    with (out / "results.jsonl").open("a", encoding="utf-8") as f:
        for k, m in enumerate(moments):
            sim.tick(m.at)
            sim.context_to(m.seq)
            row = next(r for r in sim.ctx.rows if r.seq == m.seq)
            rows = sim.tasks_at()
            board = tasks_mod.board(rows, {}, [r["name"] for r in rows if r.get("busy")], today=NIGHT_DAY)
            names = [r["name"] for r in rows]
            offered = sim.offered()
            under_way = sim.loops.show()
            want = expectation(m, sim)
            fake = fake_answer(*ideal(m, want, sim, offered, names), m, k)
            if m.seq in kept:   # --resume: placed as its recorded answer, not asked again
                got = {k: kept[m.seq][k] for k in kept[m.seq] if k in ("answer", "ms", "cost", "prompt_tokens",
                                                                      "cached_tokens", "completion_tokens", "wall_ms")}
                got["kept"] = True
            elif m.seq in chosen:
                got = await rig.route(m, row, sim.ctx, ns.seen(m.seq, m.who), board, under_way, offered, names, fake)
            else:   # outside --only/--limit: placed as the dry answer would, so the later lines see a night
                got = {"answer": fake, "skipped": True}
            a = got.get("answer") or {"to": "new_loop", "reason": "(router failed: placed as a new loop)"}
            tasks = [f.get("task", "") for f in a.get("follow_ups") or []] if a.get("to") == "implementer" else []
            hand = (a.get("hand_offs") or [None])[0] if a.get("to") == "new_implementer" else None
            placed = sim.place(m, row, a.get("to", "new_loop") if "answer" in got else "new_loop", a.get("loop", ""),
                               tasks, hand)
            dest = sim.placed.get(m.seq, [])
            extra = {"tasks": names, "loops": offered, "under_way": under_way, "want": want, "placed": placed,
                     "dest_work": sorted(set().union(*(sim.work.group(d) for d in dest))) if dest else []}
            if m.seq in chosen and (m.seq not in kept or getattr(args, "kept", None)):
                if k % PROMPT_EVERY == 0 and m.seq not in kept:
                    rig.keep_prompt(f"night-{m.seq}")
                f.write(json.dumps(results_row(m, got, extra), ensure_ascii=False) + "\n")
                f.flush()
                report(m, got)
            if rig.spent > args.budget:
                say(f"stopping: ${rig.spent:.4f} spent, over --budget {args.budget}")
                break


def expectation(m: Moment, sim: NightSim) -> dict:
    """What the line should go to, now: its kind, and for ask/thing the work it should join (the
    loops and tasks the replay put the earlier lines of its ask, or about its thing, in)."""
    e = m.expect
    kind = e.get("kind", "talk")
    out = {"kind": kind, "bar": e.get("bar", ""), "nowhere_ok": bool(e.get("nowhere")), "work": [], "fallback": False}
    if kind in ("ask", "thing", "thing_or_new"):
        key = "ask" if kind == "ask" else "thing"
        earlier = [s for s, x in NIGHT.items() if s < m.seq and x.get(key) == e.get(key)]
        work = sim.work_of(earlier)
        out["work"] = sorted(work)
        if not work:
            out["fallback"] = True
    return out


def ideal(m: Moment, want: dict, sim: NightSim, offered: list[str], names: list[str]) -> tuple[str, str]:
    """The dry run's right answer for a night line: (to, the loop or task)."""
    kind = want["kind"]
    if kind == "talk":
        return "nowhere", ""
    if kind == "new" or want["fallback"]:
        return "new_loop", ""
    loops = [w for w in want["work"] if w in offered]
    tasks = [w[5:] for w in want["work"] if w.startswith("task:") and w[5:] in names]
    if loops:
        return "loop", loops[-1]
    if tasks:
        return "implementer", tasks[-1]
    return ("new_loop", "") if kind == "thing_or_new" else (("nowhere", "") if want["nowhere_ok"] else ("new_loop", ""))


def select(moments: list[Moment], args) -> list[Moment]:
    if args.only:
        want = {int(x) for x in re.split(r"[,\s]+", args.only) if x.strip().isdigit()}
        moments = [m for m in moments if m.n in want]
    if args.limit:
        moments = moments[:args.limit]
    return moments


def report(m: Moment, got: dict) -> None:
    a = got.get("answer") or {}
    where = a.get("loop") or (", ".join(f.get("task", "") for f in a.get("follow_ups") or []) if a.get("to") == "implementer" else
                              (a.get("hand_offs") or [{}])[0].get("name", "") if a.get("to") == "new_implementer" else "")
    rate = got.get("cached_tokens", 0) / got["prompt_tokens"] if got.get("prompt_tokens") else 0
    say(f"{m.set} #{m.n} {got.get('ms', got.get('wall_ms', 0))} ms ${got.get('cost', 0):.4f} "
        f"{got.get('prompt_tokens', 0)} in {rate:.0%} cached | {a.get('to', 'ERROR ' + got.get('error', ''))} {where} "
        f"| {short(m.words, 60)!r}")


def rescore(src: Path, dest: Path, data: Path) -> None:
    """A run's answers judged again as the set is built and labelled now: no model. The playtest:
    each row's `truth` is replaced by the line's truth now; a row whose line is no longer in the set
    is dropped (the tasks the router was shown were the old run's). The night: the night is played
    again with every line placed by its recorded answer, and each row's `want` computed again from
    NIGHT (the WORK UNDER WAY and TASKS it shows are what the run showed)."""
    meta = json.loads((src / "meta.json").read_text())
    if meta.get("set") == "night":
        rows = [json.loads(x) for x in (src / "results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        kept = {r["n"]: r for r in rows if "answer" in r}
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "results.jsonl").unlink(missing_ok=True)
        args = SimpleNamespace(only="", limit=0, think_s=meta.get("think_s", 15.0), budget=1.0, resume=False,
                               kept=kept)
        asyncio.run(run_night(args, SimpleNamespace(spent=0.0), dest, data))
        meta.update(rescored_from=str(src), rescored=datetime.now(UTC).isoformat(timespec="seconds"))
        (dest / "meta.json").write_text(json.dumps(meta, indent=1))
        say(f"{len(kept)} answers placed again in {dest}")
        return
    ps = PlaytestSet(data)
    now = {m.n: m.truth for m in ps.moments()}
    rows = [json.loads(x) for x in (src / "results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    dest.mkdir(parents=True, exist_ok=True)
    kept = 0
    with (dest / "results.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            if r["n"] in now:
                r["truth_then"], r["truth"] = r["truth"], now[r["n"]]
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                kept += 1
    meta.update(rescored_from=str(src), rescored=datetime.now(UTC).isoformat(timespec="seconds"))
    (dest / "meta.json").write_text(json.dumps(meta, indent=1))
    (dest / "playtest_set.json").write_text(json.dumps(
        {"tasks": [{"name": k["name"], "player": k["player"], "chain": k["chain"], "scripts": sorted(k["scripts"])}
                   for k in ps.tasks.values()], "left_out": ps.left}, indent=1))
    say(f"{kept} of {len(rows)} rows judged again in {dest}")


def list_set(name: str, data: Path) -> None:
    """The moments, their labels and what they should go to, without a model."""
    if name == "playtest":
        ps = PlaytestSet(data)
        ms = ps.moments()
        print(f"{len(ps.tasks)} tasks rebuilt; {len(ms)} lines")
        for m in ms:
            its = "; ".join(f"{x['id']} {x['kind']} {'/'.join(x['scripts'])}"
                            + (f" -> {','.join(x['right'])}" if x.get("right") else "") for x in m.truth)
            print(f"#{m.n} {m.at[11:19]} {m.who}: {short(m.words, 70)} || {its}")
        return
    ns = NightSet(data)
    for m in ns.moments():
        e = m.expect
        print(f"#{m.seq} {m.at[11:19]} {m.label} {e.get('kind')} ask={e.get('ask', '-')} thing={e.get('thing', '-')} "
              f"{e.get('bar', '')} | {m.who}: {short(m.words, 70)}")


def git_head() -> str:
    r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "brain"], capture_output=True,
                           text=True).stdout.strip()
    return r.stdout.strip() + (" (brain/ changed)" if dirty else "")


async def main_async(args) -> None:
    data = args.data
    sets = ["playtest", "night"] if args.set == "both" else [args.set]
    for s in sets:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        label = "dry" if args.dry else f"{(args.model or V41).split('/')[-1]}-{args.reasoning or 'off'}"
        out = Path(args.out) / s if args.out and len(sets) > 1 else Path(args.out) if args.out else \
            data / "runs" / f"{s}-{label}-{stamp}"
        out.mkdir(parents=True, exist_ok=True)
        os.environ["VFG_USAGE_QUEUE"] = str(out.resolve())   # this process's calls queue in OUT, never a box's
        rig = Rig(args, out)
        prompt = REPO / "brain" / "prompts" / "router.md"
        meta = {"set": s, "started": datetime.now(UTC).isoformat(timespec="seconds"), "dry": args.dry,
                "router": "brain/brain/router.py" if rig.mod else "stand-in (router.py not there)",
                "model": "dry (no model)" if args.dry else rig.model, "reasoning": rig.reasoning,
                "providers": ",".join(rig.providers), "timeout_s": rig.timeout, "owner": rig.cfg.owner,
                "think_s": args.think_s, "take_up_s": TAKE_UP_S, "said": SAID, "head": git_head(),
                "prompt_sha": hashlib.sha256(prompt.read_bytes()).hexdigest()[:12] if prompt.exists() else None,
                "only": args.only, "limit": args.limit, "budget": args.budget,
                "commands": "loaded" if rig.refs is not None else "not loaded"}
        (out / "meta.json").write_text(json.dumps(meta, indent=1))
        say(f"{s}: OUT {out}; {meta['model']} {meta['reasoning']} on {meta['providers']}; router {meta['router']}")
        import httpx
        import vfg_llm
        async with httpx.AsyncClient(timeout=rig.timeout) as http:
            vfg_llm.client = http
            try:
                if s == "playtest":
                    await run_playtest(args, rig, out, data)
                else:
                    await run_night(args, rig, out, data)
            except CreditGone as e:
                meta["stopped"] = f"402 twice, {CREDIT_WAIT_S:.0f} s apart: {e}"
                say(f"{s}: STOPPED, no credit (402 twice): go on later with --resume --out {out}")
                stopped = True
            else:
                stopped = False
            finally:
                vfg_llm.client = None
        meta.update(ended=datetime.now(UTC).isoformat(timespec="seconds"), calls=rig.calls, spent=round(rig.spent, 6))
        (out / "meta.json").write_text(json.dumps(meta, indent=1))
        say(f"{s}: {rig.calls} calls, ${rig.spent:.4f}; score: python3 integration/replay/router/score.py {out}")
        score = REPLAY / "router" / "score.py"
        if score.exists():
            subprocess.run([sys.executable, str(score), str(out)], check=False)
        if stopped:
            break


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA, help="the unpacked data (default "
                    "$VFG_ROUTER_REPLAY_DATA, else ~/.vfg/router-replay)")
    ap.add_argument("--unpack", action="store_true", help="put the night's and the playtest's data in --data")
    ap.add_argument("--list", choices=("playtest", "night"), help="the moments, no model")
    ap.add_argument("--set", choices=("playtest", "night", "both"), default="both")
    ap.add_argument("--dry", action="store_true", help="a fake model: no call, no key")
    ap.add_argument("--model", default="", help="the router's model (default: VFG_ROUTER_MODEL's)")
    ap.add_argument("--reasoning", default="", help="off or an effort (default: VFG_ROUTER_REASONING's)")
    ap.add_argument("--providers", default=None, help="hosts in order, comma-separated (default: the router's)")
    ap.add_argument("--timeout", type=float, default=0, help="seconds a call may take (30; 60 when it reasons)")
    ap.add_argument("--think-s", type=float, default=15.0, help="seconds a simulated loop thinks (15)")
    ap.add_argument("--only", default="", help="these line numbers alone (LINE#n), comma-separated")
    ap.add_argument("--limit", type=int, default=0, help="the first N lines of each set")
    ap.add_argument("--budget", type=float, default=1.0, help="stop a set once it has spent this many dollars (1)")
    ap.add_argument("--out", default="", help="where the run goes (default DATA/runs/<set>-<model>-<time>)")
    ap.add_argument("--rescore", default="", help="a run's folder: its answers judged again on the set as built "
                    "and labelled now, into --out; no model")
    ap.add_argument("--resume", action="store_true", help="into an --out that has rows: keep the answered ones "
                    "(the night places its lines by them) and ask the failed ones again")
    args = ap.parse_args()
    if args.unpack:
        unpack(args.data)
        return
    if args.list:
        list_set(args.list, args.data)
        return
    if args.rescore:
        if not args.out:
            ap.error("--rescore needs --out, the folder for the run judged again")
        rescore(Path(args.rescore), Path(args.out), args.data)
        return
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
