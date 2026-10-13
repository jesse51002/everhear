#!/usr/bin/env python3
"""D with and without the gather call: Jev's production routing (jev/), line by line, on the 843 lines
of the 2026-10-08 playtest, and the gather call on every line it sends to the brain or Respond.
gather_score.py scores it.

THE FILTER IS PRODUCTION'S. Each line goes through `filter.Filter._judge_one` itself: the routing call
(`prompt.questions` on the production state `prompt.build_state`: the rules, the lookout the brain
sets, `brain/prompts/lookout.md`, the examples block, the brain's menu), walked by `prompt.route`; a
refused call asked once more with half the history, then passed as context; and, for a line sent to
the AI, `Filter._gather`: the window from `store.Store.gather_window`, one call with
`prompt.gather_questions` on the gather state (`GATHER_RULES`, `GATHER_HEADING`, `GATHER_EXAMPLES`),
the yes lines fitted by `filter.fit_group`. The routing call is whatever production asks: D2's two
questions onto four classes when `prod/` was recorded (2026-10-11), the to-the-AI-or-a-friend question
onto three since Prototype_Addressing_The_AI (2026-10-13); a line to the AI is `to_ai`, or in rows
recorded before it `action_oriented` or `conversational`, and every reader here takes all three. The
gather call's state is what it was when `prod/` was recorded: GATHER_RULES and GATHER_EXAMPLES are the
rules and examples of then, word for word. The window is read
from a sqlite database built from §4's schema (`jev/tests/schema.sql`), holding every line of the
playtest with THIS replay's verdicts so far, so the window's rule is production's own.

Two things are the bench's:
- THE HISTORY. `Filter._state` is replaced by one that builds the same state from what Jev had when
  it judged the line then (tree_bench.Bench's rows: the lines and the brain's records visible by
  the line's `judged` time, three hours back), since the database holds every line, said or not
  yet said. The clock stands at the line's `judged` time.
- RETRIES. A call that fails (`JevDown`: a timeout, no connection, a 5xx, the routing answer missing) is
  tried again, a second apart, five times, before the filter sees it fail, as the other benches
  do; production would retry the routing call on the next step and send the ask alone on a failed
  gather call. Every failed try is in the row. A refusal goes straight to the filter.

THE LINES go in production's order, `Store.unjudged`: by `at`, then `id`. A blank line is
`not_relevant` with no call, as `Filter._judge_new` does.

    (cd brain && uv sync --frozen --inexact)
    PY=brain/.venv/bin/python; DB=DIR/vfg/vfg.db; L=integration/replay
    $PY $L/gather_bench.py run --db $DB --out OUT --limit 30 --env ENV      # a pilot
    $PY $L/gather_bench.py run --db $DB --out OUT --env ENV                 # goes on to all 843
    $PY $L/gather_bench.py regather g2 --db $DB --out OUT --half first --env ENV   # a candidate wording
    $PY $L/gather_bench.py ten --db $DB --out OUT --env ENV                 # 10-question calls
    $PY $L/gather_bench.py live first g3 --runs RUNS --out OUT --env ENV    # the live runs' gather states

`run` writes OUT/prod/rows.jsonl, one row a line, in order: its label and whole-ask input, the class
the filter stored, every routing call (each answer as Jev gave it, [choice, confidence,
{option: probability}], its ms, input tokens, the state's estimate and the history lines kept,
failed tries), and for a line to the AI its gather: the window's ids, how many lines the window
holds without the cap of 10, the call as above, the yes and no ids, the group fed with the ask
(after fit_group), and the filter's journal lines. A run into a file that has rows goes on after
them: the database is built again with the recorded verdicts.

`regather NAME` asks the gather call again with a candidate wording (WORDINGS) on every recorded
window of a half, the state built the same way at the same moment: the windows are D's and do not
depend on the gather's answers. Into OUT/NAME/rows.jsonl. `repeat` is production's wording asked again.

`ten` asks production's gather call with all 10 questions, on windows made full (see `ten`).

`live W …` asks each wording on the gather state of every line a live realmc run (integration/jev_live.py)
sent to the AI: production's Store on a copy of the run's database cut at the ask, and the state the
filter builds then. RUNS is the stand-in's vfg/ folder, its earlier runs under vfg/runs/. Into
OUT/live/<wording>/rows.jsonl, each window line marked when it is its scenario's first piece.

The TypeSafe key as classes.py reads it (`--env`), never printed. No OpenRouter calls.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tree_bench import Bench, raw_answers, read_rows  # noqa: E402  (puts brain/ and jev/ on the path)
from classes import HISTORY_H, STATE_TOKENS, TRIES, typesafe_key  # noqa: E402
import prompt  # noqa: E402  (jev/prompt.py)
from filter import GATHER_LOOKBACK, GATHER_MAX, Config, Filter, fit_group  # noqa: E402  (jev/filter.py)
from jev_api import ENDPOINT, MODEL, PRICE_PER_INPUT_TOKEN, Jev, JevDown, JevRefused  # noqa: E402
from jev_clock import iso, parse_iso  # noqa: E402
from prompt import GATHER_EXAMPLES, GATHER_HEADING, GATHER_RULES, PART_OF_IT, build_state, estimate  # noqa: E402
from store import Store  # noqa: E402

REPO = HERE.parents[1]
SCHEMA = REPO / "jev" / "tests" / "schema.sql"     # §4's DDL, verbatim
LOOKOUT = REPO / "brain" / "prompts" / "lookout.md"   # what the brain sets with `vfg lookout set`
LABELS = HERE / "labels_2026-10-08.jsonl"
INPUTS = HERE / "whole_asks_2026-10-08.jsonl"
FAR = 10 ** 9   # a window's limit with no cap
TO_THE_AI = ("to_ai", "action_oriented", "conversational")   # a line sent to the AI, now and in older rows


# ------------------------------------------------------------------ the wordings of the gather call

# Candidates, written after reading production's answers on the first half's windows: it said yes to 1
# of 196 lines there, and left out pieces of a sentence cut at a pause ("My castle." … "it built yet.",
# P 0.43). Its `not_part_of_it` says "or the ask makes full sense without it", which a short ask read
# alone nearly always does. Both candidates describe the two sides by what the line is, and say
# nothing about whether the ask stands alone; g3 also says why one ask can come in pieces.
BALANCED = {
    "part_of_the_ask": "Part of the ask: a piece of the same sentence or request, cut off by a pause, or it says what, "
                       "where, who or how for the ask.",
    "not_part_of_it": "Not part of the ask: something else: a reaction, a remark, talk to a player, or another "
                      "subject or ask.",
}
PIECES = "Speech-to-text cuts a player at every pause, so one ask can arrive as several lines. "
# The wording production had when `run` recorded `prod/` (2026-10-11), before g3 replaced it.
FIRST = {
    "part_of_the_ask": "Part of the ask: it says what, where, who or how for the ask, or the ask doesn't make sense "
                       "without it.",
    "not_part_of_it": "Not part of the ask: it is about something else, or the ask makes full sense without it.",
}


def gather_wording(criteria: dict, why: str = ""):
    """prompt.gather_questions with other `criteria`, and `why` said before the line asked about."""
    def qs(ask, window):
        head = "%s's line [ask] at %s, “%s”, goes to the AI. %s" % (ask["player_name"], prompt.when(ask["at"]),
                                                                  prompt.quote(ask), why)
        return {"line_%d" % k: {
            "type": "choice",
            "instructions": head + "Line [%d], said by %s before it at %s: “%s”. Is line [%d] part of that ask?"
                                   % (k, row["player_name"], prompt.when(row["at"]), prompt.quote(row), k),
            "criteria": dict(criteria)}
            for k, row in enumerate(window, 1)}
    return qs


# Round two, after the live realmc runs (integration/jev_live.py): g3 took the playtest's sentences cut
# at a pause, but gave "I'm going down into that ocean monument", which says where the ask's "there"
# is, 0.50 to 0.71 where the first wording gave 0.75 to 0.87. The guess: "a remark" and "talk to a
# player" in g3's `not_part_of_it` describe that line too. g4 is the first wording with g3's sentence
# on pauses. g5 says what each side is about, not what form the line takes: a piece, or what the ask's
# words point at; another subject, another ask or a reaction.
REFERS = {
    "part_of_the_ask": "Part of the ask: a piece of the same sentence, cut off by a pause, or it says what, where, who "
                       "or how for the ask, such as what \"it\", \"there\" or \"one\" in the ask points at.",
    "not_part_of_it": "Not part of the ask: it is about something else, such as another subject, another ask or a "
                      "reaction, and the ask needs nothing from it.",
}
# g6 is g5 in the spec's own order and words ("Part of it: it says what, where, who or how for the ask
# … Not part of it: anything else."), with g5's two additions, and "anything else" named.
SPEC = {
    "part_of_the_ask": "Part of the ask: it says what, where, who or how for the ask, such as what \"it\", \"there\" "
                       "or \"one\" in the ask points at, or it is a piece of the same sentence, cut off by a pause.",
    "not_part_of_it": "Not part of the ask: anything else: another subject, another ask, or a reaction.",
}

# name -> (what it changes, the questions function). `prod` is the first wording (FIRST), which `run`
# recorded; g5 is jev/prompt.py's own since round two, so `run` now asks g5.
WORDINGS = {
    "prod": ("the first wording: FIRST, production's when `run` recorded it", gather_wording(FIRST)),
    "first": ("the first wording (FIRST), asked on the live states", gather_wording(FIRST)),
    "repeat": ("the first wording asked again, for run-to-run noise", gather_wording(FIRST)),
    "g2": ("the criteria balanced (BALANCED), the instruction production's", gather_wording(BALANCED)),
    "g3": ("g2, and the instruction says speech-to-text cuts a player at every pause (PIECES)",
           gather_wording(BALANCED, PIECES)),
    "g4": ("the first wording's criteria (FIRST), and g3's sentence on pauses (PIECES)", gather_wording(FIRST, PIECES)),
    "g5": ("criteria by what the line is about (REFERS), and g3's sentence on pauses (PIECES)",
           gather_wording(REFERS, PIECES)),
    "g6": ("g5 in the spec's order and words (SPEC), and g3's sentence on pauses (PIECES)",
           gather_wording(SPEC, PIECES)),
}
_row = {"id": "x", "at": "2026-10-08T06:00:00.000Z", "player_name": "Steve", "source": "voice", "text": "the big one",
        "peak_dbfs": None}
assert gather_wording(REFERS, PIECES)(_row, [_row]) == prompt.gather_questions(_row, [_row])   # g5 is production


# ------------------------------------------------------------------ the stand-ins around production


class Clock:
    """Stands where the bench puts it: the line's `judged` time."""

    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t


