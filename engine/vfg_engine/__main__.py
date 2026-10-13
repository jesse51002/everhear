"""python -m vfg_engine [--fake-io] -- the engine process (the game engine spec: "one engine process,
one loop"), as the systemd unit vfg-engine runs it on the box.

It reads its settings from the environment (systemd reads /opt/vfg/.env into it), takes the engine's
lock, makes the control socket (server.py), starts the engine and runs its loop on the main thread,
which the loop needs for the timer signal that cuts a slow call. The socket serves on a thread of
its own and hands each request to ``Engine.submit``. SIGTERM or SIGINT stops the loop; the socket
closes, answering what still waits, and the engine closes, saving its state.

Logs go to VFG_ENGINE_LOG (/opt/vfg/engine/engine.log) as JSON lines, one object a line: ``at``,
``level``, ``logger``, ``msg``, any fields the call gave (``extra=``), and ``exc`` with a traceback;
and to stderr, as plain lines, for the journal. The file is kept to 4 x 20 MB.

Exit codes, as the bridge's:

    0   stopped by a signal, state saved
    1   crashed: an error escaped the engine, it could not start (Minecraft not up yet, say), or
        another engine holds the lock; systemd starts it again (Restart=on-failure)
    78  a setting is wrong, or the socket or the log cannot be made where they are asked for:
        nothing a restart mends, so systemd leaves it stopped (RestartPreventExitStatus=78)

``--fake-io`` runs the engine on the testing FakeIo instead of Minecraft: for trying content and
the control socket on a PC with no server.

Under VFG_TEST=1 (the per-test engine vfg_engine.livetest starts) it refuses, exit 78, a server
whose folder (the one holding VFG_MC_PROPERTIES) has no `.vfg-test-server` marker, or whose marker
names another RCON port: a test's engine never talks to the live server.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import json
import logging
import logging.handlers
import os
import signal
import sys
import threading

from .server import DEFAULT_SOCKET, ControlServer, SetupError, one_line

EX_CONFIG = 78
EXIT_CRASH = 1
DEFAULT_LOG = "/opt/vfg/engine/engine.log"
LOG_MAX_BYTES = 20 * 1024 * 1024
LOG_BACKUPS = 3
STDERR_FORMAT = "%(levelname)s %(name)s: %(message)s"

log = logging.getLogger("vfg_engine")


class ConfigError(Exception):
    """A setting is wrong; the message names it. The process exits 78."""


class Held(Exception):
    """Another engine holds the lock."""


# -- paths -------------------------------------------------------------------------

def paths(environ=None) -> dict:
    """The process's own files: the control socket, the log, and the lock beside the socket."""
    env = os.environ if environ is None else environ
    sock = env.get("VFG_ENGINE_SOCKET") or DEFAULT_SOCKET
    logfile = env.get("VFG_ENGINE_LOG") or DEFAULT_LOG
    for name, value in (("VFG_ENGINE_SOCKET", sock), ("VFG_ENGINE_LOG", logfile)):
        if not os.path.isabs(value):
            raise ConfigError("%s must be an absolute path: %r" % (name, value))
    return {"socket": sock, "log": logfile, "lock": sock + ".lock"}


# -- logs --------------------------------------------------------------------------

_RECORD_FIELDS = frozenset(vars(logging.LogRecord("x", logging.INFO, "", 0, "", (), None))) | {
    "message", "asctime"}


class JsonLines(logging.Formatter):
    """One JSON object a line: at, level, logger, msg, the call's own fields, exc."""

    def format(self, record: logging.LogRecord) -> str:
        at = _dt.datetime.fromtimestamp(record.created, _dt.timezone.utc)
        doc = {"at": at.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (at.microsecond // 1000),
               "level": record.levelname.lower(), "logger": record.name, "msg": record.getMessage()}
        for key, value in record.__dict__.items():
            if key not in _RECORD_FIELDS and not key.startswith("_") and key not in doc:
                doc[key] = value
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            doc["stack"] = self.formatStack(record.stack_info)
        return json.dumps(doc, default=str, ensure_ascii=False)


def setup_logging(path: str, stream=None) -> list[logging.Handler]:
    """JSON lines to ``path`` and plain lines to stderr, on the root logger, so content's own
    loggers land there too. ConfigError when the log cannot be opened."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        to_file = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES,
                                                       backupCount=LOG_BACKUPS, encoding="utf-8")
    except OSError as exc:
        raise ConfigError("the engine's log %s cannot be opened: %s (VFG_ENGINE_LOG)" % (path, one_line(exc)))
    to_file.setFormatter(JsonLines())
    to_stderr = logging.StreamHandler(sys.stderr if stream is None else stream)
    to_stderr.setFormatter(logging.Formatter(STDERR_FORMAT))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (to_file, to_stderr):
        root.addHandler(handler)
    logging.captureWarnings(True)
    return [to_file, to_stderr]


# -- the lock ----------------------------------------------------------------------

def claim(path: str) -> int:
    """Hold the engine's lock for the life of the process: two engines on one world would both
    write to it. Answers the lock's fd; Held when another engine has it."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise ConfigError("the engine's lock %s cannot be made: %s (beside VFG_ENGINE_SOCKET)"
                          % (path, one_line(exc))) from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            holder = os.pread(fd, 32, 0).decode("ascii", "replace").strip() or "?"
        except OSError:
            holder = "?"
        os.close(fd)
        raise Held("another engine is running (pid %s holds %s); stop it first" % (holder, path)) from None
    os.ftruncate(fd, 0)
    os.pwrite(fd, ("%d\n" % os.getpid()).encode("ascii"), 0)
    return fd


