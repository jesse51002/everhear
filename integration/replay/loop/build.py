#!/usr/bin/env python3
"""The dataset of 2026-10-11 for the brain loop's replay (Prototype_Brain_Loop, _Test_): the 110
brain calls of that night, in order, each with the feed it was answered in and what it cost then.

    python3 integration/replay/loop/build.py            # writes the files beside it
    python3 integration/replay/loop/build.py --check    # builds in memory, checks, writes nothing

Run once; what it writes is the replay's input, and integration/loop_replay.py reads only that.

**The session.** One player, KrownJM, 05:36–06:59 UTC on the brain of branch brain-incantation-fix
(before Named Tasks): GPT-6.1 Sol, low, one call per event; tasks were numbered T<n>, and every line
the brain acted on got one, actions included. Its brain made 110 calls with a usage row, $1.18: 69
for lines (action_oriented ones, and conversational ones Respond handed up) and 41 for implementer
reports (6 tasks: T207, T211, T212, T215, T216, T217; each first run and its 35 follow-ups). Four
more line calls failed the answer's checks twice and wrote no usage row (#19, #21, #25, #31): they
are kept in session.json's `failed_calls`, never replayed.

**Where each piece comes from, read-only:**
- `vfg.db` of the box, from `~/Documents/MinecraftServers/aws-backup/vfg-backup-2026-10-11.tgz`:
  ONLY the member `vfg/vfg.db` is read, through tarfile, into a temporary folder that is removed
  after. The archive also holds the box's `.env`, which is never extracted, read or printed.
  Its `feed` (every row, its processed mark), `lines` (when each LINE was fed: its `fed_at`) and
  `usage` (the brain's 110 rows: tokens, cached tokens, cost; this table has no `ms` yet).
- `~/.vfg/brain/brain.log`, that night's brain on this PC: each call's ms (its `brain <ref> <ms>
  ms` line), its decision (`-> <parts> || reply: …`), Respond's calls and hand-ups, the tasks
  started and followed up, the actions it ran, the "stays open" of a report whose task had a
  follow-up queued.
- `~/.vfg/brain/tasks.json`: each task's player, the line that opened it and its first brief.
- The implementers' transcripts, `~/.claude/projects/-var-home-jm-Documents-EverHear--worktrees-
  brain-incantation-fix/<session>.jsonl`, one per task: each run's brief (the prompt that started
  or resumed it: a follow-up is `FOLLOW-UP to T207, from the brain: <brief>`), its report (the
  run's last text, which the SDK returns as its result), what it made (made/main/ files), and the
  incantations it stored (`vfg action run incantation_create|edit|remove …` that answered ok).
  Nothing else of a transcript is read: no system prompt, no attachment.
- `made/main/` of the brain-incantation-fix worktree: the actions and scripts that night's
  implementers made, copied into `made/` here as they were at the end of the night.

**What it writes, beside this file:**
- `feed.jsonl`: every feed row of the night, in seq order: `seq`, `at`, `line` (as the box wrote
  it), `processed_at`, and `fed`, when the row was in the feed (a LINE's `lines.fed_at`, which
  comes after it was said; any other row's own time).
- `events.jsonl`: the 110 events in the order their calls started. Each: `n`, `kind` (`line` or
  `report`), `ref` (the usage row's: `#<seq>` or the task), `at` and `ended` (the call), `class`
  (what the night's call did: `talk`, `actions`, `hand-off`, or `report`), `context_to` (the last
  feed seq the call could read: every row in the feed when it started, the line's own at least),
  `new` (the seqs of the rows no earlier call had read, which the brain shows as NEW: each call,
  Respond's too, takes in what is unread when it starts; a hand-up keeps Respond's read), and
  `original`: the night's decision line, its reply, the actions it ran with their results, the
  hand-offs it made (`task`, `kind` new or follow-up, `run`, the full `brief`), and its usage
  (`cost`, `input_tokens`, `cached_tokens`, `output_tokens`, `ms`). A line: `seq`, `player`,
  `words`, `jev` (its class), `source`, `handed_up` (Respond's reason, for a conversational
  line). A report: `task`, `run` (`new`, `fu1` …), `status`, `report` (the implementer's whole
  report; the night's had no DESCRIPTION line, so `description` is empty), `following` (a
  follow-up to the task was queued, so the task stayed open), `made`, `implementer_ended`, and
  `task_row` (`player`, `line`, the run's `brief`).
- `world.jsonl`: what the night's implementers did to the world, in time order, which the replay
  does itself at those times since its implementers start nothing: `made` (an action and its
  script first saved: copy them from `made/`) and `vfg` (an incantation stored, edited or
  removed: the `vfg` argv, its `--task` left off).
- `session.json`: the counts and the cost (checked against the spec: 110 calls, 21 talk, 16
  actions, 32 hand-off, 41 reports, $1.18), the six tasks (player, line, first brief, every run,
  `fallback_name`: what the replay calls one when the loop did not open it), the five misread
  asks of _The hand-off is a brief_ (T207 fu1 and fu4, T211 fu1, T212 fu1, T216 fu1, in
  Prototype_Implementer_Harness's numbering: the k-th follow-up run of the task), the failed
  calls, KrownJM's items at the start, and where every source was.

Player names are as they were: it is the owner's own test data. No key, token or address is
written; the build fails if one looks to be.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
BACKUP = HOME / "Documents" / "MinecraftServers" / "aws-backup" / "vfg-backup-2026-10-11.tgz"
MEMBER = "vfg/vfg.db"
BRAIN_LOG = HOME / ".vfg" / "brain" / "brain.log"
TASKS_JSON = HOME / ".vfg" / "brain" / "tasks.json"
TRANSCRIPTS = HOME / ".claude" / "projects" / "-var-home-jm-Documents-EverHear--worktrees-brain-incantation-fix"
MADE = HOME / "Documents" / "EverHear" / ".worktrees" / "brain-incantation-fix" / "made" / "main"
DAY, START, END = "2026-10-11", "2026-10-11T05:30:00", "2026-10-11T07:05:00"

# What the spec says the night was (Prototype_Brain_Loop, _Most asks are one step_ and _Cost_).
SPEC = {"calls": 110, "talk": 21, "actions": 16, "hand-off": 32, "report": 41, "cost": 1.18}

# What the replay calls a task when the loop did not open one where the night did: the name of
# what the night's implementer made, else of what it was (T215 made nothing). `ice_beam` is a
# library action's name, which no task may take, so T217 is its action's, ice_beam_cast.
FALLBACK = {"T207": "hot_fireball", "T211": "high_step", "T212": "destroy_everything",
            "T215": "voice_latency", "T216": "speed_dash", "T217": "ice_beam_cast"}

# The five misread asks (Prototype_Implementer_Harness, _The 35 follow-ups_; Prototype_Brain_Loop,
# _The hand-off is a brief_): the follow-up run that fixed what the first build read wrong, what
# was missed, in the spec's words, and words a done-when list that names it would use (a
# heuristic for the report; the lists themselves are shown beside it for a person to judge).
MISREADS = [
    {"task": "T207", "run": "fu1",
     "missed": "the fireball passed through mobs: it should stop at the first mob it meets",
     "named": r"\bmobs?\b"},
    {"task": "T207", "run": "fu4",
     "missed": "it broke no blocks: it should explode where it hits and the blast break blocks",
     "named": r"break\w*\W+(\w+\W+){0,3}blocks?|blocks?\W+(\w+\W+){0,3}(break|broken|destroy)"},
    {"task": "T211", "run": "fu1",
     "missed": "High Step teleported him into the sky instead of a jump up and a slow float down",
     "named": r"teleport|\bjump|velocity|momentum|\bfloat"},
    {"task": "T212", "run": "fu1",
     "missed": "the nuke was bound without the five seconds of fire before the blast that was asked for",
     "named": r"\bfire\b|flame|burn|ignit"},
    {"task": "T216", "run": "fu1", "missed": "Speed Dash was a Speed effect, not a launch forward",
     "named": r"launch|velocity|momentum|knockback|push|propel|fling|(not|no|never)\W+(\w+\W+){0,3}speed effect"},
]

# KrownJM's items when the replay starts: a plausible kit for the night, what he carried at 06:13
# (#216's reply) less what the brain gave him before then, and an axe, so the spec's asks have
# something to act on. Slots as `item replace entity` takes them.
SETUP = {"player": "KrownJM", "items": {
    "hotbar.0": "minecraft:iron_axe", "hotbar.1": "minecraft:stone_pickaxe", "hotbar.2": "minecraft:cooked_beef 32",
    "hotbar.3": "minecraft:dirt 48", "hotbar.4": "minecraft:wheat_seeds 27",
    "inventory.0": "minecraft:hay_block 5", "inventory.1": "minecraft:obsidian 7",
    "inventory.2": "minecraft:dandelion 7",
    "inventory.3": "minecraft:poppy", "inventory.4": "minecraft:string 2", "inventory.5": "minecraft:spider_eye",
    "inventory.6": "minecraft:leather", "inventory.7": "minecraft:beef",
    "armor.head": "minecraft:golden_helmet[enchantments={respiration:1}]",
    "armor.legs": "minecraft:golden_leggings[enchantments={fire_protection:4}]"}}

# brain.log, the brain of that night (brain/loop.py and brain/act.py of brain-incantation-fix).
LOG = re.compile(r"^(?P<t>\d{4}-\d\d-\d\dT[\d:.]+Z) (?P<area>\S+) (?P<msg>.*)$")
SPENT = re.compile(r"^(?P<ref>#\d+|T\d+) (?P<ms>\d+) ms \$(?P<cost>[\d.]+)\+?, (?P<in>\d+) in, (?P<pct>\d+)% cached, "
                   r"(?P<out>\d+) out$")
DECIDED_LINE = re.compile(r"^#(?P<seq>\d+) (?P<who>\S+): (?P<words>.*) -> (?P<said>.*)$")
DECIDED_REPORT = re.compile(r"^(?P<task>T\d+) report \((?P<status>done|failed)\) -> (?P<said>.*)$")
FAILED_CALL = re.compile(r"^#(?P<seq>\d+) FAILED(?: \((?P<spent>[^)]*)\))?: (?P<why>.*)$")
RESPOND_SPENT = re.compile(r"^#(?P<seq>\d+) (?P<ms>\d+) ms ")
RESPOND_FAILED = re.compile(r"^#(?P<seq>\d+) FAILED \((?P<ms>\d+) ms")
HANDED_UP = re.compile(r"^#(?P<seq>\d+) handed up: (?P<why>.*)$")
TASK_STARTED = re.compile(r"^(?P<task>T\d+) started for (?P<who>\S+): (?P<brief>.*)$")
TASK_FOLLOW = re.compile(r"^(?P<task>T\d+) follow-up: (?P<brief>.*)$")
TASK_OPEN = re.compile(r"^(?P<task>T\d+) stays open: ")
ACTION_RAN = re.compile(r"^(?P<task>T\d+) #\d+ (?P<action>\S+) (?P<state>ok|failed|dropped)\b(?: in [\d.]+ s)?: "
                        r"(?P<result>.*)$")
IMPL_ENDED = re.compile(r"^(?P<task>T\d+) (?P<status>done|failed): ")
FOLLOW_UP_PROMPT = re.compile(r"^FOLLOW-UP to (?P<task>T\d+), from the brain: (?P<brief>.*)$", re.S)
# The runner's own line under every follow-up's brief, not the brain's: left off.
RUNNER_TAIL = re.compile(r"\n+Do it as before, check it once, and end with a new report\.\s*$")
INCANTATION = re.compile(r"vfg action run (incantation_(?:create|edit|remove))\b")
STORED = re.compile(r'\b(stored|changed|removed) \\?"')
MADE_PATH = re.compile(r"made/main/(actions|scripts|presets)/([a-z][a-z0-9_]*)")
# What must never be in the output: an OpenRouter, Anthropic or OpenAI key, a bearer token, an
# address, or a key's name with a value after it.
SECRET = re.compile(r"sk-or-v1-|sk-ant-|\bsk-[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9._-]{20,}|[\w.+-]+@[\w-]+\.[\w.]+"
                    r"|(OPEN_ROUTER_API|TYPESAFE_API_KEY|API_KEY)\s*=\s*\S")


def ts(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def iso_minus(iso: str, ms: int) -> str:
    """`iso` less `ms` milliseconds: a call's start from its end and its ms."""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")) - timedelta(milliseconds=ms)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def in_session(t: str) -> bool:
    return START <= t[:19] <= END


