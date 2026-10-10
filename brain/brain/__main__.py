"""python -m brain: start, run, stop.

Start: take the lock; `vfg up`; `vfg watchers set join,leave,death,advancement,server`; Jev's
lookout (prompts/lookout.md) and menu (every action's line, set again when the actions change);
the actions, which each brain call reloads where a file changed; the command references in the
background (about 95 s the first time on a server version), tried again every 5 minutes until
they load; the context from `vfg context --json`, the last VFG_BRAIN_HISTORY_HOURS, what is
older than two minutes and unread marked processed (history is never acted on), a line from the
last two minutes still unread answered; the tasks a crash left open failed as interrupted; then
`vfg watch --after <seq>` followed for good, from the feed's highest seq when the context was read
(`vfg context --json` gives it last, with rows or none), so nothing older is ever read as new; and
the greeting, skipped when nobody is on. A read of the context that fails is tried again, never
followed from 0.
Stop (SIGTERM or SIGINT): read nothing new, let the calls in flight finish for up to 10 s, stop
the implementers and fail their tasks as stopped, save, exit 0.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import functools
import json
import logging
import os
import signal
import sys
from pathlib import Path

import httpx
import vfg_llm

from . import actions, config, running
from .act import Acts
from .action_planner import Planner
from .call import Call
from .config import Config, log
from .context import Context, parse
from .loop import Brain, jev_menu
from .respond import Respond
from .tasks import Tasks
from .vfg import Later, follow
from .vfg import vfg as run_vfg
from .world import World

LOOKOUT = Path(__file__).resolve().parent.parent / "prompts" / "lookout.md"
GREETING = "AI is on — say what you want into your mic, or type it in chat"
WATCHERS = "join,leave,death,advancement,server"
LOAD = object()      # serve(refs=LOAD): load them, as a real start does
REFS_RETRY_S = 300   # a failed load of the references is tried again this often, until it works
CONTEXT_RETRY_S = 5.0  # a failed read of the context is tried again this often, until it works
IMPLEMENTERS_GRACE_S = 2.0
DRAIN_S = 1.5        # for the rows and usage still being written, at the very end


class _LogLines(logging.Handler):
    """structured()'s own warnings (a repaired answer, a retried call) as log lines, `<time> llm
    <message>`, and vfg_actions' (an action left out), `<time> actions <message>`."""

    def __init__(self, area: str):
        super().__init__()
        self.area = area

    def emit(self, record: logging.LogRecord) -> None:
        log(self.area, record.getMessage())


class NoImplementers:
    """When brain/implementers cannot be loaded: an implementer part fails at once, said so."""

    def __init__(self, on_report, why: str):
        self.on_report, self.why = on_report, why

    async def start(self, task_id: str, brief: str) -> None:
        await self.on_report(task_id, "failed", f"Failed: no implementers here ({self.why}).")

    async def follow_up(self, task_id: str, text: str, session: str | None = None, brief: str = "") -> None:
        await self.start(task_id, text)

    def running(self) -> list[str]:
        return []

    async def close(self, grace_s: float) -> list[str]:
        return []


def implementers(on_report, on_session=None):
    try:
        from .implementers.runner import ImplementerConfig, Implementers
    except ImportError as e:
        log("start", f"the implementers cannot be loaded ({e}): implementer parts fail at once")
        return NoImplementers(on_report, str(e))
    return Implementers(ImplementerConfig.load(), on_report, on_session=on_session)


def asker(model: str, reasoning: str, timeout: float, providers: tuple[str, ...] = ()):
    """structured() with one model's settings; a call past `timeout` is an LlmError."""
    return functools.partial(vfg_llm.structured, model=model, reasoning=Config.effort_of(reasoning),
                             providers=list(providers) or None, timeout=timeout)


async def load_refs(cfg: Config, vfg, http):
    from . import refs as refs_module
    try:
        refs = await refs_module.load(cfg.brain_dir, vfg, http=http,
                                      log=lambda msg, *more: log("refs", str(msg).removeprefix("refs: "), *more))
    except Exception as e:  # noqa: BLE001 — run on without them rather than die
        log("refs", f"FAILED to load ({type(e).__name__}: {e}); a refused command is not retried until they load")
        return None
    log("refs", f"server {refs.version}: {len(refs.names())} commands")
    return refs


async def keep_loading_refs(cfg: Config, vfg, http, brain: Brain, planner: Planner, every: float) -> None:
    """In the background while lines are answered: load the references, and when that fails
    try again every `every` seconds until it works."""
    while True:
        refs = await load_refs(cfg, vfg, http)
        if refs is not None:
            brain.refs = planner.refs = refs
            return
        await asyncio.sleep(every)


def read_context(out: str) -> tuple[Context, int] | None:
    """`vfg context --json`'s answer: its rows as a Context, and the feed's highest seq, its last
    object; None when it gives neither (a vfg older than the brain), never a start from 0."""
    ctx, last = Context(), None
    for raw in out.splitlines():
        try:
            obj = json.loads(raw)
            if "last_seq" in obj:
                last = int(obj["last_seq"])
                continue
            row = parse(str(obj["line"]), seq=int(obj["seq"]), at=str(obj["at"]),
                        processed=obj.get("processed_at") is not None)
        except (ValueError, KeyError, TypeError):
            continue
        if row is not None:
            ctx.add(row)
    if last is None and not ctx.rows:
        return None
    return ctx, max(last or 0, ctx.last_seq)


async def load_context(vfg, hours: float, stop: asyncio.Event | None = None) -> tuple[Context, int] | None:
    """The feed since `hours` ago, from `vfg context --json`, and the feed's highest seq, which the
    watch follows from. A read that fails is tried again every CONTEXT_RETRY_S until it works:
    followed from 0, `vfg watch` would print the feed's last rows, however old, as new. None when
    `stop` is set first."""
    since = f"-{int(hours)}h" if float(hours).is_integer() else f"-{max(1, round(hours * 60))}m"
    while True:
        r = await vfg("context", "--since", since, "--json", timeout=120)
        got = read_context(r.stdout) if r.code == 0 else None
        if got is not None:
            return got
        why = ((r.stderr or r.stdout).strip()[-200:] if r.code != 0
               else "no rows and no last_seq: a vfg older than the brain?")
        log("start", f"vfg context failed ({why}); trying again in {CONTEXT_RETRY_S:g} s, nothing followed "
                     "until it works")
        if stop is None:
            await asyncio.sleep(CONTEXT_RETRY_S)
            continue
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), CONTEXT_RETRY_S)
        if stop.is_set():
            return None


def old_ids(cfg: Config, ctx: Context) -> str:
    """Every text where a task id may already be in use: the context, running.json and the old
    inboxes, so a new T<n> is never one of them."""
    texts = [f"{r.task} {r.text}" for r in ctx.rows]
    for path in (cfg.running_file, cfg.home / "orch" / "inbox.jsonl", cfg.home / "dispatch" / "inbox.jsonl"):
        with contextlib.suppress(OSError):
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(texts)


async def serve(cfg: Config, stop: asyncio.Event, *, vfg=run_vfg, watch=follow, catalog=None, refs=LOAD,
                ask=None, respond_ask=None, plan_ask=None, make_implementers=implementers,
                http: httpx.AsyncClient | None = None) -> int:
    cfg.brain_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    later = Later(vfg)
    up = await vfg("up", timeout=600)
    if up.code != 0:
        log("start", f"vfg up failed ({(up.stderr or up.stdout).strip()[-300:]}); carrying on, vfg watch reconnects")
    for args in (("watchers", "set", WATCHERS), ("lookout", "set", LOOKOUT.read_text(encoding="utf-8").strip())):
        later(*args)

    catalog = actions.load(cfg.repo, cfg.server) if catalog is None else catalog
    later("jev", "menu", "set", jev_menu(catalog))
    hidden = actions.hidden_commands()
    copies = running.Refresher(vfg, cfg.running_ttl_s)
    world = World(vfg, cfg.running_file, catalog, refresher=copies)
    tasks = Tasks(cfg.tasks_file)
    planner = Planner(plan_ask or asker(cfg.plan_model, cfg.plan_reasoning, cfg.timeout_s), cfg.plan_model,
                      None if refs is LOAD else refs, hidden=hidden, check=cfg.plan_check)
    call = Call(catalog, ask or asker(cfg.model, cfg.reasoning, cfg.timeout_s, cfg.providers), owner=cfg.owner,
                hidden=hidden, checkpoint=cfg.checkpoint)
    respond = Respond(catalog, respond_ask or asker(cfg.respond_model, cfg.respond_reasoning, cfg.timeout_s),
                      owner=cfg.owner, checkpoint=cfg.checkpoint)

    loaded = await load_context(vfg, cfg.history_hours, stop)
    if loaded is None:
        await later.drain(DRAIN_S)
        log("stop", "stopped before the context could be read")
        return 0
    ctx, start = loaded
    tasks.at_least(old_ids(cfg, ctx))

    async def on_report(tid: str, status: str, text: str) -> None:
        await brain.on_report(tid, status, text)

    impl = make_implementers(on_report, tasks.set_session)  # a session is kept as soon as the CLI names it
    acts = Acts(vfg=vfg, later=later, catalog=catalog, planner=planner, tasks=tasks, implementers=impl,
                world=world, action_timeout_s=cfg.action_timeout_s)
    brain = Brain(ctx=ctx, call=call, respond=respond, acts=acts, world=world, tasks=tasks, implementers=impl,
                  later=later, catalog=catalog, refs=None if refs is LOAD else refs, batch_s=cfg.batch_s,
                  start=start, model=cfg.model, respond_model=cfg.respond_model,
                  menu=lambda text: later("jev", "menu", "set", text))

    for t in tasks.open_ones():  # their implementers went with the last run
        record = f"{t['id']} failed: interrupted, the brain restarted while it ran"
        tasks.close(t["id"], "failed", record)
        acts.post("result", t["id"], record)
    history = brain.catch_up()

    background = [asyncio.create_task(brain.follow(watch), name="watch")]
    if refs is LOAD:
        background.append(asyncio.create_task(
            keep_loading_refs(cfg, vfg, http, brain, planner, REFS_RETRY_S), name="refs"))
    brain.timer.start()
    copies.start()
    greeting = await vfg("say", GREETING, timeout=30)
    if greeting.code != 0 and "nobody" not in (greeting.stderr + greeting.stdout).lower():
        log("start", f"the greeting failed: {(greeting.stderr or greeting.stdout).strip()[-200:]}")
    log("start", f"pid {os.getpid()}; {len(ctx.rows)} rows of context, following from #{brain.position()} "
                 f"({len(history)} history marked read); {len(catalog.names())} actions; brain {cfg.model} "
                 f"{cfg.reasoning}; respond {cfg.respond_model}; planner {cfg.plan_model}; batch after "
                 f"{cfg.batch_s:g} s; owner {cfg.owner or '(not set)'}; server {cfg.server}")

    await stop.wait()
    log("stop", "stopping: nothing new is read")
    for t in background:
        t.cancel()
    await asyncio.gather(*background, return_exceptions=True)
    await brain.stop(cfg.stop_grace_s, IMPLEMENTERS_GRACE_S)
    await copies.stop()
    await world.close()
    await later.drain(DRAIN_S)
    tasks.save()
    log("stop", "stopped")
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
            # A second signal (a Ctrl-C at a terminal reaches the whole group) landing while
            # asyncio.run closes the loop's self-pipe prints a traceback. The stop is under way,
            # so later ones are ignored.
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
                signal.signal(sig, signal.SIG_IGN)


def main() -> int:
    for name, area in ((vfg_llm.__name__, "llm"), ("vfg_actions", "actions")):
        logger = logging.getLogger(name)
        logger.addHandler(_LogLines(area))
        logger.propagate = False
    cfg = config.load()
    cfg.brain_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(cfg.lock_file, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("start", f"another brain holds {cfg.lock_file}; exiting")
        return 1
    try:
        return asyncio.run(_main(cfg))
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
