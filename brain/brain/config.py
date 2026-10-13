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
BRAIN = "google/gemini-3.8-flash"


@dataclass(frozen=True)
class Config:
    home: Path                      # VFG_HOME, default ~/.vfg
    brain_dir: Path                 # VFG_BRAIN_DIR, default ~/.vfg/brain: brain.log, lock, refs/
    repo: Path                      # VFG_REPO, default this repo
    server: str = SERVER            # VFG_SERVER: whose made/<server>/actions/ are on the menu too
    owner: str = ""                 # VFG_OWNER: the owner's Minecraft name, whose word wins
    # The brain's loop: every step on Gemini 3.8 Flash, reasoning low (Prototype_Brain_Loop). It is the
    # orchestrator's model of Prototype_Named_Tasks: it kept one thing to one task on the follow-up replay.
    model: str = BRAIN                         # VFG_BRAIN_MODEL
    reasoning: str = "low"                     # VFG_BRAIN_REASONING: off, or an effort
    providers: tuple[str, ...] = ()            # VFG_BRAIN_PROVIDERS, comma-separated, the brain's steps only;
                                               # (): OpenRouter's choice
    loop_steps: int = 10                       # VFG_LOOP_STEPS: a loop's steps at most; the last ends it
    loop_seconds: float = 120.0                # VFG_LOOP_SECONDS: a step that starts past it is the last
    # Respond, the brain's cheap mode, for conversational lines: DeepSeek V4 Flash with reasoning off,
    # through the providers that answer it fastest (at low reasoning it thinks ~900 tokens: 7.7 s a call).
    respond_model: str = CHEAP                      # VFG_RESPOND_MODEL
    respond_reasoning: str = "off"                  # VFG_RESPOND_REASONING
    respond_providers: tuple[str, ...] = FAST       # VFG_RESPOND_PROVIDERS
    respond_timeout_s: float = 20.0                 # VFG_RESPOND_TIMEOUT_S: one Respond call, retries included; a
                                                    # hung one holds the player's turn, then hands the line up
    # One turn per player (turns.py): the lines that may wait for it, and how long a turn waits for the
    # AI rows it wrote to come back on the watch before the next starts.
    turn_waiting: int = 3                           # VFG_TURN_WAITING
    turn_settle_s: float = 3.0                      # VFG_TURN_SETTLE_S; 0: no wait
    checkpoint: int = 5             # VFG_BRAIN_CHECKPOINT: a cache mark every this many minute-blocks
    history_hours: float = 6.0      # VFG_BRAIN_HISTORY_HOURS: how much of the feed the context starts with
    # VFG_BRAIN_TIMEOUT_S: one brain step's structured() call, its resend and retries included.
    # A step on Gemini 3.8 Flash low thinks first (a median 731 tokens out on the first pilot), a median 9.7 s
    # and a hand-off's step up to 16.5 s; at 20 s two of them timed out.
    timeout_s: float = 45.0
    running_ttl_s: float = 30.0     # VFG_RUNNING_TTL_S: how often running.json is fetched again; 0: never
    action_timeout_s: float = 20.0  # VFG_DISPATCH_ACTION_TIMEOUT_S: a once action's, unless its class says
    stop_grace_s: float = 10.0      # how long in-flight calls and actions may finish on SIGTERM

    @staticmethod
    def effort_of(reasoning: str) -> bool | str:
        """What structured() takes: False for off, else the effort."""
        return False if reasoning in ("", "off") else reasoning

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
        model=get("VFG_BRAIN_MODEL", BRAIN),
        reasoning=get("VFG_BRAIN_REASONING", "low"),
        providers=names("VFG_BRAIN_PROVIDERS"),
        loop_steps=number("VFG_LOOP_STEPS", 10, int, 1),
        loop_seconds=number("VFG_LOOP_SECONDS", 120.0, float, 1),
        respond_model=get("VFG_RESPOND_MODEL", CHEAP),
        respond_reasoning=get("VFG_RESPOND_REASONING", "off"),
        respond_providers=names("VFG_RESPOND_PROVIDERS", FAST),
        respond_timeout_s=number("VFG_RESPOND_TIMEOUT_S", 20.0, float, 1),
        turn_waiting=number("VFG_TURN_WAITING", 3, int, 0),
        turn_settle_s=number("VFG_TURN_SETTLE_S", 3.0, float, 0),
        checkpoint=number("VFG_BRAIN_CHECKPOINT", 5, int, 1),
        history_hours=number("VFG_BRAIN_HISTORY_HOURS", 6.0, float, 0),
        timeout_s=number("VFG_BRAIN_TIMEOUT_S", 45.0, float, 1),
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