# ------------------------------------------------------------------ the box's database

def read_db(backup: Path) -> tuple[list[dict], dict, list[dict]]:
    """The feed, each LINE's fed_at by (player, at), and the brain's usage rows, from the one member
    vfg/vfg.db of the backup, read through tarfile into a temporary folder removed after: the
    archive's .env is never extracted."""
    with tarfile.open(backup, "r:gz") as tar, tempfile.TemporaryDirectory(prefix="loop-build-") as tmp:
        member = tar.getmember(MEMBER)
        src = tar.extractfile(member)
        if src is None:
            sys.exit(f"{MEMBER} in {backup} is not a file")
        db = Path(tmp) / "vfg.db"
        with src, db.open("wb") as f:
            shutil.copyfileobj(src, f)
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            feed = [dict(r) for r in con.execute("SELECT seq, at, line, processed_at FROM feed ORDER BY seq")]
            fed = {(r["player_name"], r["at"]): r["fed_at"] for r in
                   con.execute("SELECT player_name, at, fed_at FROM lines WHERE fed_at IS NOT NULL")}
            usage = [dict(r) for r in con.execute(
                "SELECT id, at, component, model, input_tokens, output_tokens, cost_usd, ref, cache_read_tokens "
                "FROM usage WHERE component = 'brain' ORDER BY id")]
        finally:
            con.close()
    return feed, fed, usage