class ReplayStore(Store):
    """Production's store on the replay database, noting each gather window it reads, and how many
    lines that window holds without the cap."""

    def __init__(self, path):
        super().__init__(path)
        self.windows = []

    def gather_window(self, ask, since, limit=10, sent_since=None):
        rows = super().gather_window(ask, since, limit, sent_since=sent_since)
        every = super().gather_window(ask, since, FAR, sent_since=sent_since)
        self.windows.append({"ask": ask["id"], "since": since, "ids": [r["id"] for r in rows], "all": len(every)})
        return rows


class Recorder:
    """The Jev client as the filter calls it, keeping every call: the answers as Jev gave them (with
    their probabilities), ms, input tokens, the state's estimate and history kept, and failed tries.
    A `JevDown` is tried again, a second apart, TRIES times in all; a refusal goes to the filter."""

    def __init__(self, jev, flt_ref):
        self.jev = jev
        self.flt = flt_ref   # a one-item list holding the filter, for the history kept by its `_state`
        self.calls = []
        self.tokens = 0

    def _used(self):
        try:
            got = json.loads((self.jev.last_body or b"").decode("utf-8"))
            n = (got.get("usage") or {}).get("input_tokens")
            return int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) else 0
        except (ValueError, AttributeError):
            return 0

    def ask(self, state, questions, required="line_1"):
        call = {"kind": "route" if required else "gather", "questions": len(questions), "estimate": estimate(state),
                "history_kept": self.flt[0].kept, "tries": []}
        self.calls.append(call)
        for _ in range(TRIES):
            try:
                res = self.jev.ask(state, questions, required=required)
            except JevRefused as e:
                call["tries"].append({"refused": str(e), "tokens": self._used()})
                self.tokens += call["tries"][-1]["tokens"]
                raise
            except JevDown as e:
                call["tries"].append({"down": str(e), "tokens": self._used()})
                self.tokens += call["tries"][-1]["tokens"]
                time.sleep(1.0)
                continue
            call.update(answers=raw_answers(self.jev.last_body, questions), ms=res.ms,
                        tokens=res.input_tokens or 0, missing=sorted(set(questions) - set(res.answers)))
            self.tokens += call["tokens"]
            return res
        call["failed"] = True
        raise JevDown(call["tries"][-1]["down"])


