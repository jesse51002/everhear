"""Settings and the log line.

Each setting comes from the environment, then ~/.vfg/config (KEY=value, as the
toolkit reads it), then its default. The OpenRouter key is not one of them: only
vfg_llm reads it, and nothing here ever logs it.

Every agent runs DeepSeek V4.1 Flash (Prototype_Conversation_And_Router): the conversation agent
and the router with no reasoning, the brain at low, which the loop replay chose. Picking
who does the work was the main reason the brain ran on Gemini; the router does that now. Every
provider list is an order with fallbacks allowed: a loop's steps stay on one host for the cache,
so Together, which kept 99% of a like-sized prompt warm after 13 minutes, comes first.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from vfg_llm import _config as read_config_file  # the one KEY=value parser, shared with structured()

# The repo this brain/ is in: actions/, engine/ and made/ live beside it.
REPO = Path(__file__).resolve().parents[2]
SERVER = "main"
# DeepSeek V4.1 Flash, and the hosts that serve it, in the order they are tried: every agent's.
V41 = "deepseek/deepseek-v4.1-flash"
V41_HOSTS = ("together", "coreweave", "parasail")
# The cheap model of before, and the providers that answer it fastest, first: the incantation alias
# call's, and the gate bench's (integration/replay/gate_bench.py imports both). Respond no longer
# defaults to them.
CHEAP = "~deepseek/deepseek-v4-flash-latest"
FAST = ("parasail", "wafer", "phala", "together", "venice")
BRAIN = V41
# A brain step's timeout when VFG_BRAIN_TIMEOUT_S is not set: a step that does not think has only its
# answer to write, and one that thinks a little gets twice as long (the spec, _The brain_).
BRAIN_TIMEOUT_OFF_S, BRAIN_TIMEOUT_THINKING_S = 60.0, 120.0
# The router's, likewise: one quick call with no reasoning; one that thinks gets longer.
ROUTER_TIMEOUT_OFF_S, ROUTER_TIMEOUT_THINKING_S = 30.0, 60.0
# Keys no longer read: set in the environment or the file, each is logged once as the settings load.
GONE = {"VFG_LOOP_SECONDS": "a loop has no time limit now: its steps and the step timeout bound it"}


def thinks(reasoning: str) -> bool:
    """Whether a reasoning setting asks the model to think: anything but off (or nothing)."""
    return reasoning.strip().lower() not in ("", "off")


@dataclass(frozen=True)
class Config:
    home: Path                      # VFG_HOME, default ~/.vfg
    brain_dir: Path                 # VFG_BRAIN_DIR, default ~/.vfg/brain: brain.log, lock, refs/
    repo: Path                      # VFG_REPO, default this repo
    server: str = SERVER            # VFG_SERVER: the server whose made/<server>/content/ its implementers write
    owner: str = ""                 # VFG_OWNER: the owner's Minecraft name, whose word wins
    # The brain's loops: every step on DeepSeek V4.1 Flash, reasoning low. The loop replay of the 2026-10-11
    # session chose low over off on every measure: $0.201 against $0.235, 16 answers sent back against 62, 2 lines
    # failed against 10, and 42 asks done as Gemini did them against 38
    # (integration/replay/loop/report-deepseek-v4.1-flash-{off,low}.md). The router picks the task now, the job
    # the brain ran on Gemini for.
    model: str = BRAIN                         # VFG_BRAIN_MODEL
    reasoning: str = "low"                     # VFG_BRAIN_REASONING: off, or an effort
    providers: tuple[str, ...] = V41_HOSTS     # VFG_BRAIN_PROVIDERS, comma-separated, the brain's steps only;
                                               # (): OpenRouter's choice
    # A loop's budget (Prototype_Conversation_And_Router, _A line added to a loop_): 10 steps for its first
    # line and 10 more for each line added, never past the cap; at the cap it is deleted after its ending.
    loop_steps: int = 10                       # VFG_LOOP_STEPS: the grant, a line's steps
    loop_cap: int = 20                         # VFG_LOOP_CAP: no loop goes past it
    loop_take_up_s: float = 120.0              # VFG_LOOP_TAKE_UP_S: an ended loop takes a line only this long
                                               # after it was created
    # The conversation agent (respond.py), which answers every line to the AI at once: no reasoning.
    respond_model: str = V41                        # VFG_RESPOND_MODEL
    respond_reasoning: str = "off"                  # VFG_RESPOND_REASONING
    respond_providers: tuple[str, ...] = V41_HOSTS  # VFG_RESPOND_PROVIDERS
    respond_timeout_s: float = 20.0                 # VFG_RESPOND_TIMEOUT_S: one call, retries included; a hung
                                                    # one holds the player's turn, then hands the line off
    # The router (router.py), which picks who does the work: no reasoning to start, so its answer is quick
    # and the conversation agent's line is held for it (hold, below); low if its replay falls short.
    router_model: str = V41                         # VFG_ROUTER_MODEL
    router_reasoning: str = "off"                   # VFG_ROUTER_REASONING
    router_providers: tuple[str, ...] = V41_HOSTS   # VFG_ROUTER_PROVIDERS
    router_timeout_s: float = ROUTER_TIMEOUT_OFF_S  # VFG_ROUTER_TIMEOUT_S: 30, or 60 when it reasons
    # One turn per player (turns.py), in front of the conversation agent and the router: the lines that may
    # wait for it, and how long a turn waits for the AI rows it wrote to come back on the watch.
    turn_waiting: int = 3                           # VFG_TURN_WAITING
    turn_settle_s: float = 3.0                      # VFG_TURN_SETTLE_S; 0: no wait
    checkpoint: int = 5             # VFG_BRAIN_CHECKPOINT: a cache mark every this many minute-blocks
    history_hours: float = 6.0      # VFG_BRAIN_HISTORY_HOURS: how much of the feed the context starts with
    # VFG_BRAIN_TIMEOUT_S: one brain step's structured() call, its resend and retries included: 120 s when it
    # thinks (low, the default), 60 s with no reasoning. On the 2026-10-12 night 5 of 33 Gemini steps timed out
    # at 45 s.
    timeout_s: float = BRAIN_TIMEOUT_THINKING_S
    action_timeout_s: float = 20.0  # VFG_DISPATCH_ACTION_TIMEOUT_S: an engine op's, unless its action says
    stop_grace_s: float = 10.0      # how long in-flight calls and actions may finish on SIGTERM

    @staticmethod
    def effort_of(reasoning: str) -> bool | str:
        """What structured() takes: False for off, else the effort."""
        return reasoning if thinks(reasoning) else False

    @property
    def hold(self) -> bool:
        """Whether the conversation agent's line to a hand-off is held until the router has
        answered, so the player gets one line per ask: only while the router does not reason,
        and so answers in a second or two. A router that thinks holds nothing: the agent's line
        goes out at once, and the router's own after it (the spec, _One line per ask_)."""
        return not thinks(self.router_reasoning)

    @property
    def lock_file(self) -> Path:
        return self.brain_dir / "lock"



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

    for key, why in GONE.items():
        if get(key):
            log("config", f"{key}={get(key)!r} is no longer read: {why}")

    reasoning = get("VFG_BRAIN_REASONING", "low")
    router_reasoning = get("VFG_ROUTER_REASONING", "off")
    return Config(
        home=home,
        brain_dir=path("VFG_BRAIN_DIR", home / "brain"),
        repo=path("VFG_REPO", REPO),
        server=get("VFG_SERVER", SERVER),
        owner=get("VFG_OWNER"),
        model=get("VFG_BRAIN_MODEL", BRAIN),
        reasoning=reasoning,
        providers=names("VFG_BRAIN_PROVIDERS", V41_HOSTS),
        loop_steps=number("VFG_LOOP_STEPS", 10, int, 1),
        loop_cap=number("VFG_LOOP_CAP", 20, int, 1),
        loop_take_up_s=number("VFG_LOOP_TAKE_UP_S", 120.0, float, 0),
        respond_model=get("VFG_RESPOND_MODEL", V41),
        respond_reasoning=get("VFG_RESPOND_REASONING", "off"),
        respond_providers=names("VFG_RESPOND_PROVIDERS", V41_HOSTS),
        respond_timeout_s=number("VFG_RESPOND_TIMEOUT_S", 20.0, float, 1),
        router_model=get("VFG_ROUTER_MODEL", V41),
        router_reasoning=router_reasoning,
        router_providers=names("VFG_ROUTER_PROVIDERS", V41_HOSTS),
        router_timeout_s=number("VFG_ROUTER_TIMEOUT_S", ROUTER_TIMEOUT_THINKING_S if thinks(router_reasoning)
                                else ROUTER_TIMEOUT_OFF_S, float, 1),
        turn_waiting=number("VFG_TURN_WAITING", 3, int, 0),
        turn_settle_s=number("VFG_TURN_SETTLE_S", 3.0, float, 0),
        checkpoint=number("VFG_BRAIN_CHECKPOINT", 5, int, 1),
        history_hours=number("VFG_BRAIN_HISTORY_HOURS", 6.0, float, 0),
        timeout_s=number("VFG_BRAIN_TIMEOUT_S", BRAIN_TIMEOUT_THINKING_S if thinks(reasoning)
                         else BRAIN_TIMEOUT_OFF_S, float, 1),
        action_timeout_s=number("VFG_DISPATCH_ACTION_TIMEOUT_S", 20.0, float, 1),
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log(area: str, *parts: object) -> None:
    """One line on stdout: `<ISO UTC time> <area> <message>`. vfg brain start sends it to brain.log."""
    msg = " ".join(p if isinstance(p, str) else repr(p) for p in parts).replace("\n", " ")
    sys.stdout.write(f"{now_iso()} {area} {msg}\n")
    sys.stdout.flush()