FEED_HEAD = re.compile(r"^(?P<type>[A-Z]+)#(?P<seq>\d+) (?P<hms>\d\d:\d\d:\d\d)(?: (?P<fields>[^|]*?))? \| "
                       r"(?P<text>.*)$", re.S)


def feed_rows(feed: list[dict], fed: dict) -> list[dict]:
    """Each feed row with `fed`, when it was in the feed: a LINE's fed_at, any other row's own time."""
    out = []
    for r in feed:
        m = FEED_HEAD.match(r["line"])
        kind = m.group("type") if m else ""
        fields = (m.group("fields") or "").split() if m else []
        when = r["at"]
        if kind == "LINE" and fields:
            when = fed.get((fields[0], r["at"])) or r["at"]
        out.append({"seq": r["seq"], "at": r["at"], "fed": when, "processed_at": r["processed_at"], "line": r["line"]})
    return out


def parse_feed(line: str) -> dict:
    m = FEED_HEAD.match(line)
    if not m:
        return {"type": "", "fields": [], "text": line}
    return {"type": m.group("type"), "fields": (m.group("fields") or "").split(), "text": m.group("text")}


# ------------------------------------------------------------------ brain.log

def read_log(path: Path) -> dict:
    """What the night's brain.log says, in the session's window."""
    calls = []          # each brain call that answered: ref, end, ms, decision
    spent: dict[str, list[dict]] = defaultdict(list)
    failed = []
    reads = []          # every call's read, Respond's too: (start, kind, seq)
    handed_up: dict[int, str] = {}
    started, follow, stays, ran, ended = [], [], [], [], []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LOG.match(raw)
        if not m or not in_session(m.group("t")):
            continue
        t, area, msg = m.group("t"), m.group("area"), m.group("msg")
        if area == "brain":
            if s := SPENT.match(msg):
                spent[s.group("ref")].append({"end": t, "ms": int(s.group("ms")), "cost": float(s.group("cost")),
                                              "in": int(s.group("in")), "out": int(s.group("out"))})
            elif d := DECIDED_LINE.match(msg):
                calls.append({"ref": "#" + d.group("seq"), "end": t, "said": d.group("said"),
                              "seq": int(d.group("seq"))})
            elif d := DECIDED_REPORT.match(msg):
                calls.append({"ref": d.group("task"), "end": t, "said": d.group("said"), "status": d.group("status")})
            elif f := FAILED_CALL.match(msg):
                failed.append({"seq": int(f.group("seq")), "end": t, "why": f.group("why")[:300]})
        elif area == "respond":
            if s := RESPOND_SPENT.match(msg) or RESPOND_FAILED.match(msg):
                reads.append({"start": iso_minus(t, int(s.group("ms"))), "kind": "respond", "seq": int(s.group("seq"))})
            if h := HANDED_UP.match(msg):
                handed_up[int(h.group("seq"))] = h.group("why")
            elif "FAILED" in msg and msg.endswith("handed up") and (f := re.match(r"^#(\d+) ", msg)):
                handed_up[int(f.group(1))] = "Respond could not answer"
        elif area == "task":
            if s := TASK_STARTED.match(msg):
                started.append({"t": t, "task": s.group("task"), "who": s.group("who"), "brief": s.group("brief")})
            elif s := TASK_FOLLOW.match(msg):
                follow.append({"t": t, "task": s.group("task"), "brief": s.group("brief")})
            elif s := TASK_OPEN.match(msg):
                stays.append({"t": t, "task": s.group("task")})
        elif area == "action":
            if s := ACTION_RAN.match(msg):
                ran.append({"t": t, "task": s.group("task"), "action": s.group("action"), "state": s.group("state"),
                            "result": s.group("result")})
        elif area == "implementer":
            if s := IMPL_ENDED.match(msg):
                ended.append({"t": t, "task": s.group("task"), "status": s.group("status")})
    return {"calls": calls, "spent": spent, "failed": failed, "reads": reads, "handed_up": handed_up,
            "started": started, "follow": follow, "stays": stays, "ran": ran, "ended": ended}


