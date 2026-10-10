"""The EXAMPLES block: what an implementer starts with for its own job, added to its brief by the
guard (brief.STEPS) so it never spends a turn looking. Its static part — its rules, the kit, the
first line and VALUES of every library preset and script — is in its system prompt (prefix.py);
this is the part that changes:

    EXAMPLES, picked for T7 by Jev in 0.3 s.
    ice_beam runs this job: run it, or change its values. galick_gun is close.
    Presets to copy: crater, replace_blocks.
    Running: galick-alex (galick_gun, cooldown=0, T5), horror_mode (T2).
    Online: Steve 120 64 -40 overworld, Alex 98 70 -12 overworld. Night, clear.

    Made on this server (made/main/), by first line:
      ice_trail (preset): Lay packed ice under a player's feet as they walk.

    === scripts/ice_beam/main.py ===
    …

Then, whole and never cut, with no limit on how many: every script the router loaded (those that
run the job first), every preset it loaded, every script the brief names, and the script of every
running instance the brief names, with that instance's values now. `vfg route` (route.py) and
`vfg state --json` run at once; whatever fails, the block says what is missing and the brief still
goes, never more than VFG_ROUTE_TIMEOUT_S and a second after the guard asked.

The runner turns it on with use(cfg, store); until then the step leaves a brief as it is."""
import asyncio
import importlib.util
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import route as routing
from . import vfg as vfg_module
from .config import Config, log
from .tasks import Store
from .vfg import why

# toolkit/lib/library.py of the repo this orchestrator is in: how vfg itself reads the library and
# made/<server>/ (first lines, ASKS, VALUES), so a name or a first line here is the same as there.
TOOLKIT_LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))),
                           "toolkit", "lib")

# The whole step, past VFG_ROUTE_TIMEOUT_S: the implementer is never held up longer.
STEP_SLACK_S = 1.0


@dataclass
class Setup:
    cfg: Config
    store: Store | None


_setup: Setup | None = None


def use(cfg: Config, store: Store | None) -> None:
    """Turns the step on: the runner's config (repo, server, route timeout) and its task store."""
    global _setup
    _setup = Setup(cfg, store)


def reset() -> None:
    """Turns it off again (tests)."""
    global _setup
    _setup = None


async def examples(brief: str, tool_input: dict) -> str:
    """The BriefStep: the brief, then the EXAMPLES block for its job."""
    s = _setup
    if s is None:
        return brief
    job = tool_input.get("prompt") if isinstance(tool_input.get("prompt"), str) else brief
    block = await build(s.cfg, s.store, job, str(tool_input.get("description") or ""))
    return "%s\n\n%s" % (brief.rstrip(), block)


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


def task_id_in(text: str) -> str | None:
    """The task id at the head of an implementer's description or brief ("T7 castle at spawn")."""
    m = re.match(r"\s*(T\d+)\b", text or "", re.ASCII)
    return m.group(1) if m else None


# ------------------------------------------------------------------ the pieces

async def server_state(timeout_s: float) -> tuple[dict | None, str | None]:
    """`vfg state --json`: (the state, None), or (None, why not). Exit 5 is Minecraft not answering,
    with the rest of the state still printed."""
    r = await vfg_module.vfg(["state", "--json"], timeout_s * 1000)
    try:
        data = json.loads(r.stdout) if r.stdout.strip() else None
    except ValueError:
        data = None
    if r.code in (0, 5) and isinstance(data, dict):
        return data, None
    return None, why(r)


async def route_picks(job: str, task: str | None, timeout_s: float) -> tuple[dict | None, str | None]:
    try:
        return await routing.run(job, task, timeout_s), None
    except routing.RouteFailed as e:
        return None, str(e)


