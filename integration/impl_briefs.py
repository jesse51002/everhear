#!/usr/bin/env python3
"""Test 1 of Prototype_Implementer_Harness, the brief rebuilt offline: the real system prompt (the
implementer's rules, then `vfg prefix`) and each task's brief, built by the real composer
(brief.compose and examples.build) with no implementer model, for the six tasks of 2026-10-11 and
the 109 implementer tasks of 2026-10-08. Each part's size is logged in characters and in tokens at
3.0 characters a token, DeepSeek's (Measured on DeepSeek).

    cd brain && uv run python ../integration/impl_briefs.py [--jev NAME] [--out DIR]

The router: the word fallback for every task (Jev made to fail, as when it is down); with --jev
NAME, also live Jev through `vfg route` on the six, against realmc server NAME's stand-in box, its
budget raised to 10 s so it never falls back (its usage rows go to that stand-in, ref implbriefs).
The brain's brief is the stand-in brain's (impl_briefs_write.py) where tasks.json has one, else the
orchestrator's of the night: on 2026-10-08, its task and context. The server state is faked (Steve
online at the arena, noon), no place is saved, nothing runs and nothing is made, so a brief holds
only what the router picked and the job named.

Passes when no brief holds more than 3 router scripts or 7 actions in full, and T212's first
request, its system prompt and brief together, is under 110K tokens. Writes OUT/briefs.jsonl (one
row per brief) and OUT/test1.md (the table); exit 1 when it fails."""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "brain"))
sys.path.insert(0, os.path.join(REPO, "toolkit", "lib"))

import route as R  # noqa: E402  (toolkit's: the cards, the picks, the fallback)
from brain.implementers import brief, examples, prefix as implementer_prefix  # noqa: E402
from brain.implementers.config import ImplementerConfig, set_quiet  # noqa: E402
from brain.implementers.vfg import VfgResult  # noqa: E402
sys.path.insert(0, HERE)
from impl_data import DATA, ROOT  # noqa: E402

TASKS_0810 = os.path.join(HERE, "replay", "tasks.jsonl")
REALMC = os.path.join(HERE, "realmc")
CHARS_A_TOKEN = 3.0
SERVER = "ih-offline"   # an empty made/<server>/: nothing made
STATE = {"online": [{"name": "Steve", "pos": [0, 85, 0], "dimension": "overworld"},
                    {"name": "Alex", "pos": [4, 85, 2], "dimension": "overworld"}],
         "time": {"phase": "noon"}, "weather": "clear", "instances": [], "rcon": None}


def tok(chars: int) -> int:
    return round(chars / CHARS_A_TOKEN)


