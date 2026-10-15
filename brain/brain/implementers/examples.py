"""The EXAMPLES block: what an implementer starts with for its own job, added to its brief by the
runner (brief.compose) so it never spends a turn looking. Its static part — its rules,
engine/WRITING.md, engine/KIT.md, and every library class's kind, first line, Config and Args — is
in its system prompt (prefix.py); this is the part that changes:

    EXAMPLES, picked for frost_staff_steve by Jev in 0.3 s.
    ice_staff (item, library) runs this job: vfg engine give <player> ice_staff. fireball (ability, library)
    is close: copy what you need into your own class.
    Named in your brief, in full below: meteor (scenario, library).
    Running: ability:fireball#3 (Steve), scenario:castle#2.
    Online: Steve 120 64 -40 overworld, Alex 98 70 -12 overworld. Night, clear.
    That is the server now, read for you by vfg state and vfg engine instances: act on it, ...

    Made on this server (made/main/content/), by first line:
      frost_ring_alex (mark): Ice round a player's feet for a while.

    === engine/library/ice_staff.py ===
    …

The router (`vfg route`, route.py) picks at most three classes, the one that runs the job first;
"Running:" is every instance the engine runs (`vfg engine instances --json`). Then, whole and never
cut: the classes the router picked, best first, each made one followed by its test when it has one
(made/<server>/tests/<id>.py: a worked example), the task's own test when it has one, and every
class the brief names. The classes are read from their files here as `vfg prefix` reads the
library's: an id is its file's name, a kind the base its class subclasses, a first line its
docstring's.

`vfg route`, `vfg state --json` and `vfg engine instances --json` are read at once; whatever fails,
the block says what is missing and the brief still goes, never more than VFG_ROUTE_TIMEOUT_S and a
second after the runner asked. The picks go into the brain's log, never into the task's row, which
the brain alone writes; the size of each part of the block goes into `sizes`, for brief.compose's
one log line."""
import ast
import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field

from . import route as routing
from .config import ImplementerConfig, log
from .guard import CONTENT_ID, KINDS
from .vfg import VfgRunner, engine_read, why

# The whole step, past VFG_ROUTE_TIMEOUT_S: the implementer is never held up longer for the router,
# the state and the engine.
STEP_SLACK_S = 1.0

# The library, the server's base content and the examples implementers read.
LIBRARY = "engine/library"


# ------------------------------------------------------------------ the classes, from their files

@dataclass
class ContentClass:
    name: str  # its id: its file's name
    kind: str  # item, ability, mark, actor or scenario; "?" when it cannot be told
    source: str  # "library" or "made"
    rel: str  # its file, from the repository
    first_line: str = ""
    problem: str = ""  # why it could not be read


def library_dir(repo: str) -> str:
    """engine/library/, from the repository."""
    return LIBRARY


def read_class(repo: str, rel: str, source: str) -> ContentClass:
    """One content file: its id (the file's name), its kind (the base its class subclasses) and its
    docstring's first line (the module's, else its class's); or why it could not be read."""
    name = os.path.splitext(os.path.basename(rel))[0]
    try:
        with open(os.path.join(repo, rel), "rb") as f:
            tree = ast.parse(f.read(), rel)
    except OSError as e:
        return ContentClass(name, "?", source, rel, problem="could not be read: %s" % (e.strerror or e))
    except SyntaxError as e:
        return ContentClass(name, "?", source, rel, problem="does not parse: %s, line %s" % (e.msg, e.lineno))
    except ValueError as e:  # a null byte, say
        return ContentClass(name, "?", source, rel, problem="does not parse: %s" % e)
    kind, doc = "?", ast.get_docstring(tree)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = [b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else ""
                 for b in node.bases]
        found = next((b.lower() for b in bases if b.lower() in KINDS), None)
        if found:
            kind, doc = found, doc or ast.get_docstring(node)
            break
    return ContentClass(name, kind, source, rel, ((doc or "").strip().split("\n") or [""])[0].strip())