def decision_parts(said: str) -> tuple[list[dict], dict]:
    """A decision line's parts and its tail (reply, record, status), as brain/call.py's describe()
    wrote them: `<to>: <instruction>` joined by ` | `, then ` || reply: …`. An implementer part is
    `implementer[ T207]: <its first 120 characters>`."""
    head, *tail = said.split(" || ")
    parts = []
    if head != "no parts":
        for p in head.split(" | "):
            to, _, rest = p.partition(": ")
            task = ""
            if to.startswith("implementer"):
                task = to.removeprefix("implementer").strip()
                to = "implementer"
            elif " [dropped: " in to:
                to = to.split(" [dropped: ")[0]
            parts.append({"to": to, "task": task, "instruction": rest})
    out = {}
    for t in tail:
        k, _, v = t.partition(": ")
        if k in ("reply", "record", "status"):
            out[k] = v
    return parts, out


# ------------------------------------------------------------------ the transcripts

def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
    return ""


def read_runs(folder: Path) -> dict[str, list[dict]]:
    """Each task's runs, in order, from its implementer's transcript: the prompt that started or
    resumed it, its report (the run's last text), the made/ names its tools touched, and the
    incantation commands that answered ok. A transcript's task is its first follow-up's T<n>, else
    the one tasks.json names for its session."""
    runs: dict[str, list[dict]] = {}
    for f in sorted(folder.glob("*.jsonl")):
        session = f.stem
        rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
        out, cur, uses = [], None, {}
        for r in rows:
            kind = r.get("type")
            if kind not in ("user", "assistant"):
                continue
            content = r.get("message", {}).get("content")
            if kind == "user" and isinstance(content, str):
                cur = {"session": session, "started": r["timestamp"], "prompt": content, "report": "", "reported": "",
                       "made": defaultdict(list), "incantations": []}
                out.append(cur)
                continue
            if cur is None or not isinstance(content, list):
                continue
            for p in content:
                if not isinstance(p, dict):
                    continue
                if kind == "assistant" and p.get("type") == "text" and p.get("text", "").strip():
                    cur["report"], cur["reported"] = p["text"].strip(), r["timestamp"]
                elif kind == "assistant" and p.get("type") == "tool_use" and p.get("name") == "Bash":
                    uses[p["id"]] = p.get("input", {}).get("command", "")
                elif kind == "user" and p.get("type") == "tool_result":
                    said = text_of(p.get("content"))
                    for m in MADE_PATH.finditer(said):
                        if m.group(2) not in cur["made"][m.group(1)]:
                            cur["made"][m.group(1)].append(m.group(2))
                    cmd = uses.get(p.get("tool_use_id"), "")
                    if not p.get("is_error") and INCANTATION.search(cmd) and STORED.search(said):
                        cur["incantations"].append({"t": r["timestamp"], "command": cmd, "said": said[:400]})
                    if not p.get("is_error"):
                        for m in MADE_PATH.finditer(said):
                            cur.setdefault("first_made", {}).setdefault((m.group(1), m.group(2)), r["timestamp"])
        task = next((m.group("task") for x in out if (m := FOLLOW_UP_PROMPT.match(x["prompt"]))), None)
        runs[task or session] = out
    return runs