class Journal(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class History:
    """What Jev had when it judged a line then: the bench's lines and the brain's records visible by
    `began`, three hours back, newest first, leaving out the lines asked about."""

    def __init__(self, b: Bench):
        self.rows = b.said + b.records

    def others(self, began: float, exclude) -> list[dict]:
        since, now, skip = iso(began - HISTORY_H * 3600), iso(began), set(exclude)
        return sorted([r for r in self.rows if r["id"] not in skip and since <= r["at"] and r["visible"] <= now],
                      key=lambda r: (r["at"], r["id"]), reverse=True)


def replay_filter(history: History):
    class ReplayFilter(Filter):
        """Production's filter with `_state` building the history as it was then (see above)."""

        kept = None

        def _state(self, marked, others, share=1.0, rules=prompt.RULES, heading=prompt.HISTORY_HEADING,
                   examples=None):
            # production's `others` is read from the replay database, which holds every line, said or not
            # yet: the history is rebuilt as it was then, at the clock's time (the line's `judged` time)
            others = history.others(self.clock.time(), [r["id"] for _, r in marked])
            menu = self.menu.text()
            state, self.kept = build_state(self.lookout.text(), marked, others, self.cfg.state_tokens, share, menu,
                                           rules, heading, examples)
            return state

    return ReplayFilter


# ------------------------------------------------------------------ the replay database


def make_db(path: Path, b: Bench, verdicts: dict[str, tuple]) -> None:
    """§4's schema with every line of the playtest, the AI's included, unjudged but for `verdicts`
    ({id: (class, confidence, judged at)}, an earlier run's)."""
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path, isolation_level=None)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    for ln in b.pt.lines:
        cls, conf, judged = verdicts.get(ln.id, (None, None, None))
        con.execute("INSERT INTO lines (id, at, ended_at, player_name, source, text, peak_dbfs, jev_class, "
                    "jev_confidence, jev_judged_at) VALUES (?, ?, ?, ?, 'voice', ?, ?, ?, ?, ?)",
                    (ln.id, ln.at, ln.ended, ln.who, ln.text, ln.peak, cls, conf, judged))
    for a in b.pt.ai:
        con.execute("INSERT INTO lines (id, at, player_name, source, text) VALUES (?, ?, 'AI', 'ai', ?)",
                    (a["id"], a["at"], a["text"]))
    con.close()


