"""Environment, paths and the log line format."""
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

HERE = os.path.dirname(os.path.abspath(__file__))  # orchestrator/orchestrator
ORCH = os.path.dirname(HERE)  # orchestrator/


@dataclass
class Config:
    # The session's working directory and the scripts folder's parent, resolved: VFG_REPO, else
    # the repo this orchestrator/ is in.
    repo: str
    # Inbox, log, pid, state, lock, fresh.
    orch_dir: str
    inbox: str
    state_file: str
    lock_file: str
    fresh_file: str
    # orchestrator/prompts, beside the package.
    prompts_dir: str
    # Every task goes to an implementer; the session itself runs no command. Haiku 5.5, as every
    # agent is (the actions spec); VFG_ORCH_MODEL changes it.
    model: str
    effort: str
    # The implementer subagents' own effort (AgentDefinition.effort).
    implementer_effort: str
    # A fresh session once a main-thread call's input passes this many tokens (0: never).
    max_context: int
    # Haiku 5.5, as every agent is (the actions spec); VFG_IMPL_MODEL changes it.
    implementer_model: str
    max_implementers: int
    stale_minutes: int
    implementer_minutes: int
    # The script library's settings, read as vfg reads them (vfg_setting): the server whose
    # made/<server>/ implementers write in, how long vfg route may take, the implementers' cache life.
    server: str = "main"
    route_timeout_s: float = 3.0
    subagent_cache_ttl: str = "5m"


def expand_home(p: str) -> str:
    """`~` and `~/…` only, as the shell would; never `~user`."""
    if p == "~":
        return os.path.expanduser("~")
    if p.startswith("~/"):
        return os.path.join(os.path.expanduser("~"), p[2:])
    return p


def _real(p: str) -> str:
    path = os.path.abspath(expand_home(p))
    try:
        return os.path.realpath(path) if os.path.exists(path) else path
    except OSError:
        return path


_NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def js_number(v: str | None) -> float:
    """A string as JavaScript's Number() reads it, for the forms that matter here: NaN when it
    is not a plain decimal number, and 0 for an empty one."""
    if v is None:
        return math.nan
    s = v.strip()
    if s == "":
        return 0.0
    return float(s) if _NUMBER_RE.match(s) else math.nan


def _int(v: str | None, default: int, minimum: int = 1) -> int:
    n = math.nan if v is None or v == "" else js_number(v)
    return math.floor(n) if math.isfinite(n) and n >= minimum else default


_SERVER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
CACHE_TTLS = ("5m", "1h")


def vfg_config(env: Mapping[str, str]) -> dict[str, str]:
    """$VFG_HOME/config (~/.vfg/config), KEY=value lines, as toolkit/vfg reads it; {} when unreadable."""
    path = os.path.join(expand_home(env.get("VFG_HOME") or "~/.vfg"), "config")
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                out[key] = value
    except OSError:
        pass
    return out


def vfg_setting(env: Mapping[str, str], file: Mapping[str, str], key: str) -> str:
    """A VFG_* setting the way vfg takes it: the environment, else ~/.vfg/config; "" for the default."""
    return env.get(key) or file.get(key) or ""


def _seconds_setting(v: str, default: float, lo: float, hi: float) -> float:
    n = js_number(v) if v else math.nan
    return n if math.isfinite(n) and lo <= n <= hi else default


