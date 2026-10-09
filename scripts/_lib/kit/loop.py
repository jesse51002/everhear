"""The loop a script runs in, who it is for, and work in the background.

    for p in loop(items.clicks(v["player"], "Railgun", cooldown=v["cooldown"])):
        fire(p)                              # once per click
    for n in loop(every=1):
        ...                                  # once a second while anyone is online

A stop (SIGTERM from `vfg script stop`, or Ctrl-C) runs every cleanup the script and the kit
registered with mc.on_stop, newest first (bossbars, forceloads, gamerules, items given,
objectives the kit made), then kills @e[tag=TAG] and exits 0, all within 2 s. Commands from
other threads are dropped from the moment a stop begins, so nothing comes back after it.
"""
import random
import sys
import threading
import time
import traceback
import types

import mc
from .values import now

_online = {"names": [], "at": 0.0}
_lock = threading.Lock()


def online():
    """Everyone online, as bare names ("[Owner] Steve" is "Steve"); read at most every second."""
    with _lock:
        if time.time() - _online["at"] >= 1.0:
            _online["names"] = mc.players()
            _online["at"] = time.time()
        return list(_online["names"])


def who(player="@a"):
    """The online players a player value names: "@a" everyone, "@r" one at random, and
    "Steve,Alex" those of them who are online."""
    player = now(player) or "@a"
    names = online()
    if player == "@a":
        return names
    if player == "@r":
        return [random.choice(names)] if names else []
    want = [mc.bare(n) for n in str(player).split(",") if n.strip()]
    return [n for n in want if n in names]


def loop(source=None, every=0.1, idle=2.0):
    """Without a source: yield a pass number, 0, 1, 2..., every `every` s (at least 0.05)
    while anyone is online, looking again every `idle` s while nobody is. With a source (a
    kit generator such as items.clicks): yield what it yields. Ends when the script stops."""
    if source is not None:
        for x in source:
            if mc.stopping():
                return
            yield x
        return
    n = 0
    while not mc.stopping():
        if not online():
            time.sleep(now(idle))
            continue
        yield n
        n += 1
        time.sleep(max(0.05, now(every)))


def background(fn, *args, **kwargs):
    """Run fn(*args) in a thread of its own, so a long job (a crater) does not hold up the
    loop. An error in it is logged with where it happened, not raised. Returns the thread."""
    def go():
        try:
            fn(*args, **kwargs)
        except Exception as e:
            if mc.stopping():
                return
            where = traceback.extract_tb(sys.exc_info()[2])[-1]
            mc.log("error in %s: %s: %s (line %d of %s)" % (getattr(fn, "__name__", "job"), type(e).__name__, e,
                                                             where.lineno, where.filename.split("/")[-1]))
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


def wait(seconds):
    """Sleep up to `seconds`, ending early if the script is stopping. False if it is."""
    end = time.time() + max(0.0, now(seconds))
    while time.time() < end:
        if mc.stopping():
            return False
        time.sleep(min(0.1, max(0.0, end - time.time())))
    return not mc.stopping()


class _Callable(types.ModuleType):
    """So that `from kit import loop` can be called: for p in loop(...)."""

    def __call__(self, *args, **kwargs):
        return loop(*args, **kwargs)


sys.modules[__name__].__class__ = _Callable