def facts():
    """The clean label and whole-ask input of every line."""
    labels = {r["id"]: r for r in read_rows(LABELS)}
    of_input = {i: inp["id"] for inp in read_rows(INPUTS) for i in inp["lines"]}
    return labels, of_input


def commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "?"


def meta(b: Bench, path: Path, **more) -> None:
    path.write_text(json.dumps({
        "started": datetime.now(UTC).isoformat(timespec="seconds"), "commit": commit(), "model": MODEL,
        "state_tokens": STATE_TOKENS, "history_hours": HISTORY_H, "jev_timeout": b.jev.timeout,
        "lookout": str(LOOKOUT), "lookout_is_default": LOOKOUT.read_text(encoding="utf-8").strip()
        == prompt.DEFAULT_LOOKOUT, "menu_lines": len(b.menu.splitlines()), "gather_lookback": GATHER_LOOKBACK,
        "gather_max": GATHER_MAX, "rules": prompt.RULES, "gather_rules": GATHER_RULES, "examples": prompt.EXAMPLES,
        "gather_examples": GATHER_EXAMPLES,
        "gather_criteria": prompt.GATHER_CRITERIA, **more}, indent=1, ensure_ascii=False))


# ------------------------------------------------------------------ run: the production filter, line by line


def run(args) -> None:
    b = Bench(args)
    labels, of_input = facts()
    out = Path(args.out) / "prod"
    out.mkdir(parents=True, exist_ok=True)
    rows_file = out / "rows.jsonl"
    have = read_rows(rows_file)
    verdicts = {r["id"]: (r["class"], r["confidence"], r["judged_at"]) for r in have}
    db = out / "replay.db"
    make_db(db, b, verdicts)
    store = ReplayStore(str(db))
    assert store.ready()
    clock = Clock()
    cfg = Config(db=str(db), lookout=str(LOOKOUT), menu=str(Path(args.out) / "menu.md"), model=MODEL,
                 state_tokens=STATE_TOKENS, history_hours=HISTORY_H)
    ref = [None]
    rec = Recorder(b.jev, ref)
    flt = replay_filter(History(b))(store, rec, None, clock, cfg)
    ref[0] = flt
    journal = Journal()
    log = logging.getLogger("vfg-jev")
    log.setLevel(logging.INFO)
    log.propagate = False
    log.addHandler(journal)
    tokens = sum(c.get("tokens", 0) + sum(t.get("tokens", 0) for t in c.get("tries", []))
                 for r in have for c in r["route"] + ([r["gather"]["call"]] if r.get("gather") and
                                                       r["gather"].get("call") else []))
    meta(b, out / f"meta{'-limit' if args.limit else ''}.json", lines=len(b.lines), already=len(have),
         limit=args.limit)
    print(f"prod: {len(b.lines)} lines, {len(have)} already, ${tokens * PRICE_PER_INPUT_TOKEN:.4f}", flush=True)
    done = 0
    with rows_file.open("a", encoding="utf-8") as f:
        while True:
            nxt = store.unjudged(1)
            if not nxt or (args.limit and len(have) + done >= args.limit):
                break
            row = nxt[0]
            ln = b.pt.by_id[row["id"]]
            clock.t = parse_iso(ln.judged)
            rec.calls, rec.tokens, store.windows, journal.lines = [], 0, [], []
            lab = labels[ln.id]
            out_row = {"id": ln.id, "n": ln.n, "at": ln.at, "who": ln.who, "text": ln.text, "judged": ln.judged,
                       "label": lab["label"], "disputed": lab["disputed"], "half": b.half[ln.id],
                       "input": of_input[ln.id]}
            if not row["text"].strip():
                store.set_class(row["id"], "not_relevant", None, iso(clock.time()))
                out_row["blank"] = True
            elif not flt._judge_one(row):
                # failed five times: the filter would fail open and pass it as context
                store.set_class(row["id"], "context", None, iso(clock.time()))
                out_row["failed"] = True
            got = store.conn.execute("SELECT jev_class, jev_confidence, jev_judged_at FROM lines WHERE id = ?",
                                     (row["id"],)).fetchone()
            out_row.update({"class": got[0], "confidence": got[1], "judged_at": got[2]})
            out_row["route"] = [c for c in rec.calls if c["kind"] == "route"]
            gathers = [c for c in rec.calls if c["kind"] == "gather"]
            if store.windows:
                w = store.windows[0]
                group = flt.groups.pop(row["id"], [])
                call = gathers[0] if gathers else None
                g = {"window": w["ids"], "window_all": w["all"], "since": w["since"], "call": call}
                if call is not None and "answers" in call:
                    a = call["answers"]
                    yes = [i for k, i in enumerate(w["ids"], 1) if (a.get(f"line_{k}") or [None])[0] == PART_OF_IT]
                    g.update(yes=yes, no=[i for i in w["ids"] if i not in yes], group=[r["id"] for r in group])
                else:
                    g.update(yes=[], no=[], group=[])
                out_row["gather"] = g
            out_row["journal"] = journal.lines
            f.write(json.dumps(out_row, ensure_ascii=False) + "\n")
            f.flush()
            done += 1
            tokens += rec.tokens
            r0 = (out_row["route"] or [{}])[0]
            gtxt = ""
            if out_row.get("gather"):
                g = out_row["gather"]
                c = g["call"] or {}
                gtxt = f" | gather {len(g['window'])}/{g['window_all']} yes {len(g['yes'])} {c.get('ms', 0)} ms"
            print(f"{ln.at[11:19]} {lab['label']:12} {out_row['class']:15} {r0.get('ms', 0):4} ms "
                  f"{r0.get('tokens', 0):5} tok{gtxt} | {ln.text[:50]!r}"
                  f"{' FAILED' if out_row.get('failed') else ''}", flush=True)
            if tokens * PRICE_PER_INPUT_TOKEN > args.jev_budget:
                print(f"stopping: ${tokens * PRICE_PER_INPUT_TOKEN:.3f} of Jev, over --jev-budget {args.jev_budget}")
                break
    print(f"prod: {tokens:,} Jev input tokens, ${tokens * PRICE_PER_INPUT_TOKEN:.4f} in this file")


