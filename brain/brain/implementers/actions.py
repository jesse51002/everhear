"""The actions and the running copies, as an implementer's brief gives them (examples.py adds both).

THE ACTIONS come from vfg's actions cache, $VFG_HOME/actions.json, through lib/py/vfg_actions.py,
the one loader vfg and the brain use too. It is read for every brief, and built again first
when an action file is newer than it, so an action an implementer saved a minute ago is in the
next brief. Every action goes in whole, nothing capped: its line, its script, how it runs, and
every value with its type, limits, default (or fixed) and description, those it takes marked *
(the cache's `describe`); the library's by name, then this server's.

WHAT RUNS comes from $VFG_HOME/running.json, which vfg writes through on every run, edit and stop
and the brain fetches from EC2: each copy's name, its action, the values that differ from the
action's own and the task that started it.

Neither goes in the implementer's system prompt (prefix.py): that is the same bytes for every
implementer, so the provider's cache serves it to the second one on, and an action made mid-game
changes only the briefs after it."""
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from .config import ROOT

# lib/py/ of the repo this brain/ is in: vfg_actions, loaded as vfg loads it.
LIB_PY = os.path.join(ROOT, "lib", "py")

_loader: Any = None
_catalogs: dict[tuple[str, str], Any] = {}
# One read at a time: a rebuild imports action files, and two briefs can start at once.
_lock = threading.Lock()


def loader():
    """lib/py/vfg_actions.py, imported once."""
    global _loader
    if _loader is None:
        mod = sys.modules.get("vfg_actions")
        if mod is None:
            spec = importlib.util.spec_from_file_location("vfg_actions", os.path.join(LIB_PY, "vfg_actions.py"))
            mod = importlib.util.module_from_spec(spec)
            sys.modules["vfg_actions"] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                sys.modules.pop("vfg_actions", None)
                raise
        _loader = mod
    return _loader


class _Catalog:
    """vfg_actions.Catalog, made the first time the cache is stale and kept: a later rebuild
    loads only the files that changed. A fresh cache never loads an action."""

    def __init__(self, repo: str, server: str):
        self._args = (repo, server)
        self._cat = None

    def __getattr__(self, name: str):
        if self._cat is None:
            self._cat = loader().Catalog(Path(self._args[0]), self._args[1])
        return getattr(self._cat, name)


def cache(repo: str, server: str, home: str) -> dict:
    """The actions cache, as vfg_actions.cache gives it ({"built_at", "actions": {name: {...}},
    "errors"}), built again first when it is stale. It blocks: the brief runs it in a thread."""
    with _lock:
        va = loader()
        key = (repo, server)
        if key not in _catalogs:
            _catalogs[key] = _Catalog(repo, server)
        return va.cache(Path(repo), server, Path(home), catalog=_catalogs[key])


def forget() -> None:
    """Drops the kept catalogs (tests)."""
    with _lock:
        _catalogs.clear()


# ------------------------------------------------------------------ the actions

def entries(data: dict | None) -> list[tuple[str, dict]]:
    """The cache's actions, the library's by name, then this server's."""
    acts = (data or {}).get("actions") or {}
    return sorted(((n, a) for n, a in acts.items() if isinstance(a, dict)),
                  key=lambda x: (x[1].get("source") != "library", x[0]))


def baseline(entry: dict | None) -> dict:
    """An action's values when nothing is given: each field's default, then its defaults and fixed."""
    out = {k: f.get("default") for k, f in ((entry or {}).get("fields") or {}).items() if isinstance(f, dict)}
    out.update((entry or {}).get("defaults") or {})
    out.update((entry or {}).get("fixed") or {})
    return out


def actions_of(data: dict | None, script: str) -> list[str]:
    """The actions that run `script`: the one named as it first, then the library's, then this server's."""
    return [n for n, a in sorted(entries(data), key=lambda x: x[0] != script) if a.get("script") == script]


