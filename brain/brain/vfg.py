"""Runs the `vfg` toolkit: an argv list, no shell, a timeout, its exit code and output.

Everything that takes a vfg runner accepts any async callable of this shape, so the
tests pass fakes: `await vfg("orch", "reply", "T7", "done", "text", timeout=60)`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .config import REPO


@dataclass
class VfgResult:
    code: int
    stdout: str
    stderr: str


Vfg = Callable[..., Awaitable[VfgResult]]


def binary() -> str:
    """`vfg` on PATH, else the toolkit in this repo."""
    return shutil.which("vfg") or str(REPO / "toolkit" / "vfg")


async def vfg(*args: str, timeout: float = 30) -> VfgResult:
    """Exit 124 on a timeout and 127 when vfg cannot be started. A cancelled call kills
    vfg and everything it started (it runs in its own process group)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            binary(), *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as e:
        return VfgResult(127, "", f"cannot run vfg: {e}")
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        _kill(proc)
        await proc.wait()
        return VfgResult(124, "", f"timed out after {timeout:g} s")
    except asyncio.CancelledError:
        _kill(proc)
        raise
    return VfgResult(proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


def why(r: VfgResult) -> str:
    """The last line of stderr, else of stdout, else the exit code: for a log line or a failure."""
    for text in (r.stderr, r.stdout):
        lines = [x.strip() for x in text.strip().splitlines() if x.strip()]
        if lines:
            return lines[-1][:500]
    return f"exit {r.code}"
