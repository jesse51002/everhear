#!/usr/bin/env python3
"""The brief writer, a stand-in for the brain's own until Prototype_Brain_Loop is merged: one
structured() call per hand-off or follow-up of the replay's data, whose answer is stored in
integration/replay/impl/tasks.json (impl_data.py), so every replay hands its
implementers the same briefs.

    cd brain && uv run python ../integration/impl_briefs_write.py [--only T207,T212] [--again]

Each call gets the sections of the brain's prompt that write a hand-off (THE NAME, THE DESCRIPTION,
THE BRIEF, for a hand-off, and the standing rules) or a follow-up (THE DESCRIPTION, THE
FOLLOW-UP'S BRIEF), read from brain/prompts/brain.md (the Brain_Loop worktree's until it is merged
here), then the player's own words and what the conversation said; and answers in Brain_Loop's
shapes, HandOff (rendered by its brief(), the text the implementer starts from) and FollowUp. A
follow-up also says the thing's whole DONE WHEN after the change, for the judge (impl_judge.py)
alone; the implementer never sees it. Asks are written in order within a task, since a follow-up
reads the task's brief and description so far; tasks run side by side.

The model is the brain's in Brain_Loop, google/gemini-3.8-flash at low reasoning; the usage
component is `brain` (the brain writes the briefs), ref `implreplay`, queued in
~/.vfg/impl-replay/usage/ (VFG_USAGE_QUEUE), never sent to a box. OPEN_ROUTER_API is read
from Internal/.env into this process's environment only: never printed, logged or written. An ask
that already has a brief keeps it unless --again."""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys

from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from impl_data import DATA, ROOT as OUT  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ENV_FILE = os.environ.get("VFG_IMPL_ENV") or "/var/home/jm/Documents/EverHear/Internal/.env"  # OPEN_ROUTER_API
# The brain's prompt: this repo's once Brain_Loop is merged, else the Brain_Loop worktree's (read only).
PROMPTS = [os.path.join(REPO, "brain", "prompts", "brain.md"),
           "/var/home/jm/Documents/EverHear/.worktrees/brain-loop/brain/prompts/brain.md"]
MODEL = "google/gemini-3.8-flash"
OWNER = "Steve"

sys.path.insert(0, os.path.join(REPO, "lib", "py"))


def load_key() -> None:
    """OPEN_ROUTER_API from Internal/.env into this process's environment, unprinted."""
    if os.environ.get("OPEN_ROUTER_API"):
        return
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*(?:export\s+)?OPEN_ROUTER_API\s*=\s*['\"]?([^'\"\s]+)", line)
            if m:
                os.environ["OPEN_ROUTER_API"] = m.group(1)
                return
    raise SystemExit("no OPEN_ROUTER_API in Internal/.env")


# ------------------------------------------------------------------ Brain_Loop's shapes, copied

def one_line(text) -> str:
    return " ".join(str(text or "").split())


class HandOff(BaseModel):
    """Brain_Loop's HandOff (brain/brain/call.py), copied: the brief in parts, rendered by brief()."""
    name: str = Field(description="the new task's name: lowercase letters, digits and underscores, the thing "
                                  "then whose (hot_fireball_krown)")
    description: str = Field(description="one or two sentences: what the thing will be, for whom, and how a "
                                         "player uses it")
    words: str = Field(description="the player's own words for it, quoted as they said them")
    picture: str = Field(description="the whole picture: how it behaves, looks, sounds and feels, in full")
    assumed: str = Field(description="what the player takes for granted and never said: what anyone would "
                                     "expect of such a thing")
    cast: str = Field(description="how it is cast or used, and for whom")
    done_when: list[str] = Field(description="the checklist the implementer checks item by item before it "
                                             "reports: each a short thing that can be seen in the game")

    def brief(self) -> str:
        items = "\n".join(f"- {one_line(x)}" for x in self.done_when if x.strip())
        return (f"THE PLAYER'S WORDS\n{self.words.strip()}\n\n"
                f"THE WHOLE PICTURE\n{self.picture.strip()}\n\n"
                f"WHAT THE PLAYER TAKES FOR GRANTED\n{self.assumed.strip()}\n\n"
                f"HOW IT IS CAST, AND FOR WHOM\n{self.cast.strip()}\n\n"
                f"DONE WHEN\n{items}")