# ------------------------------------------------------------------ regather: a candidate wording


def gather_call(b: Bench, history: History, lookout: str, ask: dict, window: list[dict], judged: str, qfun) -> dict:
    """One gather call on `window` for `ask`, the state built as the filter's at `judged`: {answers, ms,
    tokens, yes, group, missing, estimate, history_kept, tries}. Refused, or failed five times: `failed`."""
    marked = [("ask", ask)] + list(enumerate(window, 1))
    others = history.others(parse_iso(judged), [r["id"] for _, r in marked])
    state, kept = build_state(lookout, marked, others, STATE_TOKENS, 1.0, b.menu, GATHER_RULES, GATHER_HEADING,
                              GATHER_EXAMPLES)
    qs = qfun(ask, window)
    row = {"window": [r["id"] for r in window], "estimate": estimate(state), "history_kept": kept, "tries": []}
    for _ in range(TRIES):
        try:
            res = b.jev.ask(state, qs, required=None)
        except JevRefused as e:
            row["tries"].append({"refused": str(e)})
            break
        except JevDown as e:
            row["tries"].append({"down": str(e)})
            time.sleep(1.0)
            continue
        a = raw_answers(b.jev.last_body, qs)
        yes = [r for k, r in enumerate(window, 1) if (a.get(f"line_{k}") or [None])[0] == PART_OF_IT]
        row.update(answers=a, ms=res.ms, tokens=res.input_tokens or 0, yes=[r["id"] for r in yes],
                   group=[r["id"] for r in fit_group(yes, ask)], missing=sorted(set(qs) - set(res.answers)))
        break
    if "answers" not in row:   # refused, or failed five times: the ask goes alone
        row.update(failed=True, tokens=0, yes=[], group=[])
    return row


