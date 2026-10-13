#!/usr/bin/env python3
"""The real check of Prototype_Implementer_Harness's step 4 (messages, "never mind", the limits),
through the real Implementers and the Claude Code adapter on OpenRouter's DeepSeek V4.1 Flash, on a
realmc server:

  messages  Hot fireball's first ask, with two follow-ups sent 2 s and 20 s into its run: both go
            into the same run, and its one report covers them.
  late      a follow-up sent the moment the run's report is written, before its result: never lost
            (it starts the next turn of the same run, or resumes the session).
  cancel    a copy of ice_walk made and run for Steve; then a follow-up that edits its code and its
            running copy, cancelled 2 s after it first changes the copy: the files and the copy come back.
  putback   no model: a snapshot taken through the guard, then real vfg commands that change a made
            file and a running copy, start a copy and save a place; code's put-back undoes them.
  limit     Speed Dash, an ask no command builds, with 8 turns: the warning at 3, then a Failed
            report, and code's put-back.

    export REALMC_ROOT=…/.worktrees/script-library/integration/realmc/.server
    integration/realmc/up ih-a --fresh && eval "$(integration/realmc/env ih-a)" && export VFG_SERVER=ih-a
    python3 integration/realmc/players.py spawn Steve && vfg action list --json >/dev/null
    cd brain && uv run python ../integration/impl_messages_check.py OUT_DIR [check …] > OUT_DIR/brain.log

OPEN_ROUTER_API is read from Internal/.env into this process's environment only: never printed,
logged or written. Writes OUT_DIR/checks.json."""
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

ENV_FILE = "/var/home/jm/Documents/EverHear/Internal/.env"
REPLAYED = "/var/home/jm/Documents/EverHear/.worktrees/ds-cost/integration/ds_cost.out/relay/T207.json"
SPEED_DASH = ("Create an incantation for Steve with the phrase \"Speed dash\" that launches Steve forward several "
              "blocks in the direction he is looking, fast, like a dash; not a Speed effect.")


def load_key() -> None:
    with open(ENV_FILE) as f:
        for line in f:
            m = re.match(r"^\s*(?:export\s+)?OPEN_ROUTER_API\s*=\s*['\"]?([^'\"\s]+)", line)
            if m:
                os.environ["OPEN_ROUTER_API"] = m.group(1)
                return
    raise SystemExit("no OPEN_ROUTER_API in Internal/.env")


def t207() -> list[str]:
    with open(REPLAYED) as f:
        return [r["ask"] for r in json.load(f)["runs"]]