def load_config(env: Mapping[str, str] | None = None) -> Config:
    if env is None:
        env = os.environ
    file = vfg_config(env)
    server = vfg_setting(env, file, "VFG_SERVER")
    ttl = vfg_setting(env, file, "VFG_SUBAGENT_CACHE_TTL")
    orch_dir = os.path.abspath(expand_home(env.get("VFG_ORCH_DIR") or "~/.vfg/orch"))
    return Config(
        repo=_real(env.get("VFG_REPO") or os.path.dirname(ORCH)),
        orch_dir=orch_dir,
        inbox=os.path.join(orch_dir, "inbox.jsonl"),
        state_file=os.path.join(orch_dir, "state.json"),
        lock_file=os.path.join(orch_dir, "lock"),
        fresh_file=os.path.join(orch_dir, "fresh"),
        prompts_dir=os.path.join(ORCH, "prompts"),
        model=env.get("VFG_ORCH_MODEL") or "claude-haiku-5-5",
        effort=env.get("VFG_ORCH_EFFORT") or "low",
        implementer_model=env.get("VFG_IMPL_MODEL") or env.get("VFG_IMPLEMENTER_MODEL") or "claude-haiku-5-5",
        implementer_effort=env.get("VFG_IMPL_EFFORT") or "low",
        max_context=0 if env.get("VFG_ORCH_MAX_CONTEXT") == "0" else _int(env.get("VFG_ORCH_MAX_CONTEXT"), 70_000),
        max_implementers=_int(env.get("VFG_ORCH_MAX_IMPLEMENTERS"), 4),
        stale_minutes=_int(env.get("VFG_ORCH_STALE_MINUTES"), 10),
        implementer_minutes=_int(env.get("VFG_IMPLEMENTER_MINUTES"), 10),
        # A name vfg would refuse is never joined into a path here; vfg itself says what is wrong.
        server=server if _SERVER_RE.match(server) else "main",
        route_timeout_s=_seconds_setting(vfg_setting(env, file, "VFG_ROUTE_TIMEOUT_S"), 3.0, 0.1, 60),
        subagent_cache_ttl=ttl if ttl in CACHE_TTLS else "5m",
    )


def ignored_mode(env: Mapping[str, str] | None = None) -> str | None:
    """VFG_ORCH_MODE when it asks for a mode that no longer exists: implementers is the only one."""
    mode = (os.environ if env is None else env).get("VFG_ORCH_MODE") or ""
    return mode if mode and mode != "implementers" else None


_quiet = False


def set_quiet(q: bool) -> None:
    """Tests silence the log."""
    global _quiet
    _quiet = q


def _part(p) -> str:
    if isinstance(p, str):
        return p
    if isinstance(p, BaseException):
        return str(p)
    return json.dumps(p, ensure_ascii=False, separators=(",", ":"), default=str)


def log(area: str, *parts) -> None:
    """One log line on stdout: `<ISO time> <area> <message>`; `vfg orch start` sends it to runner.log."""
    if _quiet:
        return
    msg = " ".join(_part(p) for p in parts).replace("\n", " ")
    sys.stdout.write("%s %s %s\n" % (iso(now_ms()), area, msg))
    sys.stdout.flush()


def now_ms() -> float:
    """Milliseconds since the epoch: the runner's clock."""
    return datetime.now(timezone.utc).timestamp() * 1000


def iso(ms: float) -> str:
    """2026-10-07T18:22:31.412Z: milliseconds, UTC, as every timestamp in state.json and the log."""
    d = datetime.fromtimestamp(math.floor(ms) / 1000, timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


def hms(ms: float) -> str:
    """HH:MM:SS of a time, UTC."""
    return iso(ms)[11:19]


_DATE_ONLY_RE = re.compile(r"^\d{4}-\d\d-\d\d$")


def parse_time(s) -> float | None:
    """An ISO 8601 time in milliseconds, or None when it is not one (Date.parse's NaN). A time
    without an offset is local time and a bare date is UTC, as JavaScript reads them."""
    if not isinstance(s, str):
        return None
    t = s.strip()
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc) if _DATE_ONLY_RE.match(t) else d.astimezone()
    return d.timestamp() * 1000


def js_round(x: float) -> int:
    """Math.round: halves go up."""
    return math.floor(x + 0.5)


def js_num(x: float) -> str:
    """A number as JavaScript prints it in a template string: 2, 0.5, never 2.0."""
    return str(int(x)) if float(x).is_integer() else repr(float(x))
