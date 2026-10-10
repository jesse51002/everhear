"""The runner: config, single-instance lock, signals, and the loops."""
import asyncio
import os
import shutil
import signal
import sys
import traceback

from . import examples
from .config import Config, ignored_mode, load_config, log, now_ms
from .inbox import InboxFollower
from .reply import Outbox
from .session import MSG, FatalError, Prompts, Session
from .tasks import Store
from .usage import UsageMeter
from .vfg import vfg


def is_runner(cmdline: bytes) -> bool:
    """A runner's /proc/<pid>/cmdline: this one (`python -m orchestrator`), or the TypeScript one
    it replaced (`bun run src/main.ts`), whose lock an upgrade may find still held."""
    args = cmdline.split(b"\0")
    if any(a == b"-m" and b == b"orchestrator" for a, b in zip(args, args[1:])):
        return True
    return b"bun" in cmdline or b"main.ts" in cmdline


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        pass
    except OSError:
        return False
    try:
        # A pid reused by something that is not a runner does not hold the lock.
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return is_runner(f.read())
    except OSError:
        return True


def _read_pid(file: str) -> int:
    try:
        with open(file) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def take_lock(file: str) -> bool:
    """Creates `lock` holding this pid. False when another live runner holds it; a dead pid is taken over."""
    for _ in range(3):
        try:
            fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            pid = _read_pid(file)  # 0 if it vanished meanwhile
            if pid and pid != os.getpid() and alive(pid):
                return False
            try:
                os.unlink(file)
            except OSError:
                pass  # someone else took it over first
            continue
        try:
            os.write(fd, b"%d\n" % os.getpid())
        finally:
            os.close(fd)
        return True
    return False


def release_lock(file: str) -> None:
    if _read_pid(file) == os.getpid():
        try:
            os.unlink(file)
        except OSError:
            pass  # already gone


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_prompts(cfg: Config) -> Prompts | None:
    """The session's prompt and the implementer's: orchestrator.md and implementer.md."""
    o = os.path.join(cfg.prompts_dir, "orchestrator.md")
    i = os.path.join(cfg.prompts_dir, "implementer.md")
    if not os.path.exists(o) or not os.path.exists(i):
        return None
    return Prompts(orchestrator=_read(o), implementer=_read(i))


