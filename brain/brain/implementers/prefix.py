"""The implementer's static system prompt: its rules (prompts/implementer.md), then `vfg prefix` —
how a script and a preset are written, the kit in full, the library's presets and example scripts
by first line, and the library's actions one menu line each. The provider's cache reuses a prompt's
start only when it is the same bytes, so the runner reads this once and every implementer gets
exactly it; what changes during play goes in each brief instead (examples.py): what this server's
implementers made, its made actions' menu lines, the state of the server, what runs, and the
actions picked for the job in full (actions.py), so an action made mid-game never changes this
prefix."""
import hashlib

from .config import log
from .vfg import VfgRunner, why

# `vfg prefix` reads files on this PC only; it never waits on EC2.
PREFIX_TIMEOUT_S = 30.0


async def load(run: VfgRunner, timeout_s: float = PREFIX_TIMEOUT_S) -> str:
    """`vfg prefix`'s output exactly, or "" (logged) when vfg could not give it: the implementers
    then start with their rules alone, and can still read the library."""
    r = await run("prefix", timeout=timeout_s)
    if r.code != 0 or not r.stdout.strip():
        log("prefix", "unavailable, implementers start without the kit, the library's first lines and its actions: %s"
            % (why(r) if r.code != 0 else "vfg prefix printed nothing"))
        return ""
    log("prefix", "%d characters, sha256 %s" % (len(r.stdout), hashlib.sha256(r.stdout.encode("utf-8")).hexdigest()[:12]))
    return r.stdout


def system_prompt(rules: str, prefix: str) -> str:
    """The implementer's system prompt: its rules, then the prefix."""
    if not prefix:
        return rules
    return rules.rstrip("\n") + "\n\n" + prefix
