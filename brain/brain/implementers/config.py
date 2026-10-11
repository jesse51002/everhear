"""The implementers' settings, from the environment and ~/.vfg/config, and the log line format."""
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

HERE = os.path.dirname(os.path.abspath(__file__))  # brain/brain/implementers
BRAIN = os.path.dirname(os.path.dirname(HERE))  # brain/
ROOT = os.path.dirname(BRAIN)  # the repo this brain/ is in


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


@dataclass
class ImplementerConfig:
    # The implementers' working directory and the scripts folder's parent, resolved: VFG_REPO,
    # else the repo this brain/ is in.
    repo: str
    # vfg's own folder, VFG_HOME (~/.vfg): the actions cache and running.json that vfg keeps,
    # which every implementer's brief is read from.
    home: str
    # The brain's folder, VFG_BRAIN_DIR (VFG_HOME/brain): py_compile's cache and the implementer's
    # system prompt file go in it.
    brain_dir: str
    # brain/prompts/implementer.md: the implementer's rules.
    prompt: str
    # Haiku 5.5, as every agent is (the actions spec): VFG_IMPL_MODEL, VFG_IMPL_EFFORT.
    model: str = "claude-haiku-5-5"
    effort: str = "low"
    # Turns before the CLI stops it (VFG_IMPL_MAX_TURNS), and how long one may run before it is
    # stopped and reported failed (VFG_IMPLEMENTER_MINUTES).
    max_turns: int = 40
    time_limit_s: float = 600.0
    # The script library's settings, read as vfg reads them (vfg_setting): the server whose
    # made/<server>/ implementers write in, how long vfg route may take, and the prompt cache's
    # life (VFG_IMPL_CACHE_TTL, before it VFG_SUBAGENT_CACHE_TTL).
    server: str = "main"
    route_timeout_s: float = 3.0
    cache_ttl: str = "5m"

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None) -> "ImplementerConfig":
        if env is None:
            env = os.environ
        file = vfg_config(env)
        server = vfg_setting(env, file, "VFG_SERVER")
        ttl = vfg_setting(env, file, "VFG_IMPL_CACHE_TTL") or vfg_setting(env, file, "VFG_SUBAGENT_CACHE_TTL")
        home = os.path.abspath(expand_home(env.get("VFG_HOME") or "~/.vfg"))
        return cls(
            repo=_real(env.get("VFG_REPO") or ROOT),
            home=home,
            brain_dir=os.path.abspath(expand_home(env.get("VFG_BRAIN_DIR") or os.path.join(home, "brain"))),
            prompt=os.path.join(BRAIN, "prompts", "implementer.md"),
            model=env.get("VFG_IMPL_MODEL") or env.get("VFG_IMPLEMENTER_MODEL") or "claude-haiku-5-5",
            effort=env.get("VFG_IMPL_EFFORT") or "low",
            max_turns=_int(env.get("VFG_IMPL_MAX_TURNS"), 40),
            time_limit_s=60.0 * _int(env.get("VFG_IMPLEMENTER_MINUTES"), 10),
            # A name vfg would refuse is never joined into a path here; vfg itself says what is wrong.
            server=server if _SERVER_RE.match(server) else "main",
            route_timeout_s=_seconds_setting(vfg_setting(env, file, "VFG_ROUTE_TIMEOUT_S"), 3.0, 0.1, 60),
            cache_ttl=ttl if ttl in CACHE_TTLS else "5m",
        )


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
    """One log line on stdout: `<ISO time> <area> <message>`, as the brain's own, into brain.log."""
    if _quiet:
        return
    msg = " ".join(_part(p) for p in parts).replace("\n", " ")
    sys.stdout.write("%s %s %s\n" % (iso(now_ms()), area, msg))
    sys.stdout.flush()


def now_ms() -> float:
    """Milliseconds since the epoch."""
    return datetime.now(timezone.utc).timestamp() * 1000


def iso(ms: float) -> str:
    """2026-10-07T18:22:31.412Z: milliseconds, UTC, as every timestamp in the log."""
    d = datetime.fromtimestamp(math.floor(ms) / 1000, timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


def js_num(x: float) -> str:
    """A number as JavaScript prints it in a template string: 2, 0.5, never 2.0."""
    return str(int(x)) if float(x).is_integer() else repr(float(x))
