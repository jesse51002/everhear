"""python -m dispatcher: the lock, the signals, and one asyncio task per new task.

Start: take the lock; load the actions (library and VFG_SERVER's made ones), which each task
reloads where a file changed; hand on whatever a crash left mid-run and a batch left waiting,
and send the answers left unsent; follow the inbox at once, failing as expired any task that
waited more than VFG_ORCH_STALE_MINUTES; fetch the running copies again every
VFG_RUNNING_TTL_S in the background; and load the command references in the background
(about 95 s the first time on a server version), trying again every 5 minutes if that fails.
Until they are in, picks of an action with a Command field are handed on and every other
action works.
Stop (SIGTERM or SIGINT): read no new task, let a running action finish for up to 10 s,
hand everything unfinished, save, exit 0.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import functools
import logging
import os
import signal
import sys

import httpx
import vfg_llm

from . import actions, config
from .batch import Batch
from .running import Refresher
from .config import Config, log
from .inbox import Inbox, State
from .reply import Reply
from .task import Dispatcher
from .vfg import vfg as run_vfg

POLL_S = 0.1          # how often the inbox is looked at
# A stop lets running actions finish for up to 10 s, then tries these for a little longer.
# Both are kept in state.json, so whatever is left is handed or sent at the next start; the
# total stays under the 15 s vfg orch stop waits before SIGKILL.
HAND_ON_STOP_S = 3    # `vfg orch hand`
SEND_ON_STOP_S = 1.5  # `vfg orch reply`
LOAD = object()       # serve(refs=LOAD): load them, as a real start does
REFS_RETRY_S = 300    # a failed load of the references is tried again this often, until it works


class _LogLines(logging.Handler):
    """structured()'s own warnings (a repaired answer, a retried call) as log lines, `<time> llm
    <message>`, and vfg_actions' (an action left out), `<time> actions <message>`."""

    def __init__(self, area: str):
        super().__init__()
        self.area = area

    def emit(self, record: logging.LogRecord) -> None:
        log(self.area, record.getMessage())


async def ask(prompt, output, *, system=None, model, providers=None, timeout: float = 10,
              reasoning: bool | str = False):
    """structured(), reasoning off unless VFG_DISPATCH_REASONING names an effort: speed first;
    `providers` is the OpenRouter order. A call
    past `timeout` is an LlmError, which the selector and the planner treat as a hand-on."""
    return await vfg_llm.structured(prompt, output, system=system, model=model, reasoning=reasoning,
                                    providers=providers, timeout=timeout)


def asker(cfg: Config):
    """`ask` with VFG_DISPATCH_PROVIDERS and VFG_DISPATCH_MODEL_TIMEOUT_S: what the selector
    and the planner call."""
    return functools.partial(ask, providers=list(cfg.providers) or None, timeout=cfg.model_timeout_s,
                             reasoning=cfg.effort)


async def load_refs(cfg: Config, vfg, http):
    try:
        from . import refs as refs_module
    except ImportError as e:
        log("refs", f"refs.py cannot be imported ({e}); every command pick is handed on")
        return None
    try:
        refs = await refs_module.load(cfg.dispatch_dir, vfg, http=http,
                                      log=lambda msg, *more: log("refs", str(msg).removeprefix("refs: "), *more))
    except Exception as e:  # noqa: BLE001 — run on without them rather than die
        log("refs", f"FAILED to load ({type(e).__name__}: {e}); command picks are handed on until they load")
        return None
    log("refs", f"server {refs.version}: {len(refs.names())} commands")
    return refs


async def keep_loading_refs(cfg: Config, vfg, http, d: Dispatcher, every: float) -> None:
    """In the background while tasks run: load the references, and when that fails try again
    every `every` seconds until it works. Until then `command` picks are handed on; each task
    takes the references there are when it starts."""
    attempt = 1
    d.planner.refs = await load_refs(cfg, vfg, http)
    if d.planner.refs is None:
        log("refs", f"trying again every {every:g} s")
    while d.planner.refs is None:
        await asyncio.sleep(every)
        attempt += 1
        log("refs", f"attempt {attempt}: loading the references again")
        d.planner.refs = await load_refs(cfg, vfg, http)
    log("refs", f"loaded at attempt {attempt}: command picks run again")


async def serve(cfg: Config, stop: asyncio.Event, *, vfg=run_vfg, ask=None, catalog=None, refs=LOAD,
                http: httpx.AsyncClient | None = None, poll_s: float = POLL_S,
                refs_retry_s: float = REFS_RETRY_S) -> int:
    cfg.dispatch_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = State.load(cfg.state_file, cfg.inbox)
    reply = Reply(state, vfg)
    batch = Batch(state, vfg, cfg.window_s)

    catalog = actions.load(cfg.repo, cfg.server) if catalog is None else catalog
    ask = asker(cfg) if ask is None else ask
    d = Dispatcher(cfg, catalog=catalog, refs=None if refs is LOAD else refs, vfg=vfg, ask=ask, state=state,
                   reply=reply, batch=batch, hidden=actions.hidden_commands())
    batch.on_refused = d.refused
    copies = Refresher(vfg, cfg.running_ttl_s)

    # What the last run left needs no references: it goes first, so a slow first fetch of
    # the references never holds it back.
    if state.tasks:
        log("start", f"{len(state.tasks)} task(s) left mid-run: handing them on with what was done")
        d.recover()
    reply.start()
    batch.start()
    copies.start()

    # The first fetch for a server version takes about 95 s: tasks are read meanwhile, and
    # every action but `command` works.
    loading = None
    if refs is LOAD:
        loading = asyncio.create_task(keep_loading_refs(cfg, vfg, http, d, refs_retry_s), name="refs")

    inbox = Inbox(cfg.inbox, state, stale_minutes=cfg.stale_minutes, on_expired=d.expired)
    log("start", f"pid {os.getpid()}; {cfg.inbox} from byte {state.offset}; model {cfg.model}; "
                 f"window {cfg.window_s:g} s; retries {cfg.retries}; action timeout {cfg.action_timeout_s:g} s; "
                 f"stale after {cfg.stale_minutes} min; server {cfg.server}; running copies every "
                 f"{cfg.running_ttl_s:g} s")
    while not stop.is_set():
        try:
            for line in inbox.poll():
                d.start(line["id"])
        except OSError as e:
            log("inbox", f"cannot read {cfg.inbox}: {e}")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), poll_s)

    log("stop", "stopping: no new task is read")
    if loading and not loading.done():
        loading.cancel()
    await copies.stop()
    await d.stop(cfg.stop_grace_s)
    handed = await batch.flush(HAND_ON_STOP_S)
    sent = await reply.drain(SEND_ON_STOP_S)
    await reply.stop()
    state.save()
    left = []
    if not handed:
        left.append(f"{len(state.batch)} line(s) to hand")
    if not sent:
        left.append(f"{len(state.outbox)} answer(s) to send")
    log("stop", "stopped" + (f"; left for the next start: {', '.join(left)}" if left else ""))
    return 0


async def _main(cfg: Config) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    async with httpx.AsyncClient(timeout=30) as http:
        vfg_llm.client = http
        try:
            return await serve(cfg, stop, http=http)
        finally:
            vfg_llm.client = None
            # vfg orch stop signals the whole group, and uv run forwards its own SIGTERM too: a
            # second one landing while asyncio.run closes the loop's self-pipe prints a traceback.
            # The stop is under way, so later ones are ignored.
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
                signal.signal(sig, signal.SIG_IGN)


def main() -> int:
    for name, area in ((vfg_llm.__name__, "llm"), ("vfg_actions", "actions")):
        logger = logging.getLogger(name)
        logger.addHandler(_LogLines(area))
        logger.propagate = False
    cfg = config.load()
    cfg.dispatch_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(cfg.lock_file, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("start", f"another dispatcher holds {cfg.lock_file}; exiting")
        return 1
    try:
        return asyncio.run(_main(cfg))
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
