"""The night of 2026-10-12/13 as the conversation agent would have had it under Prototype_Conversation_
And_Router: what respond_bench.py's `night` set reads. 02:37–03:30 UTC on 10-13, KrownJM (the owner)
and Styrkaeopteryx; the brain on Gemini 3.8 Flash, then GPT-6.1 Sol from 03:20; Respond on DeepSeek
V4 Flash; main at 94e517e.

THE DATA, read and never written: the box's session folder, unpacked from `~/Documents/
MinecraftServers/aws-backup/session-logs-2026-10-13-033024.tgz` (`tar xzf … -C DIR`): `vfg/vfg.db`
(the feed with each row's time, the lines with when Jev fed each, the tasks), and the brain's log of
the night, `brain-log-2026-10-13.log` beside the tgz. The night's speech stays there and in the
bench's own output folder: nothing of it is written into the repo. The analysis and the hand labels
are `session-2026-10-13.md` (§1), in `~/.claude/projects/-var-home-jm-Documents-EverHear/
251d92bc-df8c-436f-8626-ab83aa786880/research/`.

THE LINES: the 64 feed lines Jev sent to the AI that night (class action_oriented or conversational,
two of them typed), each by its feed number (`LINE#58`), with its hand label (LABELS):
- `do`: really said to the AI (T), asking for something in the game: done, changed, fixed, stopped,
  or an answer to the AI's question someone acts on. The bar: every one handed off.
- `look`: T, a question only a look at the game answers ("do I have any incantations?"); the prompt
  hands it off too, but it asks for nothing done, so it is kept apart from the bar.
- `talk`: T and talk: a greeting, an acknowledgement.
- `F`: not said to the AI (between the players, to the stream, about the AI), and `U`: unclear.
`F` and `talk` are the talk lines, whose hand-off costs a router call (no bar); `U` is reported on
its own, since several of those lines were asks after all (#263 "it still gives me a frostor").

THE CONVERSATION of each line is the night's feed up to it: every row up to the line's feed number,
the night's own AI rows (its brain's answers to earlier lines, late as they were) included, a Jev
`context` line kept but never shown (context.py). With `classes="to_ai"` every live line's class
reads `to_ai`, as after Prototype_Addressing_The_AI. Under this spec every line to the AI and every
implementer's report takes in what no call has read, so NEW SINCE THE LAST READ is the rows shown
since the last line to the AI or `AI result` row before it.

THE WORLD: who is online, from the joins and leaves; everything else unknown in this replay (their
places and state, the time, the weather, the copies running, the incantations): the prompt says so.

TASKS, as the box's tasks table had them at the line's moment, rebuilt from the feed: an `AI task
<name> | new: …` row opens a task with that description and an implementer on it, a `follow-up:` row
reopens it with an implementer on it, an `AI result <name> | <name> done|failed: …` row ends the run
with that status; the description an implementer wrote at its report (the brain log's `implementer
<name> made …; description: …`) replaces the first from then on. The player is the table's.

WORK UNDER WAY: the night's brain loops for lines (the brain log's `loop #<n>` lines), each moved to
start when its line was fed plus UNDER_WAY_LAG_S (the conversation agent's and the router's calls:
no queue, as under this spec), and running as long as it ran that night (its own time, never its
wait), in loops.show(brief=True)'s words: "being done now" while it runs; once ended, until
TAKE_UP_S after its start (VFG_LOOP_TAKE_UP_S), "done a moment ago", "passed on to be built a
moment ago" (it handed off) or "failed a moment ago", with its last reply. The brain's stop at
03:20:25 forgets every loop, as a restart does under this spec. A loop for a report is left out:
under this spec a report goes to the conversation agent. So the loops shown are the night's, at
the times they would have run with no queue; what they would have been under this spec (lines the
new agent answers itself have none) is not known. The players with work under way (`busy`, for the
guard): a loop of theirs being done now, or a task of theirs with an implementer on it.

V4 FLASH'S ANSWERS: Respond's 29 calls that night, on the lines Jev classed conversational (the 30th,
#177, was dropped at the 03:20 stop), from the brain log: the ms and cost, and a hand-up with its
reason or a reply with its reason. The guard (`respond guard` lines) fired on none.
"""