def and_list(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return "%s and %s" % (", ".join(names[:-1]), names[-1])


def _named(item: dict) -> str:
    return item["name"] + (" (made)" if item.get("source") == "made" else "")


def _values(libmod, pairs: dict) -> str:
    """`cooldown=0, player=Alex`, each value as vfg script run takes it."""
    return ", ".join("%s=%s" % (k, libmod.argv_word(v)) for k, v in pairs.items())


def instance_line(libmod, inst: dict) -> str:
    """`galick-alex (galick_gun, cooldown=0, T5)`: the script when it is not the instance's name,
    made when it is this server's, the values that differ from its defaults, its words, its task."""
    bits = []
    script = inst.get("script") or "?"
    if script != inst.get("instance"):
        bits.append(script)
    if inst.get("source") == "made":
        bits.append("made")
    if inst.get("changed"):
        bits.append(_values(libmod, inst["changed"]))
    if inst.get("args"):
        bits.append(" ".join(str(a) for a in inst["args"]))
    if inst.get("task"):
        bits.append(inst["task"])
    return "%s (%s)" % (inst.get("instance"), ", ".join(bits)) if bits else str(inst.get("instance"))


def _coord(c) -> str:
    return str(int(c)) if isinstance(c, (int, float)) and float(c).is_integer() else str(c)


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


@dataclass
class Gathered:
    """What one block is made of; build() gathers it, compose() writes it."""
    task: str | None
    picks: dict | None
    route_failed: str | None
    state: dict | None
    state_failed: str | None
    server: str
    made: list = field(default_factory=list)  # library Items made on this server
    named: list = field(default_factory=list)  # Items the brief names that the router did not load
    changing: list = field(default_factory=list)  # (instance, its Item or None) the brief names
    files: list[str] = field(default_factory=list)  # repo-relative, in the order they go in


def header(g: Gathered, libmod) -> list[str]:
    who = "for %s" % g.task if g.task else "for this job"
    lines = []
    p = g.picks
    if p is None:
        lines.append("EXAMPLES %s: nothing was picked, the router failed (%s). Every library script's and "
                     "preset's first line is in your system prompt, and you can read any of them." % (who, g.route_failed))
    else:
        secs = "%.1f s" % ((p.get("ms") or 0) / 1000)
        if p.get("fallback"):
            lines.append("EXAMPLES, picked %s by words in %s: Jev did not answer (%s)." % (who, secs, p.get("why") or "no answer"))
        elif p.get("unanswered"):
            lines.append("EXAMPLES, picked %s by Jev in %s; by words for the %d Jev left unanswered."
                         % (who, secs, len(p["unanswered"])))
        else:
            lines.append("EXAMPLES, picked %s by Jev in %s." % (who, secs))
        runs = [_named(s) for s in p["scripts"] if s.get("verdict") == "runs_it"]
        close = [_named(s) for s in p["scripts"] if s.get("verdict") == "close"]
        words = [_named(s) for s in p["scripts"] if s.get("verdict") not in ("runs_it", "close")]
        says = []
        if runs:
            says.append("%s %s this job: run %s, or change %s values." % (
                and_list(runs), "runs" if len(runs) == 1 else "run", "it" if len(runs) == 1 else "one", "its"))
        if close:
            says.append("%s %s close." % (and_list(close), "is" if len(close) == 1 else "are"))
        if words:
            says.append("Matched by words: %s." % and_list(words))
        lines.append(" ".join(says) if says else "No example script runs this job or is close to it.")
        lines.append("Presets to copy: %s." % (and_list([_named(x) for x in p["presets"]]) or "none"))
    if g.named:
        lines.append("Named in your brief, in full below: %s." % and_list([_named({"name": i.name, "source": i.source})
                                                                          for i in g.named]))
    if g.state is None:
        lines.append("Server state: unavailable (%s); vfg state shows who is online, where, and what runs." % g.state_failed)
        return lines
    running = g.state.get("instances") or []
    lines.append("Running: %s." % (", ".join(instance_line(libmod, i) for i in running) if running else "nothing"))
    for inst, item in g.changing:
        where = "its script is below" if item is not None else "its script is not on this PC"
        lines.append("Your brief names %s, running %s%s now with %s; %s." % (
            inst.get("instance"), inst.get("script"), " for " + inst["task"] if inst.get("task") else "",
            _values(libmod, inst.get("values") or {}) or "no values", where))
    lines.append(world_line(g.state))
    return lines


def compose(g: Gathered, repo: str, libmod) -> str:
    """The block's text: the header, this server's first lines, then every file whole."""
    lines = header(g, libmod)
    lines.append("")
    if g.made:
        lines.append("Made on this server (made/%s/), by first line:" % g.server)
        for it in g.made:
            first = it.first_line or "(no first line)"
            if it.problem:
                first += " [%s]" % it.problem
            lines.append("  %s (%s): %s" % (it.name, it.kind, first))
    else:
        lines.append("Made on this server (made/%s/): nothing yet." % g.server)
    out = "\n".join(lines) + "\n"
    for rel in g.files:
        try:
            with open(os.path.join(repo, rel), "rb") as f:
                text = f.read().decode("utf-8", errors="replace")
        except OSError as e:
            text = "(could not be read: %s)\n" % (e.strerror or e)
        out += "\n=== %s ===\n%s" % (rel, text if text.endswith("\n") else text + "\n")
    return out


# ------------------------------------------------------------------ the block

async def build(cfg: Config, store: Store | None, job: str, description: str) -> str:
    """The EXAMPLES block for one implementer's job; its picks go into the task's record."""
    t0 = time.monotonic()
    task = task_id_in(description) or task_id_in(job)
    budget = cfg.route_timeout_s + routing.SLACK_S
    jobs = {"route": asyncio.ensure_future(route_picks(job, task, cfg.route_timeout_s)),
            "state": asyncio.ensure_future(server_state(budget))}
    done, pending = await asyncio.wait(set(jobs.values()), timeout=cfg.route_timeout_s + STEP_SLACK_S)
    for p in pending:
        p.cancel()
    late = "no answer within %s s" % ("%g" % (cfg.route_timeout_s + STEP_SLACK_S))
    picks, route_failed = jobs["route"].result() if jobs["route"] in done else (None, late)
    state, state_failed = jobs["state"].result() if jobs["state"] in done else (None, late)

    lib_mod = library_module()
    lib = library(cfg.repo, cfg.server)
    scripts = lib.scripts()
    g = Gathered(task=task, picks=picks, route_failed=route_failed, state=state, state_failed=state_failed,
                 server=cfg.server)
    g.made = [it for it in scripts + lib.presets() if it.source == "made"]
    seen: set[str] = set()

    def add(rel: str | None) -> bool:
        if not rel or rel in seen:
            return False
        seen.add(rel)
        g.files.append(rel)
        return True

    for s in (picks or {}).get("scripts") or []:
        add(s.get("path"))
    for p in (picks or {}).get("presets") or []:
        add(p.get("path"))
    said = job + "\n" + description
    by_name = {it.name: it for it in scripts}
    for name in names_in(said, list(by_name)):
        if add(by_name[name].rel):
            g.named.append(by_name[name])
    for inst in (state or {}).get("instances") or []:
        if not names_in(said, [n for n in (inst.get("instance"), inst.get("script")) if n]):
            continue
        item = lib.script(inst.get("script") or "")
        g.changing.append((inst, item))
        if item is not None:
            add(item.rel)

    block = compose(g, cfg.repo, lib_mod)
    ms = int((time.monotonic() - t0) * 1000)
    routing.record(store, task, description, {
        "route": routing.summary(picks, route_failed), "loaded": g.files,
        "named": [it.name for it in g.named], "changing": [i.get("instance") for i, _ in g.changing],
        "state": "ok" if state is not None else state_failed, "ms": ms, "characters": len(block)})
    log("examples", "%s: %d files (%d named, %d running), %d made, state %s, %d characters in %d ms; %s" % (
        task or "-", len(g.files), len(g.named), len(g.changing), len(g.made),
        "ok" if state is not None else "unavailable", len(block), ms, block.split("\n", 1)[0][:200]))
    return block