def regather(args) -> None:
    name = args.wording
    if name == "prod":
        sys.exit("prod's answers are run's own (OUT/prod/rows.jsonl); ask production's wording again as `repeat`")
    b = Bench(args)
    _, qfun = WORDINGS[name]
    prod = [r for r in read_rows(Path(args.out) / "prod" / "rows.jsonl")
            if r.get("gather") and r["gather"]["window"] and (args.half == "all" or r["half"] == args.half)]
    out = Path(args.out) / name
    out.mkdir(parents=True, exist_ok=True)
    rows_file = out / "rows.jsonl"
    have = read_rows(rows_file)
    done = {r["id"] for r in have}
    tokens = sum(r.get("tokens", 0) for r in have)
    lookout, _ = prompt.read_lookout(str(LOOKOUT))
    history = History(b)
    todo = prod[:args.limit] if args.limit else prod
    meta(b, out / f"meta-{args.half}.json", wording=name, about=WORDINGS[name][0], windows=len(todo),
         already=len(done))
    print(f"{name}: {len(todo)} windows ({args.half}), {len(done)} already, ${tokens * PRICE_PER_INPUT_TOKEN:.4f}",
          flush=True)
    with rows_file.open("a", encoding="utf-8") as f:
        for p in todo:
            if p["id"] in done:
                continue
            window = [b.said_by_id[i] for i in p["gather"]["window"]]
            row = {"id": p["id"], "half": p["half"],
                   **gather_call(b, history, lookout, b.said_by_id[p["id"]], window, p["judged"], qfun)}
            tokens += row["tokens"]
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{p['at'][11:19]} {len(window):2} asked, yes {len(row['yes'])} (prod {len(p['gather']['yes'])}) "
                  f"{row.get('ms', 0):4} ms | {p['text'][:60]!r}", flush=True)
            if tokens * PRICE_PER_INPUT_TOKEN > args.jev_budget:
                print(f"stopping: ${tokens * PRICE_PER_INPUT_TOKEN:.3f} of Jev, over --jev-budget {args.jev_budget}")
                break
    print(f"{name}: {tokens:,} Jev input tokens, ${tokens * PRICE_PER_INPUT_TOKEN:.4f} in this file")


def ten(args) -> None:
    """Production's gather call with all 10 questions: no window of the replay reached the cap, so this
    asks it on windows made full, the speaker's 10 newest unsent lines before a line to the AI, with
    neither the 180 s nor the last-sent bound. Into OUT/ten/rows.jsonl."""
    b = Bench(args)
    rows = read_rows(Path(args.out) / "prod" / "rows.jsonl")
    out = Path(args.out) / "ten"
    out.mkdir(parents=True, exist_ok=True)
    rows_file = out / "rows.jsonl"
    have = read_rows(rows_file)
    done = {r["id"] for r in have}
    eligible = []
    for k, r in enumerate(rows):
        if r["class"] not in TO_THE_AI:
            continue
        unsent = [x for x in rows[:k] if x["who"] == r["who"] and x["class"] in ("context", "not_relevant")
                  and x["text"].strip()]
        if len(unsent) >= GATHER_MAX:
            eligible.append((r, unsent[-GATHER_MAX:]))
    n = args.limit or 12
    todo = [eligible[round(i * (len(eligible) - 1) / max(1, n - 1))] for i in range(min(n, len(eligible)))]
    lookout, _ = prompt.read_lookout(str(LOOKOUT))
    history = History(b)
    meta(b, out / "meta.json", eligible=len(eligible), asked=len(todo))
    tokens = sum(r.get("tokens", 0) for r in have)
    with rows_file.open("a", encoding="utf-8") as f:
        for r, unsent in todo:
            if r["id"] in done:
                continue
            window = [b.said_by_id[x["id"]] for x in unsent]
            row = {"id": r["id"], "half": r["half"], "spans_s": round(parse_iso(r["at"]) - parse_iso(unsent[0]["at"])),
                   **gather_call(b, history, lookout, b.said_by_id[r["id"]], window, r["judged"],
                                 prompt.gather_questions)}
            tokens += row["tokens"]
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{r['at'][11:19]} 10 asked over {row['spans_s']} s, {10 - len(row.get('missing', []))} answered, "
                  f"yes {len(row['yes'])}, {row.get('ms', 0)} ms, {row['tokens']} tok", flush=True)
    print(f"ten: {tokens:,} Jev input tokens, ${tokens * PRICE_PER_INPUT_TOKEN:.4f} in this file")


