"""Runs the `vfg` toolkit: an argv list, no shell, a timeout, its exit code and output.

Everything that takes a vfg runner accepts any async callable of this shape, so the
tests pass fakes: `await vfg("say", "on it", "--task", "T7", timeout=30)`. Also here: Later,
for the calls nobody waits for (the usage queue's `vfg usage flush` among them, never two at
once), and follow(), the lines of `vfg watch --after <seq>`.
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
    task, a failure logged: `later("usage", "add", …)`. The usage queue after a model call goes
    by flush_usage(), coalesced."""

    def __init__(self, vfg: Vfg):
        self.vfg = vfg
        self.tasks: set[asyncio.Task] = set()
        self.flushing: asyncio.Task | None = None  # the `vfg usage flush` running, and the one after it
        self.flush_again = False                   # asked for since the one running began: one more is due

    def __call__(self, *args: str, timeout: float = 30) -> asyncio.Task:
        return self._keep(self._run(args, timeout), f"vfg {' '.join(args[:2])}")

    def flush_usage(self) -> asyncio.Task:
        """`vfg usage flush` after a model call: the rows structured() queued, sent to the box. Never
        two at once, so a box that is down never has SSH attempts piling up at it: asked while one
        runs, it runs once more when that one ends, for every row queued meanwhile, however many
        asked. A row is never lost: the queue holds it until a flush sends it."""
        self.flush_again = True
        if self.flushing is None or self.flushing.done():
            self.flushing = self._keep(self._flushes(), "vfg usage flush")
        return self.flushing

    async def _flushes(self) -> None:
        while self.flush_again:
            self.flush_again = False
            await self._run(("usage", "flush"), 30)

    def _keep(self, coro, name: str) -> asyncio.Task:
        t = asyncio.create_task(coro, name=name)
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
