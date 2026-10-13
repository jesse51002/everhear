"""What a run changes, noted before it changes it, and put back by code after a cancel or a run that
reached its limit (Prototype_Implementer_Harness, _“Never mind”_ and _A run that reaches its
limit_). The guard sees every tool call before it runs (guard.gate), and gives each one it lets
through to the run's Snapshot first:

- **a made file**, before the run first writes it: a Write or an Edit under made/<server>/, and
  the files `vfg script new` (its script's folder, its action and its skeleton test), `vfg test`
  (a skeleton test, when there is none), `vfg script save` (an action) and `vfg preset save` (a
  preset) make. Its bytes are kept, or that it was not there.
- **a running copy**, before the run first changes it: `vfg script set|stop <copy>`, `vfg action run
  … as=<copy>`, and the edit and stop actions' copy=<copy>. Its entry in running.json is kept, or
  that it was not running; and, at the run's first such call, the names of every copy then
  running, so a copy the run starts under a name the box picks is known as new.
- **a saved place**, before the run first saves it: its entry (`vfg place list --json`), or that
  there was none.

After the implementer's report, put_back() makes each of them what it was: a made file not back to
its bytes is written back, a file or folder the run created is removed, a copy the run started is
stopped, a copy it changed or stopped is set back or started again with its values, and a place it
saved is forgotten or saved again where it was. An incantation it bound, edited or removed stays
the implementer's to undo. A snapshot is kept with its task until the task's next real run, so a
cancel after the run ended works against it; and on disk (save, load), so it does after a restart."""
import base64
import copy as copying
import json
import os
import shutil
from dataclasses import dataclass, field

from . import actions
from .config import log
from .guard import MADE_NAME, made_script, new_script_name, realish, run_words, split_shell, within
from .vfg import VfgRunner, why

VFG_TIMEOUT_S = 60.0

# what put_back() did, item by item, and what it could not do
Done = list[str]


@dataclass
class Snapshot:
    repo: str
    server: str
    home: str
    task: str | None
    vfg: VfgRunner
    # a made file's real path -> its bytes before the run's first write, None when it was not there
    files: dict[str, bytes | None] = field(default_factory=dict)
    # a script folder `vfg script new` makes -> whether it was there before
    dirs: dict[str, bool] = field(default_factory=dict)
    # a copy -> its running.json entry before the run first changed it, None when it was not running
    copies: dict[str, dict | None] = field(default_factory=dict)
    # every copy running at the run's first call that can start one; None until then
    running_before: set[str] | None = None
    # a place -> its entry before the run first saved it, None when there was none
    places: dict[str, dict | None] = field(default_factory=dict)

    # ------------------------------------------------------------------ before a call runs

    async def before(self, call: dict) -> None:
        """Notes what this call (let through by the guard, not yet run) is about to change."""
        name = call.get("tool_name")
        inp = call.get("tool_input") or {}
        if name in ("Write", "Edit") and inp.get("file_path"):
            path = realish(os.path.abspath(os.path.join(self.repo, str(inp["file_path"]))))
            if within(self.made, path):
                self._file(path)
        elif name == "Bash":
            for cmd in split_shell(str(inp.get("command") or "")):
                await self._command(cmd.words)

    @property
    def made(self) -> str:
        return os.path.join(self.repo, "made", self.server)

    async def _command(self, words: list[str]) -> None:
        if words[:1] != ["vfg"]:
            return
        what = words[1:3]
        if what == ["script", "new"]:
            name = new_script_name(words)
            if name:
                self._dir(os.path.join(self.made, "scripts", name))
                self._file(os.path.join(self.made, "actions", name + ".py"))
                self._file(os.path.join(self.made, "tests", name + ".py"))  # its skeleton test
        elif words[1:2] == ["test"] and len(words) >= 3 and MADE_NAME.fullmatch(words[2]):
            self._file(os.path.join(self.made, "tests", words[2] + ".py"))  # a skeleton when there is none
        elif what == ["script", "save"]:
            name = new_script_name(words)
            if name:
                self._file(os.path.join(self.made, "actions", name + ".py"))
        elif what == ["preset", "save"] and len(words) == 6:
            self._file(os.path.join(self.made, "presets", words[5] + ".py"))
        elif what in (["script", "set"], ["script", "stop"]) and len(words) >= 4:
            self._copy(words[3])
        elif what == ["action", "run"]:
            action, as_ = run_words(words[3:])
            self._running()
            if as_:
                self._copy(as_)
            given = dict(w.split("=", 1) for w in words[3:] if "=" in w and not w.startswith("-"))
            if action in ("edit", "stop") and given.get("copy"):
                self._copy(given["copy"])
        elif what == ["place", "save"] and len(words) >= 4:
            await self._place(words[3])

    def _file(self, path: str) -> None:
        path = realish(path)
        if path in self.files:
            return
        try:
            with open(path, "rb") as f:
                self.files[path] = f.read()
        except FileNotFoundError:
            self.files[path] = None
        except OSError as e:  # a folder, say: nothing to keep
            log("putback", "%s: %s is not kept: %s" % (self.task, path, e))

    def _dir(self, path: str) -> None:
        path = realish(path)
        if path not in self.dirs:
            self.dirs[path] = os.path.exists(path)

    def _running(self) -> None:
        if self.running_before is None:
            self.running_before = set(actions.read_running(self.home) or {})

    def _copy(self, name: str) -> None:
        self._running()
        if name not in self.copies:
            self.copies[name] = copying.deepcopy((actions.read_running(self.home) or {}).get(name))

    async def _place(self, name: str) -> None:
        if name in self.places:
            return
        r = await self.vfg("place", "list", "--json", "--fresh", timeout=VFG_TIMEOUT_S)  # not the cache
        try:
            found = json.loads(r.stdout) if r.code == 0 else None
        except ValueError:
            found = None
        if not isinstance(found, dict):
            log("putback", "%s: the places could not be read before %s was saved (%s): it is not put back"
                % (self.task, name, why(r)))
            return
        self.places[name] = copying.deepcopy(found.get(name))


