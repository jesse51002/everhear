"""The EXAMPLES block: what an implementer starts with for its own job, added to its brief by the
runner (brief.compose) so it never spends a turn looking. Its static part — its rules, the kit, the
first lines of the library's presets and scripts, the library's actions one menu line each — is in
its system prompt (prefix.py); this is the part that changes:

    EXAMPLES, picked for ice_beam_steve by Jev in 0.3 s.
    ice_beam runs this job: run its action, ice_beam, with the values the job needs. galick_gun is close.
    Presets to copy: crater and replace_blocks. Already in a loaded script: beam_clash (in galick_gun).
    Running: galick_gun_alex (galick_gun, player=Alex, cooldown=0), horror_mode (spooky_night).
    Online: Steve 120 64 -40 overworld, Alex 98 70 -12 overworld. Night, clear.
    That is the server now, read for you by vfg state: act on it, with no vfg state or vfg script list first.

    Made on this server (made/main/), by first line:
      ice_trail (preset): Lay packed ice under a player's feet as they walk.

    ACTIONS: the library's are in your system prompt, ... Here, in full, are the 3 picked for your job ...
    …
    Made on this server (made/main/actions/), one menu line each:
    …

    === scripts/ice_beam/main.py ===
    …

The router picks scripts and actions; the line saying which runs the job names the actions over
them, from the actions cache. ACTIONS gives at most 7 actions whole, the router's picks best first
(by the job's words when the router failed), and this server's made actions one menu line each,
read from that cache as the implementer starts (actions.py); "Running:" is every copy in
running.json, with the values that differ from its action's. Then, whole and never cut: at most 3
scripts the router loaded (those that run the job first), each followed by its test when it has one
(scripts/<name>/test.py: a worked example of a test), the task's own test when it has one
(made/<server>/tests/<task>.py), every preset it loaded, every script the brief names, and the
script of every running copy the brief names, with that copy's values now.

`vfg route` (route.py), `vfg state --json` and the actions cache are read at once; whatever fails,
the block says what is missing and the brief still goes, never more than VFG_ROUTE_TIMEOUT_S and a
second after the runner asked for the router and the state, and ACTIONS_TIMEOUT_S for the actions
(a fresh cache is one file read; a rebuild after an action file changed loads that file). The
picks go into the brain's log, never into the task's row, which the brain alone writes; the size
of each part of the block goes into `sizes`, for brief.compose's one log line."""
import asyncio
import importlib.util
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import actions
from . import route as routing
from .config import ROOT, ImplementerConfig, log
from .vfg import VfgRunner, why

# toolkit/lib/library.py of the repo this brain/ is in: how vfg itself reads the library and
# made/<server>/ (names, first lines, files), so a name or a first line here is the same as there.
TOOLKIT_LIB = os.path.join(ROOT, "toolkit", "lib")

# The whole step, past VFG_ROUTE_TIMEOUT_S: the implementer is never held up longer for the router
# and the state.
STEP_SLACK_S = 1.0
# The actions cache, from the step's start: a rebuild after an action file changed loads it again.
ACTIONS_TIMEOUT_S = 10.0


# ------------------------------------------------------------------ the library, read as vfg does

_library_module: Any = None
_libraries: dict[tuple[str, str], Any] = {}


