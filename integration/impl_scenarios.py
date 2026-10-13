#!/usr/bin/env python3
"""The scenarios of the game eval (impl_game.py): one per task of the replay's data, the six of
2026-10-11 first, then the 20 of 2026-10-08, each derived from the night's record by one
structured() call and stored once in integration/replay/impl/scenarios.json, where a person
reviews them (an implementer cannot read it: guard.readable).

    cd brain && uv run python ../integration/impl_scenarios.py [--only T207,T212] [--again]

A scenario is a player and what he wants, never what went wrong that night:
- the player (Steve) and a persona line; the line he opened with; his goal as a whole;
- his hidden wants, in order: the things he added or changed that night once the first worked
  (a bigger explosion, a slow fall for 5 s, a 5x5 obsidian platform), each with how he would say it.
  A complaint about the night's build (Haiku's bug: "the explosion is gone", "it teleports me") is
  not a want: what it asks for is either in the goal already or dropped;
- the setup: what the world needs before he asks, as vfg or server commands (a running copy to
  change, for an edit of 2026-10-08), never another scenario's work;
- DONE WHEN for the whole goal, the opening want and every hidden want, each a thing a player sees;
- how he uses it (the phrase, the item), and the most turns (6);
- `try`, how the player tries it (impl_game.py): cast, item, build, event or research, from its kind
  and use (try_of), which a person may change;
- `buildable`: false for research an implementer in the game cannot do (no web; it reads only
  scripts/, actions/ and made/), with the reason in `not_buildable`: the judge scores such a
  scenario apart, outside the total.

FIXES holds a person's corrections to a derived scenario (T211's checklist forbade the levitation
its opening asks for); they are applied each time the file is written, and --fixes applies them to
the stored file with no model call.

The model is google/gemini-3.8-flash at low reasoning; usage component `brain`, ref `implscenario`,
queued in $VFG_IMPL_REPLAY/usage. A scenario already stored is kept unless --again; a person's
edits to the file stay (`reviewed` marks one read by a person). OPEN_ROUTER_API is read from
Internal/.env into this process's environment only."""
import argparse
import asyncio
import json
import os
import re
import sys

from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib", "py"))

from impl_briefs_write import load_key  # noqa: E402
from impl_data import DATA, IMPL, ROOT  # noqa: E402

SCENARIOS = os.path.join(IMPL, "scenarios.json")
# How the player tries what was made (impl_game.try_it): say the incantation, use an item, look
# around at a build or an event, or only read what the AI says (a research ask).
TRIES = ("cast", "item", "build", "event", "research")


def try_of(sc: dict) -> str:
    """The try a scenario's kind and use call for, when a person has not set one."""
    kind, use = sc.get("kind") or "", (sc.get("use") or "").lower()
    if kind == "research":
        return "research"
    if kind == "build":
        return "build"
    if kind in ("mob_event", "world_event"):
        return "event"
    if re.search(r"['\"“][^'\"”]{3,40}['\"”]", sc.get("use") or "") or use.startswith("say "):
        return "cast"
    if re.search(r"right-click|click|hold|sneak|crouch|item|wand|staff|rifle|trident|gun|bow", use):
        return "item"
    return "event"
MODEL = "google/gemini-3.8-flash"
NOT_BUILDABLE = ("research an implementer in the game cannot do: it has no web and reads only scripts/, actions/ "
                 "and made/, so any measurement it reports is its own; scored apart, outside the total")
# A person's corrections, by scenario: fields replaced, checklist items by index, and why.
FIXES = {
    "T211": {
        "goal": "Create an incantation called 'high step' that launches Steve high into the air with real upward "
                "velocity like a jump (not a teleport), then has him float and come down slowly for about five "
                "seconds, and protects him from all fall damage on landing.",
        "checklist": {2: "after the launch he floats and comes down slowly for about five seconds (levitation, slow "
                         "falling or both: he asked to 'levitate and fall slowly')"},
        "fixed": "builder D, 2026-10-12: item 3 said 'without extra upward levitation' while the opening asks him to "
                 "'levitate and fall slowly'; it now takes either, and the goal says the same",
    },
}


def apply_fixes(sc: dict) -> dict:
    """The scenario with its buildable mark and a person's fixes (FIXES) applied."""
    if sc.get("try") == "research" or sc.get("kind") == "research":
        sc["buildable"], sc["not_buildable"] = False, NOT_BUILDABLE
    else:
        sc.setdefault("buildable", True)
    fix = FIXES.get(sc["id"])
    if fix:
        for k, v in fix.items():
            if k == "checklist":
                for n, text in v.items():
                    if n < len(sc["checklist"]):
                        sc["checklist"][n] = text
            else:
                sc[k] = v
    return sc


class Want(BaseModel):
    want: str = Field(description="what he wants added or changed, in a few plain words")
    line: str = Field(description="how he says it, close to his own words that night, with no talk of bugs")


class Scenario(BaseModel):
    persona: str = Field(description="one line: who this player is and how he talks to the AI")
    opening: str = Field(description="the line he opened with, his own words, cleaned of speech-to-text garble only")
    goal: str = Field(description="what he wants as a whole by the end, one or two sentences")
    use: str = Field(description="how he uses it: the incantation's phrase, the item and its click, or 'it runs on "
                                 "its own'")
    hidden_wants: list[Want] = Field(description="the extra things he asked for that night, in order, revealed one "
                                                 "at a time once what came before works; [] when he asked for nothing "
                                                 "more")
    checklist: list[str] = Field(description="DONE WHEN for the whole goal: the opening want and every hidden want, "
                                             "each one short thing a player sees in the game")
    setup: list[str] = Field(description="what the world needs before he asks, each a `vfg …` command (a running copy "
                                         "to change) or a server command with no slash; [] when nothing")
    max_turns: int = Field(6, description="the most lines he says, the opening included: 6 unless the night had more "
                                          "hidden wants than 4")


