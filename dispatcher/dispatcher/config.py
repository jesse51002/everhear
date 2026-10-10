"""Settings and the log line.

Each setting comes from the environment, then ~/.vfg/config (KEY=value, as the
toolkit reads it), then its default. The OpenRouter key is not one of them: only
vfg_llm reads it, and nothing here ever logs it.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from vfg_llm import _config as read_config_file  # the one KEY=value parser, shared with structured()

DEFAULT_MODEL = "~deepseek/deepseek-v4-flash-latest"
# The OpenRouter providers tried first for every model call, fastest first: in the replay of
# the 2026-10-08 playtest these lost no work, while others serving the same model gave
# actions the orchestrator's work. "none" leaves the choice to OpenRouter.
DEFAULT_PROVIDERS = ("parasail", "wafer", "phala", "together", "venice")
# The repo this dispatcher/ is in: actions/ and scripts/ live beside it.
REPO = Path(__file__).resolve().parents[2]
SERVER = "main"


@dataclass(frozen=True)
class Config:
    home: Path                      # VFG_HOME, default ~/.vfg
    dispatch_dir: Path              # VFG_DISPATCH_DIR, default ~/.vfg/dispatch
    orch_dir: Path                  # VFG_ORCH_DIR, default ~/.vfg/orch (replay reads its inbox)
    repo: Path                      # VFG_REPO, default this repo
    server: str = SERVER            # VFG_SERVER: whose made/<server>/actions/ are on the menu too
    model: str = DEFAULT_MODEL      # VFG_DISPATCH_MODEL
    providers: tuple[str, ...] = DEFAULT_PROVIDERS  # VFG_DISPATCH_PROVIDERS, comma-separated; () is OpenRouter's choice
    retries: int = 1                # VFG_DISPATCH_RETRIES, per action
    window_s: float = 10.0          # VFG_DISPATCH_WINDOW_S
    action_timeout_s: float = 20.0  # VFG_DISPATCH_ACTION_TIMEOUT_S
    model_timeout_s: float = 10.0   # VFG_DISPATCH_MODEL_TIMEOUT_S: one structured() call, retries included
    stop_grace_s: float = 10.0      # how long a running action may finish on SIGTERM
    stale_minutes: int = 10         # VFG_ORCH_STALE_MINUTES: a task that waited longer is failed, never run
    running_ttl_s: float = 30.0     # VFG_RUNNING_TTL_S: how often running.json is fetched again in the background; 0: never

    @property
    def inbox(self) -> Path:
        return self.dispatch_dir / "inbox.jsonl"

    @property
    def state_file(self) -> Path:
        return self.dispatch_dir / "state.json"

    @property
    def lock_file(self) -> Path:
        return self.dispatch_dir / "lock"

    @property
    def running_file(self) -> Path:
        return self.home / "running.json"


def load(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    home = Path(env.get("VFG_HOME") or "~/.vfg").expanduser()
    file = read_config_file(home / "config")

    def get(key: str, default: str = "") -> str:
        return (env.get(key) or "").strip() or (file.get(key) or "").strip() or default

    def number(key: str, default: float, kind=float, least: float = 0):
        raw = get(key)
        if not raw:
            return default
        try:
            value = kind(raw)
        except ValueError:
            value = None
        if value is None or value < least:
            log("config", f"{key}={raw!r} is not a {kind.__name__} >= {least:g}; using {default}")
            return default
        return value

    def names(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        raw = get(key)
        if not raw:
            return default
        if raw.lower() == "none":
            return ()
        return tuple(x.strip() for x in raw.split(",") if x.strip())

    def path(key: str, default: Path) -> Path:
        raw = get(key)
        return Path(raw).expanduser() if raw else default

    return Config(
        home=home,
        dispatch_dir=path("VFG_DISPATCH_DIR", home / "dispatch"),
        orch_dir=path("VFG_ORCH_DIR", home / "orch"),
        repo=path("VFG_REPO", REPO),
        server=get("VFG_SERVER", SERVER),
        model=get("VFG_DISPATCH_MODEL", DEFAULT_MODEL),
        providers=names("VFG_DISPATCH_PROVIDERS", DEFAULT_PROVIDERS),
        retries=number("VFG_DISPATCH_RETRIES", 1, int, 0),
        window_s=number("VFG_DISPATCH_WINDOW_S", 10.0, float, 0),
        action_timeout_s=number("VFG_DISPATCH_ACTION_TIMEOUT_S", 20.0, float, 1),
        model_timeout_s=number("VFG_DISPATCH_MODEL_TIMEOUT_S", 10.0, float, 1),
        stale_minutes=number("VFG_ORCH_STALE_MINUTES", 10, int, 1),
        running_ttl_s=number("VFG_RUNNING_TTL_S", 30.0, float, 0),
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log(area: str, *parts: object) -> None:
    """One line on stdout: `<ISO UTC time> <area> <message>`. vfg orch start sends it to dispatcher.log."""
    msg = " ".join(p if isinstance(p, str) else repr(p) for p in parts).replace("\n", " ")
    sys.stdout.write(f"{now_iso()} {area} {msg}\n")
    sys.stdout.flush()
