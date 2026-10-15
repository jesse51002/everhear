"""A fake engine for the box tests: the core's process-facing API (submit, start, run, close) with
none of its insides. ``submit`` queues a request as the real engine does; each tick on ``run``'s
thread takes the queue in order, skipping a request whose future was cancelled
(``set_running_or_notify_cancel``), and answers it by its op:

    status  {"ok": true, "tick": n, "pid": ...}      echo     {"ok": true, "args": args}
    refuse  {"ok": false, "error": "refused"}        boom     raises
    shape   answers a list, not a reply              odd      a reply with a value JSON cannot hold
    never   taken, and never answered                sleep    answered after args["s"] seconds

``make(fake_io)`` builds one for ``vfg_engine.__main__.main(make=...)`` in a subprocess, its
behaviour from FAKE_ENGINE (ok, config, start, run, close) and FAKE_ENGINE_STATE (a file close
writes ``saved`` to)."""

from __future__ import annotations

import os
import queue
import threading
import time
from concurrent.futures import Future


class FakeIo:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeEngine:
    def __init__(self, tick_s=0.01, start_error=None, run_error=None, close_error=None, state=None):
        self.io = FakeIo()
        self.tick_s = tick_s
        self.start_error = start_error
        self.run_error = run_error
        self.close_error = close_error
        self.state = state
        self.inbox: queue.Queue = queue.Queue()
        self.handled: list[str] = []
        self.taken: list[Future] = []
        self.ticks = 0
        self.started = self.closed = False

    def submit(self, op, args):
        if op == "raise_submit":
            raise RuntimeError("the engine is closed")
        future: Future = Future()
        self.inbox.put((op, args, future))
        return future

    def start(self):
        if self.start_error:
            raise RuntimeError(self.start_error)
        self.started = True

    def run(self, stop):
        while not stop.is_set():
            self.tick()
            if self.run_error and self.ticks >= 3:
                raise RuntimeError(self.run_error)
            stop.wait(self.tick_s)

    def tick(self):
        self.ticks += 1
        while True:
            try:
                op, args, future = self.inbox.get_nowait()
            except queue.Empty:
                return
            if not future.set_running_or_notify_cancel():
                continue
            self.handled.append(op)
            self.taken.append(future)
            self.answer(op, args, future)

    def answer(self, op, args, future):
        if op == "status":
            future.set_result({"ok": True, "tick": self.ticks, "pid": os.getpid()})
        elif op == "echo":
            future.set_result({"ok": True, "args": args})
        elif op == "refuse":
            future.set_result({"ok": False, "error": "refused"})
        elif op == "boom":
            future.set_exception(RuntimeError("the handler broke"))
        elif op == "shape":
            future.set_result(["not", "a", "reply"])
        elif op == "odd":
            future.set_result({"ok": True, "when": {1, 2}})
        elif op == "never":
            pass
        elif op == "sleep":
            threading.Timer(float(args.get("s", 1)), future.set_result, ({"ok": True},)).start()
        else:
            future.set_result({"ok": False, "error": "no op %r" % op})

    def close(self):
        self.closed = True
        self.io.close()
        if self.state:
            with open(self.state, "w") as f:
                f.write("saved\n")
        if self.close_error:
            raise RuntimeError(self.close_error)


def make(fake_io=False):
    from vfg_engine.__main__ import ConfigError

    mode = os.environ.get("FAKE_ENGINE", "ok")
    if mode == "config":
        raise ConfigError("the engine's settings are wrong: VFG_ENGINE_TICK_HZ is not a number")
    return FakeEngine(start_error="RCON refused" if mode == "start" else None,
                      run_error="the loop broke" if mode == "run" else None,
                      close_error="the disk is full" if mode == "close" else None,
                      state=os.environ.get("FAKE_ENGINE_STATE"))


def wait_for(check, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return bool(check())