def jobs() -> list[dict]:
    """(id, date, task name, the brain's brief, whose it is) for the six and the 109."""
    with open(DATA, encoding="utf-8") as f:
        data = {t["id"]: t for t in json.load(f)["tasks"]}
    out = []
    for t in data.values():
        if t["date"] != "2026-10-11":
            continue
        a = t["asks"][0]
        text = (a.get("brief") or {}).get("text") or a["orchestrator"]
        out.append({"id": t["id"], "date": t["date"], "task": t.get("name") or t["id"].lower(), "brief": text,
                    "by": "brain stand-in" if a.get("brief") else "orchestrator"})
    with open(TASKS_0810, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if not (r.get("run") or {}).get("jobs"):
                continue
            t = data.get(r["id"])
            written = t and t["asks"][0].get("brief")
            text = written["text"] if written else "%s. %s" % (r["task"], r["context"])
            out.append({"id": r["id"], "date": "2026-10-08", "task": (t or {}).get("name") or r["id"].lower(),
                        "brief": text, "by": "brain stand-in" if written else "orchestrator"})
    return out


class FakeVfg:
    """vfg for the composer: the router's picks given, the state above, no saved place."""

    def __init__(self, picks: dict):
        self.picks = picks

    async def __call__(self, *argv, timeout=None, stdin=None):
        if argv[:1] == ("route",):
            return VfgResult(0, json.dumps(self.picks), "")
        if argv[:1] == ("state",):
            return VfgResult(0, json.dumps(STATE), "")
        if argv[:2] == ("place", "list"):
            return VfgResult(0, "none\n", "")
        return VfgResult(1, "", "not faked")


def words(cards: list, text: str, task: str) -> dict:
    """The router's picks by the job's words: Jev made to fail."""
    def down(state, questions, budget):
        raise R.JevFailed("offline: the word fallback")
    return R.route(cards, text, task, "", "", down, 0.3, 3.0)


def live_jev(name: str, text: str) -> dict | None:
    """`vfg route` on realmc server NAME, Jev given 10 s; None when it failed."""
    env = dict(os.environ)
    out = subprocess.run([sys.executable, os.path.join(REALMC, "realmc.py"), "env", name],
                         capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        if line.startswith("unset "):
            for k in line.split()[1:]:
                env.pop(k, None)
        elif line.startswith("export "):
            k, v = line[len("export "):].split("=", 1)
            env[k] = (os.path.join(REALMC, "bin") + os.pathsep + env.get("PATH", "")) if k == "PATH" else v.strip("'")
    env.update(VFG_ROUTE_TIMEOUT_S="10", VFG_SERVER=SERVER)
    r = subprocess.run(["vfg", "route", "--ref", "implbriefs"], input=text, capture_output=True, text=True,
                       env=env, timeout=60)
    if r.returncode != 0:
        print("  vfg route failed: %s" % (r.stderr.strip() or r.stdout.strip())[:300], file=sys.stderr)
        return None
    sys.stderr.write("  " + r.stderr.strip().splitlines()[-1][:300] + "\n")
    return json.loads(r.stdout)


_build = examples.build


async def compose(cfg, job: dict, picks: dict) -> tuple[str, dict]:
    """The brief as the runner composes it (brief.compose), and the sizes of its EXAMPLES block's parts."""
    sizes: dict = {}

    async def build(cfg, job_text, task, run, sizes=None):
        block = await _build(cfg, job_text, task, run, sizes=sizes)
        captured.update(sizes or {})
        return block
    captured = sizes
    examples.build = build
    try:
        text = await brief.compose(job["brief"], job["task"], cfg, FakeVfg(picks))
    finally:
        examples.build = _build
    return text, sizes


def row(job: dict, router: str, picks: dict, text: str, sizes: dict, system: int) -> dict:
    router_scripts = len((picks or {}).get("scripts") or [])
    kept_scripts = sizes.get("scripts_n", 0)
    r = {"id": job["id"], "date": job["date"], "task": job["task"], "brief_by": job["by"], "router": router,
         "brain_brief": len(job["brief"]), "brief": len(text), "system": system,
         "parts": {k: sizes.get(k, 0) for k in examples.PARTS},
         "router_scripts": kept_scripts, "router_scripts_picked": router_scripts,
         "actions_in_full": sizes.get("actions_n", 0), "presets": sizes.get("presets_n", 0),
         "named": sizes.get("named_n", 0),
         "scripts_names": [s.get("name") for s in (picks or {}).get("scripts") or []][:3],
         "actions_names": [a.get("name") for a in (picks or {}).get("actions") or []][:7],
         "cut": (picks or {}).get("cut")}
    r["first_request_tokens"] = tok(system + len(text))
    r["ok"] = r["router_scripts"] <= 3 and r["actions_in_full"] <= 7
    return r


def table(rows: list[dict], system_parts: dict) -> str:
    six = [r for r in rows if r["date"] == "2026-10-11"]
    ten = [r for r in rows if r["date"] == "2026-10-08" and r["router"] == "words"]
    L = ["## Test 1: the brief rebuilt offline", "",
         "Tokens at %.1f characters a token. The system prompt: the rules %s, how a script is written, the kit "
         "and the library's first lines %s, the library's actions as menu lines %s: **%s chars, %s tokens**, the "
         "same for every task." % (CHARS_A_TOKEN, "{:,}".format(system_parts["rules"]),
                                  "{:,}".format(system_parts["prefix"] - system_parts["menu"]),
                                  "{:,}".format(system_parts["menu"]), "{:,}".format(system_parts["system"]),
                                  "{:,}".format(tok(system_parts["system"]))), "",
         "| Task | Router | Brain's brief | Actions in full | Router scripts | Presets | Named | Brief in all | "
         "First request |", "|---|---|---|---|---|---|---|---|---|"]
    for r in six:
        p = r["parts"]
        L.append("| %s %s | %s | %s tok | %d, %s tok | %d, %s tok | %d, %s tok | %d | %s tok | **%s tok** |" % (
            r["id"], r["task"], r["router"], "{:,}".format(tok(r["brain_brief"])), r["actions_in_full"],
            "{:,}".format(tok(p["actions"])), r["router_scripts"], "{:,}".format(tok(p["scripts"])), r["presets"],
            "{:,}".format(tok(p["presets"])), r["named"], "{:,}".format(tok(r["brief"])),
            "{:,}".format(r["first_request_tokens"])))
    if ten:
        b = sorted(r["brief"] for r in ten)
        f = sorted(r["first_request_tokens"] for r in ten)
        L += ["", "**The 109 of 2026-10-08, by words:** brief %s–%s tokens (median %s); first request %s–%s "
                  "tokens (median %s); router scripts at most %d, actions in full at most %d; %d of %d within "
                  "the caps." % ("{:,}".format(tok(b[0])), "{:,}".format(tok(b[-1])), "{:,}".format(tok(b[len(b) // 2])),
                                 "{:,}".format(f[0]), "{:,}".format(f[-1]), "{:,}".format(f[len(f) // 2]),
                                 max(r["router_scripts"] for r in ten), max(r["actions_in_full"] for r in ten),
                                 sum(r["ok"] for r in ten), len(ten))]
    return "\n".join(L) + "\n"


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--jev", metavar="NAME", help="also route the six through live Jev on this realmc server")
    ap.add_argument("--out", default=os.path.join(ROOT, "test1"))
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    set_quiet(True)
    home = tempfile.mkdtemp(prefix="impl-briefs-home-")
    env = dict(os.environ, VFG_HOME=home, VFG_REPO=REPO, VFG_SERVER=SERVER)
    r = subprocess.run([os.path.join(REPO, "toolkit", "vfg"), "prefix"], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise SystemExit("vfg prefix failed: %s" % r.stderr.strip()[:300])
    cfg = ImplementerConfig.load({"VFG_REPO": REPO, "VFG_HOME": home, "VFG_SERVER": SERVER})
    with open(cfg.prompt, encoding="utf-8") as f:
        rules = f.read().replace("%REPO%", cfg.repo).replace("%SERVER%", "main")
    system = implementer_prefix.system_prompt(rules, r.stdout)
    menu_at = r.stdout.find("THE LIBRARY'S ACTIONS")
    sysparts = {"rules": len(rules), "prefix": len(r.stdout), "menu": len(r.stdout) - menu_at if menu_at >= 0 else 0,
                "system": len(system)}
    with open(os.path.join(home, "actions.json"), encoding="utf-8") as f:
        cards = R.load_cards(REPO, SERVER, json.load(f))
    rows = []
    for job in jobs():
        tries = [("words", words(cards, job["brief"], job["task"]))]
        if a.jev and job["date"] == "2026-10-11":
            picks = live_jev(a.jev, job["brief"])
            if picks is not None:
                tries.append(("jev" if not picks.get("fallback") else "jev fell back", picks))
        for router, picks in tries:
            text, sizes = await compose(cfg, job, picks)
            rows.append(row(job, router, picks, text, sizes, len(system)))
            if job["date"] == "2026-10-11":
                with open(os.path.join(a.out, "%s_%s.txt" % (job["id"], router.replace(" ", "_"))), "w") as f:
                    f.write(text)
    with open(os.path.join(a.out, "briefs.jsonl"), "w", encoding="utf-8") as f:
        for x in rows:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    t212 = max((x["first_request_tokens"] for x in rows if x["id"] == "T212"), default=0)
    bad = [x for x in rows if not x["ok"]]
    md = table(rows, sysparts)
    md += "\n%s: no brief over 3 router scripts or 7 actions in full (%d over, of %d built); T212's first " \
          "request at most %s tokens (bar 110,000).\n" % ("PASS" if not bad and t212 < 110_000 else "FAIL", len(bad),
                                                          len(rows), "{:,}".format(t212))
    with open(os.path.join(a.out, "test1.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    for x in bad:
        print("over the caps: %s %s: %d scripts, %d actions" % (x["id"], x["router"], x["router_scripts"],
                                                                 x["actions_in_full"]))
    return 0 if not bad and t212 < 110_000 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