from __future__ import annotations

import ast
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (REPO / "brain", REPO / "bridge"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from brain.context import Context, Row, parse  # noqa: E402
from brain.tasks import Tasks  # noqa: E402
from brain.world import Seen  # noqa: E402

LIVE = ("action_oriented", "conversational", "to_ai")   # brain.context.parse reads the first two as to_ai
UNDER_WAY_LAG_S = 2.0   # from a line fed to its loop's start: the conversation agent's call and the router's
TAKE_UP_S = 120.0       # an ended loop is shown until this long after its start (VFG_LOOP_TAKE_UP_S)
WORDS_MAX = 80          # a loop's line in WORK UNDER WAY, in characters (loops.WORDS_MAX)
REPLY_MAX = 120         # a loop's last reply in WORK UNDER WAY, in characters (loops.SAID_MAX)
# WORK UNDER WAY's heading, and the text when nothing is under way, as loops.show(brief=True) writes them
UNDER_WAY_HEAD = "WORK UNDER WAY (what is being done for the players now, or was a moment ago):"
UNDER_WAY_NONE = "WORK UNDER WAY: nothing right now"
ENDED = {"handoff": "passed on to be built a moment ago", "failed": "failed a moment ago"}

# The hand labels of session-2026-10-13.md §1, by feed number; `do`, `look` and `talk` split its T.
LABELS = {
    "do": (14, 32, 34, 58, 60, 63, 86, 87, 90, 113, 115, 116, 128, 144, 154, 162, 174, 180, 183, 200, 201, 203,
           214, 217, 222, 232, 266, 267),
    "look": (4, 52),
    "talk": (5, 54),
    "F": (8, 20, 22, 38, 56, 57, 74, 78, 80, 95, 99, 107, 109, 111, 114, 121, 139, 141, 156, 177, 192, 213, 226,
          227, 233),
    "U": (41, 82, 147, 197, 249, 260, 263),
}
LABEL_OF = {n: lab for lab, ns in LABELS.items() for n in ns}
HAND = {"do": "T", "look": "T", "talk": "T", "F": "F", "U": "U"}

LOOP = re.compile(r"^(\S+) loop #(\d+) (\S+): (\d+) step\(s\), .*?([\d.]+) s, ended (.+)$")
STOP = re.compile(r"^(\S+) stop stopping: ")
START = re.compile(r"^\S+ start pid .*; owner (\S+);")
STEP_REPLY = re.compile(r"^\S+ brain #(\d+) s\d+ .*?\|\| reply: (.*?)(?: \|\| record: | -> )")
MADE = re.compile(r"^(\S+) implementer (\S+) made .*?; description: (.*)$")
RESPOND_USE = re.compile(r"^(\S+) respond #(\d+) (\d+) ms \$([\d.]+), (\d+) in, (\d+)% cached, (\d+) out$")
RESPOND_UP = re.compile(r"^(\S+) respond #(\d+) handed up: (.*)$")
_STR = r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""
RESPOND_SAID = re.compile(rf"^(\S+) respond #(\d+) (\S+): (?:{_STR}) -> (?P<reply>{_STR}) \((?P<reason>.*)\)$")
GUARD = re.compile(r"^(\S+) respond guard #(\d+) ")
TASK_ROW = re.compile(r"^(new|follow-up): (.*)$", re.S)
RESULT_ROW = re.compile(r"^(\S+) (done|failed): (.*)$", re.S)


def when(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def short(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


@dataclass
class Loop:
    """One of the night's brain loops for a line, moved to this spec's timing."""
    n: int
    who: str
    words: str
    start: datetime
    end: datetime
    ended: str
    reply: str


@dataclass
class Call:
    """What the conversation agent is given for one line: answer()'s arguments, and its label."""
    n: int
    label: str
    at: str                 # when the line was fed: the call's moment
    row: Row
    ctx: Context
    seen: Seen
    tasks: str
    under_way: str
    new: list[Row]
    busy: set[str] = field(default_factory=set)
    loops: int = 0          # loops shown in WORK UNDER WAY


class Night:
    def __init__(self, folder: Path, brain_log: Path):
        db = sqlite3.connect(f"file:{Path(folder) / 'vfg' / 'vfg.db'}?mode=ro&immutable=1", uri=True)
        self.feed = db.execute("SELECT seq, at, line FROM feed ORDER BY seq").fetchall()
        self.fed = {(at, who): fed for at, who, fed in
                    db.execute("SELECT at, player_name, fed_at FROM lines WHERE fed_at IS NOT NULL")}
        self.players = dict(db.execute("SELECT name, player FROM tasks"))
        db.close()
        self.log = Path(brain_log).read_text(encoding="utf-8", errors="replace").splitlines()
        self.lines: dict[int, tuple[str, str]] = {}   # feed number: (when it was said, when it was fed)
        for seq, at, line in self.feed:
            row = parse(line, seq=seq, at=at)
            if row is not None and row.type == "LINE" and row.cls in LIVE:
                self.lines[seq] = (at, self.fed[(at, row.who)])
        if sorted(self.lines) != sorted(LABEL_OF):
            raise ValueError("the feed's lines to the AI are not the 64 labelled: is this the night of 2026-10-12/13?")
        self.stops = [when(m.group(1)) for x in self.log if (m := STOP.match(x))]
        self.owner = next((m.group(1) for x in self.log if (m := START.match(x))), "")   # VFG_OWNER that night
        self.loops = self._loops()
        self.described = [(m.group(1), m.group(2), m.group(3)) for x in self.log if (m := MADE.match(x))]

    # ------------------------------------------------------------- the night's loops

    def _loops(self) -> list[Loop]:
        replies: dict[int, str] = {}
        for x in self.log:
            if m := STEP_REPLY.match(x):
                replies[int(m.group(1))] = m.group(2)
        out = []
        for x in self.log:
            m = LOOP.match(x)
            if m is None or int(m.group(2)) not in self.lines:
                continue
            n = int(m.group(2))
            start = when(self.lines[n][1]) + timedelta(seconds=UNDER_WAY_LAG_S)
            words = next(parse(line, seq=seq, at=at).text for seq, at, line in self.feed if seq == n)
            out.append(Loop(n, m.group(3), words, start, start + timedelta(seconds=float(m.group(5))),
                            m.group(6), replies.get(n, "")))
        return out

    def under_way(self, n: int, now: datetime) -> tuple[str, set[str], int]:
        """WORK UNDER WAY at `now` for the line `n` (its own loop not yet begun), as loops.show(brief=True)
        writes it; the players with a loop being done now, and how many loops it shows."""
        rows, busy = [], set()
        stop = max((t for t in self.stops if t <= now), default=None)
        for lp in sorted(self.loops, key=lambda lp: lp.start):
            if lp.n == n or lp.start > now or (now - lp.start).total_seconds() >= TAKE_UP_S:
                continue
            if stop is not None and lp.start < stop:
                continue   # the stop forgot it
            words = short(lp.words, WORDS_MAX)
            if now < lp.end:
                busy.add(lp.who)
                rows.append(f"- {lp.who}: “{words}” — being done now ({(now - lp.start).total_seconds():.0f} s)")
            else:
                said = f": “{short(lp.reply, REPLY_MAX)}”" if lp.reply else ""
                rows.append(f"- {lp.who}: “{words}” — {ENDED.get(lp.ended, 'done a moment ago')}{said}")
        text = f"{UNDER_WAY_HEAD}\n" + "\n".join(rows) if rows else UNDER_WAY_NONE
        return text, busy, len(rows)

    # ------------------------------------------------------------- the tasks

    def tasks(self, upto: int, now: datetime) -> tuple[Tasks, set[str]]:
        """The tasks as the table had them after the feed's rows up to `upto`, and the ones with an
        implementer on them; the descriptions implementers wrote up to `now`."""
        rows: dict[str, dict] = {}
        running: set[str] = set()
        for seq, at, line in self.feed:
            if seq > upto:
                break
            r = parse(line, seq=seq, at=at)
            if r is None or r.type != "AI" or r.kind not in ("task", "result") or not r.task:
                continue
            name = r.task
            if r.kind == "task" and (m := TASK_ROW.match(r.text)):
                if m.group(1) == "new":
                    rows[name] = {"name": name, "description": m.group(2), "player": self.players.get(name),
                                  "status": "open", "created_at": at, "updated_at": at}
                elif name in rows:
                    rows[name].update(status="open", updated_at=at)
                running.add(name)
            elif r.kind == "result" and (m := RESULT_ROW.match(r.text)) and name in rows:
                rows[name].update(status=m.group(2), updated_at=at, result=m.group(3))
                running.discard(name)
        for at, name, description in self.described:
            if name in rows and when(at) <= now:
                rows[name]["description"] = description
        return Tasks(rows.values(), clock=lambda: ""), running

    # ------------------------------------------------------------- the calls

    def online(self, upto: int) -> list[str]:
        names: list[str] = []
        for seq, at, line in self.feed:
            if seq > upto:
                break
            r = parse(line, seq=seq, at=at)
            if r is None or r.type != "GAME":
                continue
            if r.kind == "join" and r.who not in names:
                names.append(r.who)
            elif r.kind == "leave" and r.who in names:
                names.remove(r.who)
        return names

    def calls(self, classes: str = "night", only: set[int] | None = None):
        """Each line's Call, in feed order: the conversation up to it, read as described above."""
        reads = sorted(set(self.lines) | {seq for seq, _, line in self.feed if line.startswith("AI#")
                                          and " result " in line.split(" |")[0]})
        for n, (said, fed) in sorted(self.lines.items()):
            if only is not None and n not in only:
                continue
            last_read = max((s for s in reads if s < n), default=0)
            ctx = Context()
            for seq, at, line in self.feed:
                if seq > n:
                    break
                row = parse(line, seq=seq, at=at)
                if row is None:
                    continue
                if row.type == "LINE" and row.cls in LIVE and classes == "to_ai":
                    row.cls = "to_ai"
                row.processed = row.processed or seq <= last_read
                ctx.add(row)
            row = ctx.rows[-1]
            new = [r for r in ctx.rows if r.shown and not r.processed and r.seq != n]
            now = when(fed)
            speaker = row.who
            online = self.online(n)
            if speaker not in online:
                online.append(speaker)
            players = "\n".join(f"- {p}: position unknown" for p in online)
            seen = Seen(players=f"PLAYERS ONLINE (their position and state unknown in this replay):\n{players}",
                        clock="TIME: unknown. WEATHER: unknown.", running="RUNNING NOW: unknown in this replay",
                        places="", incantations="INCANTATIONS: unknown in this replay")
            tasks, running = self.tasks(n, now)
            under_way, busy, shown = self.under_way(n, now)
            busy |= {t["player"] for t in tasks.all() if t["name"] in running and t.get("player")}
            yield Call(n, LABEL_OF[n], fed, row, ctx, seen, tasks.show(running), under_way, new, busy, shown)

    # ------------------------------------------------------------- the night's own answers

    def recorded(self) -> dict[int, dict]:
        """Respond's answers that night, by feed number: {ms, cost, prompt_tokens, cached, out, up, reply,
        reason, guard}."""
        out: dict[int, dict] = {}
        for x in self.log:
            if m := RESPOND_USE.match(x):
                n = int(m.group(2))
                tokens = int(m.group(5))
                out.setdefault(n, {}).update(at=m.group(1), ms=int(m.group(3)), cost=float(m.group(4)),
                                             prompt_tokens=tokens,
                                             cached_tokens=round(tokens * int(m.group(6)) / 100),
                                             completion_tokens=int(m.group(7)))
            elif m := RESPOND_UP.match(x):
                out.setdefault(int(m.group(2)), {}).update(up=True, reply="", reason=m.group(3))
            elif m := RESPOND_SAID.match(x):
                out.setdefault(int(m.group(2)), {}).update(up=False, reply=ast.literal_eval(m.group("reply")),
                                                           reason=m.group("reason"))
            elif m := GUARD.match(x):
                out.setdefault(int(m.group(2)), {})["guard"] = True
        for r in out.values():
            r.setdefault("guard", False)
        return out