# ------------------------------------------------------------------ live: the realmc runs' gather states

# integration/jev_live.py's SCENARIOS: each ask, and its first piece. Every other line in an ask's window
# is another line of the script, about another scenario.
LIVE_PIECES = {
    "AI, give me something so I can see in there": "the cave over there is pitch black",
    "AI, can you give me a new one": "my diamond pickaxe just broke",
    "AI, I need to be able to breathe down there": "I'm going down into that ocean monument",
}
_GATHER_LOGGED = re.compile(r"\bgather (\S+) \S+ · \d+ asked, \d+ yes: (.*?) · no: (.*?) · \d+ms")


def live_runs(root: Path) -> list[tuple[str, Path]]:
    """jev_live.py's transcripts: each earlier run's folder under vfg/runs/, and the last run's, vfg/."""
    runs = root / "runs"
    found = [(d.name, d) for d in sorted(runs.iterdir()) if (d / "vfg.db").exists()] if runs.is_dir() else []
    return found + ([("last", root)] if (root / "vfg.db").exists() else [])


def logged_windows(folder: Path) -> dict[str, list[str]]:
    """{ask id: the ids its gather call asked about, yes then no}, from the run's jev.log when there is one
    (the last run's is beside vfg/, in the stand-in's folder)."""
    out = {}
    for log_path in (folder / "jev.log", folder.parent / "jev.log" if folder.name == "vfg" else None):
        if log_path is None or not log_path.exists():
            continue
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _GATHER_LOGGED.search(line)
            if m:
                out[m.group(1)] = [part.split()[0] for part in (m.group(2) + ", " + m.group(3)).split(", ")
                                   if part.strip() and part.strip() != "-"]
    return out


def live_states(root: Path):
    """(run, ask, window, state) for every line a live run sent to the AI whose gather window has lines:
    production's Store on a copy of the run's database cut at the ask, and the state the filter builds
    then, with the run's own lookout and menu (the defaults when it had none)."""
    for name, folder in live_runs(root):
        lookout = prompt.read_lookout(str(folder / "lookout.md"))[0]
        menu = prompt.read_menu(str(folder / "menu.md"))[0]
        src = sqlite3.connect("file:%s?mode=ro" % (folder / "vfg.db"), uri=True)
        src.row_factory = sqlite3.Row
        asks = src.execute("SELECT id, at FROM lines WHERE jev_class IN (%s) ORDER BY at, id"
                           % ",".join("?" * len(TO_THE_AI)), TO_THE_AI).fetchall()
        src.close()
        for ask_id, ask_at in asks:
            tmp = Path(tempfile.mkdtemp())
            try:
                for suffix in ("", "-wal", "-shm"):   # the WAL too: a run's last writes may be only there
                    if (folder / ("vfg.db" + suffix)).exists():
                        shutil.copy(folder / ("vfg.db" + suffix), tmp / ("vfg.db" + suffix))
                con = sqlite3.connect(tmp / "vfg.db")
                con.execute("DELETE FROM lines WHERE at > ?", (ask_at,))
                con.execute("DELETE FROM feed WHERE at > ?", (ask_at,))
                con.commit()
                con.close()
                st = Store(str(tmp / "vfg.db"))
                assert st.ready()
                ask = dict(st.conn.execute("SELECT * FROM lines WHERE id = ?", (ask_id,)).fetchone())
                said = parse_iso(ask["at"])
                window = [dict(r) for r in st.gather_window(ask, iso(said - GATHER_LOOKBACK), GATHER_MAX)]
                if window:
                    since = iso(said - HISTORY_H * 3600)
                    marked = [("ask", ask)] + list(enumerate(window, 1))
                    others = sorted(list(st.history(since, exclude=[r["id"] for _, r in marked]))
                                    + st.ai_records(since), key=lambda r: (r["at"], r["id"]), reverse=True)
                    state, kept = build_state(lookout, marked, others, STATE_TOKENS, 1.0, menu, GATHER_RULES,
                                              GATHER_HEADING, GATHER_EXAMPLES)
                    yield name, folder, ask, window, state, kept
                st.close()
            finally:
                shutil.rmtree(tmp)