# ------------------------------------------------------------------ after the report

def _read(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def changed(s: Snapshot) -> list[str]:
    """The made files the run changed or created: those not as the snapshot kept them."""
    return sorted(p for p, before in s.files.items() if _read(p) != before)


def _write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.putback-%d" % (path, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def given_words(home: str, action: str, values: dict) -> list[str]:
    """A copy's values as `vfg action run` words, as it would have been given them: running.json
    keeps every value (default <- defaults <- fixed <- given), so the fixed ones, which vfg refuses
    to be given, and those equal to the action's own default (the actions cache) are left out."""
    try:
        with open(os.path.join(home, "actions.json"), encoding="utf-8") as f:
            entry = (json.load(f).get("actions") or {}).get(action) or {}
    except (OSError, ValueError, AttributeError):
        entry = {}
    fixed = set(entry.get("fixed") or {})
    fields = entry.get("fields") or {}
    own = {k: f.get("default") for k, f in fields.items() if isinstance(f, dict) and "default" in f}
    return ["%s=%s" % (k, actions.word(v)) for k, v in sorted(values.items())
            if k not in fixed and not (k in own and own[k] == v)]


async def put_back(s: Snapshot) -> tuple[Done, Done]:
    """Makes what the snapshot noted what it was: (what was put back, what could not be)."""
    did: Done = []
    failed: Done = []
    rel = lambda p: os.path.relpath(p, s.repo)  # noqa: E731
    now = actions.read_running(s.home) or {}

    # copies the run started: stopped first, so nothing runs a file about to change
    started = [n for n, e in now.items()
               if (n in s.copies and s.copies[n] is None)
               or (s.running_before is not None and n not in s.running_before and n not in s.copies
                   and s.task is not None and e.get("task") == s.task)]
    for name in sorted(started):
        r = await s.vfg("script", "stop", name, timeout=VFG_TIMEOUT_S)
        (did if r.code == 0 else failed).append("stopped %s" % name if r.code == 0
                                                else "could not stop %s (%s)" % (name, why(r)))

    # made files and folders
    new_dirs = [p for p, there in s.dirs.items() if not there]  # the run's own: they go whole
    gone = [p for p in new_dirs if os.path.exists(p)]
    restored_scripts: set[str] = set()
    for path, before in sorted(s.files.items()):
        if any(path == d or path.startswith(d + "/") for d in new_dirs):
            continue
        current = _read(path)
        try:
            if before is None and os.path.lexists(path):
                os.remove(path)
                did.append("removed %s" % rel(path))
            elif before is not None and current != before:
                _write(path, before)
                did.append("restored %s" % rel(path))
            else:
                continue
            script = made_script(path, _Where(s.repo, s.server))
            if script:
                restored_scripts.add(script)
            if os.path.dirname(path) == realish(os.path.join(s.made, "actions")):
                restored_scripts.add(os.path.basename(path)[:-3])
        except OSError as e:
            failed.append("could not put back %s (%s)" % (rel(path), e))
    for path in sorted(gone):
        try:
            shutil.rmtree(path)
            did.append("removed %s/" % rel(path))
        except OSError as e:
            failed.append("could not remove %s/ (%s)" % (rel(path), e))

    # copies the run changed or stopped: as they were, on the code as it was
    now = actions.read_running(s.home) or {}
    for name, before in sorted(s.copies.items()):
        if before is None:
            continue
        cur = now.get(name)
        values = before.get("values") or {}
        if (cur is None or cur.get("started") != before.get("started")
                or before.get("script") in restored_scripts or before.get("action") in restored_scripts):
            if cur is not None:
                await s.vfg("script", "stop", name, timeout=VFG_TIMEOUT_S)
            words = given_words(s.home, before.get("action") or "", values)
            task = ["--task", before["task"]] if before.get("task") else []
            r = await s.vfg("action", "run", str(before.get("action")), *words, "as=%s" % name, *task,
                            timeout=VFG_TIMEOUT_S)
            (did if r.code == 0 else failed).append(
                "started %s again as it was" % name if r.code == 0 else "could not start %s again (%s)" % (name, why(r)))
        elif (cur.get("values") or {}) != values:
            changed = {k: v for k, v in values.items() if (cur.get("values") or {}).get(k) != v}
            r = await s.vfg("script", "set", name, *["%s=%s" % (k, actions.word(v)) for k, v in sorted(changed.items())],
                            timeout=VFG_TIMEOUT_S)
            (did if r.code == 0 else failed).append(
                "set %s back (%s)" % (name, ", ".join(sorted(changed))) if r.code == 0
                else "could not set %s back (%s)" % (name, why(r)))

    # places the run saved
    for name, before in sorted(s.places.items()):
        if before is None:
            r = await s.vfg("place", "forget", name, timeout=VFG_TIMEOUT_S)
            ok = r.code == 0
            (did if ok else failed).append("forgot the place %s" % name if ok
                                           else "could not forget the place %s (%s)" % (name, why(r)))
            continue
        pos = before.get("pos") or []
        if len(pos) != 3:
            failed.append("could not save the place %s again: its old position is unknown" % name)
            continue
        by = ["--by", before["by"]] if before.get("by") else []
        r = await s.vfg("place", "save", name, "--at", *[actions.word(v) for v in pos],
                        "--dim", str(before.get("dim") or "overworld"), *by, timeout=VFG_TIMEOUT_S)
        (did if r.code == 0 else failed).append("saved the place %s where it was" % name if r.code == 0
                                                else "could not save the place %s again (%s)" % (name, why(r)))
    return did, failed


# ------------------------------------------------------------------ on disk

def save(s: Snapshot, path: str) -> None:
    """The snapshot as JSON, the files' bytes in base64, mode 600 in a 700 folder."""
    data = {"repo": s.repo, "server": s.server, "home": s.home, "task": s.task,
            "files": {p: None if b is None else base64.b64encode(b).decode("ascii") for p, b in s.files.items()},
            "dirs": s.dirs, "copies": s.copies,
            "running_before": None if s.running_before is None else sorted(s.running_before), "places": s.places}
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def load(path: str, vfg: VfgRunner) -> Snapshot | None:
    """A snapshot save() wrote, or None when there is none or it cannot be read."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        rb = d.get("running_before")
        return Snapshot(repo=d["repo"], server=d["server"], home=d["home"], task=d.get("task"), vfg=vfg,
                        files={p: None if b is None else base64.b64decode(b) for p, b in d["files"].items()},
                        dirs=dict(d["dirs"]), copies=dict(d["copies"]), running_before=None if rb is None else set(rb),
                        places=dict(d["places"]))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        log("putback", "the snapshot in %s cannot be read: %s" % (path, e))
        return None


@dataclass
class _Where:
    """What guard.made_script reads of a GuardContext."""
    repo: str
    server: str


def line(did: Done, failed: Done) -> str:
    """The report's line saying what code put back."""
    if not did and not failed:
        return "Code put back: nothing; the run had changed no made file, copy or place."
    out = "Code put back: %s." % ("; ".join(did) if did else "nothing")
    if failed:
        out += " It could not: %s." % "; ".join(failed)
    return out
