"""What a run changes, noted before it changes it, and put back by code after a cancel or a run that
reached its limit (Prototype_Implementer_Harness, _“Never mind”_ and _A run that reaches its
limit_; in the engine's world, CONTRACT2's "Put-back of a run"). The guard sees every tool call
before it runs (guard.gate), and gives each one it lets through to the run's Snapshot first:

- **a made file**, before the run first writes it: a Write or an Edit of a class's file,
  made/<server>/content/<id>.py, or of its test, made/<server>/tests/<id>.py. Its bytes are kept,
  or that it was not there.
- **a binding**, before the run first binds or unbinds it (`vfg engine bind|unbind <player>
  <phrase> ...`): the engine's bindings, read once at the run's first such call (`vfg engine
  incantations --json`), keep what it was, or that there was none.
- **a grant**, before the run first grants or revokes it (`vfg engine grant|revoke <player>
  <ability>`): who had which ability, read once at the run's first such call (`vfg engine instances
  --kind ability --json`), keeps whether the player had it.

After the implementer's report, put_back() makes each of them what it was, in this order: a made
file not back to its bytes is written back, and one the run created is removed; then one `vfg
engine reload` with no file, when the run wrote a class's file, so a removed class unloads and a
changed one reverts; then, from the engine's bindings as they are now, a binding the run made is
unbound and one it replaced or unbound is bound again as it was; then a grant it made is revoked
and one it revoked is granted again. A scenario it started, an item it gave, a mark it applied and
a place it saved stay the implementer's to undo (a class that goes with its file takes its running
instances with it). A snapshot is kept with its task until the task's next real run, so a cancel
after the run ended works against it; and on disk (save, load), so it does after a restart."""
import base64
import copy as copying
import json
import os
from dataclasses import dataclass, field
from typing import Any

from .config import log
from .guard import _expand_home, binding_id, engine_call, grant_key, made_file, realish, split_shell
from .vfg import VfgRunner, engine_read, why

VFG_TIMEOUT_S = 60.0
# The engine's reload op waits up to 120 s for its own answer (engine/README.md).
RELOAD_TIMEOUT_S = 150.0

# what put_back() did, item by item, and what it could not do
Done = list[str]


@dataclass
class Snapshot:
    repo: str
    server: str
    task: str | None
    vfg: VfgRunner
    # a made file's real path -> its bytes before the run's first write, None when it was not there
    files: dict[str, bytes | None] = field(default_factory=dict)
    # the engine's bindings by id, as `vfg engine incantations --json` listed them at the run's first
    # bind or unbind; None until then
    incantations: dict[str, dict] | None = None
    # the bindings the run bound or unbound: id -> [player, phrase], as it gave them
    bound: dict[str, list[str]] = field(default_factory=dict)
    # who had which ability ("steve:fireball", grant_key) at the run's first grant or revoke; None until then
    granted: list[str] | None = None
    # the grants the run made or revoked: grant_key -> [player, ability], as it gave them
    grants: dict[str, list[str]] = field(default_factory=dict)

    # ------------------------------------------------------------------ before a call runs

    async def before(self, call: dict) -> None:
        """Notes what this call (let through by the guard, not yet run) is about to change."""
        name = call.get("tool_name")
        inp = call.get("tool_input") or {}
        if name in ("Write", "Edit") and inp.get("file_path"):
            path = str(inp["file_path"])
            if made_file(self.repo, self.server, path):
                self._file(os.path.abspath(os.path.join(self.repo, _expand_home(path))))
        elif name == "Bash":
            for cmd in split_shell(str(inp.get("command") or "")):
                await self._command(cmd.words)

    async def _command(self, words: list[str]) -> None:
        if words[:2] != ["vfg", "engine"]:
            return
        call = engine_call(words[2:])
        if call is None:
            return
        if call.op in ("bind", "unbind"):
            await self._binding(call.words[0], call.words[1])
        elif call.op in ("grant", "revoke"):
            await self._grant(call.words[0], call.words[1])

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

    async def _binding(self, player: str, phrase: str) -> None:
        bid = binding_id(player, phrase)
        if bid is None or bid in self.bound:
            return
        if self.incantations is None:
            data, failed = await engine_read(self.vfg, "incantations", timeout=VFG_TIMEOUT_S)
            if data is None:
                log("putback", "%s: the engine's bindings could not be read before %s was bound or unbound (%s): "
                    "it is not put back" % (self.task, bid, failed))
                return
            self.incantations = {e["id"]: copying.deepcopy(e) for e in data.get("incantations") or []
                                 if isinstance(e, dict) and isinstance(e.get("id"), str)}
        self.bound[bid] = [player, phrase]

    async def _grant(self, player: str, ability: str) -> None:
        key = grant_key(player, ability)
        if key in self.grants:
            return
        if self.granted is None:
            data, failed = await engine_read(self.vfg, "instances", "--kind", "ability", timeout=VFG_TIMEOUT_S)
            if data is None:
                log("putback", "%s: the engine's grants could not be read before %s was granted or revoked (%s): "
                    "it is not put back" % (self.task, key, failed))
                return
            self.granted = granted(data)
        self.grants[key] = [player, ability]