async def _run(exit_) -> int | None:
    """Everything up to the running loops. Returns an exit code to stop at once, or None to run
    until `exit_` is called."""
    cfg = load_config()
    log("main", "starting: pid %d, %s at %s effort, implementers %s at %s effort, repo %s, state %s" % (
        os.getpid(), cfg.model, cfg.effort, cfg.implementer_model, cfg.implementer_effort, cfg.repo, cfg.orch_dir))
    mode = ignored_mode()
    if mode:
        log("main", "warning: VFG_ORCH_MODE=%s is ignored: every task goes to an implementer" % mode)

    # 2. Fatal: no vfg, or a prompt missing.
    if not shutil.which("vfg"):
        log("main", "fatal: vfg is not on the PATH")
        return 2
    prompts = load_prompts(cfg)
    if not prompts:
        log("main", "fatal: a prompt file is missing in %s" % cfg.prompts_dir)
        return 2

    # 3. One runner at a time.
    os.makedirs(cfg.orch_dir, exist_ok=True)
    if not take_lock(cfg.lock_file):
        log("main", "already running (pid %s)" % _read(cfg.lock_file).strip())
        return 1

    # 4. State; tasks closed over a day ago go.
    store = Store(cfg.state_file)
    store.load()
    dropped = store.drop_closed_before(24)
    if dropped:
        log("main", "dropped %d tasks closed over a day ago" % len(dropped))

    outbox = Outbox(store, vfg)
    store.on_reply = outbox.kick
    # Every allowed Agent call's brief gets the EXAMPLES block; its picks go into this store.
    examples.use(cfg, store)
    log("main", "script library: server %s (implementers write in made/%s/), vfg route within %g s, "
        "implementers' cache %s" % (cfg.server, cfg.server, cfg.route_timeout_s, cfg.subagent_cache_ttl))
    usage = UsageMeter(store, vfg, cfg.implementer_model)
    follower: InboxFollower | None = None
    session: Session | None = None

    # 5. A new conversation when asked for.
    if os.path.exists(cfg.fresh_file):
        os.unlink(cfg.fresh_file)
        store.state.session_id = None
        store.state.usage = {}
        log("main", "fresh: starting a new session")

    # 6. Interrupted tasks are failed, never re-run.
    interrupted = store.open_ids()
    for tid in interrupted:
        store.fail(tid, MSG.interrupted)
    if interrupted:
        log("main", "closed as interrupted: %s" % ", ".join(interrupted))
    store.save()

    loop = asyncio.get_running_loop()
    stopping = False
    tasks: set[asyncio.Task] = set()

    def spawn(coro) -> None:
        t = loop.create_task(coro)
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    async def shutdown(code: int, reason: str) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        log("main", "stopping: %s" % reason)
        if follower:
            follower.stop()
        closed = await session.stop() if session else []
        if closed:
            log("main", "closed as stopped: %s" % ", ".join(closed))
        outbox.start()  # replies recorded before the loops started go out too
        sent = await outbox.drain(10_000)
        if not sent:
            log("main", "%d replies unsent; they go out on the next start" % outbox.pending())
        store.save()
        if session:
            session.close_query()
        await outbox.stop()
        release_lock(cfg.lock_file)
        log("main", "stopped (exit %d)" % code)
        exit_(code)

    def fatal(e: Exception) -> None:
        log("main", "fatal: %s" % e)
        spawn(shutdown(2, "fatal"))

    loop.add_signal_handler(signal.SIGTERM, lambda: spawn(shutdown(0, "SIGTERM")))
    loop.add_signal_handler(signal.SIGINT, lambda: spawn(shutdown(0, "SIGINT")))

    def on_exception(_loop, context) -> None:
        e = context.get("exception")
        what = "".join(traceback.format_exception(e)) if e else context.get("message")
        log("main", "%s: %s" % ("unhandled rejection" if "future" in context or "task" in context else "uncaught exception", what))

    loop.set_exception_handler(on_exception)

    session = Session(cfg=cfg, store=store, outbox=outbox, usage=usage, prompts=prompts, on_fatal=fatal)

    # 7. The session, resuming the saved one; an API key is fatal.
    started_at = now_ms()
    try:
        await session.open(store.state.session_id)
    except FatalError as e:
        log("main", "fatal: %s" % e)
        session.close_query()
        store.save()
        release_lock(cfg.lock_file)
        return 2
    if stopping:
        return None

    # 8. Tell the session what the restart closed.
    if interrupted:
        session.push(MSG.restarted(now_ms(), interrupted))

    # 9. The inbox follower, the outbox and the watchdog.
    def on_task(tid: str, message: str) -> None:
        usage.note([tid])
        session.push(message)

    follower = InboxFollower(inbox=cfg.inbox, store=store, stale_minutes=cfg.stale_minutes, on_task=on_task)
    follower.start(250)
    outbox.start()
    session.start_watchdog(10_000)
    log("main", "following %s" % cfg.inbox)

    async def keep() -> None:
        try:
            await session.keep_alive(started_at)
        except FatalError as e:
            fatal(e)
        except Exception:
            log("main", "the session keeper broke: %s" % traceback.format_exc())

    spawn(keep())
    return None


async def _main() -> int:
    """process.exit, for asyncio: the first exit code wins, whatever is still running."""
    loop = asyncio.get_running_loop()
    exited = loop.create_future()

    def exit_(code: int) -> None:
        if not exited.done():
            exited.set_result(code)

    body = loop.create_task(_run(exit_))
    await asyncio.wait({body, exited}, return_when=asyncio.FIRST_COMPLETED)
    if not exited.done():
        try:
            code = body.result()
        except Exception:
            log("main", "fatal: %s" % traceback.format_exc())
            code = 2
        if code is not None:
            exit_(code)
    return await exited


def main() -> None:
    sys.exit(asyncio.run(_main()))