def content_classes(repo: str, server: str) -> list[ContentClass]:
    """The library's classes, then this server's (made/<server>/content/), each by name."""
    out = []
    for folder, source in ((library_dir(repo), "library"), ("made/%s/content" % server, "made")):
        try:
            names = sorted(os.listdir(os.path.join(repo, folder)))
        except OSError:
            continue
        out += [read_class(repo, "%s/%s" % (folder, f), source) for f in names
                if f.endswith(".py") and CONTENT_ID.fullmatch(f[:-3])]
    return out


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


async def engine_instances(timeout_s: float, run: VfgRunner) -> tuple[list | None, str | None]:
    """`vfg engine instances --json`: (what the engine runs, None), or (None, why not)."""
    data, failed = await engine_read(run, "instances", timeout=timeout_s)
    if data is None:
        return None, failed
    return [i for i in data.get("instances") or [] if isinstance(i, dict)], None


async def route_picks(job: str, task: str | None, timeout_s: float, run: VfgRunner) -> tuple[dict | None, str | None]:
    try:
        return await routing.run(job, task, timeout_s, run), None
    except routing.RouteFailed as e:
        return None, str(e)


def and_list(names: list[str], word: str = "and") -> str:
    if len(names) <= 1:
        return "".join(names)
    return "%s %s %s" % (", ".join(names[:-1]), word, names[-1])


def described(c: dict) -> str:
    """`ice_staff (item, library)`."""
    return "%s (%s, %s)" % (c.get("name"), c.get("kind") or "?", "made" if c.get("source") == "made" else "library")


# How each kind is used as it is: THE LADDER's first rung.
USE = {"scenario": "vfg engine start {0} key=value ..., with the args the job needs",
       "ability": "vfg engine grant <player> {0}, and bind the phrase to it if the job names one",
       "item": "vfg engine give <player> {0}",
       "mark": "vfg engine apply <player> {0} key=value ...",
       "actor": "a scenario spawns it (self.spawn)"}


def runs_line(runs: list[dict]) -> str:
    """`ice_staff (item, library) runs this job: vfg engine give <player> ice_staff.`"""
    verb = "runs" if len(runs) == 1 else "run"
    how = "; ".join(USE.get(c.get("kind"), "use it as it is").format(c.get("name")) for c in runs)
    return "%s %s this job: %s." % (and_list([described(c) for c in runs]), verb, how)


def instance_line(i: dict) -> str:
    """`ability:fireball#3 (Steve)`: the instance, and its user when it has one."""
    name = i.get("instance") or "%s:%s" % (i.get("kind") or "?", i.get("id") or "?")
    return "%s (%s)" % (name, i["user"]) if i.get("user") else str(name)


def world_line(state: dict) -> str:
    """`Online: Steve 120 64 -40 overworld, Alex 98 70 -12 overworld. Night, clear.`"""
    if state.get("rcon"):
        return "Minecraft is not answering (%s): who is online, the time and the weather are unknown." % state["rcon"]
    people = []
    for p in state.get("online") or []:
        pos = " ".join(coord(c) for c in p["pos"]) if p.get("pos") else "?"
        people.append("%s %s %s" % (p.get("name"), pos, p.get("dimension") or "?"))
    phase = ((state.get("time") or {}).get("phase") or "time unknown").capitalize()
    weather = state.get("weather") or "weather unknown"
    return "Online: %s. %s, %s." % (", ".join(people) if people else "nobody", phase, weather)


def coord(c) -> str:
    """A coordinate as a brief writes it: whole numbers without .0, others to a tenth."""
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        return str(int(c)) if float(c).is_integer() else str(round(float(c), 1))
    return str(c)


# After the state, when it was read: the implementer acts on it rather than asking again.
NOW = ("That is the server now, read for you by vfg state and vfg engine instances: act on it, with no vfg state "
       "or vfg engine instances first.")
# When it was not (vfg state failed, or Minecraft did not answer): one look is worth it.
UNKNOWN = ("The server's state is unknown here: one vfg state is worth running before you act, for who is online "
           "and where.")


