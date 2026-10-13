"""The harnesses an implementer runs in, by VFG_IMPL_HARNESS: one line each, imported only when
chosen, so the Claude Agent SDK is loaded only for Claude Code. The interface is base.py's."""
import importlib

from ..config import ImplementerConfig
from .base import Harness

# name -> "module:class"; a new adapter is one line here.
ADAPTERS = {
    "claude-code": "brain.implementers.harness.claude_code:ClaudeCode",
    "opencode": "brain.implementers.harness.opencode:OpenCode",
}


def make(cfg: ImplementerConfig) -> Harness:
    """The adapter VFG_IMPL_HARNESS names, made for these settings."""
    where = ADAPTERS.get(cfg.harness)
    if where is None:
        raise ValueError("VFG_IMPL_HARNESS=%s is no harness; these are: %s" % (cfg.harness, ", ".join(ADAPTERS)))
    module, cls = where.split(":")
    return getattr(importlib.import_module(module), cls)(cfg)