SYSTEM = (
    "You turn the record of one task from a Minecraft play session into a test scenario for an AI builder. A "
    "simulated player will say the opening line, try what the builder made, and then say his next line: a "
    "complaint about what does not work as he wanted, or the next of his hidden wants once the current thing "
    "works, or that he is done. From the record, write the player and what he wants. The record's follow-ups "
    "are of two kinds; tell them apart:\n"
    "- a new want: he adds or changes something about a thing that worked (bigger explosion, more damage, a "
    "platform under him, ten blocks wide, remove the countdown). These are the hidden wants, in order.\n"
    "- a complaint about that night's build: something it got wrong or broke (it passes through mobs, the "
    "explosion is gone, it teleports me instead of launching, it does not break blocks, it killed me). These "
    "are NOT wants: what such a complaint asks for belongs in the goal and the checklist when the opening line "
    "already implied it (a fireball explodes and hits mobs; a launch is not a teleport), else it is dropped.\n"
    "Also drop asks about how the AI works (give it to an implementer, the orchestrator). The checklist covers "
    "the whole goal at the end, opening want and hidden wants, each thing visible in the game. The player is "
    "Steve unless the record names Alex.")


def record(t: dict) -> str:
    """What the night says of one task: each ask's player lines and what the AI made of it."""
    out = ["TASK %s (%s), %s" % (t["id"], t.get("kind"), t.get("date"))]
    if t.get("setup"):
        out.append("SET UP BEFORE IT (vfg commands): %s" % json.dumps(t["setup"]))
    for a in t["asks"]:
        said = " / ".join(x["text"] for x in a.get("lines") or []) or "(none: the AI's own after a report)"
        out.append("%s %d, the player: %s\n   the AI made of it: %s" % (
            a["kind"], a["n"], said, " ".join((a.get("orchestrator") or "").split())[:700]))
        for c in (a.get("conversation") or [])[-4:] if a["kind"] == "start" else []:
            out.append("   (before it) %s" % c[:300])
    runs = (t.get("night") or {}).get("runs") or []
    for r in runs:
        out.append("run %d %s: %s" % (r["n"], r.get("status"), " ".join((r.get("report") or "").split())[:300]))
    if t.get("night") and not runs:
        out.append("THE NIGHT'S RESULT: %s, %s" % (t["night"].get("status"), t["night"].get("result")))
    return "\n".join(out)


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default="")
    ap.add_argument("--again", action="store_true")
    ap.add_argument("--fixes", action="store_true", help="only apply FIXES and the buildable marks to the stored file")
    a = ap.parse_args(argv)
    if a.fixes:
        with open(SCENARIOS, encoding="utf-8") as f:
            doc = json.load(f)
        doc["scenarios"] = [apply_fixes(sc) for sc in doc["scenarios"]]
        with open(SCENARIOS, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, ensure_ascii=False)
            f.write("\n")
        print("fixed: %s; not buildable: %s" % (", ".join(sorted(FIXES)), ", ".join(
            sc["id"] for sc in doc["scenarios"] if sc.get("buildable") is False)))
        return 0
    load_key()
    os.makedirs(os.path.join(ROOT, "usage"), exist_ok=True)
    os.environ["VFG_USAGE_QUEUE"] = os.path.join(ROOT, "usage")
    from vfg_llm import structured
    with open(DATA, encoding="utf-8") as f:
        tasks = [t for t in json.load(f)["tasks"] if t["date"] in ("2026-10-11", "2026-10-08")]
    only = [x for x in a.only.split(",") if x]
    if only:
        tasks = [t for t in tasks if t["id"] in only]
    try:
        with open(SCENARIOS, encoding="utf-8") as f:
            have = {s["id"]: s for s in json.load(f)["scenarios"]}
    except (OSError, ValueError, KeyError):
        have = {}
    cost = 0.0

    async def one(t):
        nonlocal cost
        if t["id"] in have and not a.again:
            return
        ans = await structured(record(t), Scenario, component="brain", call_id="implscenario", system=SYSTEM,
                               model=MODEL, reasoning="low", timeout=90)
        cost += ans.cost
        s = ans.value.model_dump()
        # the setup a task carried in the replay's data (a running copy to change) is kept as it was
        if t.get("setup"):
            s["setup"] = ["vfg " + " ".join(c) for c in t["setup"]]
        player = (t["asks"][0]["lines"] or [{}])[0].get("who") or "Steve"
        have[t["id"]] = {"id": t["id"], "date": t["date"], "kind": t["kind"], "player": player, **s,
                         "from": "derived by %s from the night's record" % MODEL, "reviewed": False}
        have[t["id"]]["try"] = try_of(have[t["id"]])
        print("%s: %d hidden wants, %d items" % (t["id"], len(s["hidden_wants"]), len(s["checklist"])), flush=True)
    await asyncio.gather(*(one(t) for t in tasks))
    order = {t["id"]: i for i, t in enumerate(json.load(open(DATA, encoding="utf-8"))["tasks"])}
    with open(SCENARIOS, "w", encoding="utf-8") as f:
        json.dump({"about": "impl_game.py's scenarios: integration/impl_scenarios.py",
                   "scenarios": [apply_fixes(x) for x in sorted(have.values(), key=lambda s: order.get(s["id"], 999))]},
                  f, indent=1,
                  ensure_ascii=False)
        f.write("\n")
    print("%d scenarios in %s; $%.4f" % (len(have), SCENARIOS, cost))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
