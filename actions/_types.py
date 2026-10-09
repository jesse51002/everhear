"""The Action base class and the option types every action shares.

Imported on the owner's PC by the dispatcher (Python >= 3.12, pydantic 2). Every
value an action carries is checked here, before anything is sent to EC2; the
handlers there run under Python 3.9 without pydantic and get plain dicts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

REPO = Path(__file__).resolve().parent.parent

# Server commands that are the owner's to run. scripts/_lib/mc.py (REFUSED),
# toolkit/remote/action.py and the orchestrator's guard refuse the same words;
# a test keeps this list equal to mc.py's.
REFUSED = frozenset(["stop", "op", "deop", "ban", "ban-ip", "pardon", "pardon-ip",
                     "whitelist", "kick", "save-off"])

# Commands that place or remove blocks: building is the orchestrator's.
BLOCK_COMMANDS = frozenset(["setblock", "fill", "clone", "place", "fillbiome"])

# What one RCON packet carries, in bytes of UTF-8.
RCON_MAX = 1400

PLAYER_PATTERN = r"^[A-Za-z0-9_]{1,16}$"
PLAYER_OR_ALL_PATTERN = r"^([A-Za-z0-9_]{1,16}|@a)$"
ID_PATTERN = r"^#?(minecraft:)?[a-z0-9_]+$"
PLACE_PATTERN = r"^[a-z0-9_]{1,32}$"


def _lower(v: object) -> object:
    return v.strip().lower() if isinstance(v, str) else v


def _drop_namespace(v: str) -> str:
    """'#minecraft:village' -> '#village', 'minecraft:zombie' -> 'zombie'."""
    tag = v.startswith("#")
    v = v.lstrip("#")
    if v.startswith("minecraft:"):
        v = v[len("minecraft:"):]
    return ("#" if tag else "") + v


def _no_tag(v: str) -> str:
    if v.startswith("#"):
        raise ValueError("one mob, not a #tag")
    return v


def _place_name(v: object) -> object:
    """'My Castle' -> 'my_castle', so a name said aloud still fits the pattern."""
    if isinstance(v, str):
        return re.sub(r"[\s-]+", "_", v.strip().lower())
    return v


# The pattern comes first in each Annotated, so it stays in the JSON schema that
# constrains the model's structured output; a BeforeValidator placed after it wraps
# the checked string and still runs first. (A BeforeValidator ahead of the pattern
# turns the pattern into a function check that never reaches the schema.)
Player = Annotated[str, StringConstraints(strip_whitespace=True, pattern=PLAYER_PATTERN)]
PlayerOrAll = Annotated[str, StringConstraints(strip_whitespace=True, pattern=PLAYER_OR_ALL_PATTERN)]
Id = Annotated[str, Field(pattern=ID_PATTERN), BeforeValidator(_lower), AfterValidator(_drop_namespace)]
MobId = Annotated[Id, AfterValidator(_no_tag)]
BiomeId = Id
StructureId = Id
PlaceName = Annotated[str, Field(pattern=PLACE_PATTERN), BeforeValidator(_place_name)]


def script_names(scripts: Path = REPO / "scripts") -> tuple[str, ...]:
    """The folders in scripts/ that have a main.py, skipping every one whose name starts
    with _ (_lib, _test, _presets...) or a dot."""
    if not scripts.is_dir():
        return ()
    return tuple(sorted(p.name for p in scripts.iterdir()
                        if p.is_dir() and not p.name.startswith(("_", "."))
                        and (p / "main.py").is_file()))


SCRIPT_NAMES = script_names()
if SCRIPT_NAMES:
    ScriptName = Literal[SCRIPT_NAMES]  # type: ignore[valid-type]
else:  # no scripts at all: nothing validates
    ScriptName = Annotated[str, Field(pattern=r"^(?!)$")]  # type: ignore[misc]


def command_problem(cmd: str) -> str | None:
    """Why a command may not run, in words for the action planner, or None.

    Looks at its first word and at the word after every `run`, so
    `execute as @a at @s run fill ...` and `return run setblock ...` are caught."""
    if any(c in cmd for c in "\r\n\x00"):
        return "a command is one line"
    n = len(cmd.encode("utf-8"))
    if n > RCON_MAX:
        return "the command is %d bytes; RCON takes at most %d" % (n, RCON_MAX)
    words = cmd.split()
    checks = words[:1] + [words[i + 1] for i, w in enumerate(words[:-1]) if w.lower() == "run"]
    for w in checks:
        w = w.lstrip("/").lower()
        if w.startswith("minecraft:"):
            w = w[len("minecraft:"):]
        if w in BLOCK_COMMANDS:
            return "%s places or removes blocks, and building is the orchestrator's: hand it on" % w
        if w in REFUSED:
            return "%s is the server owner's to do, never an action's" % w
    return None


def clean_command(cmd: str) -> str:
    """Strip spaces and one leading slash; raise ValueError when it may not run."""
    if not isinstance(cmd, str):
        raise ValueError("a command is text")
    cmd = cmd.strip()
    if cmd.startswith("/"):
        cmd = cmd[1:].lstrip()
    if not cmd:
        raise ValueError("empty command")
    why = command_problem(cmd)
    if why:
        raise ValueError("%s: %s" % (why, cmd[:80]))
    return cmd


class Action(BaseModel):
    """One thing the dispatcher can do on the Minecraft server."""

    model_config = ConfigDict(extra="forbid")

    # Seconds the executor allows the action; None is VFG_DISPATCH_ACTION_TIMEOUT_S.
    timeout_s: ClassVar[int | None] = None

    def vfg_argv(self, task: str) -> list[str]:
        """The vfg argv that runs this action for task `task` (T7)."""
        args = self.model_dump(mode="json", exclude={"action"})
        argv = ["action", "run", self.action, "--args",  # type: ignore[attr-defined]
                json.dumps(args, ensure_ascii=False, separators=(",", ":")), "--task", task]
        if self.timeout_s:
            argv += ["--timeout", str(self.timeout_s)]
        return argv
