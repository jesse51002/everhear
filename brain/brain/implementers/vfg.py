"""Runs the `vfg` toolkit: an argv list, no shell, a timeout in seconds, its exit code and output.

The shape is the brain's own runner's (brain/vfg.py) with stdin added, which vfg route reads the
brief from: `await vfg("route", "--task", "galick_gun_styrk", timeout=3.75, stdin=brief)`.
Everything here that runs vfg takes a runner of this shape, so the tests pass a fake. A read of
the engine, `vfg engine <read> --json`, prints the engine's reply as it came (CONTRACT2), which
engine_read gives as a dict."""
import asyncio
import contextlib
import json
import os
import signal
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class VfgResult:
    code: int
    stdout: str
    stderr: str


# (*args, timeout=seconds, stdin=None) -> the result.
VfgRunner = Callable[..., Awaitable[VfgResult]]


async def vfg(*args: str, timeout: float = 60, stdin: str | None = None) -> VfgResult:
    """Exit 124 on a timeout and 127 when vfg cannot be started. A cancelled call kills vfg and
    everything it started (it runs in its own process group)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "vfg", *args,
            stdin=asyncio.subprocess.DEVNULL if stdin is None else asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as e:
        return VfgResult(127, "", str(e))
    try:
        out, err = await asyncio.wait_for(proc.communicate(None if stdin is None else stdin.encode("utf-8")), timeout)
    except TimeoutError:
        _kill(proc)
        await proc.wait()
        return VfgResult(124, "", "timed out after %g s" % timeout)
    except asyncio.CancelledError:
        _kill(proc)
        raise
    return VfgResult(proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))


def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


def why(r: VfgResult) -> str:
    """First line of stderr (or stdout), for a log line."""
    if r.code == 124:
        return "timed out"
    line = (r.stderr.strip() or r.stdout.strip()).split("\n")[0]
    return "exit %d%s" % (r.code, ": " + line[:200] if line else "")


async def engine_read(run: VfgRunner, *args: str, timeout: float) -> tuple[dict | None, str | None]:
    """`vfg engine <args> --json`: (the engine's reply, None) when it answered ok; else (None, why
    not), the engine's own words when it said any (exit 1: it refused; 6: it is not running)."""
    r = await run("engine", *args, "--json", timeout=timeout)
    try:
        data = json.loads(r.stdout) if r.stdout.strip() else None
    except ValueError:
        data = None
    if r.code == 0 and isinstance(data, dict) and data.get("ok") is not False:
        return data, None
    if isinstance(data, dict) and data.get("error"):
        return None, "exit %d: %s" % (r.code, str(data["error"]).split("\n")[0][:200])
    if r.code == 0:
        out = r.stdout.strip().split("\n")[0][:120]
        return None, "vfg printed no reply from the engine%s" % (": " + out if out else "")
    return None, why(r)