def digest(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None


async def main(out: str, which: list[str]) -> None:
    load_key()
    os.environ["VFG_IMPL_PROVIDER"] = "openrouter"
    os.environ["VFG_USAGE_QUEUE"] = out
    from brain.implementers import actions, harness as harnesses
    from brain.implementers.config import ImplementerConfig, log
    from brain.implementers.harness.base import Done, Text, ToolCall
    from brain.implementers.runner import CANCEL, Implementers

    record: dict = {}

    class Watched(Implementers):
        """Implementers with a hook on each event, for the checks' timing."""
        watchers: list = []

        def _seen(self, t, ev, spent, st=None):
            super()._seen(t, ev, spent, st)
            for w in list(self.watchers):
                w(t.name, ev)

    def make(max_turns=None):
        cfg = ImplementerConfig.load()
        if max_turns:
            cfg.max_turns = max_turns
        h = harnesses.make(cfg)
        counts = {"start": 0, "resume": 0}
        start, resume = h.start, h.resume

        async def counted_start(*a, **k):
            counts["start"] += 1
            return await start(*a, **k)

        async def counted_resume(*a, **k):
            counts["resume"] += 1
            return await resume(*a, **k)

        h.start, h.resume = counted_start, counted_resume
        reports: asyncio.Queue = asyncio.Queue()

        async def on_report(name, status, report, description, made):
            await reports.put((name, status, report))

        imps = Watched(cfg, on_report, harness=h)
        imps.watchers = []
        return cfg, imps, counts, reports

    async def next_report(reports, timeout=600):
        return await asyncio.wait_for(reports.get(), timeout)

    async def quiet(reports, seconds):
        """Any report that comes within `seconds`."""
        try:
            return await asyncio.wait_for(reports.get(), seconds)
        except TimeoutError:
            return None

    cfg, imps, counts, reports = make()
    asks = t207()

    if "messages" in which:
        log("check", "messages: start, then a message at 2 s and at 20 s")
        t0 = time.time()
        await imps.start("hot_fireball", asks[0])
        await asyncio.sleep(2)
        await imps.follow_up("hot_fireball", asks[1])
        await asyncio.sleep(18)
        await imps.follow_up("hot_fireball", asks[3])
        first = await next_report(reports)
        extra = await quiet(reports, 5)
        record["messages"] = {"runs": dict(counts), "report": first, "extra_report": extra,
                              "seconds": round(time.time() - t0, 1)}
        log("check", "messages: %s" % json.dumps(record["messages"])[:600])

    if "late" in which:
        log("check", "late: a follow-up the moment the run writes its report")
        before = dict(counts)
        sent = {"done": False}

        def on_event(name, ev):
            if name == "hot_fireball" and isinstance(ev, Text) and not sent["done"] and (
                    re.match(r"\W*(done|failed)\b", ev.text, re.I) or "DESCRIPTION:" in ev.text):
                sent["done"] = True
                log("check", "late: the report is written; the follow-up goes now")
                asyncio.get_running_loop().create_task(imps.follow_up(
                    "hot_fireball", "One more thing: start your report's first line with the word PINEAPPLE."))

        imps.watchers.append(on_event)
        await imps.follow_up("hot_fireball", "Make the explosion a little smaller than it is now.")
        got = [await next_report(reports)]
        while not any("PINEAPPLE" in r[2] for r in got):
            r = await quiet(reports, 240)
            if r is None:
                break
            got.append(r)
        imps.watchers.remove(on_event)
        record["late"] = {"runs": {k: counts[k] - before[k] for k in counts}, "reports": got,
                          "pineapple": any("PINEAPPLE" in r[2] for r in got), "sent": sent["done"]}
        log("check", "late: %s" % json.dumps(record["late"])[:600])

    if "cancel" in which:
        log("check", "cancel: a running copy of ice_walk, then a follow-up that changes it, cancelled")
        name = os.environ.get("CANCEL_TASK", "frost_steps_steve")
        await imps.start(name, "Steve wants a path of packed ice under his feet wherever he walks. Copy the library's "
                               "ice_walk script with vfg script new %s --from ice_walk, keep it as it is, and run its "
                               "action for Steve as=%s so it is running when you report. Do not change its code." % (name, name))
        made_report = await next_report(reports)
        script = os.path.join(cfg.repo, "made", cfg.server, "scripts", name, "main.py")
        action = os.path.join(cfg.repo, "made", cfg.server, "actions", name + ".py")
        copy_before = (actions.read_running(cfg.home) or {}).get(name)
        before = {"script": digest(script), "action": digest(action), "copy": copy_before}
        changed = {"at": None}

        def on_event(n, ev):
            if n != name or changed["at"] or not isinstance(ev, ToolCall):
                return
            cmd = str(ev.input.get("command") or "")
            if re.search(r"vfg (script (set|stop)|action run)", cmd):  # it changes the copy
                changed["at"] = time.time()
                log("check", "cancel: it changes the copy (%s); the cancel goes in 2 s" % cmd[:120])

                async def later():
                    await asyncio.sleep(2)
                    await imps.follow_up(name, CANCEL)

                asyncio.get_running_loop().create_task(later())

        imps.watchers.append(on_event)
        await imps.follow_up(name, "Change its code so the path is blue_ice instead of packed ice, make it wider "
                                   "(radius 4) on the running copy, and restart the copy so Steve sees the new path.")
        cancel_report = await next_report(reports)
        extra = await quiet(reports, 5)
        imps.watchers.remove(on_event)
        copy_after = (actions.read_running(cfg.home) or {}).get(name)
        after = {"script": digest(script), "action": digest(action), "copy": copy_after}
        record["cancel"] = {"made": made_report, "report": cancel_report, "extra_report": extra, "before": before,
                            "after": after, "files_back": before["script"] == after["script"]
                            and before["action"] == after["action"],
                            "copy_back": (copy_before or {}).get("values") == (copy_after or {}).get("values")
                            and bool(copy_before) == bool(copy_after)}
        log("check", "cancel: %s" % json.dumps(record["cancel"], default=str)[:900])

    if "putback" in which:
        from brain.implementers import putback
        from brain.implementers.guard import GuardContext, gate
        from brain.implementers.vfg import vfg
        name = os.environ.get("CANCEL_TASK", "frost_steps_steve")
        log("check", "putback: real vfg changes to %s, then code's put-back" % name)
        script = os.path.join(cfg.repo, "made", cfg.server, "scripts", name, "main.py")
        snap = putback.Snapshot(repo=cfg.repo, server=cfg.server, home=cfg.home, task="pb_check", vfg=vfg)
        g = GuardContext(repo=cfg.repo, server=cfg.server, task="pb_check", snapshot=snap)
        copy_before = (actions.read_running(cfg.home) or {}).get(name)
        before = {"script": digest(script), "copy": (copy_before or {}).get("values"), "copy_started": bool(copy_before)}
        steps = [
            ("Edit", {"file_path": script, "old_string": "x", "new_string": "y"}),
            ("Bash", {"command": "vfg script set %s under_radius=4" % name}),
            ("Bash", {"command": "vfg action run tnt_rain player=Steve as=pb_rain --task pb_check"}),
            ("Bash", {"command": "vfg place save pb_spot --at 1 2 3 --by pb_check"}),
        ]
        verdicts = []
        for tool, inp in steps:
            verdicts.append(await gate({"tool_name": tool, "tool_input": inp}, g))
            if tool == "Edit":
                with open(script, "a") as f:
                    f.write("\n# changed by the put-back check\n")
            else:
                r = await vfg(*inp["command"].split()[1:], timeout=90)
                log("check", "putback: %s -> exit %d" % (inp["command"], r.code))
        mid = {"script": digest(script), "copy": ((actions.read_running(cfg.home) or {}).get(name) or {}).get("values"),
               "pb_rain": "pb_rain" in (actions.read_running(cfg.home) or {})}
        did, could_not = await putback.put_back(snap)
        places = await vfg("place", "list", "--json", "--fresh", timeout=60)
        after = {"script": digest(script), "copy": ((actions.read_running(cfg.home) or {}).get(name) or {}).get("values"),
                 "pb_rain": "pb_rain" in (actions.read_running(cfg.home) or {}),
                 "pb_spot": "pb_spot" in (json.loads(places.stdout) if places.code == 0 else {})}
        record["putback"] = {"verdicts": verdicts, "before": before, "mid": mid, "after": after, "did": did,
                             "could_not": could_not, "line": putback.line(did, could_not),
                             "back": before["script"] == after["script"] and before["copy"] == after["copy"]
                             and not after["pb_rain"] and not after["pb_spot"]}
        log("check", "putback: %s" % json.dumps(record["putback"], default=str)[:1500])

    await imps.close(5)

    if "limit" in which:
        log("check", "limit: Speed Dash with 8 turns")
        cfg8, imps8, counts8, reports8 = make(max_turns=8)
        ended = {"how": None}  # how the run's Done said it ended

        def on_event(n, ev):
            if isinstance(ev, Done):
                ended["how"] = ev.ended

        imps8.watchers.append(on_event)
        await imps8.start("speed_dash", SPEED_DASH)
        r = await next_report(reports8)
        record["limit"] = {"report": r, "runs": dict(counts8), "ended": ended["how"]}
        log("check", "limit: %s" % json.dumps(record["limit"])[:900])
        await imps8.close(5)

    with open(os.path.join(out, "checks.json"), "w") as f:
        json.dump(record, f, indent=1, default=str)


if __name__ == "__main__":
    out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "impl_messages_check.out")
    os.makedirs(out, exist_ok=True)
    asyncio.run(main(out, sys.argv[2:] or ["messages", "late", "cancel", "limit"]))