def library_module():
    """toolkit/lib/library.py, loaded once."""
    global _library_module
    if _library_module is None:
        spec = importlib.util.spec_from_file_location("vfg_toolkit_library", os.path.join(TOOLKIT_LIB, "library.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _library_module = mod
    return _library_module


def library(repo: str, server: str):
    """The library and this server's folder; one per repo and server, so its files are parsed
    again only when they change."""
    key = (repo, server)
    if key not in _libraries:
        _libraries[key] = library_module().Library(repo, server)
    return _libraries[key]


# ------------------------------------------------------------------ names in a brief

_WORD = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    """Lowercase letters and digits; anything else (spaces, _ and -) separates words."""
    return _WORD.findall(text.lower())


def names_in(text: str, names: list[str]) -> list[str]:
    """The names the text says, in the order given: a name's words in a row, so `galick_gun`,
    "galick gun" and "Galick-Gun" all say galick_gun."""
    words = tokens(text)
    out = []
    for name in names:
        want = tokens(name)
        n = len(want)
        if n and any(words[i:i + n] == want for i in range(len(words) - n + 1)):
            out.append(name)
    return out


# ------------------------------------------------------------------ the pieces

async def server_state(timeout_s: float, run: VfgRunner) -> tuple[dict | None, str | None]:
    """`vfg state --json`: (the state, None), or (None, why not). Exit 5 is Minecraft not answering,
    with the rest of the state still printed."""
    r = await run("state", "--json", timeout=timeout_s)
    try:
        data = json.loads(r.stdout) if r.stdout.strip() else None
    except ValueError:
        data = None
    if r.code in (0, 5) and isinstance(data, dict):
        return data, None
    return None, why(r)


async def route_picks(job: str, task: str | None, timeout_s: float, run: VfgRunner) -> tuple[dict | None, str | None]:
    try:
        return await routing.run(job, task, timeout_s, run), None
    except routing.RouteFailed as e:
        return None, str(e)


async def read_actions(cfg: ImplementerConfig) -> tuple[dict | None, str | None]:
    """The actions cache, (it, None), or (None, why not); read in a thread, since a rebuild imports files."""
    try:
        return await asyncio.to_thread(actions.cache, cfg.repo, cfg.server, cfg.home), None
    except Exception as e:
        log("examples", "the actions cache could not be read: %s: %s" % (type(e).__name__, e))
        return None, ("%s: %s" % (type(e).__name__, e))[:200]


def and_list(names: list[str], word: str = "and") -> str:
    if len(names) <= 1:
        return "".join(names)
    return "%s %s %s" % (", ".join(names[:-1]), word, names[-1])


def _named(item: dict) -> str:
    return item["name"] + (" (made)" if item.get("source") == "made" else "")


def runs_line(runs: list[dict], acts: dict | None, server: str) -> str:
    """`ice_beam runs this job: run its action, ice_beam, with the values the job needs.`"""
    who = and_list([_named(s) for s in runs])
    verb = "runs" if len(runs) == 1 else "run"
    found: list[str] = []
    for s in runs:
        found += [a for a in actions.actions_of(acts, s["name"]) if a not in found]
    if acts is None:
        return "%s %s this job: run %s action with the values the job needs." % (
            who, verb, "its" if len(runs) == 1 else "one's")
    if not found:
        return ("%s %s this job, but no action runs %s yet: write one in made/%s/actions/<name>.py, "
                "vfg action check <name>, then run it." % (who, verb, "it" if len(runs) == 1 else "them", server))
    whose = "its" if len(runs) == 1 else "their"
    what = ("%s action, %s," % (whose, found[0]) if len(found) == 1 else
            "one of %s actions, %s," % (whose, and_list(found, "or")))
    return "%s %s this job: run %s with the values the job needs." % (who, verb, what)


def world_line(state: dict) -> str:
    """`Online: Steve 120 64 -40 overworld, Alex 98 70 -12 overworld. Night, clear.`"""
    if state.get("rcon"):
        return "Minecraft is not answering (%s): who is online, the time and the weather are unknown." % state["rcon"]
    people = []
    for p in state.get("online") or []:
        pos = " ".join(_coord(c) for c in p["pos"]) if p.get("pos") else "?"
        people.append("%s %s %s" % (p.get("name"), pos, p.get("dimension") or "?"))
    phase = ((state.get("time") or {}).get("phase") or "time unknown").capitalize()
    weather = state.get("weather") or "weather unknown"
    return "Online: %s. %s, %s." % (", ".join(people) if people else "nobody", phase, weather)


def _coord(c) -> str:
    return str(int(c)) if isinstance(c, (int, float)) and float(c).is_integer() else str(c)


# After the state, when it was read: the implementer acts on it rather than asking again.
NOW = "That is the server now, read for you by vfg state: act on it, with no vfg state or vfg script list first."
# When it was not (vfg state failed, or Minecraft did not answer): one look is worth it.
UNKNOWN = ("The server's state is unknown here: one vfg state is worth running before you act, for who is online, "
           "where, and what runs.")


@dataclass
class Gathered:
    """What one block is made of; build() gathers it, compose() writes it."""
    task: str | None
    picks: dict | None
    route_failed: str | None
    state: dict | None
    state_failed: str | None
    server: str
    acts: dict | None = None  # the actions cache
    acts_failed: str | None = None
    copies: dict | None = None  # running.json's copies (else vfg state's), None when unknown
    made: list = field(default_factory=list)  # library Items made on this server
    named: list = field(default_factory=list)  # Items the brief names that the router did not load
    changing: list = field(default_factory=list)  # (copy name, its entry, its script's Item or None) the brief names
    files: list[str] = field(default_factory=list)  # repo-relative, in the order they go in
    kinds: dict = field(default_factory=dict)  # a file's part: scripts, presets, named or running
    full: list[str] = field(default_factory=list)  # the actions given in full, best first
    full_how: str = ""  # how they were picked: by Jev, by words


def changing_line(g: Gathered, name: str, copy: dict, item) -> str:
    """`Your brief names galick_gun_alex, a copy of galick_gun running with player=Alex: …`"""
    diff = actions.changed(g.acts, copy)
    action = copy.get("action") or copy.get("script") or "?"
    where = "its script is below" if item is not None else "its script is not on this PC"
    # Another task's, or the brain's own copy of an action: its code stays as it is while it runs (the
    # guard refuses an edit), except the task's own script, named as the task. The copy takes the
    # task's name while the task has no script of that name yet.
    mine = g.task and (copy.get("task") == g.task or copy.get("script") == g.task)
    new = g.task if g.task and not any(it.kind == "script" and it.name == g.task for it in g.made) else "<new_name>"
    code = "" if mine else "; to change its code, copy it: vfg script new %s --from %s" % (
        new, copy.get("script") or action)
    task = copy.get("task")
    return "Your brief names %s, a copy of %s running%s with %s: vfg script set %s key=value changes it; %s%s." % (
        name, action, " for " + task if task and task != name else "",
        actions.pairs(diff) if diff else "its action's values", name, where, code)


def header(g: Gathered) -> list[str]:
    who = "for %s" % g.task if g.task else "for this job"
    lines = []
    p = g.picks
    if p is None:
        lines.append("EXAMPLES %s: nothing was picked, the router failed (%s). Every library script's, "
                     "preset's and action's first line is in your system prompt, and you can read any of them."
                     % (who, g.route_failed))
    else:
        secs = "%.1f s" % ((p.get("ms") or 0) / 1000)
        if p.get("fallback"):
            lines.append("EXAMPLES, picked %s by words in %s: Jev did not answer (%s)." % (who, secs, p.get("why") or "no answer"))
        elif p.get("unanswered"):
            lines.append("EXAMPLES, picked %s by Jev in %s; by words for the %d Jev left unanswered."
                         % (who, secs, len(p["unanswered"])))
        else:
            lines.append("EXAMPLES, picked %s by Jev in %s." % (who, secs))
        runs = [s for s in p["scripts"] if s.get("verdict") == "runs_it"]
        close = [_named(s) for s in p["scripts"] if s.get("verdict") == "close"]
        words = [_named(s) for s in p["scripts"] if s.get("verdict") not in ("runs_it", "close")]
        says = []
        if runs:
            says.append(runs_line(runs, g.acts, g.server))
        if close:
            says.append("%s %s close." % (and_list(close), "is" if len(close) == 1 else "are"))
        if words:
            says.append("Matched by words: %s." % and_list(words))
        lines.append(" ".join(says) if says else "No example script runs this job or is close to it.")
        presets = "Presets to copy: %s." % (and_list([_named(x) for x in p["presets"]]) or "none")
        held = [x for x in p.get("held") or [] if isinstance(x, dict) and x.get("name")]
        if held:
            presets += " Already in a loaded script: %s." % and_list(
                ["%s (in %s)" % (_named(x), x.get("in") or "a loaded script") for x in held])
        lines.append(presets)
    if g.named:
        lines.append("Named in your brief, in full below: %s." % and_list([_named({"name": i.name, "source": i.source})
                                                                          for i in g.named]))
    tests = [rel for rel in g.files if g.kinds.get(rel) == "tests"]
    if tests:
        mine = "made/%s/tests/%s.py" % (g.server, g.task)
        lines.append("Tests below%s: %s; vfg test <name> runs one on the test server." % (
            ", each after its script" if [t for t in tests if t != mine] else "",
            and_list(["%s (yours)" % t if t == mine else t for t in tests])))
    if g.copies is not None:
        lines.append("Running: %s." % (", ".join(actions.copy_line(n, c, g.acts) for n, c in sorted(g.copies.items()))
                                       if g.copies else "nothing"))
        for name, copy, item in g.changing:
            lines.append(changing_line(g, name, copy, item))
    if g.state is None:
        lines.append("Server state: unavailable (%s)." % g.state_failed)
        lines.append(UNKNOWN)
        return lines
    lines.append(world_line(g.state))
    lines.append(UNKNOWN if g.state.get("rcon") else NOW)
    return lines


# The parts of the block, in its order, as `sizes` names them.
PARTS = ("header", "made", "actions", "made_actions", "scripts", "tests", "presets", "named", "running")


def compose(g: Gathered, repo: str, sizes: dict | None = None) -> str:
    """The block's text: the header, this server's first lines, the actions, then every file whole.
    `sizes`, when given, gets each part's characters and how many it holds: {"header": 812,
    "actions": 9120, "actions_n": 5, ...}."""
    lines = header(g)
    head = "\n".join(lines) + "\n\n"
    lines = []
    if g.made:
        lines.append("Made on this server (made/%s/), by first line:" % g.server)
        for it in g.made:
            first = it.first_line or "(no first line)"
            if it.problem:
                first += " [%s]" % it.problem
            lines.append("  %s (%s): %s" % (it.name, it.kind, first))
    else:
        lines.append("Made on this server (made/%s/): nothing yet." % g.server)
    made = "\n".join(lines) + "\n\n"
    acts = actions.section(g.acts, g.acts_failed, g.server, g.task, g.full, g.full_how)
    out = head + made + acts
    size = dict.fromkeys(PARTS, 0)
    size.update(header=len(head), made=len(made))
    mark = "\nMade on this server (made/%s/actions/)" % g.server
    size["actions"] = acts.index(mark) + 1 if mark in acts else len(acts)
    size["made_actions"] = len(acts) - size["actions"]
    counts = {"actions_n": len(g.full), "made_actions_n": sum(
        1 for a in ((g.acts or {}).get("actions") or {}).values() if isinstance(a, dict) and a.get("source") != "library")}
    for rel in g.files:
        try:
            with open(os.path.join(repo, rel), "rb") as f:
                text = f.read().decode("utf-8", errors="replace")
        except OSError as e:
            text = "(could not be read: %s)\n" % (e.strerror or e)
        part = "\n=== %s ===\n%s" % (rel, text if text.endswith("\n") else text + "\n")
        kind = g.kinds.get(rel, "named")
        size[kind] += len(part)
        counts[kind + "_n"] = counts.get(kind + "_n", 0) + 1
        out += part
    if sizes is not None:
        sizes.update(size)
        sizes.update(dict(("%s_n" % k, counts.get("%s_n" % k, 0)) for k in PARTS if k not in ("header", "made")))
    return out


def test_of(cfg: ImplementerConfig, name: str | None, source: str | None) -> str | None:
    """A script's test, from the repo, when it has one: a library script's scripts/<name>/test.py,
    a task's (or a made script's) made/<server>/tests/<name>.py."""
    if not name:
        return None
    rel = ("made/%s/tests/%s.py" % (cfg.server, name) if source == "made" else "scripts/%s/test.py" % name)
    return rel if os.path.isfile(os.path.join(cfg.repo, rel)) else None


# ------------------------------------------------------------------ the block

async def build(cfg: ImplementerConfig, job: str, task: str | None, run: VfgRunner,
                sizes: dict | None = None) -> str:
    """The EXAMPLES block for one implementer's job, the brain's brief; its picks go into the log, and
    the size of each of its parts into `sizes` (compose), with how it went."""
    t0 = time.monotonic()
    budget = cfg.route_timeout_s + routing.SLACK_S
    acts_job = asyncio.ensure_future(read_actions(cfg))
    jobs = {"route": asyncio.ensure_future(route_picks(job, task, cfg.route_timeout_s, run)),
            "state": asyncio.ensure_future(server_state(budget, run))}
    done, pending = await asyncio.wait(set(jobs.values()), timeout=cfg.route_timeout_s + STEP_SLACK_S)
    for p in pending:
        p.cancel()
    late = "no answer within %s s" % ("%g" % (cfg.route_timeout_s + STEP_SLACK_S))
    picks, route_failed = jobs["route"].result() if jobs["route"] in done else (None, late)
    state, state_failed = jobs["state"].result() if jobs["state"] in done else (None, late)
    if not acts_job.done():
        await asyncio.wait({acts_job}, timeout=max(0.0, ACTIONS_TIMEOUT_S - (time.monotonic() - t0)))
    if acts_job.done():
        acts, acts_failed = acts_job.result()
    else:
        acts_job.cancel()
        acts, acts_failed = None, "not read within %g s" % ACTIONS_TIMEOUT_S

    # The caps, whatever vfg route printed: 3 scripts, 7 actions.
    picks = routing.trim(picks)
    lib = library(cfg.repo, cfg.server)
    scripts = lib.scripts()
    # What runs: running.json, read after vfg state (which writes it through), else the state's own list.
    copies = actions.read_running(cfg.home)
    if copies is None and state is not None:
        copies = actions.from_state(state.get("instances") or [])
    g = Gathered(task=task, picks=picks, route_failed=route_failed, state=state, state_failed=state_failed,
                 server=cfg.server, acts=acts, acts_failed=acts_failed, copies=copies)
    g.made = [it for it in scripts + lib.presets() if it.source == "made"]
    if acts is not None:
        g.full, g.full_how = actions.chosen(acts, picks, job)
    seen: set[str] = set()

    def add(rel: str | None, kind: str) -> bool:
        if not rel or rel in seen:
            return False
        seen.add(rel)
        g.files.append(rel)
        g.kinds[rel] = kind
        return True

    for s in (picks or {}).get("scripts") or []:
        if add(s.get("path"), "scripts"):
            add(test_of(cfg, s.get("name"), s.get("source")), "tests")   # a worked example, with its script
    if task:
        add(test_of(cfg, task, "made"), "tests")   # the task's own test, already written
    for p in (picks or {}).get("presets") or []:
        add(p.get("path"), "presets")
    by_name = {it.name: it for it in scripts}
    for name in names_in(job, list(by_name)):
        if add(by_name[name].rel, "named"):
            g.named.append(by_name[name])
    for name, copy in sorted((copies or {}).items()):
        if not names_in(job, [n for n in (name, copy.get("action"), copy.get("script")) if n]):
            continue
        item = lib.script(copy.get("script") or "")
        g.changing.append((name, copy, item))
        if item is not None:
            add(item.rel, "running")

    block = compose(g, cfg.repo, sizes)
    ms = int((time.monotonic() - t0) * 1000)
    log("route", "%s picks %s, loaded %s; actions in full %s: %s" % (
        task or "-", json.dumps(routing.summary(picks, route_failed), ensure_ascii=False, separators=(",", ":")),
        ", ".join(g.files) or "nothing", g.full_how or "-", ", ".join(g.full) or "none"))
    if sizes is not None:
        sizes.update(ms=ms, actions_how=g.full_how,
                     route="failed" if picks is None else "words" if picks.get("fallback") else "jev",
                     state="ok" if state is not None else "unavailable",
                     actions_read="no" if acts is None else len(acts.get("actions") or {}))
    return block
