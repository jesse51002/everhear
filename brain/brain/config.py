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

# The repo this brain/ is in: actions/ and scripts/ live beside it.
REPO = Path(__file__).resolve().parents[2]
SERVER = "main"
# The cheap model, and the providers that answer it fastest, first (the incantation alias call's).
CHEAP = "~deepseek/deepseek-v4-flash-latest"
FAST = ("parasail", "wafer", "phala", "together", "venice")


@dataclass(frozen=True)
class Config:
    home: Path                      # VFG_HOME, default ~/.vfg
    brain_dir: Path                 # VFG_BRAIN_DIR, default ~/.vfg/brain: tasks.json, brain.log, lock, refs/
    repo: Path                      # VFG_REPO, default this repo
    server: str = SERVER            # VFG_SERVER: whose made/<server>/actions/ are on the menu too
    owner: str = ""                 # VFG_OWNER: the owner's Minecraft name, whose word wins
    # The brain's one call: GPT-6.1 Sol, reasoning low (Prototype_Brain).
    model: str = "openai/gpt-6.1-sol"          # VFG_BRAIN_MODEL
    reasoning: str = "low"                     # VFG_BRAIN_REASONING: off, or an effort
    providers: tuple[str, ...] = ()            # VFG_BRAIN_PROVIDERS, comma-separated, the brain's call only;
                                               # (): OpenRouter's choice
    # Respond, the brain's cheap mode, for conversational lines: DeepSeek V4 Flash with reasoning
    # off, through the providers that answer it fastest (at low reasoning it thinks ~900 tokens:
    # 7.7 s a call). The action planner, kept only to retry a command Minecraft refused: Gemini 3.8
    # Flash, low, which read every item listing right where DeepSeek misread 2 of 6 (2026-10-10).
    respond_model: str = CHEAP                      # VFG_RESPOND_MODEL
    respond_reasoning: str = "off"                  # VFG_RESPOND_REASONING
    respond_providers: tuple[str, ...] = FAST       # VFG_RESPOND_PROVIDERS
    plan_model: str = "google/gemini-3.8-flash"     # VFG_PLAN_MODEL
    plan_reasoning: str = "low"                     # VFG_PLAN_REASONING
    plan_providers: tuple[str, ...] = ()            # VFG_PLAN_PROVIDERS
    plan_check: bool = False        # VFG_PLAN_CHECK: on checks a retried command against its references again
    batch_s: float = 60.0           # VFG_BRAIN_BATCH_S: this long with no read and something unread: a batch call
    checkpoint: int = 5             # VFG_BRAIN_CHECKPOINT: a cache mark every this many minute-blocks
    history_hours: float = 6.0      # VFG_BRAIN_HISTORY_HOURS: how much of the feed the context starts with
    timeout_s: float = 20.0         # VFG_BRAIN_TIMEOUT_S: one structured() call, its retries included
    running_ttl_s: float = 30.0     # VFG_RUNNING_TTL_S: how often running.json is fetched again; 0: never
    action_timeout_s: float = 20.0  # VFG_DISPATCH_ACTION_TIMEOUT_S: a once action's, unless its class says
    stop_grace_s: float = 10.0      # how long in-flight calls and actions may finish on SIGTERM

    @staticmethod
    def effort_of(reasoning: str) -> bool | str:
        """What structured() takes: False for off, else the effort."""
        return False if reasoning in ("", "off") else reasoning

    @property
    def tasks_file(self) -> Path:
        return self.brain_dir / "tasks.json"

    @property
    def lock_file(self) -> Path:
        return self.brain_dir / "lock"

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

    def names(key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
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
        brain_dir=path("VFG_BRAIN_DIR", home / "brain"),
        repo=path("VFG_REPO", REPO),
        server=get("VFG_SERVER", SERVER),
        owner=get("VFG_OWNER"),
        model=get("VFG_BRAIN_MODEL", "openai/gpt-6.1-sol"),
        reasoning=get("VFG_BRAIN_REASONING", "low"),
        providers=names("VFG_BRAIN_PROVIDERS"),
        respond_model=get("VFG_RESPOND_MODEL", CHEAP),
        respond_reasoning=get("VFG_RESPOND_REASONING", "off"),
        respond_providers=names("VFG_RESPOND_PROVIDERS", FAST),
        plan_model=get("VFG_PLAN_MODEL", "google/gemini-3.8-flash"),
        plan_reasoning=get("VFG_PLAN_REASONING", "low"),
        plan_providers=names("VFG_PLAN_PROVIDERS"),
        plan_check=get("VFG_PLAN_CHECK", "off").lower() in ("on", "1", "true", "yes"),
        batch_s=number("VFG_BRAIN_BATCH_S", 60.0, float, 1),
        checkpoint=number("VFG_BRAIN_CHECKPOINT", 5, int, 1),
        history_hours=number("VFG_BRAIN_HISTORY_HOURS", 6.0, float, 0),
        timeout_s=number("VFG_BRAIN_TIMEOUT_S", 20.0, float, 1),
        running_ttl_s=number("VFG_RUNNING_TTL_S", 30.0, float, 0),
        action_timeout_s=number("VFG_DISPATCH_ACTION_TIMEOUT_S", 20.0, float, 1),
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log(area: str, *parts: object) -> None:
    """One line on stdout: `<ISO UTC time> <area> <message>`. vfg brain start sends it to brain.log."""
    msg = " ".join(p if isinstance(p, str) else repr(p) for p in parts).replace("\n", " ")
    sys.stdout.write(f"{now_iso()} {area} {msg}\n")
    sys.stdout.flush()
