#!/usr/bin/env python3
"""Tests 3 and 4 of Prototype_Implementer_Harness, messages and "never mind", through the real
Implementers and the Claude Code adapter, on DeepSeek V4.1 Flash through OpenRouter, on a realmc
server, on the stand-in brain's briefs of tasks.json:

  messages  Hot fireball's hand-off (T207), then its first two follow-ups' briefs sent 2 s and 20 s
            after the run's first event: both land in the same run (no second start or resume), the
            runner logs each read, and its one report covers them.
  late      a follow-up sent the moment the run writes its report: never lost; it is read in a next
            turn of the same run, or resumes the session (its marker word is in a report).
  split     Destroy Everything (T212): its hand-off, then the three parts of one line (asks 4, 5
            and 6), sent one after another after its run ended: one resume and one report (the
            later parts go into it as messages, or are joined to its prompt while it starts).
  cancel    a follow-up that edits a script, a running copy and an incantation (a copy of ice_walk
            run for Steve and bound to "frost steps"), cancelled 2 s after it first changes one of
            them: the made files, the copy and the binding come back, and the report says Cancelled:.
  cancelnew a new task (T18, Alex's pretty house) cancelled 3 s after it first makes something (a
            made file, a copy, a place), or at 60 s if it has made nothing yet:
            what it created is gone, no copy of it runs, no place of it is saved, Cancelled:.

    export REALMC_ROOT=…/.worktrees/script-library/integration/realmc/.server
    integration/realmc/up ih-d1 --fresh
    cd brain && uv run python ../integration/impl_messages.py --name ih-d1 [check …]

Writes OUT/brain.log, OUT/checks.json and OUT/checks.md; PASS or FAIL per check. Spend is priced by
config.py's table. OPEN_ROUTER_API is read from Internal/.env into this process's environment only."""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "brain"))
sys.path.insert(0, os.path.join(HERE, "realmc"))
sys.path.insert(0, HERE)

import players  # noqa: E402
import realmc  # noqa: E402
from impl_briefs_write import load_key  # noqa: E402
from impl_data import DATA, ROOT  # noqa: E402
from impl_replay import set_aside  # noqa: E402
from impl_wild import Tee, use_realmc  # noqa: E402

CHECKS = ("messages", "late", "split", "cancel", "cancelnew")
MARKER = "PINEAPPLE"


