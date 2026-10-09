"""Runs the `vfg` toolkit with an argv array (no shell), a timeout, its exit code and output."""
import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class VfgResult:
    code: int
    stdout: str
    stderr: str
    timed_out: bool


# (args, timeout in ms) -> the result.
VfgRunner = Callable[..., Awaitable[VfgResult]]


async def vfg(args: list[str], timeout_ms: float = 60_000) -> VfgResult:
    try:
        proc = await asyncio.create_subprocess_exec(
            "vfg", *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        return VfgResult(127, "", str(e), False)
    output = asyncio.ensure_future(proc.communicate())
    done, _ = await asyncio.wait({output}, timeout=timeout_ms / 1000)
    timed_out = not done
    if timed_out:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    stdout, stderr = await output
    return VfgResult(124 if timed_out else proc.returncode,
                     stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace"), timed_out)


def why(r: VfgResult) -> str:
    """First line of stderr (or stdout), for a log line."""
    line = (r.stderr.strip() or r.stdout.strip()).split("\n")[0]
    if r.timed_out:
        return "timed out"
    return "exit %d%s" % (r.code, ": " + line[:200] if line else "")