def granted(data: dict) -> list[str]:
    """Who has which ability, as grant_key names each, from `vfg engine instances --kind ability --json`."""
    return sorted({grant_key(i.get("user"), i.get("id")) for i in data.get("instances") or []
                   if isinstance(i, dict) and i.get("user") and i.get("id") and i.get("kind", "ability") == "ability"})


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


def word(v: Any) -> str:
    """A value as key=<this> gives it: true/false, whole numbers without .0, text as it is, lists and
    objects as JSON (vfg reads a value starting with [ or { as JSON)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return "%d" % v
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def bind_words(e: dict) -> list[str]:
    """`vfg engine bind`'s words that bind a listed entry again: its player and phrase, then its
    ability, or its scenario and that scenario's fixed args; --by when the listing names it."""
    out = ["engine", "bind", str(e.get("player")), str(e.get("phrase"))]
    if e.get("ability"):
        out.append(str(e["ability"]))
    else:
        out += ["--scenario", str(e.get("scenario"))]
        out += ["%s=%s" % (k, word(v)) for k, v in sorted((e.get("args") or {}).items())]
    if e.get("by"):
        out += ["--by", str(e["by"])]
    return out


def _target(e: dict) -> tuple:
    """What a binding casts or starts: two that differ in it are not the same binding."""
    return e.get("ability"), e.get("scenario"), json.dumps(e.get("args") or {}, sort_keys=True)


async def put_back(s: Snapshot) -> tuple[Done, Done]:
    """Makes what the snapshot noted what it was: (what was put back, what could not be)."""
    did: Done = []
    failed: Done = []
    rel = lambda p: os.path.relpath(p, s.repo)  # noqa: E731

    # made files: as they were before the run, or gone
    for path, before in sorted(s.files.items()):
        current = _read(path)
        try:
            if before is None and os.path.lexists(path):
                os.remove(path)
                did.append("removed %s" % rel(path))
            elif before is not None and current != before:
                _write(path, before)
                did.append("restored %s" % rel(path))
        except OSError as e:
            failed.append("could not put back %s (%s)" % (rel(path), e))

    # the engine runs the classes as their files are now: a removed one unloads, a changed one reverts
    if any((made_file(s.repo, s.server, p) or ("",))[0] == "content" for p in s.files):
        r = await s.vfg("engine", "reload", timeout=RELOAD_TIMEOUT_S)
        (did if r.code == 0 else failed).append(
            "reloaded the engine" if r.code == 0
            else "could not reload the engine (%s), so it may still run the run's classes" % why(r))

    await _bindings_back(s, did, failed)
    await _grants_back(s, did, failed)
    return did, failed


