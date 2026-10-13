"""The implementer's static system prompt: its rules (prompts/implementer.md), then `vfg prefix` —
engine/WRITING.md (how a content class is written), engine/KIT.md when it is there (the kit's
helpers), and every library class (engine/library/) by id: its kind, its docstring's first line, and
its Config and Args as written (CONTRACT2, `vfg prefix`). The provider's cache reuses a prompt's
start only when it is the same bytes, so the runner reads this once and every implementer gets
exactly it; what changes during play goes in each brief instead (examples.py): the classes this
server's implementers made, what the engine runs, the state of the server, and the classes picked
for the job, whole, so a class made mid-game never changes this prefix."""
import hashlib

from .config import log
from .vfg import VfgRunner, why

# `vfg prefix` reads files on this PC only; it never waits on EC2.
PREFIX_TIMEOUT_S = 30.0


async def load(run: VfgRunner, timeout_s: float = PREFIX_TIMEOUT_S) -> str:
    """`vfg prefix`'s output exactly, or "" (logged) when vfg could not give it: the implementers
    then start with their rules alone, and can still read engine/."""
    r = await run("prefix", timeout=timeout_s)
    if r.code != 0 or not r.stdout.strip():
        log("prefix", "unavailable, implementers start without WRITING.md, KIT.md and the library's classes: %s"
            % (why(r) if r.code != 0 else "vfg prefix printed nothing"))
        return ""
    log("prefix", "%d characters, sha256 %s" % (len(r.stdout), hashlib.sha256(r.stdout.encode("utf-8")).hexdigest()[:12]))
    return r.stdout


def system_prompt(rules: str, prefix: str) -> str:
    """The implementer's system prompt: its rules, then the prefix."""
    if not prefix:
        return rules
    return rules.rstrip("\n") + "\n\n" + prefix
