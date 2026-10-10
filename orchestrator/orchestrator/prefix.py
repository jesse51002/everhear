"""The implementer's static system prompt: its rules (prompts/implementer.md), then `vfg prefix` —
how a script and a preset are written, the kit in full, and the first line and VALUES of every
library preset and example script. The provider's cache reuses a prompt's start only when it is
the same bytes, so this is read once each time a session opens (a start, a restart, a rollover)
and every implementer of that session gets exactly it; what changes during play (what this
server's implementers made, the state of the server) goes in each brief instead (examples.py)."""
import hashlib

from . import vfg as vfg_module
from .config import log
from .vfg import why

# `vfg prefix` reads files on this PC only; it never waits on EC2.
PREFIX_TIMEOUT_MS = 30_000


async def load(timeout_ms: float = PREFIX_TIMEOUT_MS) -> str:
    """`vfg prefix`'s output exactly, or "" (logged) when vfg could not give it: the implementers
    then start with their rules alone, and can still read the library."""
    r = await vfg_module.vfg(["prefix"], timeout_ms)
    if r.code != 0 or not r.stdout.strip():
        log("prefix", "unavailable, implementers start without the kit and the library's first lines: %s"
            % (why(r) if r.code != 0 else "vfg prefix printed nothing"))
        return ""
    log("prefix", "%d characters, sha256 %s" % (len(r.stdout), hashlib.sha256(r.stdout.encode("utf-8")).hexdigest()[:12]))
    return r.stdout


def system_prompt(rules: str, prefix: str) -> str:
    """The implementer's system prompt: its rules, then the prefix."""
    if not prefix:
        return rules
    return rules.rstrip("\n") + "\n\n" + prefix