class FollowUp(BaseModel):
    """Brain_Loop's FollowUp, copied, with done_when_now for the judge only."""
    task: str = Field(description="the task's name, exactly as given")
    description: str = Field(description="what the thing is after this change, for whom, and how a player uses it")
    brief: str = Field(description="what to change: one to three sentences its implementer acts on alone")
    cancel: bool = Field(False, description="true when the player says never mind")
    done_when_now: list[str] = Field(description="not for the implementer: the whole thing's DONE WHEN as it "
                                                 "should be after this change, every item still wanted, each a "
                                                 "short thing that can be seen in the game, for a judge")


# ------------------------------------------------------------------ the brain's prompt, in part

HEADS = ("THE NAME, for a hand-off", "THE DESCRIPTION, for a hand-off", "THE BRIEF, for a hand-off",
         "THE FOLLOW-UP'S BRIEF", "STANDING RULES AND LIMITS")


def sections(path: str) -> dict[str, str]:
    """Each heading of HEADS and its text, to the next heading in capitals."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    out = {}
    for head in HEADS:
        at = text.index(head)
        nxt = re.search(r"\n\n[A-Z][A-Z ,'’-]{6,}(\n|:| \(|,)", text[at + len(head):])
        out[head] = text[at:at + len(head) + (nxt.start() if nxt else len(text))].strip()
    return out


def prompt_path() -> str:
    for p in PROMPTS:
        try:
            with open(p, encoding="utf-8") as f:
                if "THE BRIEF, for a hand-off" in f.read():
                    return p
        except OSError:
            pass
    raise SystemExit("no brain.md with THE BRIEF, for a hand-off: %s" % ", ".join(PROMPTS))


INTRO = ("You are the brain of the AI on a Minecraft server (vanilla Java 26.3) where a few friends play and "
         "talk to you. THE OWNER: %s. The owner's word wins over any player's. " % OWNER)


def handoff_system(s: dict) -> str:
    return (INTRO + "A player's ask needs new work, so you hand it to an implementer, which builds it in the game "
            "(it writes scripts and their actions, runs commands, binds incantations) and never sees the "
            "conversation. Write the hand-off, as your prompt says:\n\n" + "\n\n".join(
                s[h] for h in ("THE NAME, for a hand-off", "THE DESCRIPTION, for a hand-off",
                               "THE BRIEF, for a hand-off", "STANDING RULES AND LIMITS")))


def follow_up_system(s: dict) -> str:
    return (INTRO + "A player's words change, fix or add to the thing a task's implementer made or is making; "
            "that implementer still has its session, its code and its brief. Write the follow-up, as your "
            "prompt says:\n\n" + "\n\n".join(s[h] for h in ("THE DESCRIPTION, for a hand-off",
                                                               "THE FOLLOW-UP'S BRIEF")))


def said(ask: dict) -> str:
    return "\n".join("#%s %s: %s" % (x.get("seq") or "-", x["who"], x["text"]) for x in ask["lines"]) or "(none)"


def handoff_prompt(task: dict, ask: dict) -> str:
    return ("THE CONVERSATION, oldest first:\n%s\n\nTHE PLAYER'S LINE, the ask you hand off:\n%s\n\n"
            "Write the hand-off." % ("\n".join(ask["conversation"]) or "(nothing before it)", said(ask)))


def follow_up_prompt(task: dict, ask: dict, history: list[dict]) -> str:
    first = history[0]
    lines = ["THE TASK: %s" % task["name"],
             "WHAT IT IS NOW: %s" % (history[-1].get("description") or first["description"]),
             "ITS HAND-OFF BRIEF:\n%s" % first["text"]]
    if len(history) > 1:
        lines.append("ITS FOLLOW-UPS SO FAR, oldest first:\n%s" % "\n".join(
            "%d. %s" % (i, h["text"]) for i, h in enumerate(history[1:], 1)))
    lines.append("THE CONVERSATION since the task began, oldest first (its record names it %s):\n%s" % (
        task["id"], "\n".join(ask["conversation"]) or "(nothing)"))
    if ask["source"] == "report":
        run = task["night"]["runs"][ask["n"] - 1]
        lines.append("THE IMPLEMENTER'S REPORT you are reading, which left something undone:\n%s" % run["report"])
        lines.append("Write the follow-up that names what did not land.")
    else:
        lines.append("THE PLAYER'S LINE, which changes this task's thing:\n%s" % said(ask))
        lines.append("Write the follow-up.")
    return "\n\n".join(lines)


# ------------------------------------------------------------------ the writer

class Writer:
    def __init__(self, s: dict, source: str, again: bool):
        self.s, self.source, self.again = s, source, again
        self.cost, self.calls, self.failed = 0.0, 0, []

    async def call(self, prompt: str, output, system: str):
        from vfg_llm import LlmError, structured
        try:
            ans = await structured(prompt, output, component="brain", call_id="implreplay", system=system,
                                   model=MODEL, reasoning="low", timeout=90)
        except LlmError as e:
            self.cost += e.cost
            raise
        self.cost += ans.cost
        self.calls += 1
        return ans

    async def task(self, task: dict) -> None:
        """Every ask of one task, in order."""
        history = []  # {"text", "description"} of the hand-off and each follow-up so far
        for ask in task["asks"]:
            have = ask.get("brief")
            if have and not self.again:
                history.append({"text": have["text"], "description": have.get("description", "")})
                continue
            try:
                if ask["kind"] == "start":
                    a = await self.call(handoff_prompt(task, ask), HandOff, handoff_system(self.s))
                    h = a.value
                    task["name"] = re.sub(r"[^a-z0-9_]", "_", h.name.lower())[:32]
                    task["checklist"] = [one_line(x) for x in h.done_when if x.strip()]
                    ask["brief"] = {"text": h.brief(), "name": task["name"], "description": h.description,
                                    "done_when": task["checklist"], "parts": h.model_dump(),
                                    "by": MODEL, "prompt": self.source, "cost": round(a.cost, 6)}
                else:
                    a = await self.call(follow_up_prompt(task, ask, history), FollowUp, follow_up_system(self.s))
                    f = a.value
                    ask["brief"] = {"text": f.brief.strip(), "description": f.description, "cancel": f.cancel,
                                    "done_when_now": [one_line(x) for x in f.done_when_now if x.strip()],
                                    "by": MODEL, "prompt": self.source, "cost": round(a.cost, 6)}
                    task["checklist"] = ask["brief"]["done_when_now"] or task.get("checklist")
            except Exception as e:  # noqa: BLE001 — the task's later asks need this one: stop here
                self.failed.append("%s ask %d: %s" % (task["id"], ask["n"], str(e)[:200]))
                return
            history.append({"text": ask["brief"]["text"], "description": ask["brief"]["description"]})
            print("%s ask %d (%s): %s" % (task["id"], ask["n"], ask["kind"], one_line(ask["brief"]["text"])[:160]),
                  flush=True)


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default="", help="task ids, comma-separated (default: every task)")
    ap.add_argument("--again", action="store_true", help="write every brief again")
    ap.add_argument("--at-once", type=int, default=6, help="tasks written side by side")
    a = ap.parse_args(argv)
    load_key()
    os.makedirs(os.path.join(OUT, "usage"), exist_ok=True)
    os.environ["VFG_USAGE_QUEUE"] = os.path.join(OUT, "usage")
    path = prompt_path()
    with open(path, "rb") as f:
        source = "%s sha256 %s" % (path.replace(os.path.expanduser("~"), "~"), hashlib.sha256(f.read()).hexdigest()[:12])
    with open(DATA, encoding="utf-8") as f:
        doc = json.load(f)
    only = set(filter(None, a.only.split(",")))
    tasks = [t for t in doc["tasks"] if not only or t["id"] in only]
    w = Writer(sections(path), source, a.again)
    gate = asyncio.Semaphore(a.at_once)

    async def one(t):
        async with gate:
            await w.task(t)
    await asyncio.gather(*(one(t) for t in tasks))
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print("%d calls, $%.4f (OpenRouter's cost); %d failed%s" % (
        w.calls, w.cost, len(w.failed), "".join("\n  " + x for x in w.failed)))
    return 1 if w.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