# -- the engine --------------------------------------------------------------------

def test_server_check(fake_io: bool = False, environ=None) -> None:
    """Under VFG_TEST=1, ConfigError unless VFG_MC_PROPERTIES is a test server's (its marker)."""
    env = os.environ if environ is None else environ
    if env.get("VFG_TEST") != "1" or fake_io:
        return
    from .livetest import test_server_refusal
    why = test_server_refusal(env.get("VFG_MC_PROPERTIES") or "/opt/mc/server.properties")
    if why:
        raise ConfigError("VFG_TEST=1: %s" % why)


def build(fake_io: bool = False):
    """The engine, from the environment: the core's Settings and Engine, on McIo (or the testing
    FakeIo). ConfigError when a setting is wrong."""
    from .engine import Engine, Settings  # CONTRACT: the core's Engine(io, settings)
    try:
        settings = Settings.from_env()
    except Exception as exc:
        raise ConfigError("the engine's settings are wrong: %s" % one_line(exc)) from exc
    if fake_io:
        from .testing import FakeIo
        io = FakeIo()
    else:
        from .mc import McIo
        io = McIo()  # the server is VFG_MC_PROPERTIES's (else /opt/mc/server.properties)
    return Engine(io, settings)


def run(engine, server: ControlServer, stop: threading.Event) -> int:
    """Start the engine, serve the socket, run the loop until ``stop``; then close both. The exit code."""
    failed = threading.Event()

    def socket_died():
        failed.set()
        stop.set()  # an engine nobody can reach stops, saving its state, and is started again

    try:
        engine.start()
    except Exception:
        log.exception("the engine could not start; its saved state is left as it was")
        server.close()
        _close_io(engine)
        return EXIT_CRASH
    server.start(on_fail=socket_died)
    log.info("the engine is running; control socket %s", server.path)
    try:
        engine.run(stop)
    except Exception:
        log.exception("an error escaped the engine's loop; stopping it")
        failed.set()
    finally:
        server.close()
        if not _close(engine):
            failed.set()
    if failed.is_set():
        return EXIT_CRASH
    log.info("the engine stopped; its state is saved")
    return 0


def _close_io(engine) -> None:
    """After a failed start, only the Io is closed: the engine's close() saves its state, and an
    engine that did not start (Minecraft not up yet, say) has not restored it, so saving would
    write an empty state over the one on disk."""
    io = getattr(engine, "io", None)
    if io is None:
        return
    try:
        io.close()
    except Exception:
        log.exception("closing the engine's connection to Minecraft failed")


def _close(engine) -> bool:
    try:
        engine.close()
    except Exception:
        log.exception("closing the engine failed: its state may not be saved")
        return False
    return True


def main(argv=None, make=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vfg_engine", description=__doc__.split("\n")[0])
    parser.add_argument("--fake-io", action="store_true",
                        help="run on the testing FakeIo, with no Minecraft server")
    args = parser.parse_args(argv)
    make = make or build

    try:
        where = paths()
        handlers = setup_logging(where["log"])
    except ConfigError as exc:
        sys.stderr.write("vfg-engine: %s\n" % exc)
        return EX_CONFIG
    stop = threading.Event()
    previous = {}

    def on_signal(signum, frame):
        log.info("signal %s: stopping", signal.Signals(signum).name)
        stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, on_signal)
    lock = None
    try:
        test_server_check(args.fake_io)
        lock = claim(where["lock"])
        engine = make(args.fake_io)
        # Made before the engine starts, while this is the only thread: the socket is bound
        # under a umask, which is the whole process's.
        server = ControlServer(where["socket"], engine)
        return run(engine, server, stop)
    except (ConfigError, SetupError) as exc:
        log.error("%s", exc)
        return EX_CONFIG
    except Held as exc:
        log.error("%s", exc)
        return EXIT_CRASH
    except Exception:
        log.exception("the engine could not be made")
        return EXIT_CRASH
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if lock is not None:
            os.close(lock)
        for handler in handlers:
            logging.getLogger().removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    sys.exit(main())
