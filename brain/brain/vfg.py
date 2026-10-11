"""Runs the `vfg` toolkit: an argv list, no shell, a timeout, its exit code and output.

Everything that takes a vfg runner accepts any async callable of this shape, so the
tests pass fakes: `await vfg("say", "on it", "--task", "galick_gun_styrk", timeout=30)`. Also here: Later,
for the calls nobody waits for, and follow(), the lines of `vfg watch --after <seq>`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from .config import REPO, log


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


class Later:
    """The vfg calls nobody waits for (a feed row, a usage row, a context mark), each its own
    task, a failure logged: `later("usage", "add", …)`."""

    def __init__(self, vfg: Vfg):
        self.vfg = vfg
        self.tasks: set[asyncio.Task] = set()

    def __call__(self, *args: str, timeout: float = 30) -> asyncio.Task:
        t = asyncio.create_task(self._run(args, timeout), name=f"vfg {' '.join(args[:2])}")
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        return t

    async def _run(self, args: tuple[str, ...], timeout: float) -> None:
        try:
            r = await self.vfg(*args, timeout=timeout)
        except Exception as e:  # noqa: BLE001 — a runner that broke is a failed call, logged
            log("vfg", f"vfg {' '.join(args[:3])} broke: {type(e).__name__}: {e}")
            return
        if r.code != 0:
            log("vfg", f"vfg {' '.join(args[:3])} failed: {why(r)}")

    async def drain(self, timeout: float) -> None:
        """Wait up to `timeout` for what is still running; cancel the rest."""
        if self.tasks:
            _, pending = await asyncio.wait(list(self.tasks), timeout=timeout)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


async def follow(after: int) -> AsyncIterator[str]:
    """`vfg watch --after <after>`'s lines as they come, until vfg exits (it reconnects over SSH
    by itself, and never exits on its own)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            binary(), "watch", "--after", str(after),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True, limit=1 << 20)
    except OSError as e:
        log("watch", f"cannot run vfg watch: {e}")
        return
    try:
        async for raw in proc.stdout:
            yield raw.decode("utf-8", "replace")
    finally:
        if proc.returncode is None:
            _kill(proc)
        await proc.wait()