def digest(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None


def briefs(tid: str) -> list[str]:
    with open(DATA, encoding="utf-8") as f:
        t = next(t for t in json.load(f)["tasks"] if t["id"] == tid)
    return [a["brief"]["text"] for a in t["asks"]]


def binding(srv, player: str, phrase: str) -> dict | None:
    try:
        with open(os.path.join(srv.vfg, "incantations.json"), encoding="utf-8") as f:
            entries = json.load(f).get("incantations") or []
    except (OSError, ValueError):
        return None
    hit = [e for e in entries if e.get("player") == player and e.get("phrase") == phrase]
    return {k: hit[-1].get(k) for k in ("action", "values", "cooldown_s")} if hit else None


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--name", required=True)
    ap.add_argument("checks", nargs="*", default=list(CHECKS))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = os.path.abspath(a.out or os.path.join(ROOT, "messages-%s" % a.name))
    os.makedirs(out, exist_ok=True)
    moved = set_aside(a.name)
    use_realmc(a.name)
    load_key()
    os.environ.update(VFG_IMPL_PROVIDER="openrouter", VFG_USAGE_QUEUE=out)
    srv = realmc.Server(a.name)
    p = players.Players(a.name)

    def spawn_both():
        """Both fake players on: a test before may have killed one, and a dead fake player leaves."""
        on = p.online()
        for who, at in (("Steve", None), ("Alex", (12.5, 85, 0.5))):
            if who not in on:
                p.spawn(who, at=at)
    spawn_both()
    tee = Tee(os.path.join(out, "brain.log"))
    sys.stdout = tee
    from brain.implementers import actions, harness as harnesses
    from brain.implementers.config import ImplementerConfig, log
    from brain.implementers.harness.base import Text, ToolCall
    from brain.implementers.runner import CANCEL, Implementers

    log("check", "set aside: %s" % (", ".join(moved) or "nothing"))
    cfg = ImplementerConfig.load()
    h = harnesses.make(cfg)
    counts = {"start": 0, "resume": 0}
    start, resume = h.start, h.resume

    async def counted_start(*x, **k):
        counts["start"] += 1
        return await start(*x, **k)

    async def counted_resume(*x, **k):
        counts["resume"] += 1
        return await resume(*x, **k)

    h.start, h.resume = counted_start, counted_resume
    reports: asyncio.Queue = asyncio.Queue()
    watchers: list = []

    class Watched(Implementers):
        def _seen(self, t, ev, *rest, **kw):
            super()._seen(t, ev, *rest, **kw)
            for w in list(watchers):
                w(t.name, ev)

    async def on_report(name, status, report, description, made):
        await reports.put({"task": name, "status": status, "report": report, "at": time.time()})

    imps = Watched(cfg, on_report, harness=h)

    async def report_of(name, timeout=720):
        while True:
            r = await asyncio.wait_for(reports.get(), timeout)
            if r["task"] == name:
                return r

    async def quiet(name, seconds):
        try:
            return await report_of(name, seconds)
        except TimeoutError:
            return None

    def reads(name, since):
        return sum(1 for ln in tee.lines[since:] if re.search(r" implementer %s read its message" % re.escape(name), ln))

    record: dict = {}

    def runs_since(before):
        return {k: counts[k] - before[k] for k in counts}

    t207 = briefs("T207")
    t212 = briefs("T212")
    try:
        if "messages" in a.checks or "late" in a.checks:
            name = "hot_fireball_msg"
            first = {"at": None}

            def on_first(n, ev):
                if n == name and first["at"] is None:
                    first["at"] = time.time()
            watchers.append(on_first)
            before, mark = dict(counts), len(tee.lines)
            log("check", "messages: the hand-off, then follow-ups 2 s and 20 s after the run's first event")
            await imps.start(name, t207[0])
            while first["at"] is None:
                await asyncio.sleep(0.2)
            await asyncio.sleep(max(0.0, first["at"] + 2 - time.time()))
            live2 = name in imps.running()
            await imps.follow_up(name, t207[1])
            await asyncio.sleep(max(0.0, first["at"] + 20 - time.time()))
            live20 = name in imps.running()
            await imps.follow_up(name, t207[2])
            r = await report_of(name)
            extra = await quiet(name, 10)
            text = r["report"].lower()
            record["messages"] = {
                "runs": runs_since(before), "reads": reads(name, mark), "live_at_2s": live2, "live_at_20s": live20,
                "extra_report": bool(extra), "status": r["status"], "covers_mobs": "mob" in text,
                "covers_damage": "damage" in text, "report": r["report"][:1500]}
            m = record["messages"]
            m["pass"] = (m["runs"] == {"start": 1, "resume": 0} and m["reads"] >= 2 and not m["extra_report"]
                         and m["covers_mobs"] and m["covers_damage"])
            log("check", "messages: %s" % json.dumps(m)[:600])

            if "late" in a.checks:
                sent = {"at": None}

                def on_report_text(n, ev):
                    if n == name and isinstance(ev, Text) and sent["at"] is None and (
                            re.match(r"\W*(done|failed)\b", ev.text, re.I) or "DESCRIPTION:" in ev.text):
                        sent["at"] = time.time()
                        log("check", "late: the report is written; the follow-up goes now")
                        asyncio.get_running_loop().create_task(imps.follow_up(
                            name, "One more thing: begin your report's first line with the word %s." % MARKER))
                watchers.append(on_report_text)
                before, mark = dict(counts), len(tee.lines)
                await imps.follow_up(name, t207[3])
                got = [await report_of(name)]
                while not any(MARKER in x["report"] for x in got):
                    x = await quiet(name, 300)
                    if x is None:
                        break
                    got.append(x)
                watchers.remove(on_report_text)
                record["late"] = {"runs": runs_since(before), "reports": len(got), "sent": sent["at"] is not None,
                                  "marker_reported": any(MARKER in x["report"] for x in got),
                                  "reads": reads(name, mark)}
                record["late"]["pass"] = record["late"]["sent"] and record["late"]["marker_reported"]
                log("check", "late: %s" % json.dumps(record["late"])[:600])

        if "split" in a.checks:
            spawn_both()
            name = "destroy_split"
            log("check", "split: the hand-off, then one line's three parts after its run")
            await imps.start(name, t212[0])
            await report_of(name)
            before, mark = dict(counts), len(tee.lines)
            for text in t212[4:7]:
                await imps.follow_up(name, text)
            r = await report_of(name)
            extra = await quiet(name, 10)
            record["split"] = {"runs": runs_since(before), "reads": reads(name, mark), "extra_report": bool(extra),
                               "status": r["status"], "report": r["report"][:1500]}
            s = record["split"]
            # one run: the parts go in as messages into the run the first resumed, or, queued behind it before
            # it started, joined into its one prompt (reads 0)
            s["pass"] = s["runs"] == {"start": 0, "resume": 1} and not s["extra_report"]
            log("check", "split: %s" % json.dumps(s)[:600])

        if "cancel" in a.checks:
            spawn_both()
            name = "frost_steps_steve"
            log("check", "cancel: a copy of ice_walk running for Steve and bound to frost steps")
            await imps.start(name, (
                "Steve wants a path of packed ice under his feet wherever he walks. Copy the library's ice_walk "
                "script with vfg script new %s --from ice_walk, keep its code as it is, run its action for Steve "
                "as=%s so it is running when you report, and bind the incantation \"frost steps\" for Steve to it "
                "(incantation_create, cooldown_s=3)." % (name, name)))
            made = await report_of(name)
            script = os.path.join(cfg.repo, "made", cfg.server, "scripts", name, "main.py")
            action = os.path.join(cfg.repo, "made", cfg.server, "actions", name + ".py")
            snap = lambda: {"script": digest(script), "action": digest(action),  # noqa: E731
                            "copy": ((actions.read_running(cfg.home) or {}).get(name) or {}).get("values"),
                            "running": name in (actions.read_running(cfg.home) or {}),
                            "binding": binding(srv, "Steve", "frost steps")}
            before = snap()
            changed = {"at": None, "what": None}

            def on_change(n, ev):
                if n != name or changed["at"] or not isinstance(ev, ToolCall):
                    return
                cmd = json.dumps(ev.input)
                if ev.name in ("Edit", "Write") or re.search(r"vfg (script (set|stop)|action run)", cmd):
                    changed.update(at=time.time(), what=cmd[:160])
                    log("check", "cancel: it changes things (%s); the cancel goes in 2 s" % cmd[:120])

                    async def later():
                        await asyncio.sleep(2)
                        await imps.follow_up(name, CANCEL)
                    asyncio.get_running_loop().create_task(later())
            watchers.append(on_change)
            await imps.follow_up(name, (
                "Steve says the path should be blue ice and wider. Change its script so the path is blue_ice "
                "instead of packed_ice, set the running copy's under_radius to 4 and restart it, and change the "
                "\"frost steps\" incantation's cooldown_s to 10."))
            r = await report_of(name)
            extra = await quiet(name, 15)
            watchers.remove(on_change)
            after = snap()
            record["cancel"] = {"made": made["report"][:600], "before": before, "after": after,
                                "changed": changed, "report": r["report"][:1500], "extra_report": bool(extra),
                                "cancelled": r["report"].lstrip().lower().startswith("cancelled"),
                                "put_back": [ln.split(" ", 2)[2] for ln in tee.lines if " put back" in ln
                                             and name in ln][-3:]}
            c = record["cancel"]
            c["files_back"] = before["script"] == after["script"] and before["action"] == after["action"]
            c["copy_back"] = before["running"] == after["running"] and before["copy"] == after["copy"]
            c["binding_back"] = before["binding"] == after["binding"]
            c["pass"] = bool(changed["at"]) and c["cancelled"] and c["files_back"] and c["copy_back"] and c["binding_back"]
            log("check", "cancel: %s" % json.dumps(c, default=str)[:900])

        if "cancelnew" in a.checks:
            spawn_both()
            with open(DATA, encoding="utf-8") as f:
                t18 = next(t for t in json.load(f)["tasks"] if t["id"] == "T18")
            name = t18["name"] + "_cn"
            wrote = {"at": None}

            def on_write(n, ev):
                if n != name or wrote["at"] or not isinstance(ev, ToolCall):
                    return
                cmd = json.dumps(ev.input)
                if (ev.name in ("Edit", "Write") and "made/" in cmd) or re.search(
                        r"vfg (script new|action run|preset save|place save)", cmd):
                    wrote.update(at=time.time(), what=cmd[:160])
                    log("check", "cancelnew: it makes something (%s); the cancel goes in 3 s" % cmd[:120])

                    async def later():
                        await asyncio.sleep(3)
                        await imps.follow_up(name, CANCEL)
                    asyncio.get_running_loop().create_task(later())
            watchers.append(on_write)

            async def fallback():  # it may look around a long while before it writes: cancel at 60 s anyway
                await asyncio.sleep(60)
                if not wrote["at"]:
                    wrote.update(at=time.time(), what="60 s passed with nothing written")
                    log("check", "cancelnew: nothing written in 60 s; the cancel goes now")
                    await imps.follow_up(name, CANCEL)
            places_before = set(json.loads(
                (await asyncio.to_thread(lambda: os.popen("vfg place list --json 2>/dev/null").read())) or "{}"))
            await imps.start(name, t18["asks"][0]["brief"]["text"])
            timer = asyncio.get_running_loop().create_task(fallback())
            r = await report_of(name)
            timer.cancel()
            extra = await quiet(name, 15)
            watchers.remove(on_write)
            places_after = set(json.loads(
                (await asyncio.to_thread(lambda: os.popen("vfg place list --json 2>/dev/null").read())) or "{}"))
            made_dir = os.path.join(cfg.repo, "made", cfg.server)
            left = sorted(x for k in ("scripts", "actions", "presets") for x in (
                os.listdir(os.path.join(made_dir, k)) if os.path.isdir(os.path.join(made_dir, k)) else [])
                if x.startswith(name))
            running = sorted(n for n in (actions.read_running(cfg.home) or {}) if n.startswith(name))
            record["cancelnew"] = {"wrote": wrote, "report": r["report"][:1500], "extra_report": bool(extra),
                                   "cancelled": r["report"].lstrip().lower().startswith("cancelled"),
                                   "left": left, "running": running, "new_places": sorted(places_after - places_before)}
            c = record["cancelnew"]
            c["pass"] = bool(wrote["at"]) and c["cancelled"] and not left and not running and not c["new_places"]
            log("check", "cancelnew: %s" % json.dumps(c, default=str)[:900])
        await imps.close(5)
    finally:
        sys.stdout = sys.__stdout__
        tee.flush()
        p.close()
    cost = round(sum(float(m.group(1)) for ln in tee.lines for m in [re.search(r" request \S+ .* cost=([\d.]+)", ln)]
                     if m), 5)
    record["cost"] = cost
    with open(os.path.join(out, "checks.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=1, default=str)
    L = ["## Tests 3 and 4: messages and never mind (%s)" % cfg.model, ""]
    for k in CHECKS:
        if k in record:
            v = record[k]
            L.append("- **%s**: %s — %s" % (k, "PASS" if v.get("pass") else "FAIL", json.dumps(
                {x: y for x, y in v.items() if x not in ("report", "made", "before", "after")}, default=str)[:500]))
    L += ["", "Spend $%.4f, priced by config.py's table." % cost]
    with open(os.path.join(out, "checks.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    return 0 if all(record[k].get("pass") for k in CHECKS if k in record) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