@dataclass
class Gathered:
    """What one block is made of; build() gathers it, compose() writes it."""
    task: str | None
    picks: dict | None
    route_failed: str | None
    state: dict | None
    state_failed: str | None
    server: str
    instances: list | None = None  # what the engine runs, None when it could not be read
    instances_failed: str | None = None
    library: str = LIBRARY  # where the library's classes are read
    made: list = field(default_factory=list)  # ContentClass made on this server
    named: list = field(default_factory=list)  # ContentClass the brief names that the router did not load
    files: list[str] = field(default_factory=list)  # repo-relative, in the order they go in
    kinds: dict = field(default_factory=dict)  # a file's part: classes, tests or named


def header(g: Gathered) -> list[str]:
    who = "for %s" % g.task if g.task else "for this job"
    lines = []
    p = g.picks
    if p is None:
        lines.append("EXAMPLES %s: nothing was picked, the router failed (%s). Every library class's kind, first "
                     "line, Config and Args are in your system prompt, and you can read any of them in %s/."
                     % (who, g.route_failed, g.library))
    else:
        secs = "%.1f s" % ((p.get("ms") or 0) / 1000)
        if p.get("fallback"):
            lines.append("EXAMPLES, picked %s by words in %s: Jev did not answer (%s)."
                         % (who, secs, p.get("why") or "no answer"))
        elif p.get("unanswered"):
            lines.append("EXAMPLES, picked %s by Jev in %s; by words for the %d Jev left unanswered."
                         % (who, secs, len(p["unanswered"])))
        else:
            lines.append("EXAMPLES, picked %s by Jev in %s." % (who, secs))
        classes = p.get("classes") or []
        runs = [c for c in classes if c.get("verdict") == "runs_it"]
        close = [described(c) for c in classes if c.get("verdict") == "close"]
        words = [described(c) for c in classes if c.get("verdict") not in ("runs_it", "close")]
        says = []
        if runs:
            says.append(runs_line(runs))
        if close:
            says.append("%s %s close: copy what you need into your own class." % (
                and_list(close), "is" if len(close) == 1 else "are"))
        if words:
            says.append("Matched by words: %s." % and_list(words))
        lines.append(" ".join(says) if says else "No class runs this job or is close to it.")
    if g.named:
        lines.append("Named in your brief, in full below: %s." % and_list(
            [described({"name": c.name, "kind": c.kind, "source": c.source}) for c in g.named]))
    tests = [rel for rel in g.files if g.kinds.get(rel) == "tests"]
    if tests:
        mine = "made/%s/tests/%s.py" % (g.server, g.task)
        lines.append("Tests below%s: %s; vfg test <id> runs one on the test server." % (
            ", each after its class" if [t for t in tests if t != mine] else "",
            and_list(["%s (yours)" % t if t == mine else t for t in tests])))
    if g.instances is None:
        lines.append("Running: unknown (%s); vfg engine instances lists it." % g.instances_failed)
    else:
        lines.append("Running: %s." % (", ".join(instance_line(i) for i in g.instances) or "nothing"))
    if g.state is None:
        lines.append("Server state: unavailable (%s)." % g.state_failed)
        lines.append(UNKNOWN)
        return lines
    lines.append(world_line(g.state))
    lines.append(UNKNOWN if g.state.get("rcon") else NOW)
    return lines


# The parts of the block, in its order, as `sizes` names them.
PARTS = ("header", "made", "classes", "tests", "named")