def describe(name: str, entry: dict) -> str:
    """One action whole, as the cache renders it."""
    text = entry.get("describe")
    if isinstance(text, str) and text.strip():
        return text.rstrip("\n")
    return "%s — %s\nscript %s · runs %s" % (name, entry.get("line") or "(no line)", entry.get("script") or "?",
                                            str(entry.get("runs") or "?").replace("_", " "))


def section(data: dict | None, failed: str | None, server: str, task: str | None) -> str:
    """The ACTIONS part of the brief: every action whole, or why there are none."""
    run = "vfg action run <action> key=value ... --task %s" % (task or "<task id>")
    if data is None:
        return ("ACTIONS: unavailable (%s). vfg action list names every action, and vfg action list --json gives "
                "each one's values; run one with %s.\n" % (failed or "no answer", run))
    rows = entries(data)
    lib = [(n, a) for n, a in rows if a.get("source") == "library"]
    made = [(n, a) for n, a in rows if a.get("source") != "library"]
    out = ["ACTIONS: every action, the library's (actions/) then this server's (made/%s/actions/), whole, as they "
           "were when you started. Run one with %s; * marks the values it takes, and any value but a fixed one "
           "can be given." % (server, run)]
    for name, a in lib:
        out += ["", describe(name, a)]
    if not lib:
        out += ["", "The library has no action."]
    if made:
        out += ["", "Made on this server (made/%s/actions/):" % server]
        for name, a in made:
            out += ["", describe(name, a)]
    else:
        out += ["", "Made on this server (made/%s/actions/): none yet." % server]
    errors = (data.get("errors") or {}) if isinstance(data.get("errors"), dict) else {}
    if errors:
        out += ["", "Left out, they did not load (vfg action check <name> says why): %s." % "; ".join(
            "%s: %s" % (k, str(v).replace("\n", " ")) for k, v in sorted(errors.items()))]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ the copies

def read_running(home: str) -> dict | None:
    """running.json's copies, {copy: {"action", "script", "values", "live", "task", "started",
    "runs"}}, or None when it cannot be read."""
    try:
        with open(os.path.join(home, "running.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    copies = data.get("copies") if isinstance(data, dict) else None
    if not isinstance(copies, dict):
        return None
    return {k: v for k, v in copies.items() if isinstance(k, str) and isinstance(v, dict)}


def word(v: Any) -> str:
    """A value as key=<this> gives it: true/false, whole numbers without .0, text as it is, lists as JSON."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return "%d" % v
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def pairs(values: dict) -> str:
    """`player=Alex, cooldown=0`."""
    return ", ".join("%s=%s" % (k, word(v)) for k, v in values.items())


def changed(data: dict | None, copy: dict) -> dict:
    """A copy's values that differ from its action's own; all of them when the action is not known."""
    entry = ((data or {}).get("actions") or {}).get(copy.get("action"))
    values = copy.get("values") if isinstance(copy.get("values"), dict) else {}
    if not isinstance(entry, dict):
        return dict(values)
    base = baseline(entry)
    return {k: v for k, v in values.items() if k not in base or base[k] != v}


def copy_line(name: str, copy: dict, data: dict | None) -> str:
    """`galick_gun_2 (galick_gun, player=Alex, cooldown=0, T5)`: the action when it is not the
    copy's name, the values that differ from the action's, the task."""
    bits = []
    if copy.get("action") and copy.get("action") != name:
        bits.append(str(copy["action"]))
    diff = changed(data, copy)
    if diff:
        bits.append(pairs(diff))
    if copy.get("task"):
        bits.append(str(copy["task"]))
    return "%s (%s)" % (name, ", ".join(bits)) if bits else name


def from_state(instances: list) -> dict:
    """`vfg state --json`'s instances as running.json's copies: what runs when running.json cannot be read."""
    out = {}
    for i in instances or []:
        if not isinstance(i, dict):
            continue
        name = i.get("copy") or i.get("instance")
        if name:
            out[name] = {"action": i.get("action") or i.get("script"), "script": i.get("script"),
                         "values": i.get("values") or {}, "task": i.get("task")}
    return out