async def _bindings_back(s: Snapshot, did: Done, failed: Done) -> None:
    """Each binding the run made, unbound; each it replaced or unbound, bound again as it was; read
    from the engine as they are now, so one the implementer put back itself is left alone."""
    if not s.bound:
        return
    data, why_not = await engine_read(s.vfg, "incantations", timeout=VFG_TIMEOUT_S)
    if data is None:
        failed.append("could not read the bindings to put them back (%s)" % why_not)
        return
    now = {e["id"]: e for e in data.get("incantations") or [] if isinstance(e, dict) and isinstance(e.get("id"), str)}
    for bid, (player, phrase) in sorted(s.bound.items()):
        before, cur = (s.incantations or {}).get(bid), now.get(bid)
        if before is None and cur is not None:
            r = await s.vfg("engine", "unbind", str(cur.get("player") or player), str(cur.get("phrase") or phrase),
                            timeout=VFG_TIMEOUT_S)
            (did if r.code == 0 else failed).append("unbound %s" % bid if r.code == 0
                                                    else "could not unbind %s (%s)" % (bid, why(r)))
        elif before is not None and (cur is None or _target(cur) != _target(before)):
            r = await s.vfg(*bind_words(before), timeout=VFG_TIMEOUT_S)
            if r.code != 0:
                failed.append("could not bind %s again (%s)" % (bid, why(r)))
            elif before.get("aliases"):
                # CONTRACT: vfg engine bind takes no aliases, so a binding bound again has none.
                did.append("bound %s again, but for its aliases (%s)" % (bid, ", ".join(map(str, before["aliases"]))))
            else:
                did.append("bound %s again as it was" % bid)


async def _grants_back(s: Snapshot, did: Done, failed: Done) -> None:
    """Each grant the run made, revoked; each it revoked, granted again; read from the engine as
    they are now (a class unloaded by the reload took its grants with it)."""
    if not s.grants:
        return
    data, why_not = await engine_read(s.vfg, "instances", "--kind", "ability", timeout=VFG_TIMEOUT_S)
    if data is None:
        failed.append("could not read the grants to put them back (%s)" % why_not)
        return
    now, before = set(granted(data)), set(s.granted or [])
    for key, (player, ability) in sorted(s.grants.items()):
        if key in now and key not in before:
            r = await s.vfg("engine", "revoke", player, ability, timeout=VFG_TIMEOUT_S)
            (did if r.code == 0 else failed).append(
                "revoked %s from %s" % (ability, player) if r.code == 0
                else "could not revoke %s from %s (%s)" % (ability, player, why(r)))
        elif key in before and key not in now:
            r = await s.vfg("engine", "grant", player, ability, timeout=VFG_TIMEOUT_S)
            (did if r.code == 0 else failed).append(
                "granted %s to %s again" % (ability, player) if r.code == 0
                else "could not grant %s to %s again (%s)" % (ability, player, why(r)))


# ------------------------------------------------------------------ on disk

def save(s: Snapshot, path: str) -> None:
    """The snapshot as JSON, the files' bytes in base64, mode 600 in a 700 folder."""
    data = {"repo": s.repo, "server": s.server, "task": s.task,
            "files": {p: None if b is None else base64.b64encode(b).decode("ascii") for p, b in s.files.items()},
            "incantations": s.incantations, "bound": s.bound, "granted": s.granted, "grants": s.grants}
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def load(path: str, vfg: VfgRunner) -> Snapshot | None:
    """A snapshot save() wrote, or None when there is none or it cannot be read (one from before the
    engine, with copies and places, among them)."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return Snapshot(repo=d["repo"], server=d["server"], task=d.get("task"), vfg=vfg,
                        files={p: None if b is None else base64.b64decode(b) for p, b in d["files"].items()},
                        incantations=d["incantations"], bound=dict(d["bound"]), granted=d["granted"],
                        grants=dict(d["grants"]))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        log("putback", "the snapshot in %s cannot be read: %s" % (path, e))
        return None


def line(did: Done, failed: Done) -> str:
    """The report's line saying what code put back."""
    if not did and not failed:
        return "Code put back: nothing; the run had changed no made file, binding or grant."
    out = "Code put back: %s." % ("; ".join(did) if did else "nothing")
    if failed:
        out += " It could not: %s." % "; ".join(failed)
    return out