def compose(g: Gathered, repo: str, sizes: dict | None = None) -> str:
    """The block's text: the header, this server's classes by first line, then every file whole.
    `sizes`, when given, gets each part's characters and how many it holds: {"header": 812,
    "classes": 9120, "classes_n": 2, ...}."""
    head = "\n".join(header(g)) + "\n\n"
    if g.made:
        lines = ["Made on this server (made/%s/content/), by first line:" % g.server]
        for c in g.made:
            first = c.first_line or "(no first line)"
            if c.problem:
                first += " [%s]" % c.problem
            lines.append("  %s (%s): %s" % (c.name, c.kind, first))
    else:
        lines = ["Made on this server (made/%s/content/): nothing yet." % g.server]
    made = "\n".join(lines) + "\n"
    out = head + made
    size = dict.fromkeys(PARTS, 0)
    size.update(header=len(head), made=len(made))
    counts: dict = {}
    for rel in g.files:
        try:
            with open(os.path.join(repo, rel), "rb") as f:
                text = f.read().decode("utf-8", errors="replace")
        except OSError as e:
            text = "(could not be read: %s)\n" % (e.strerror or e)
        part = "\n=== %s ===\n%s" % (rel, text if text.endswith("\n") else text + "\n")
        kind = g.kinds.get(rel, "named")
        size[kind] += len(part)
        counts[kind] = counts.get(kind, 0) + 1
        out += part
    if sizes is not None:
        sizes.update(size)
        sizes.update(("%s_n" % k, counts.get(k, 0)) for k in PARTS if k not in ("header", "made"))
    return out


def test_of(cfg: ImplementerConfig, name: str | None) -> str | None:
    """A class's test on this server, made/<server>/tests/<name>.py, when it has one."""
    if not name:
        return None
    rel = "made/%s/tests/%s.py" % (cfg.server, name)
    return rel if os.path.isfile(os.path.join(cfg.repo, rel)) else None


# ------------------------------------------------------------------ the block

async def build(cfg: ImplementerConfig, job: str, task: str | None, run: VfgRunner,
                sizes: dict | None = None) -> str:
    """The EXAMPLES block for one implementer's job, the brain's brief; its picks go into the log, and
    the size of each of its parts into `sizes` (compose), with how it went."""
    t0 = time.monotonic()
    budget = cfg.route_timeout_s + routing.SLACK_S
    jobs = {"route": asyncio.ensure_future(route_picks(job, task, cfg.route_timeout_s, run)),
            "state": asyncio.ensure_future(server_state(budget, run)),
            "instances": asyncio.ensure_future(engine_instances(budget, run))}
    done, pending = await asyncio.wait(set(jobs.values()), timeout=cfg.route_timeout_s + STEP_SLACK_S)
    for p in pending:
        p.cancel()
    late = "no answer within %s s" % ("%g" % (cfg.route_timeout_s + STEP_SLACK_S))
    picks, route_failed = jobs["route"].result() if jobs["route"] in done else (None, late)
    state, state_failed = jobs["state"].result() if jobs["state"] in done else (None, late)
    running, running_failed = jobs["instances"].result() if jobs["instances"] in done else (None, late)

    picks = routing.trim(picks)  # the cap, whatever vfg route printed: 3 classes
    classes = content_classes(cfg.repo, cfg.server)
    g = Gathered(task=task, picks=picks, route_failed=route_failed, state=state, state_failed=state_failed,
                 server=cfg.server, instances=running, instances_failed=running_failed,
                 library=library_dir(cfg.repo))
    g.made = [c for c in classes if c.source == "made"]
    seen: set[str] = set()

    def add(rel: str | None, kind: str) -> bool:
        if not rel or rel in seen:
            return False
        seen.add(rel)
        g.files.append(rel)
        g.kinds[rel] = kind
        return True

    for c in (picks or {}).get("classes") or []:
        if add(c.get("path"), "classes") and c.get("source") == "made":
            add(test_of(cfg, c.get("name")), "tests")   # a worked example, after its class
    if task:
        add(test_of(cfg, task), "tests")   # the task's own test, already written
    by_name = {c.name: c for c in classes}  # this server's over the library's of the same name
    for name in names_in(job, list(by_name)):
        if add(by_name[name].rel, "named"):
            g.named.append(by_name[name])

    block = compose(g, cfg.repo, sizes)
    ms = int((time.monotonic() - t0) * 1000)
    log("route", "%s picks %s, loaded %s" % (
        task or "-", json.dumps(routing.summary(picks, route_failed), ensure_ascii=False, separators=(",", ":")),
        ", ".join(g.files) or "nothing"))
    if sizes is not None:
        sizes.update(ms=ms, route="failed" if picks is None else "words" if picks.get("fallback") else "jev",
                     state="ok" if state is not None else "unavailable",
                     instances="ok" if running is not None else "unavailable")
    return block