def live(args) -> None:
    """Each wording on every live gather state. Into OUT/live/<wording>/rows.jsonl, a row an ask."""
    jev = Jev(typesafe_key(args.env), url=ENDPOINT, model=MODEL, timeout=args.jev_timeout)
    states = list(live_states(Path(args.runs)))
    logged = {}
    for name, folder in live_runs(Path(args.runs)):
        logged.update(logged_windows(folder))
    print(f"live: {len(states)} gather states in {len(live_runs(Path(args.runs)))} runs", flush=True)
    for wording in args.wording:
        _, qfun = WORDINGS[wording]
        out = Path(args.out) / "live" / wording
        out.mkdir(parents=True, exist_ok=True)
        rows_file = out / "rows.jsonl"
        have = read_rows(rows_file)
        done = {(r["run"], r["id"]) for r in have}
        tokens = sum(r.get("tokens", 0) for r in have)
        with rows_file.open("a", encoding="utf-8") as f:
            for name, folder, ask, window, state, kept in states:
                if (name, ask["id"]) in done:
                    continue
                piece = LIVE_PIECES.get(ask["text"])
                qs = qfun(ask, window)
                row = {"run": name, "id": ask["id"], "who": ask["player_name"], "text": ask["text"],
                       "window": [{"id": r["id"], "text": r["text"], "piece": r["text"] == piece} for r in window],
                       "logged": logged.get(ask["id"]), "estimate": estimate(state), "history_kept": kept,
                       "tries": []}
                for _ in range(TRIES):
                    try:
                        res = jev.ask(state, qs, required=None)
                    except JevRefused as e:
                        row["tries"].append({"refused": str(e)})
                        break
                    except JevDown as e:
                        row["tries"].append({"down": str(e)})
                        time.sleep(1.0)
                        continue
                    row.update(answers=raw_answers(jev.last_body, qs), ms=res.ms, tokens=res.input_tokens or 0,
                               missing=sorted(set(qs) - set(res.answers)))
                    break
                if "answers" not in row:
                    row.update(failed=True, tokens=0)
                tokens += row["tokens"]
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                got = row.get("answers", {})
                print(f"{wording:6} {name:16} {ask['text'][:44]:44} | " + "  ".join(
                    f"{(got.get(f'line_{k}') or ['-', 0, {}])[2].get(PART_OF_IT, 0):.2f}"
                    f"{'*' if w['piece'] else ' '} {w['text'][:28]}" for k, w in enumerate(row["window"], 1)),
                    flush=True)
        print(f"{wording}: {tokens:,} Jev input tokens, ${tokens * PRICE_PER_INPUT_TOKEN:.4f} in this file")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, about in (("run", "the production filter on every line"),
                        ("regather", "the gather call again with a candidate wording, on the recorded windows"),
                        ("ten", "production's gather call with all 10 questions, on windows made full"),
                        ("live", "wordings on the gather states of the live realmc runs (jev_live.py)")):
        s = sub.add_parser(name, help=about)
        if name == "regather":
            s.add_argument("wording", choices=sorted(WORDINGS))
            s.add_argument("--half", choices=("first", "second", "all"), default="first")
        if name == "live":
            s.add_argument("wording", nargs="+", choices=sorted(WORDINGS))
            s.add_argument("--runs", required=True, help="the realmc stand-in's vfg/ folder that jev_live.py ran in")
        else:
            s.add_argument("--db", required=True, help="the playtest's vfg.db")
        s.add_argument("--out", required=True)
        s.add_argument("--limit", type=int, default=0,
                       help="the first N lines (run) or windows (regather) only; ten: how many calls (12)")
        s.add_argument("--env", help="the file that holds TYPESAFE_API_KEY")
        s.add_argument("--jev-timeout", type=float, default=10.0, help="a Jev call's timeout, s")
        s.add_argument("--jev-budget", type=float, default=0.6, help="stop past this many dollars of Jev in the file")
    args = ap.parse_args()
    {"run": run, "regather": regather, "ten": ten, "live": live}[args.cmd](args)


if __name__ == "__main__":
    main()