def follow_up_brief(prompt: str) -> str:
    """The brain's brief in a follow-up's prompt: after `FOLLOW-UP to T<n>, from the brain: `, the
    runner's own last line left off."""
    m = FOLLOW_UP_PROMPT.match(prompt)
    return RUNNER_TAIL.sub("", m.group("brief")).strip() if m else ""


def incantation_argvs(command: str) -> list[list[str]]:
    """The `vfg action run incantation_*` commands in one Bash command line, each as vfg's argv
    (no `vfg`, no `--task`, no redirection): the line split at ;, && and |, each piece by shlex."""
    out = []
    for piece in re.split(r"&&|;|\|\|?|\n", command):
        piece = piece.strip()
        if not INCANTATION.match(piece):
            continue
        try:
            words = shlex.split(piece)
        except ValueError:
            continue
        argv, skip = [], False
        for w in words[1:]:
            if skip:
                skip = False
                continue
            if w == "--task":
                skip = True
                continue
            if re.match(r"^\d?>", w) or w in ("2>&1",):
                continue
            argv.append(w)
        out.append(argv)
    return out


# ------------------------------------------------------------------ the build

def build(args) -> dict:
    feed_raw, fed, usage = read_db(Path(args.backup))
    feed = feed_rows(feed_raw, fed)
    by_seq = {r["seq"]: r for r in feed}
    log = read_log(Path(args.log))
    tasks_json = json.loads(Path(args.tasks).read_text(encoding="utf-8")).get("tasks", {})
    runs = read_runs(Path(args.transcripts))
    problems: list[str] = []

    # -- every brain call with its usage row: the rows in order of their id, a ref's calls in order
    rows_of: dict[str, list[dict]] = defaultdict(list)
    for u in usage:
        rows_of[u["ref"]].append(u)
    calls_of: dict[str, list[dict]] = defaultdict(list)
    for c in log["calls"]:
        calls_of[c["ref"]].append(c)
    events = []
    for ref, calls in calls_of.items():
        spent = log["spent"].get(ref, [])
        rows = rows_of.get(ref, [])
        if not (len(calls) == len(spent) == len(rows)):
            problems.append(f"{ref}: {len(calls)} decisions, {len(spent)} spent lines, {len(rows)} usage rows")
        for c, s, u in zip(calls, spent, rows, strict=False):   # a count that differs is a problem, above
            if abs(float(u["cost_usd"]) - s["cost"]) > 1e-6 or u["input_tokens"] != s["in"]:
                problems.append(f"{ref} at {c['end']}: brain.log says ${s['cost']} {s['in']} in, the usage row "
                                f"${u['cost_usd']} {u['input_tokens']} in")
            events.append({"call": c, "ms": s["ms"], "usage": u})
    events.sort(key=lambda e: (iso_minus(e["call"]["end"], e["ms"]), e["call"]["end"]))
    missing = sorted(set(rows_of) - set(calls_of))
    if missing:
        problems.append(f"usage rows with no call in brain.log: {missing}")

    # -- the runs of each task, and its reports in order
    report_k: Counter = Counter()
    follow_by_task: dict[str, list[dict]] = defaultdict(list)
    for f in log["follow"]:
        follow_by_task[f["task"]].append(f)

    def run_label(k: int) -> str:
        return "new" if k == 0 else f"fu{k}"

    def find_run(task: str, brief_head: str) -> tuple[int, dict] | None:
        """The run of `task` whose prompt is this follow-up's brief (brain.log cuts it at about 300
        characters with …)."""
        head = brief_head.rstrip("…").strip()[:200]
        for k, run in enumerate(runs.get(task, [])):
            m = FOLLOW_UP_PROMPT.match(run["prompt"])
            if m and m.group("brief").strip().startswith(head):
                return k, run
        return None

    def hand_offs_at(t_end: str) -> list[dict]:
        """The tasks a call started or followed up: brain.log's task lines at its decision's time."""
        out = []
        for s in log["started"]:
            if abs(ts(s["t"]) - ts(t_end)) < 0.05:
                full = tasks_json.get(s["task"], {}).get("task") or s["brief"]
                out.append({"task": s["task"], "kind": "new", "run": "new", "brief": full})
        for f in log["follow"]:
            if abs(ts(f["t"]) - ts(t_end)) < 0.05:
                found = find_run(f["task"], f["brief"])
                if found is None:
                    problems.append(f"no run of {f['task']} has the follow-up {f['brief'][:80]!r}")
                    out.append({"task": f["task"], "kind": "follow-up", "run": "?", "brief": f["brief"]})
                    continue
                k, run = found
                out.append({"task": f["task"], "kind": "follow-up", "run": run_label(k),
                            "brief": follow_up_brief(run["prompt"])})
        return out

    def actions_at(t_end: str) -> list[dict]:
        """The actions a line's call ran, with their results: brain.log's action lines of the task
        that call opened for them (that brain gave every line it acted on a T<n>, made at the moment
        of its decision), each with its instruction, the task's in tasks.json."""
        mine = {k for k, t in tasks_json.items() if t.get("at") and abs(ts(t["at"]) - ts(t_end)) < 0.05}
        return [{"action": a["action"], "instruction": tasks_json[a["task"]].get("task", ""), "state": a["state"],
                 "result": a["result"], "task": a["task"]} for a in log["ran"] if a["task"] in mine]

    # -- reads: every call takes in what is unread when it starts (a hand-up keeps Respond's)
    starts = []   # (start, kind, ref)
    for e in events:
        starts.append((iso_minus(e["call"]["end"], e["ms"]), "brain", e["call"]["ref"]))
    for r in log["reads"]:
        starts.append((r["start"], "respond", f"#{r['seq']}"))
    starts.sort()
    seqs_fed = sorted((r["fed"], r["seq"]) for r in feed)

    def context_to(start: str, least: int = 0) -> int:
        upto = [s for fed_at, s in seqs_fed if fed_at <= start]
        return max(upto + [least])

    taken: set[int] = set()
    took: dict[tuple[str, str, str], list[int]] = {}   # (kind, ref, start): the seqs that read took in
    for start, kind, ref in starts:
        least = int(ref[1:]) if ref.startswith("#") else 0
        to = context_to(start, least)
        new = []
        for s in range(1, to + 1):
            row = by_seq.get(s)
            if row is None or s in taken:
                continue
            p = parse_feed(row["line"])
            if p["type"] == "AI":   # the AI's own rows arrive read
                taken.add(s)
                continue
            if p["type"] == "LINE" and len(p["fields"]) > 2 and p["fields"][2] == "context":
                taken.add(s)       # a context line: marked read as it arrives, never shown
                continue
            new.append(s)
            taken.add(s)
        took[(kind, ref, start)] = new

    def took_by(kind: str, ref: str, start: str | None = None) -> list[int] | None:
        """What a read took: a line's Respond or brain read (one each), or a report's at its start."""
        for (k, r, s), new in took.items():
            if k == kind and r == ref and (start is None or s == start):
                return new
        return None

    # -- the events
    out_events = []
    for n, e in enumerate(events, 1):
        c, u, ms = e["call"], e["usage"], e["ms"]
        start = iso_minus(c["end"], ms)
        parts, said = decision_parts(c["said"])
        original = {"decision": c["said"], "reply": said.get("reply", ""), "record": said.get("record", ""),
                    "status": said.get("status", ""), "parts": parts, "hand_offs": hand_offs_at(c["end"]),
                    "usage": {"cost": round(float(u["cost_usd"]), 6), "input_tokens": u["input_tokens"],
                              "cached_tokens": u["cache_read_tokens"] or 0, "output_tokens": u["output_tokens"],
                              "ms": ms, "model": u["model"]}}
        ev = {"n": n, "ref": c["ref"], "at": start, "ended": c["end"]}
        if c["ref"].startswith("#"):
            seq = c["seq"]
            row = by_seq[seq]
            p = parse_feed(row["line"])
            who, source, jev = (p["fields"] + ["", "", ""])[:3]
            kinds = {"implementer" if x["to"] == "implementer" else "talk" if x["to"] == "talk" else "action"
                     for x in parts}
            cls = "hand-off" if "implementer" in kinds else "actions" if "action" in kinds else "talk"
            original["actions"] = actions_at(c["end"]) if cls == "actions" else []
            # A hand-up keeps what Respond's read took in; the brain call reads nothing more.
            new = took_by("respond", c["ref"]) if seq in log["handed_up"] else None
            if new is None:
                new = took_by("brain", c["ref"], start) or []
            ev.update(kind="line", **{"class": cls}, seq=seq, player=who, words=p["text"], jev=jev, source=source,
                      handed_up=log["handed_up"].get(seq, "") if jev == "conversational" else "",
                      context_to=context_to(start, seq), new=[s for s in new if s != seq], original=original)
            if jev == "conversational" and seq not in log["handed_up"]:
                problems.append(f"#{seq} is conversational but brain.log has no hand-up for it")
        else:
            task = c["ref"]
            k = report_k[task]
            report_k[task] += 1
            task_runs = runs.get(task, [])
            if k >= len(task_runs):
                problems.append(f"{task}: report {k + 1} has no run in its transcript")
                continue
            run = task_runs[k]
            first = tasks_json.get(task, {})
            m = FOLLOW_UP_PROMPT.match(run["prompt"])
            brief = follow_up_brief(run["prompt"]) if m else first.get("task", "")
            word = run["report"].split(":", 1)[0].strip().lower()
            if (word.startswith("done") and c["status"] != "done") \
                    or (word.startswith("failed") and c["status"] != "failed"):
                problems.append(f"{task} {run_label(k)}: the report says {word!r}, the call {c['status']}")
            ended = next((x["t"] for x in log["ended"] if x["task"] == task
                          and ts(x["t"]) <= ts(c["end"]) and ts(c["end"]) - ts(x["t"]) < 15), None)
            following = any(x["task"] == task and abs(ts(x["t"]) - ts(c["end"])) < 0.05 for x in log["stays"])
            ev.update(kind="report", **{"class": "report"}, task=task, run=run_label(k), status=c["status"],
                      report=run["report"], description="", following=following,
                      made={kk: list(v) for kk, v in run["made"].items()}, implementer_ended=ended,
                      task_row={"player": first.get("player") or "", "line": first.get("line") or "", "brief": brief},
                      context_to=context_to(start), new=took_by("brain", task, start) or [], original=original)
        out_events.append(ev)

    # -- the world: what the implementers made and stored, when
    world = []
    for task, task_runs in runs.items():
        for k, run in enumerate(task_runs):
            for (kind, name), t in sorted((run.get("first_made") or {}).items(), key=lambda x: x[1]):
                if kind == "actions" and not any(w["kind"] == "made" and w["action"] == name for w in world):
                    world.append({"at": t, "task": task, "run": run_label(k), "kind": "made", "action": name})
            for inc in run["incantations"]:
                for argv in incantation_argvs(inc["command"]):
                    world.append({"at": inc["t"], "task": task, "run": run_label(k), "kind": "vfg", "argv": argv})
    world.sort(key=lambda w: w["at"])

    # -- the tasks, the misreads, the counts
    tasks = {}
    for task, task_runs in sorted(runs.items()):
        first = tasks_json.get(task, {})
        opened = next((e for e in out_events if e["kind"] == "line"
                       and any(h["task"] == task and h["kind"] == "new" for h in e["original"]["hand_offs"])), None)
        tasks[task] = {"player": first.get("player"), "line": first.get("line"), "brief": first.get("task"),
                       "opened_by": opened["seq"] if opened else None, "fallback_name": FALLBACK.get(task),
                       "session": task_runs[0]["session"] if task_runs else None,
                       "runs": [{"run": run_label(k), "started": r["started"], "reported": r["reported"],
                                 "brief": follow_up_brief(r["prompt"]) if k else first.get("task"),
                                 "by": next((f"#{e['seq']}" if e["kind"] == "line" else f"{e['task']} report"
                                             for e in out_events for h in e["original"]["hand_offs"]
                                             if h["task"] == task and h["run"] == run_label(k)), None)}
                                for k, r in enumerate(task_runs)]}
    misreads = []
    for m in MISREADS:
        t = tasks.get(m["task"], {})
        run = next((r for r in t.get("runs", []) if r["run"] == m["run"]), None)
        # The asks that opened the task: its line, and a line before it the call asked a question
        # about instead (T212's #192, which #194 answered).
        asks = [t.get("opened_by")]
        before = [e for e in out_events if e["kind"] == "line" and e["seq"] < (t.get("opened_by") or 0)]
        if before and before[-1]["class"] == "talk" and t.get("opened_by") \
                and t["opened_by"] - before[-1]["seq"] <= 4:
            asks.insert(0, before[-1]["seq"])
        misreads.append({**m, "asks": [a for a in asks if a], "follow_up_by": run and run["by"],
                         "follow_up": run and run["brief"], "words": t.get("line")})
    count = Counter(e["class"] for e in out_events)
    cost = round(sum(float(u["cost_usd"]) for u in usage), 6)
    counts = {"calls": len(out_events), "talk": count["talk"], "actions": count["actions"],
              "hand-off": count["hand-off"], "report": count["report"], "cost": cost,
              "follow_ups": sum(len(r) - 1 for r in runs.values()), "tasks": len(runs)}
    for k, want in SPEC.items():
        got = counts[k]
        if (round(got, 2) if k == "cost" else got) != want:
            problems.append(f"{k}: {got}, the spec says {want}")
    session = {
        "date": DAY, "player": "KrownJM", "counts": counts, "spec": SPEC,
        "window": [out_events[0]["at"], out_events[-1]["ended"]] if out_events else None,
        "model": sorted({u["model"] for u in usage}), "tasks": tasks, "misreads": misreads,
        "failed_calls": log["failed"], "setup": SETUP,
        "sources": {"backup": str(args.backup), "member": MEMBER, "backup_sha256": sha256(Path(args.backup)),
                    "brain_log": str(args.log), "tasks_json": str(args.tasks), "transcripts": str(args.transcripts),
                    "made": str(args.made)},
        "problems": problems}
    return {"feed": feed, "events": out_events, "world": world, "session": session}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--backup", default=str(BACKUP), help="the box's backup of 2026-10-11 (only vfg/vfg.db is read)")
    ap.add_argument("--log", default=str(BRAIN_LOG), help="that night's brain.log")
    ap.add_argument("--tasks", default=str(TASKS_JSON), help="that night's tasks.json")
    ap.add_argument("--transcripts", default=str(TRANSCRIPTS), help="the implementers' transcripts")
    ap.add_argument("--made", default=str(MADE), help="what that night's implementers made (made/main/)")
    ap.add_argument("--out", default=str(HERE), help="where to write (default: beside this file)")
    ap.add_argument("--check", action="store_true", help="build and check, write nothing")
    args = ap.parse_args()
    built = build(args)
    s = built["session"]
    c = s["counts"]
    print(f"{c['calls']} calls: {c['talk']} talk, {c['actions']} actions, {c['hand-off']} hand-off, "
          f"{c['report']} reports; ${c['cost']:.6f}; {c['tasks']} tasks, {c['follow_ups']} follow-ups; "
          f"{len(built['feed'])} feed rows; {len(built['world'])} world changes; "
          f"{len(s['failed_calls'])} failed calls not counted")
    for p in s["problems"]:
        print("  problem: " + p)
    text = {name: "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in built[name])
            for name in ("feed", "events", "world")}
    text["session"] = json.dumps(s, ensure_ascii=False, indent=1) + "\n"
    leaks = [(name, m.group(0)[:12]) for name, t in text.items() for m in SECRET.finditer(t)]
    if leaks:
        print(f"refused: {len(leaks)} things that look like a key or an address, in "
              f"{sorted({n for n, _ in leaks})}; nothing written", file=sys.stderr)
        return 1
    if args.check:
        return 1 if s["problems"] else 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, t in text.items():
        (out / (f"{name}.json" if name == "session" else f"{name}.jsonl")).write_text(t, encoding="utf-8")
    made = Path(args.made)
    if made.is_dir():
        dest = out / "made"
        for kind in ("actions", "scripts", "presets"):
            src = made / kind
            if not src.is_dir():
                continue
            if (dest / kind).exists():
                shutil.rmtree(dest / kind)
            shutil.copytree(src, dest / kind, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"wrote {', '.join(sorted(text))} and made/ in {out}")
    return 1 if s["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
